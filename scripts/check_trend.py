"""校验老人档案页大折线图的数据源 /health/trend。

前半部分不连数据库：喂一串合成记录给分桶函数，确认桶宽、avg/min/max、坏读数剔除、时间顺序。
后半部分连运行中的平台：三个时间范围各拉一次，确认点数受控、曲线范围与体征卡上的 24h 范围一致、非法指标被拒绝。

用法: python scripts/check_trend.py [--elder-id 1] [--skip-api]
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.models import HealthRecord  # noqa: E402
from app.services.health_service import bucket_trend  # noqa: E402

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

    def check_true(self, label: str, cond, detail: str = "") -> None:
        if cond:
            self.passed += 1
        else:
            self.failed.append(f"{label}{('：' + detail) if detail else ''}")

    def report(self) -> int:
        print(f"\n断言 {self.passed}/{self.passed + len(self.failed)} 通过")
        for item in self.failed:
            print(f"  失败 · {item}")
        return 0 if not self.failed else 1


def make_records(start: datetime, values: list, step_seconds: float = 5.0) -> list[HealthRecord]:
    records = []
    for index, value in enumerate(values):
        record = HealthRecord(elder_id=1, recorded_at=start + timedelta(seconds=index * step_seconds), heart_rate=value)
        record.suspicious_fields = ""
        records.append(record)
    return records


def logic_checks(check: Checker) -> None:
    print("分桶逻辑")
    start = datetime(2026, 9, 6, 10, 0, 0)
    end = start + timedelta(minutes=10)
    # 10 分钟、每 5 秒一条 = 120 条，要 20 个点 -> 桶宽 30 秒，每桶 6 条
    records = make_records(start, [70 + (i % 6) for i in range(120)])
    series, total, dropped = bucket_trend(records, "heart_rate", start, end, 20)
    check.check("桶数等于要求的点数", len(series), 20)
    check.check("可信读数条数", total, 120)
    check.check("没有坏读数", dropped, 0)
    check.check("每桶 6 条", {p.n for p in series}, {6})
    check.check("桶内 avg 为 70~75 的均值", series[0].avg, 72.5)
    check.check("桶内 min/max", (series[0].min, series[0].max), (70.0, 75.0))
    check.check("桶时间落在桶中央", series[0].t, start + timedelta(seconds=15))
    # 70 出现在桶的第 1 条（t+0s），75 出现在第 6 条（t+25s）：前端靠这两个时刻把峰谷按顺序连起来
    check.check("最低读数的时刻", series[0].t_min, start)
    check.check("最高读数的时刻", series[0].t_max, start + timedelta(seconds=25))
    check_sorted = all(series[i].t < series[i + 1].t for i in range(len(series) - 1))
    check.check_true("时间升序", check_sorted)

    # 一条 214 的孤立坏读数被标记后：不进曲线，只计入 dropped
    spike = make_records(start, [72, 73, 214, 72, 71])
    spike[2].suspicious_fields = "heart_rate"
    series, total, dropped = bucket_trend(spike, "heart_rate", start, start + timedelta(seconds=25), 5)
    check.check("坏读数被剔除", dropped, 1)
    check.check("剩余可信读数", total, 4)
    check.check_true("曲线最大值不含 214", max(p.max for p in series) < 100)

    # 缺字段的记录不算在内；点数少于桶数时桶只装一条，曲线即原始曲线
    sparse = make_records(start, [80, None, 90], step_seconds=60)
    series, total, dropped = bucket_trend(sparse, "heart_rate", start, start + timedelta(minutes=3), 240)
    check.check("缺字段的记录不计数", total, 2)
    check.check("稀疏数据每桶一条", [p.n for p in series], [1, 1])
    check.check("稀疏数据 avg 即原值", [p.avg for p in series], [80.0, 90.0])

    # 桶宽下限 5 秒：点数要得再多也不会切出空桶
    dense = make_records(start, [60] * 12)
    series, _, _ = bucket_trend(dense, "heart_rate", start, start + timedelta(seconds=60), 720)
    check.check("桶宽不小于采样间隔", len(series), 12)


def api_checks(check: Checker, elder_id: int) -> None:
    print("\n接口")
    client = httpx.Client(base_url=BASE, timeout=60)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    assessment = client.get("/health/assessment", params={"elder_id": elder_id}).json()
    for hours in (1, 6, 24):
        data = client.get("/health/trend", params={"elder_id": elder_id, "metric": "heart_rate", "hours": hours}).json()
        points = data["points"]
        print(f"  近 {hours:>2} 小时：{len(points)} 个点，可信读数 {data['total']} 条，剔除 {data['dropped']} 条，桶宽 {data['bucket_seconds']} 秒")
        check.check(f"{hours}h 返回指标元信息", (data["metric"], data["label"], data["unit"]), ("heart_rate", "心率", "次/分"))
        check.check_true(f"{hours}h 点数不超过 240", len(points) <= 240, str(len(points)))
        check.check_true(f"{hours}h 时间升序", all(points[i]["t"] < points[i + 1]["t"] for i in range(len(points) - 1)))
        check.check_true(f"{hours}h 每个桶 min<=avg<=max", all(p["min"] <= p["avg"] <= p["max"] for p in points))
        check.check_true(f"{hours}h 每个桶带峰谷时刻", all(p.get("t_min") and p.get("t_max") for p in points))
        if hours == 24 and points and assessment.get("vitals", {}).get("heart_rate", {}).get("max") is not None:
            stat = assessment["vitals"]["heart_rate"]
            check.check_true(
                "24h 曲线范围与体征卡一致",
                min(p["min"] for p in points) >= stat["min"] - 0.01 and max(p["max"] for p in points) <= stat["max"] + 0.01,
                f"曲线 {min(p['min'] for p in points)}~{max(p['max'] for p in points)} vs 体征卡 {stat['min']}~{stat['max']}",
            )

    for metric in ("spo2", "systolic", "diastolic", "temperature"):
        data = client.get("/health/trend", params={"elder_id": elder_id, "metric": metric, "hours": 24, "points": 120}).json()
        check.check_true(f"{metric} 点数不超过 120", len(data["points"]) <= 120)

    bad = client.get("/health/trend", params={"elder_id": elder_id, "metric": "steps"})
    check.check("非体征指标被拒绝", bad.status_code, 400)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elder-id", type=int, default=1)
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()

    check = Checker()
    logic_checks(check)
    if not args.skip_api:
        api_checks(check, args.elder_id)
    return check.report()


if __name__ == "__main__":
    sys.exit(main())
