"""校验机器狗音频通道：麦克风音频 -> 讯飞流式听写 -> 智能体 -> 聆小璇音色。

本脚本假装自己是机器狗：把一段合成语音（当作老人在说话）按真实节奏推进
/ws/dog-audio/{device_id}，随后继续送几秒带底噪的"环境音"，让服务端的静音检测收尾。
期望依次收到 ready -> partial -> transcript -> intent/stage/token -> final(audio_url) -> idle。

用法: python scripts/check_dog_ears.py [--device-id DOG-LJG-001] [--text 我最近血压怎么样]
"""

import argparse
import asyncio
import json
import random
import struct
import sys
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

BASE = "http://127.0.0.1:8080"
FRAME = 1280 * 2  # 80ms @16k


def noise(frames: int) -> bytes:
    return struct.pack(f"<{1280 * frames}h", *[random.randint(-8, 8) for _ in range(1280 * frames)])


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", default="DOG-LJG-001")
    parser.add_argument("--text", default="我最近血压怎么样")
    parser.add_argument("--base-url", default=BASE)
    args = parser.parse_args()

    client = httpx.Client(timeout=120, base_url=args.base_url)
    token = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    status = client.get("/api/v1/agent/status").json()
    print(f"ASR {status['asr']['provider']}（流式 {status['asr']['streaming']}），TTS {status['tts']['provider']} 音色 {status['tts'].get('voice')}")

    # 用平台自己的合成能力造一段"老人说的话"，再转成 16k PCM 当麦克风输入
    from app.ai.xfyun import to_pcm16k

    tts = client.post("/api/v1/agent/tts", params={"text": args.text}).json()
    audio_url = tts["audio_url"]
    raw = client.get(audio_url).content
    if audio_url.endswith(".mp3"):
        # 聆小璇是 mp3，听写需要 PCM：这里改用百炼的 wav 样本兜底
        samples = sorted(Path("backend/media").glob("tts_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not samples:
            print("没有可用的 wav 样本，无法模拟麦克风输入")
            return 1
        raw = samples[0].read_bytes()
        print(f"麦克风样本改用 {samples[0].name}")
    pcm = to_pcm16k(raw)
    print(f"模拟老人说话 {len(pcm) / 32000:.1f} 秒")

    ws_url = args.base_url.replace("http", "ws") + f"/ws/dog-audio/{args.device_id}?token={token}"
    events: list[dict] = []
    started = time.perf_counter()
    async with websockets.connect(ws_url, open_timeout=15) as ws:
        ready = json.loads(await ws.recv())
        print(f"ready: {ready}")
        events.append(ready)

        async def pump():
            for offset in range(0, len(pcm), FRAME):
                await ws.send(pcm[offset : offset + FRAME])
                await asyncio.sleep(0.08)
            for _ in range(45):
                await ws.send(noise(2))
                await asyncio.sleep(0.08)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw_event = await asyncio.wait_for(ws.recv(), timeout=60)
                event = json.loads(raw_event)
                events.append(event)
                kind = event.get("event")
                stamp = f"{time.perf_counter() - started:5.1f}s"
                if kind == "partial":
                    print(f"  [{stamp}] 听到：{event['text']}")
                elif kind == "transcript":
                    print(f"  [{stamp}] 整句：{event['text']}")
                elif kind == "stage":
                    print(f"  [{stamp}] 阶段 {event['stage']}：{event.get('detail', '')}")
                elif kind == "final":
                    reply = event["reply"]
                    print(f"  [{stamp}] 回答：{reply['reply_text']}")
                    print(f"           音频：{reply['audio_url']}  意图：{reply['intent']}  依据：{reply['tools_used']}")
                elif kind == "idle":
                    print(f"  [{stamp}] 机器狗重新开始听")
                    break
                elif kind == "error":
                    print(f"  [{stamp}] 错误：{event.get('detail')}")
                    break
        finally:
            pump_task.cancel()

        # 文字通道：语气词不该被回答；闲聊不该把读数再念一遍
        await ws.send(json.dumps({"type": "text", "text": "嗯嗯。"}))
        filler_event = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print(f"  语气词“嗯嗯。” -> {filler_event.get('event')}")

        await ws.send(json.dumps({"type": "text", "text": "你还好吗"}))
        chat_reply = None
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if event.get("event") == "final":
                chat_reply = event["reply"]
            elif event.get("event") in ("idle", "error"):
                break
        if chat_reply:
            print(f"  闲聊“你还好吗” -> [{chat_reply['intent']}] {chat_reply['reply_text']}")

    kinds = [e.get("event") for e in events]
    final = next((e["reply"] for e in events if e.get("event") == "final"), None)
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  通过 " if cond else "  失败 ") + label)
        ok = ok and bool(cond)

    print("\n断言：")
    check("收到 ready 且启用流式识别", ready.get("streaming") is True)
    check("有中间转写", "partial" in kinds)
    check("有整句转写", "transcript" in kinds)
    check("有最终回答", final is not None)
    if final:
        check("回答带称呼", any(w in final["reply_text"] for w in ("爷爷", "奶奶")))
        check("回答有讯飞合成音频", final["audio_url"].endswith(".mp3"))
        audio_ok = client.get(final["audio_url"]).status_code == 200
        check("音频可下载", audio_ok)
    check("回答后回到倾听", "idle" in kinds)
    check("语气词被忽略", filler_event.get("event") == "ignored")
    check("闲聊有回答", chat_reply is not None)
    if chat_reply:
        text = chat_reply["reply_text"]
        check("闲聊回答简短（≤60 字）", len(text) <= 60)
        check("闲聊不复述读数", not any(w in text for w in ("mmHg", "次/分", "%", "评分")))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
