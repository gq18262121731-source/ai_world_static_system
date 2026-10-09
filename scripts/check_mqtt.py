"""校验设备模拟器经 MQTT 上行的数据是否已被平台接收。

用法: python scripts/check_mqtt.py [--elder-id 1]
"""

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--elder-id", type=int, default=1)
    args = parser.parse_args()

    client = httpx.Client(timeout=30, base_url=f"{args.base_url.rstrip('/')}/api/v1")
    token = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    mqtt = client.get("/devices/mqtt/status", headers=headers).json()
    print(
        f"MQTT {mqtt['host']}:{mqtt['port']} "
        f"{'已连接' if mqtt['connected'] else '未连接'}，"
        f"已接收 {mqtt['received']} 条 / 已下发 {mqtt['published']} 条"
    )

    record = client.get(
        "/health/latest", params={"elder_id": args.elder_id}, headers=headers
    ).json()
    if record:
        print(
            f"最新手表数据 {record['recorded_at']} 来自 {record['device_id']}："
            f"心率 {record['heart_rate']}，血氧 {record['spo2']}%，"
            f"血压 {record['systolic']}/{record['diastolic']}"
        )
    else:
        print("尚未收到手表数据")

    print("设备状态：")
    devices = client.get(
        "/devices", params={"elder_id": args.elder_id}, headers=headers
    ).json()
    for device in devices:
        print(
            f"  {device['device_id']:16s} {device['status']:8s} "
            f"电量 {device['battery']:3d}%  最后心跳 {device['last_seen_at']}"
        )

    return 0 if mqtt["received"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
