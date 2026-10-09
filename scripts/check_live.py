"""校验首页实时体征接口，并观察数值是否随新数据变化。

用法: python scripts/check_live.py
"""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8080/api/v1"


def show(snapshots: list[dict], title: str) -> None:
    print(f"\n--- {title} ---")
    for item in snapshots:
        vitals = item["vitals"]

        def value(key: str) -> str:
            stat = vitals.get(key) or {}
            latest = stat.get("latest")
            if latest is None:
                return "--"
            return f"{latest:.1f}" if key == "temperature" else str(int(latest))

        print(
            f"{item['elder_name']:6s} 风险={item['risk_level']:8s} "
            f"心率={value('heart_rate'):>3s} 血氧={value('spo2'):>3s} "
            f"血压={value('systolic')}/{value('diastolic')} 体温={value('temperature')} "
            f"更新于{item['seconds_since_update']}秒前 "
            f"陈旧={item['is_stale']} 采样点={len(item['series'])} "
            f"手表={'在线' if item['watch_online'] else '离线'} "
            f"外出={item['active_task_id'] or '无'}"
        )
        if item["abnormal_items"]:
            print(f"       异常：{'；'.join(item['abnormal_items'])}")


def main() -> int:
    client = httpx.Client(timeout=30, base_url=BASE)
    token = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    before = client.get("/health/live", params={"points": 30}).json()
    if not before:
        print("没有老人档案，请先执行 python -m app.seed")
        return 1
    show(before, "初始状态")

    elder_id = before[0]["elder_id"]
    print(f"\n注入「血氧偏低」数据到 {before[0]['elder_name']} …")
    client.post(
        "/simulator/health-profile",
        json={"elder_id": elder_id, "profile": "hypoxia", "points": 6},
    )
    time.sleep(1)
    after = client.get("/health/live", params={"points": 30}).json()
    show(after, "注入异常数据后")

    print("\n恢复为「健康平稳」…")
    client.post(
        "/simulator/health-profile",
        json={"elder_id": elder_id, "profile": "normal", "points": 14},
    )
    time.sleep(1)
    restored = client.get("/health/live", params={"points": 30}).json()
    show(restored, "恢复正常后")

    target_before = next(i for i in before if i["elder_id"] == elder_id)
    target_after = next(i for i in after if i["elder_id"] == elder_id)
    changed = (
        target_before["vitals"]["spo2"]["latest"] != target_after["vitals"]["spo2"]["latest"]
    )
    risk_escalated = target_after["risk_level"] == "danger"

    print()
    if changed and risk_escalated:
        print("实时数据随设备上报正确变化，风险等级同步升级")
        return 0
    print("FAIL 数据未按预期变化")
    return 1


if __name__ == "__main__":
    sys.exit(main())
