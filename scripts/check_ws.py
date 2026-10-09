"""校验 WebSocket 实时推送：建连时能收到历史事件，之后能收到新事件。

用法: python scripts/check_ws.py
"""

import asyncio
import json
import sys

import httpx
import websockets

API = "http://127.0.0.1:8080/api/v1"
WS = "ws://127.0.0.1:8080/ws"


async def main() -> int:
    async with httpx.AsyncClient(timeout=30, base_url=API) as client:
        resp = await client.post(
            "/auth/login", json={"username": "admin", "password": "admin123"}
        )
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        elders = (await client.get("/elders", headers=headers)).json()
        elder_id = elders[0]["id"]

        received = []
        async with websockets.connect(f"{WS}?token={token}") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            history = first.get("data", {}).get("recent", [])
            print(f"建连消息: {first['event']}，携带历史事件 {len(history)} 条")
            for item in history[-3:]:
                print(f"  历史 · {item['event']}")

            async def collect():
                while len(received) < 3:
                    raw = await ws.recv()
                    if raw == "pong":
                        continue
                    message = json.loads(raw)
                    if message.get("event") in ("heartbeat", "connected"):
                        continue
                    received.append(message)
                    print(f"  实时 · {message['event']}")

            task = asyncio.create_task(collect())
            await asyncio.sleep(0.5)
            print("\n触发一次对话，观察推送：")
            await client.post(
                "/simulator/speak",
                json={"elder_id": elder_id, "text": "今天天气好不好"},
                headers=headers,
            )
            try:
                await asyncio.wait_for(task, timeout=15)
            except TimeoutError:
                task.cancel()

    ok_history = isinstance(history, list)
    ok_live = len(received) > 0
    print()
    if ok_history and ok_live:
        print(f"WebSocket 正常：历史 {len(history)} 条，实时收到 {len(received)} 条")
        return 0
    print(f"FAIL 历史={ok_history} 实时={ok_live}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
