"""校验真实手环数据是否已接入平台。

联调时用来确认：设备已注册并绑定、真机数据在入库、分包字段被正确合并。

用法: python scripts/check_wristband.py [--elder-id 1]
"""

import argparse
import re
import sys

import httpx

BASE = "http://127.0.0.1:8080/api/v1"
# 真机网关上报时带的来源标记，用于和演示数据区分
SOURCE_SERIAL = "serial"
# 真机设备编号就是手环 MAC（12 位十六进制），模拟器用的是 WATCH-LJG-001 这类名字
MAC_DEVICE_ID = re.compile(r"^[0-9A-F]{12}$")


def is_real_device(device_id: str) -> bool:
    return bool(MAC_DEVICE_ID.match(device_id.upper()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elder-id", type=int, default=1)
    parser.add_argument("--base-url", default=BASE)
    args = parser.parse_args()

    client = httpx.Client(timeout=30, base_url=args.base_url)
    token = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    failures = 0

    print("== 设备注册与绑定 ==")
    devices = client.get("/devices").json()
    watches = [d for d in devices if d["device_type"] == "watch"]
    real = [d for d in watches if is_real_device(d["device_id"])]
    for device in watches:
        bound = f"老人#{device['elder_id']}" if device["elder_id"] else "未绑定"
        print(
            f"  {device['device_id']:<24} {device['status']:<8} "
            f"电量 {device['battery']:>3}%  {bound}"
        )
    if not real:
        print("  FAIL 没有发现真实手环设备，确认网关是否在运行")
        failures += 1

    # 整体有数据不代表每只手环都有数据：只要有一只在发，总数和时间戳就一直在刷新，
    # 三只里断一只从整体上完全看不出来
    print("\n== 每只手环的数据归属 ==")
    bound = [d for d in real if d["elder_id"]]
    if not bound:
        print("  FAIL 没有任何真实手环绑定到老人，数据不会入库")
        failures += 1
    for device in bound:
        rows = client.get(
            "/health/records",
            params={"elder_id": device["elder_id"], "hours": 1, "limit": 200},
        ).json()
        mine = [r for r in rows if r["device_id"] == device["device_id"]]
        if not mine:
            print(
                f"  FAIL {device['device_id']} 已绑定老人#{device['elder_id']}，"
                "但近一小时没有它的数据"
            )
            failures += 1
            continue
        print(
            f"  {device['device_id']} → 老人#{device['elder_id']}  "
            f"近一小时 {len(mine)} 条，最近 {mine[-1]['recorded_at'][11:19]}"
        )
        intruders = {
            r["device_id"] for r in rows if is_real_device(r["device_id"])
        } - {device["device_id"]}
        if intruders:
            print(f"    注意：该老人名下还混着 {'、'.join(sorted(intruders))} 的数据")

    print("\n== 真机数据入库 ==")
    records = client.get(
        "/health/records", params={"elder_id": args.elder_id, "hours": 1, "limit": 200}
    ).json()
    serial_records = [r for r in records if is_real_device(r["device_id"])]
    print(f"  近一小时共 {len(records)} 条，其中真机 {len(serial_records)} 条")
    if not serial_records:
        print("  FAIL 没有真机数据。检查网关是否连上 MQTT、设备是否已绑定老人")
        failures += 1
    else:
        for record in serial_records[-4:]:
            print(
                f"    {record['recorded_at'][11:19]} "
                f"心率={record['heart_rate']} 血氧={record['spo2']} "
                f"血压={record['systolic']}/{record['diastolic']} 体温={record['temperature']}"
            )
        # 真机分包上报，单条记录字段不全属于正常现象
        partial = [r for r in serial_records if r["heart_rate"] is None]
        print(f"  其中 {len(partial)} 条不含心率（分包上报的正常现象）")

    print("\n== 字段级合并结果 ==")
    live = client.get("/health/live").json()
    target = next((i for i in live if i["elder_id"] == args.elder_id), None)
    if target is None:
        print("  FAIL 实时接口没有返回该老人")
        return 1

    vitals = target["vitals"]
    named = {
        "heart_rate": "心率",
        "spo2": "血氧",
        "systolic": "收缩压",
        "diastolic": "舒张压",
        "temperature": "体温",
    }
    missing = []
    for key, label in named.items():
        value = (vitals.get(key) or {}).get("latest")
        status = (vitals.get(key) or {}).get("status")
        print(f"  {label:<6} {str(value):<8} {status}")
        if value is None:
            missing.append(label)
    if missing:
        print(f"  注意：{'、'.join(missing)} 暂无有效值，可能手环未佩戴或该指标尚未上报")
    else:
        print("  四项体征齐全，分包数据已正确合并")

    print(f"\n  风险等级 {target['risk_level']}，{target['seconds_since_update']} 秒前更新")
    if target["is_stale"]:
        print("  FAIL 数据已陈旧，网关可能已断流")
        failures += 1

    print("\n== 设备自检 ==")
    check = client.post("/devices/self-check", params={"ping_timeout": 3}).json()
    summary = check["summary"]
    print(
        f"  在线 {summary['online']}/{summary['total']}，"
        f"需处理 {summary['with_issues']}，状态修正 {summary['status_changed']}，"
        f"耗时 {check['duration_ms']}ms"
    )
    for gateway in check["gateways"]:
        state = "在线" if gateway["online"] else "离线"
        print(f"  网关 {gateway['gateway_id']}（{gateway['serial_port']}）{state}")
        for band in gateway.get("wristbands", []):
            since = band["seconds_since_sample"]
            since_text = "从未收到" if since is None else f"{since:.0f} 秒前"
            print(
                f"    手环 {band['mac_address']}  {band['sample_count']:>5} 条  "
                f"{since_text}  {band['state']}"
            )
        for issue in gateway["issues"]:
            print(f"    {issue}")
    if not check["gateways"]:
        print("  没有网关心跳。真机接入时需要 wristband_gateway 在运行")
    for item in check["devices"]:
        if item["issues"]:
            print(f"  {item['device_id']}：{item['issues'][0]}")

    print("\n== 外出评估是否受真机数据驱动 ==")
    report = client.get("/outings/rule-report", params={"elder_id": args.elder_id}).json()
    print(f"  拦截={report['blocked']}  建议 {report['suggested_duration_minutes']} 分钟 / {report['suggested_radius_m']} 米")
    for hit in report["hits"]:
        flag = "拦截" if hit["level"] == "block" else "提醒"
        print(f"    [{flag}] {hit['title']}：{hit['detail']}")

    print("\n" + ("真机接入正常" if failures == 0 else f"{failures} 项存在问题"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
