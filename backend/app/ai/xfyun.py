"""讯飞开放平台：超拟人语音合成（聆小璇等）与语音听写（流式识别）。

两条 WebSocket 接口共用同一套 APPID / APIKey / APISecret，鉴权都是把
`host / date / request-line` 用 APISecret 做 HMAC-SHA256 后放进 URL 查询串。

- 合成：wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/mcd9m97e6，一次性发全文，分帧收回 mp3
- 听写：wss://iat-api.xfyun.cn/v2/iat，16kHz 16bit 单声道 PCM 按 40ms 一帧上传，
  边传边收中间结果；服务端检测到静音（vad_eos）自动结束一句，正好用来做"机器狗一直在听"。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import struct
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import urlencode

import websockets

from app.ai.base import ASRProvider, TTSProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TTS_HOST = "cbm01.cn-huabei-1.xf-yun.com"
TTS_PATH = "/v1/private/mcd9m97e6"
IAT_HOST = "iat-api.xfyun.cn"
IAT_PATH = "/v2/iat"

# 听写每帧 40ms：16000Hz × 2 字节 × 0.04s
IAT_FRAME_BYTES = 1280
IAT_FRAME_INTERVAL = 0.04
# 老人说话慢、停顿多，一句话结束的静音判定放宽到 2.5 秒
IAT_VAD_EOS_MS = 2500
CONNECT_TIMEOUT = 15


def build_auth_url(host: str, path: str, api_key: str, api_secret: str) -> str:
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    digest = hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    authorization = base64.b64encode(
        (
            f'api_key="{api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        ).encode()
    ).decode()
    return f"wss://{host}{path}?" + urlencode({"authorization": authorization, "date": date, "host": host})


def _credentials() -> tuple[str, str, str]:
    return settings.xfyun_app_id, settings.xfyun_api_key, settings.xfyun_api_secret


# ---------- 语音合成 ----------


class XfyunTTS(TTSProvider):
    """超拟人合成，默认音色是控制台里开通的聆小璇。"""

    name = "xfyun"

    def __init__(self, vcn: str = "", speed: int | None = None) -> None:
        self.vcn = vcn or settings.xfyun_tts_vcn
        self.speed = settings.xfyun_tts_speed if speed is None else speed

    # 同时开几路合成：一段 70 字的回答整句合成要 13 秒，按句拆开并行只要最长那句的时间
    MAX_PARALLEL = 3

    async def synthesize(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        digest = hashlib.md5(f"xfyun:{self.vcn}:{self.speed}:{text}".encode()).hexdigest()[:16]
        path = settings.media_dir / f"tts_{digest}.mp3"
        if path.exists():
            return f"/media/{path.name}"

        sentences = split_sentences(text)
        semaphore = asyncio.Semaphore(self.MAX_PARALLEL)

        async def one(sentence: str) -> bytes:
            async with semaphore:
                return await self._synthesize_once(sentence)

        parts = await asyncio.gather(*(one(s) for s in sentences))
        # mp3 帧可以直接拼接，浏览器和播放器都能连续播放
        path.write_bytes(b"".join(parts))
        return f"/media/{path.name}"

    async def _synthesize_once(self, text: str) -> bytes:
        app_id, api_key, api_secret = _credentials()
        request = {
            "header": {"app_id": app_id, "status": 2},
            "parameter": {
                "tts": {
                    "vcn": self.vcn,
                    "speed": self.speed,
                    "volume": 50,
                    "pitch": 50,
                    "bgs": 0,
                    "reg": 0,
                    "rdn": 0,
                    "rhy": 0,
                    "audio": {
                        "encoding": "lame",
                        "sample_rate": 24000,
                        "channels": 1,
                        "bit_depth": 16,
                        "frame_size": 0,
                    },
                }
            },
            "payload": {
                "text": {
                    "encoding": "utf8",
                    "compress": "raw",
                    "format": "plain",
                    "status": 2,
                    "seq": 0,
                    "text": base64.b64encode(text.encode()).decode(),
                }
            },
        }
        chunks: list[bytes] = []
        async with websockets.connect(
            build_auth_url(TTS_HOST, TTS_PATH, api_key, api_secret), open_timeout=CONNECT_TIMEOUT
        ) as ws:
            await ws.send(json.dumps(request, ensure_ascii=False))
            async for raw in ws:
                message = json.loads(raw)
                header = message.get("header") or {}
                if header.get("code", 0) != 0:
                    raise RuntimeError(f"讯飞合成失败 code={header.get('code')} {header.get('message')}")
                audio = (message.get("payload") or {}).get("audio") or {}
                if audio.get("audio"):
                    chunks.append(base64.b64decode(audio["audio"]))
                if audio.get("status") == 2:
                    break
        if not chunks:
            raise RuntimeError("讯飞合成没有返回音频")
        return b"".join(chunks)


def split_sentences(text: str, max_chars: int = 60) -> list[str]:
    """按句号、问号、感叹号、分号切句，太长的句子再按逗号切；短文本不切。"""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in "。！？；!?;\n" or (ch in "，," and len(current) >= max_chars):
            if current.strip():
                pieces.append(current.strip())
            current = ""
    if current.strip():
        pieces.append(current.strip())
    # 合并过短的碎片，免得一两个字也占一路连接
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + len(piece) <= max_chars // 2:
            merged[-1] += piece
        else:
            merged.append(piece)
    return merged or [text]


# ---------- 语音听写（流式） ----------


class IatSession:
    """一句话一个会话：喂 PCM 进来，中间结果与最终文本从 events() 里出去。

    用法：
        session = IatSession()
        await session.open()
        asyncio.create_task(pump())      # 不断 await session.feed(pcm)，说完 await session.finish()
        async for event in session.events():
            event == {"type": "partial", "text": ...} | {"type": "final", "text": ...}
    服务端在 vad_eos 毫秒的静音后会自己结束会话并给出 final，不需要客户端判断说完没说完。
    """

    def __init__(self, vad_eos_ms: int = IAT_VAD_EOS_MS, realtime: bool = True) -> None:
        self.vad_eos_ms = vad_eos_ms
        # 麦克风本身就是实时的，帧间不必再等；整段音频回灌时也只稍作节流，别把服务端打爆
        self.frame_gap = IAT_FRAME_INTERVAL * 0.25 if realtime else 0.004
        self._ws = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._sender: asyncio.Task | None = None
        self._segments: dict[int, str] = {}
        self.closed = False
        self.error = ""

    async def open(self) -> None:
        _app_id, api_key, api_secret = _credentials()
        self._ws = await websockets.connect(
            build_auth_url(IAT_HOST, IAT_PATH, api_key, api_secret), open_timeout=CONNECT_TIMEOUT
        )
        self._sender = asyncio.create_task(self._send_loop())

    async def feed(self, pcm: bytes) -> None:
        if not self.closed:
            await self._queue.put(pcm)

    async def finish(self) -> None:
        """客户端主动说"这句完了"；正常情况下靠服务端静音检测，不用调这个。"""
        if not self.closed:
            await self._queue.put(None)

    async def close(self) -> None:
        self.closed = True
        if self._sender:
            self._sender.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _send_loop(self) -> None:
        app_id, _key, _secret = _credentials()
        first = True
        pending = bytearray()
        try:
            while True:
                chunk = await self._queue.get()
                if chunk is None:
                    await self._send_frame(2, bytes(pending), app_id, first)
                    return
                pending.extend(chunk)
                # 按 40ms 一帧切，攒不够一帧就等下一块
                while len(pending) >= IAT_FRAME_BYTES:
                    frame, pending = bytes(pending[:IAT_FRAME_BYTES]), pending[IAT_FRAME_BYTES:]
                    await self._send_frame(0 if first else 1, frame, app_id, first)
                    first = False
                    await asyncio.sleep(self.frame_gap)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            logger.warning("讯飞听写发送中断: %s", exc)

    async def _send_frame(self, status: int, pcm: bytes, app_id: str, first: bool) -> None:
        frame: dict = {
            "data": {
                "status": status,
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": base64.b64encode(pcm).decode(),
            }
        }
        if first:
            frame["common"] = {"app_id": app_id}
            frame["business"] = {
                "language": "zh_cn",
                "domain": "iat",
                "accent": "mandarin",
                "vad_eos": self.vad_eos_ms,
                # 动态修正：中间结果会被后面更准确的结果替换，转写显示更像真人在听
                "dwa": "wpgs",
                "ptt": 1,
            }
        await self._ws.send(json.dumps(frame))

    def _apply(self, result: dict) -> str:
        """把 wpgs 模式的增量结果拼成当前完整文本。"""
        sn = result.get("sn")
        text = "".join(cw.get("w", "") for ws in result.get("ws", []) for cw in ws.get("cw", []))
        if result.get("pgs") == "rpl":
            low, high = result.get("rg", [sn, sn])
            for key in [k for k in self._segments if low <= k <= high]:
                del self._segments[key]
        if sn is not None:
            self._segments[sn] = text
        return "".join(self._segments[k] for k in sorted(self._segments))

    async def events(self) -> AsyncIterator[dict]:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if message.get("code", 0) != 0:
                    self.error = f"code={message.get('code')} {message.get('message')}"
                    yield {"type": "error", "detail": self.error}
                    break
                data = message.get("data") or {}
                result = data.get("result")
                if result:
                    text = self._apply(result)
                    yield {"type": "partial", "text": text, "status": data.get("status"), "ls": result.get("ls")}
                if data.get("status") == 2:
                    yield {"type": "final", "text": "".join(self._segments[k] for k in sorted(self._segments))}
                    break
        except websockets.ConnectionClosed as exc:
            if not self.closed:
                self.error = f"连接关闭 {exc.code}"
                yield {"type": "error", "detail": self.error}
        finally:
            await self.close()


class XfyunASR(ASRProvider):
    """整段音频识别：把 WAV/PCM 重采样到 16k 后灌进一个听写会话。"""

    name = "xfyun"

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        pcm = to_pcm16k(audio)
        if not pcm:
            return ""
        session = IatSession(vad_eos_ms=1500, realtime=False)
        await session.open()

        async def pump() -> None:
            for offset in range(0, len(pcm), IAT_FRAME_BYTES * 4):
                await session.feed(pcm[offset : offset + IAT_FRAME_BYTES * 4])
            await session.finish()

        pump_task = asyncio.create_task(pump())
        final = ""
        try:
            async for event in session.events():
                if event["type"] == "final":
                    final = event["text"]
                elif event["type"] == "error":
                    raise RuntimeError(f"讯飞听写失败 {event['detail']}")
        finally:
            pump_task.cancel()
        return final.strip()


# ---------- 音频工具 ----------


def to_pcm16k(audio: bytes) -> bytes:
    """WAV（任意采样率、单/双声道、16bit）或裸 PCM -> 16kHz 单声道 16bit PCM。"""
    sample_rate, channels, data = 16000, 1, audio
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        offset = 12
        fmt_rate, fmt_channels, bits = 16000, 1, 16
        while offset + 8 <= len(audio):
            chunk_id = audio[offset : offset + 4]
            size = struct.unpack("<I", audio[offset + 4 : offset + 8])[0]
            body = audio[offset + 8 : offset + 8 + size]
            if chunk_id == b"fmt ":
                _fmt, fmt_channels, fmt_rate = struct.unpack("<HHI", body[:8])
                bits = struct.unpack("<H", body[14:16])[0]
            elif chunk_id == b"data":
                data = body
                break
            offset += 8 + size + (size % 2)
        if bits != 16:
            return b""
        sample_rate, channels = fmt_rate, fmt_channels

    samples = list(struct.unpack(f"<{len(data) // 2}h", data[: len(data) // 2 * 2]))
    if channels == 2:
        samples = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)]
    if sample_rate != 16000 and samples:
        ratio = sample_rate / 16000
        length = int(len(samples) / ratio)
        resampled = []
        for i in range(length):
            position = i * ratio
            index = int(position)
            frac = position - index
            nxt = samples[min(index + 1, len(samples) - 1)]
            resampled.append(int(samples[index] * (1 - frac) + nxt * frac))
        samples = resampled
    return struct.pack(f"<{len(samples)}h", *samples)
