"""校验孤立坏读数识别。

前半部分不连数据库，直接喂合成序列给判定函数，确认四条规则：
孤立跳变被标记、持续变化不被误伤并撤回前一条、基线不足不下判断、血压成对失效。
后半部分连运行中的平台，确认历史数据里的坏读数已被标记、评估与曲线范围不再受影响。

用法: python scripts/check_data_quality.py [--elder-id 1] [--skip-api]
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.models import HealthRecord  # noqa: E402
from app.services import data_quality  # noqa: E402

BASE = "http://127.0.0.1:8080/api/v1"


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, label: str, actual, expected) -> None:
        if actual == expected:
            self.passed += 1
        else:
            self.failed.append(f"{label}: 期望 {expected!r}，实际 {actual!r}")

    def report(self) -> int:
        print(f"\n断言 {self.passed}/{self.passed + len(self.failed)} 通过")
        for item in self.failed:
            print(f"  失败 · {item}")
        return 0 if not self.failed else 1


def make_series(start: datetime, values: list, step_seconds: float = 5.0, **fields) -> list[HealthRecord]:
    """按固定间隔生成一串记录；values 里的 None 表示该条没带这个字段。"""
    key = fields.pop("key", "heart_rate")
    records = []
    for index, value in enumerate(values):
        record = HealthRecord(
            elder_id=1,
            recorded_at=start + timedelta(seconds=index * step_seconds),
            **{key: value},
            **fields,
        )
        record.suspicious_fields = ""
        records.append(record)
    return records


def logic_checks(check: Checker) -> None:
    t0 = datetime(2026, 9, 5, 20, 0, 0)

    print("孤立跳变")
    # 82 稳定十几个点，突然一条 214，下一条回到 82
    records = make_series(t0, [82] * 12 + [214] + [82, 81])
    counts = data_quality.reflag(records)
    check.check("214 被标记为可疑", "heart_rate" in records[12].suspicious(), True)
    check.check("前后正常值不受影响", records[11].suspicious() | records[13].suspicious(), set())
    check.check("总共只标记一条", counts, {"heart_rate": 1})
    check.check("trusted() 对可疑字段返回 None", records[12].trusted("heart_rate"), None)
    check.check("原值仍然保留", records[12].heart_rate, 214)

    # 偏低方向同样处理（实测出现过 32）
    records = make_series(t0, [80] * 10 + [32] + [79])
    data_quality.reflag(records)
    check.check("过低的孤立值同样标记", "heart_rate" in records[10].suspicious(), True)

    print("持续变化不误伤")
    # 80 → 150 并持续：第一条 150 先被怀疑，第二条 150 到达后撤回
    records = make_series(t0, [80] * 10 + [150, 152, 151, 149])
    data_quality.reflag(records)
    check.check("持续升高的第一条最终不被标记", records[10].suspicious(), set())
    check.check("后续持续值全部可信", [r.suspicious() for r in records[11:]], [set()] * 3)

    # 逐条喂（模拟实时入库），确认撤回发生在第二条到达时
    history = make_series(t0, [80] * 10)
    first = make_series(t0 + timedelta(seconds=50), [150])[0]
    flagged = data_quality.apply(first, history)
    check.check("实时入库时第一条先被怀疑", flagged, {"heart_rate"})
    history.append(first)
    second = make_series(t0 + timedelta(seconds=55), [151])[0]
    flagged = data_quality.apply(second, history)
    check.check("第二条一致读数不被标记", flagged, set())
    check.check("并撤回第一条的标记", first.suspicious(), set())

    # 上楼梯：80 → 112，幅度在阈值内，压根不算跳变
    records = make_series(t0, [80] * 10 + [112, 118])
    data_quality.reflag(records)
    check.check("常见的活动性心率上升不算跳变", [r.suspicious() for r in records[10:]], [set(), set()])

    print("基线不足不下判断")
    records = make_series(t0, [82, 214])
    data_quality.reflag(records)
    check.check("只有两个点时不判定", records[1].suspicious(), set())

    # 基线里全是 None（分包上报只带血压的那几条）也不算
    records = make_series(t0, [None] * 6 + [82, 83, 84, 214, 82])
    data_quality.reflag(records)
    check.check("只数有该字段的点", "heart_rate" in records[9].suspicious(), True)

    print("血氧与血压")
    records = make_series(t0, [97] * 10 + [87] + [98], key="spo2")
    data_quality.reflag(records)
    check.check("血氧 97 → 87 孤立下探被标记", "spo2" in records[10].suspicious(), True)

    records = make_series(t0, [120] * 6 + [195] + [118], key="systolic", diastolic=78, step_seconds=20)
    records[6].diastolic = 80
    data_quality.reflag(records)
    check.check("收缩压跳变时舒张压一并失效", records[6].suspicious(), {"diastolic", "systolic"})
    check.check("血压成对读取都为 None", (records[6].trusted("systolic"), records[6].trusted("diastolic")), (None, None))

    # 两个字段同时坏（实测 214 与 87 出现在同一秒）
    records = make_series(t0, [82] * 10 + [214] + [82], spo2=97)
    records[10].spo2 = 87
    data_quality.reflag(records)
    check.check("同一条里心率血氧都坏时两者都标记", records[10].suspicious(), {"heart_rate", "spo2"})

    print("合成数据不判定，不同来源互不为基线")
    # 演示面板点「血氧偏低」会回填 6 个 85~89 的点；之前点过「健康平稳」的话两批会交错。
    # 这些数据必须原样生效，否则演示里"系统拒绝外出"的环节就演不出来
    steady = make_series(t0, [97, 98, 97, 99, 96, 97], key="spo2", step_seconds=300, source="simulator")
    low = make_series(t0 + timedelta(seconds=60), [86, 88, 85, 87, 89, 86], key="spo2", step_seconds=300, source="simulator")
    mixed = sorted(steady + low, key=lambda r: r.recorded_at)
    data_quality.reflag(mixed)
    check.check("演示注入的异常一律不标记", [r.suspicious() for r in mixed], [set()] * 12)
    # 真机每 5 秒一条 97，演示面板往同一时段回填一条 88
    records = make_series(t0, [97] * 12, key="spo2", source="serial")
    injected = make_series(t0 + timedelta(seconds=31), [88], key="spo2", source="simulator")[0]
    mixed = sorted(records + [injected], key=lambda r: r.recorded_at)
    data_quality.reflag(mixed)
    check.check("夹在真机流里的演示点也不标记", injected.suspicious(), set())
    check.check("真机流自身不受演示点影响", [r.suspicious() for r in records], [set()] * 12)
    # 真机的孤立坏读数不能被夹在中间的其他来源读数"确认"
    records = make_series(t0, [82] * 10 + [214, 82], source="serial")
    decoy = make_series(t0 + timedelta(seconds=48), [212], source="watch")[0]
    mixed = sorted(records + [decoy], key=lambda r: r.recorded_at)
    data_quality.reflag(mixed)
    check.check("真机坏读数不被别的来源确认", "heart_rate" in records[10].suspicious(), True)


def api_checks(check: Checker, elder_id: int, base_url: str) -> None:
    client = httpx.Client(timeout=30, base_url=base_url)
    token = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    print(f"\n== 平台数据（老人 #{elder_id}，近 24 小时）==")
    records = client.get(
        "/health/records", params={"elder_id": elder_id, "hours": 24, "limit": 24000}
    ).json()
    flagged = [r for r in records if r.get("suspicious_fields")]
    print(f"  共 {len(records)} 条，其中 {len(flagged)} 条含疑似坏读数")
    for r in flagged[:10]:
        shown = ", ".join(f"{k}={r[k]}" for k in r["suspicious_fields"])
        print(f"    {r['recorded_at'][11:19]}  {shown}")
    check.check("记录接口返回 suspicious_fields 字段", "suspicious_fields" in records[0] if records else True, True)

    hr_trusted = [
        r["heart_rate"]
        for r in records
        if r["heart_rate"] is not None and "heart_rate" not in r["suspicious_fields"]
    ]
    assessment = client.get(
        "/health/assessment", params={"elder_id": elder_id, "window_hours": 24}
    ).json()
    hr = assessment["vitals"].get("heart_rate") or {}
    print(f"  评估给出的 24 小时心率范围 {hr.get('min')} ~ {hr.get('max')}，状态 {hr.get('status')}")
    if hr_trusted:
        check.check("评估的最大心率来自可信读数", hr.get("max"), float(max(hr_trusted)))
        check.check("评估的最小心率来自可信读数", hr.get("min"), float(min(hr_trusted)))
    stale_note = [item for item in assessment["abnormal_items"] if "曾出现" in item]
    for item in stale_note:
        print(f"  仍存在的提醒：{item}")

    live = client.get("/health/live").json()
    mine = next((item for item in live if item["elder_id"] == elder_id), None)
    if mine:
        peaks = [p["heart_rate"] for p in mine["series"] if p["heart_rate"] is not None]
        if peaks:
            check.check("首页曲线不含坏读数", max(peaks) <= (max(hr_trusted) if hr_trusted else max(peaks)), True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elder-id", type=int, default=1)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--skip-api", action="store_true", help="只跑离线逻辑校验")
    args = parser.parse_args()

    check = Checker()
    logic_checks(check)
    if not args.skip_api:
        api_checks(check, args.elder_id, args.base_url)
    return check.report()


if __name__ == "__main__":
    sys.exit(main())
