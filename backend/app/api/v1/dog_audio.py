"""机器狗的"耳朵"：音频流 WebSocket。

协议（浏览器演示与真机器狗共用）：
  1. 连上 /ws/dog-audio/{device_id}?token=...，收到 {"event": "ready", "streaming": bool}
  2. 检测到有人说话时连续发二进制 16kHz 16bit 单声道 PCM；说完后再送 2~3 秒环境音，
     让服务端的静音检测自己收尾；也可以发 {"type": "end"} 强制结束这一句
  3. 平台推回：
       {"event": "partial", "text"}      边说边出的中间文字
       {"event": "transcript", "text"}   这句话的最终文字
       orchestrator 的 intent / stage / token / final 事件（final.reply.audio_url 是机器狗要放的音频）
       {"event": "busy"}                 上一句还没答完，这段音频被忽略
       {"event": "idle"}                 回答完毕，重新开始听
  4. {"type": "text", "text": "..."} 直接送文字（没有麦克风时的兜底）

讯飞听写不可用时退化为：攒完整句音频，收到 end 再整段识别。
"""

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.agent import orchestrator
from app.ai.factory import get_asr, streaming_asr_available
from app.api.v1.ws import _allowed_elder_ids
from app.core.db import SessionLocal, session_scope
from app.core.logging import get_logger
from app.models import Elder
from app.services import device_service

router = APIRouter(tags=["机器狗音频"])
logger = get_logger(__name__)

MAX_UTTERANCE_BYTES = 8 * 1024 * 1024

_PUNCTUATION = "，。！？；、,.!?;:：…—~～ \u3000\n"
_FILLERS = "嗯啊哦呃额哎唉呀哈嘿噢喔哼诶欸嗐"
# 这些短词一个人自言自语时常说，但不是在跟机器狗讲话
_STANDALONE_NOISE = {"我们", "他们", "你们", "这个", "那个", "然后", "就是", "好的", "好", "对", "是"}


def is_filler(text: str) -> bool:
    """只有语气词、或短到听不出意思的碎片，不值得回答；跟求助有关的一个字也不能漏。"""
    core = "".join(ch for ch in (text or "") if ch not in _PUNCTUATION)
    if not core:
        return True
    if any(ch in core for ch in "救疼痛摔帮晕"):
        return False
    if all(ch in _FILLERS for ch in core):
        return True
    if len(core) <= 2 and core in _STANDALONE_NOISE:
        return True
    return len(core) < 2


class DogEars:
    """一条连接的状态：当前听写会话、是否正在回答、发送互斥。"""

    def __init__(self, websocket: WebSocket, device_id: str, elder_id: int) -> None:
        self.websocket = websocket
        self.device_id = device_id
        self.elder_id = elder_id
        self.streaming = streaming_asr_available()
        self.session = None
        self.reader: asyncio.Task | None = None
        self.busy = False
        self.busy_notified = False
        self.buffer = bytearray()
        self._lock = asyncio.Lock()

    async def send(self, payload: dict) -> None:
        reply = payload.get("reply")
        if reply is not None and hasattr(reply, "model_dump"):
            payload = {**payload, "reply": reply.model_dump(mode="json")}
        async with self._lock:
            await self.websocket.send_text(json.dumps(payload, ensure_ascii=False, default=str))

    # ---------- 音频 ----------

    async def on_audio(self, chunk: bytes) -> None:
        if self.busy:
            # 机器狗正在想或正在说，这时候听到的多半是它自己的声音
            if not self.busy_notified:
                self.busy_notified = True
                await self.send({"event": "busy"})
            return
        if not self.streaming:
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_UTTERANCE_BYTES:
                await self.send({"event": "error", "detail": "单句音频过长，已丢弃"})
                self.buffer.clear()
            return
        if self.session is None or self.session.closed:
            await self._open_session()
        await self.session.feed(chunk)

    async def _open_session(self) -> None:
        from app.ai.xfyun import IatSession

        session = IatSession()
        try:
            await session.open()
        except Exception as exc:  # noqa: BLE001
            logger.warning("讯飞听写会话打不开，退回整句识别: %s", exc)
            self.streaming = False
            await self.send({"event": "error", "detail": "流式识别暂不可用，改为说完后整句识别"})
            return
        self.session = session
        self.reader = asyncio.create_task(self._read_session(session))

    async def _read_session(self, session) -> None:
        final_text = ""
        try:
            async for event in session.events():
                if event["type"] == "partial":
                    await self.send({"event": "partial", "text": event["text"]})
                elif event["type"] == "final":
                    final_text = event["text"].strip()
                elif event["type"] == "error":
                    await self.send({"event": "error", "detail": f"识别中断：{event['detail']}"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("听写会话读取异常: %s", exc)
        finally:
            if self.session is session:
                self.session = None
        if final_text:
            await self.answer(final_text)

    async def on_end(self, typed_text: str = "") -> None:
        """客户端说"这句完了"。"""
        if typed_text:
            await self.answer(typed_text)
            return
        if self.streaming:
            if self.session is not None and not self.session.closed:
                await self.session.finish()
            return
        audio = bytes(self.buffer)
        self.buffer.clear()
        if not audio:
            return
        text = await get_asr().transcribe(audio, "utterance.wav")
        if not text:
            await self.send({"event": "error", "detail": "没有识别到有效语音"})
            return
        await self.answer(text)

    # ---------- 回答 ----------

    async def answer(self, text: str) -> None:
        if self.busy:
            return
        if is_filler(text):
            # 老人只是"嗯""啊"了一声，或者被环境里飘过来的半个词触发，家人不会接这种话
            await self.send({"event": "ignored", "text": text})
            return
        self.busy = True
        self.busy_notified = False
        try:
            await self.send({"event": "transcript", "text": text})
            async with session_scope() as db:
                elder = await db.get(Elder, self.elder_id)
                async for event in orchestrator.handle_utterance_events(
                    db, elder, text, self.device_id, None, orchestrator.CHANNEL_DOG
                ):
                    await self.send(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("机器狗对话失败")
            await self.send({"event": "error", "detail": str(exc)[:200]})
        finally:
            self.busy = False
            await self.send({"event": "idle"})

    async def close(self) -> None:
        if self.reader:
            self.reader.cancel()
        if self.session:
            await self.session.close()


@router.websocket("/ws/dog-audio/{device_id}")
async def dog_audio_endpoint(websocket: WebSocket, device_id: str, token: str = Query("")):
    elder_ids = await _allowed_elder_ids(token)
    if elder_ids is not None and not elder_ids:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    async with SessionLocal() as db:
        elder_id = await device_service.resolve_elder_id(db, device_id)
    if elder_id is None or (elder_ids is not None and elder_id not in elder_ids):
        await websocket.send_json({"event": "error", "detail": "设备未绑定老人或无权限"})
        await websocket.close(code=4403)
        return

    ears = DogEars(websocket, device_id, elder_id)
    await ears.send({"event": "ready", "device_id": device_id, "elder_id": elder_id, "streaming": ears.streaming})
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                await ears.on_audio(message["bytes"])
                continue
            text = message.get("text") or ""
            if text == "ping":
                await websocket.send_text("pong")
                continue
            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                continue
            kind = command.get("type")
            if kind == "end":
                await ears.on_end()
            elif kind == "text":
                await ears.on_end(str(command.get("text") or "").strip())
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("机器狗音频通道异常关闭: %s", exc)
    finally:
        await ears.close()
