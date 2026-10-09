"""校验健康评分（NEWS2 / 个人基线 / 剂量-反应版）。

前半部分不连数据库，直接喂原材料给纯函数，逐条确认：NEWS2 分档与衰减、临床响应上限、
按年龄的血压目标、个人基线偏离、步数剂量-反应、睡眠分档、缺项归一与置信度。
后半部分连运行中的平台，确认接口能返回评分，且首页快照、评估、日摘要三处一致。

用法: python scripts/check_health_score.py [--elder-id 1] [--skip-api]
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.health_score import (  # noqa: E402
    WEIGHTS,
    ScoreInputs,
    VitalReading,
    compute_score,
    grade_of,
    news_points,
    resting_heart_rate,
    systolic_target,
)

BASE = "http://127.0.0.1:8080/api/v1"
NOW = datetime(2026, 9, 6, 12, 0, 0)


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, label: str, actual, expected) -> None:
        if actual == expected:
            self.passed += 1
        else:
            self.failed.append(f"{label}: 期望 {expected!r}，实际 {actual!r}")

    def check_true(self, label: str, condition, note: str = "") -> None:
        self.check(f"{label}{f'（{note}）' if note else ''}", bool(condition), True)

    def report(self) -> int:
        print(f"\n断言 {self.passed}/{self.passed + len(self.failed)} 通过")
        for item in self.failed:
            print(f"  失败 · {item}")
        return 0 if not self.failed else 1


def series(values, minutes_apart=5, end=NOW):
    """把一串数值铺成按时间升序的读数，最后一条落在 end。"""
    out = []
    for index, value in enumerate(values):
        at = end - timedelta(minutes=(len(values) - 1 - index) * minutes_apart)
        out.append(VitalReading(value=float(value), at=at))
    return out


def healthy_inputs() -> ScoreInputs:
    """一位 72 岁、各方面都很好的老人：4 小时里每 5 分钟一组平稳读数。"""
    n = 48
    return ScoreInputs(
        now=NOW,
        age=72,
        diseases=[],
        mobility="good",
        current={"heart_rate": 72.0, "spo2": 98.0, "systolic": 122.0, "diastolic": 76.0, "temperature": 36.5},
        readings={
            "heart_rate": series([70 + (i % 5) for i in range(n)]),
            "spo2": series([97 + (i % 3) for i in range(n)]),
            "systolic": series([118 + (i % 6) for i in range(n)]),
            "diastolic": series([74 + (i % 4) for i in range(n)]),
            "temperature": series([36.4 + (i % 3) * 0.1 for i in range(n)]),
        },
        steps_today=6500,
        outings_last_week=3,
        sleep_hours=7.5,
        # 今日静息心率（第 10 百分位）是 70，基线也在 70 附近
        resting_hr_history=[70, 71, 69, 70, 72, 70, 71],
        spo2_history=[98, 97, 98, 98, 97, 98, 98],
        trend_pairs=[("hr_avg", 72.0, 73.0), ("spo2_avg", 98.0, 97.8)],
        has_trend_history=True,
        yesterday_score=88.0,
        data_age_minutes=2.0,
    )


def dim(result, key):
    return next(d for d in result.dimensions if d.key == key)


def logic_checks(check: Checker) -> None:
    check.check("权重合计 100", sum(WEIGHTS.values()), 100)
    check.check("七个维度", len(WEIGHTS), 7)

    print("NEWS2 分档")
    for key, value, expected in (
        ("heart_rate", 72, 0), ("heart_rate", 95, 1), ("heart_rate", 48, 1), ("heart_rate", 115, 2), ("heart_rate", 131, 3), ("heart_rate", 40, 3),
        ("spo2", 96, 0), ("spo2", 95, 1), ("spo2", 93, 2), ("spo2", 91, 3),
        ("systolic", 120, 0), ("systolic", 108, 1), ("systolic", 95, 2), ("systolic", 90, 3), ("systolic", 220, 3), ("systolic", 180, 0),
        ("temperature", 36.8, 0), ("temperature", 35.8, 1), ("temperature", 38.5, 1), ("temperature", 39.3, 2), ("temperature", 35.0, 3),
    ):
        check.check(f"{key} {value} 记 {expected} 分", news_points(key, value), expected)
    check.check("慢阻肺 Scale 2：血氧 90 记 0 分", news_points("spo2", 90, scale2=True), 0)
    check.check("慢阻肺 Scale 2：血氧 85 记 2 分", news_points("spo2", 85, scale2=True), 2)

    print("满分场景与置信度")
    result = compute_score(healthy_inputs())
    check.check("七个维度全部参与", result.coverage, "7/7")
    check.check("各项都好时评分 100", result.score, 100)
    check.check("等级为优", result.grade, "优")
    check.check("没有扣分原因", result.deductions, [])
    check.check("比昨天升 12 分", result.delta_vs_yesterday, 12)
    check.check("置信度满", result.confidence, 1.0)
    check.check("不是参考值", result.is_reference, False)
    check.check_true("家属端说法以好消息开头", result.friendly_text.startswith("今天状态很好"))
    print(f"  {result.summary}")

    print("急性体征：此刻与衰减的峰值")
    inputs = healthy_inputs()
    inputs.current["heart_rate"] = 118.0
    result = compute_score(inputs)
    check.check("心率 118 记 2 分 -> 维度 80", dim(result, "acute").score, 80.0)
    check.check_true("原因写明 NEWS2 分值", "NEWS2 记 2 分" in dim(result, "acute").reasons[0])
    check.check_true("单项 2 分不触发上限", result.cap_reason == "")

    inputs = healthy_inputs()
    inputs.current["temperature"] = 39.5
    inputs.current["heart_rate"] = 131.0
    result = compute_score(inputs)
    check.check("发烧 39.5 + 心率 131：NEWS 合计 5", dim(result, "acute").detail["news_total"], 5.0)
    check.check("合计 5 分总分不超过 54", result.score <= 54, True)
    check.check("等级为中或差", result.grade in ("中", "差"), True)
    check.check_true("说明受限原因", "5~6 分" in result.cap_reason)

    inputs = healthy_inputs()
    inputs.current["spo2"] = 90.0
    result = compute_score(inputs)
    check.check_true("单项 3 分总分不超过 69", result.score <= 69, f"实际 {result.score}")
    check.check_true("单项 3 分不能评良", result.grade in ("中", "差"))

    # 早上 9 点烧到 39.5，现在 12 点体温正常：3 小时半衰期后还算 1 分
    inputs = healthy_inputs()
    inputs.readings["temperature"].insert(0, VitalReading(value=39.5, at=NOW - timedelta(hours=3)))
    result = compute_score(inputs)
    check.check_true("3 小时前的发烧影响仍在", abs(dim(result, "acute").detail["news_total"] - 1.0) < 0.05, str(dim(result, "acute").detail["news_total"]))
    check.check_true("原因说明峰值正在消退", any("曾达" in r for r in dim(result, "acute").reasons))
    check.check_true("衰减后不触发上限", result.cap_reason == "")
    inputs.readings["temperature"][0] = VitalReading(value=39.5, at=NOW - timedelta(hours=12))
    result = compute_score(inputs)
    check.check_true("12 小时前的发烧基本消退", dim(result, "acute").detail["news_total"] < 0.2)

    inputs = healthy_inputs()
    inputs.diseases = ["慢阻肺"]
    inputs.current["spo2"] = 90.0
    inputs.readings["spo2"] = series([90] * 48)
    result = compute_score(inputs)
    check.check("慢阻肺血氧 90 按 Scale 2 不记分", dim(result, "acute").detail["news_total"], 0.0)
    check.check("慢阻肺血氧 90 达到 88 的目标", "spo2_median" in dim(result, "control").detail and dim(result, "control").score, 100.0)

    print("慢病控制：按年龄的血压目标")
    check.check("72 岁收缩压目标 140", systolic_target(72), 140)
    check.check("83 岁收缩压目标 150", systolic_target(83), 150)
    check.check("年龄未知按 140", systolic_target(None), 140)
    inputs = healthy_inputs()
    inputs.readings["systolic"] = series([148] * 48)
    inputs.current["systolic"] = 148.0
    result = compute_score(inputs)
    check.check("72 岁收缩压 148 控制不达标（70 分）", dim(result, "control").detail["systolic_median"], 148)
    check.check_true("扣分原因提到目标", any("目标 140" in r for r in dim(result, "control").reasons))
    check.check("NEWS2 对 148 不记分（急性维度满分）", dim(result, "acute").score, 100.0)
    inputs.age = 83
    result = compute_score(inputs)
    check.check("83 岁收缩压 148 达标", dim(result, "control").reasons, [])
    inputs = healthy_inputs()
    inputs.readings["systolic"] = series([172] * 48)
    inputs.readings["diastolic"] = series([104] * 48)
    result = compute_score(inputs)
    # 血压项 40 分，心率血氧各 100：最差一项与平均各占一半 -> 60，不能被平均掉
    check.check("收缩压 172 / 舒张压 104 控制维度 60 分", dim(result, "control").score, 60.0)
    inputs = healthy_inputs()
    inputs.readings["heart_rate"] = series([95 + (i % 5) for i in range(48)])
    result = compute_score(inputs)
    check.check_true("静息心率 95 略高于 90 扣分", any("静息心率" in r for r in dim(result, "control").reasons))

    print("个人基线偏离")
    inputs = healthy_inputs()
    check.check("静息心率取第 10 百分位", resting_heart_rate(inputs.values("heart_rate")), 70.0)
    check.check("与基线一致时满分", dim(compute_score(inputs), "baseline").score, 100.0)
    inputs.resting_hr_history = [62, 63, 61, 64, 62, 63, 62]
    result = compute_score(inputs)
    # 心率高出基线 8 -> 70 分，血氧与基线一致 -> 100 分，取平均
    check.check("比基线 62 高 8：基线维度 85 分", dim(result, "baseline").score, 85.0)
    check.check("记录偏离量", dim(result, "baseline").detail["deviation"], 8.0)
    check.check_true("原因写出自己的基线", any("平时的 62" in r for r in dim(result, "baseline").reasons))
    check.check_true("人群区间正常但个人偏离仍会扣分", compute_score(inputs).score < 100)
    inputs.resting_hr_history = [55, 56, 54]
    result = compute_score(inputs)
    check.check_true("历史不足 5 天时用血氧基线仍可评", dim(result, "baseline").score is not None)
    inputs.spo2_history = []
    result = compute_score(inputs)
    check.check("心率与血氧基线都不足时缺项", dim(result, "baseline").score, None)
    check.check_true("缺项原因说明在积累", "积累" in dim(result, "baseline").reasons[0])
    inputs = healthy_inputs()
    inputs.readings["spo2"] = series([93] * 48)
    result = compute_score(inputs)
    check.check_true("血氧比平时低 5 个点明显扣分", dim(result, "baseline").score <= 60)

    print("活动量：剂量-反应曲线")
    for steps, low, high in ((0, 0, 0), (2000, 39, 41), (4000, 74, 76), (6000, 94, 96), (8000, 100, 100)):
        inputs = healthy_inputs()
        inputs.steps_today = steps
        step_part = dim(compute_score(inputs), "activity").score
        # 外出满分占三成
        expected_low, expected_high = low * 0.7 + 30, high * 0.7 + 30
        check.check_true(f"{steps} 步活动量分", expected_low - 0.6 <= step_part <= expected_high + 0.6, f"{step_part}")
    inputs = healthy_inputs()
    inputs.steps_today = 3600
    inputs.mobility = "limited"
    check.check("行动稍有不便者 3600 步等价 6000 步", dim(compute_score(inputs), "activity").detail["plateau_steps"], 3600)
    check.check_true("折算后接近满分", dim(compute_score(inputs), "activity").score >= 96)
    inputs = healthy_inputs()
    inputs.steps_today = 800
    inputs.outings_last_week = 0
    result = compute_score(inputs)
    check.check_true("几乎没动时活动量很低", dim(result, "activity").score < 20)
    check.check_true("原因提到未外出", any("没有外出" in r for r in dim(result, "activity").reasons))

    print("睡眠：NSF 分档")
    for hours, expected in ((7.5, 100.0), (8.0, 100.0), (6.5, 85.0), (8.5, 85.0), (5.5, 60.0), (9.5, 60.0), (4.5, 30.0), (10.5, 30.0)):
        inputs = healthy_inputs()
        inputs.sleep_hours = hours
        check.check(f"睡眠 {hours} 小时得分", dim(compute_score(inputs), "sleep").score, expected)
    inputs = healthy_inputs()
    inputs.sleep_hours = None
    check.check("无睡眠数据时缺项", dim(compute_score(inputs), "sleep").score, None)

    print("趋势与稳定性")
    inputs = healthy_inputs()
    inputs.trend_pairs = [("spo2_avg", 94.0, 97.0), ("systolic_avg", 140.0, 125.0), ("hr_avg", 70.0, 76.0)]
    check.check("两项变坏各扣 25", dim(compute_score(inputs), "trend").score, 50.0)
    inputs = healthy_inputs()
    inputs.readings["heart_rate"] = series([60, 140] * 24)
    result = compute_score(inputs)
    check.check_true("心率剧烈波动时稳定性扣分", dim(result, "stability").score < 70)

    print("缺项归一与置信度")
    inputs = healthy_inputs()
    inputs.sleep_hours = None
    inputs.has_trend_history = False
    inputs.resting_hr_history, inputs.spo2_history = [], []
    result = compute_score(inputs)
    check.check("缺三项时覆盖度 4/7", result.coverage, "4/7")
    check.check("缺项不扣分", result.score, 100)
    check.check("覆盖 4/7 且数据新鲜：置信度 0.57", result.confidence, 0.57)
    check.check("置信度低于 0.6 标为参考", result.is_reference, True)
    check.check_true("说明里注明仅供参考", "参考" in result.summary and "参考" in result.friendly_text)
    inputs = healthy_inputs()
    inputs.data_age_minutes = 240
    result = compute_score(inputs)
    check.check("数据 4 小时未更新：置信度 0.6", result.confidence, 0.6)
    check.check("0.6 不算参考", result.is_reference, False)
    result = compute_score(ScoreInputs(now=NOW))
    check.check("完全没有数据时不给分", result.score, None)
    check.check("完全没有数据时覆盖度 0/7", result.coverage, "0/7")

    print("等级")
    for score, expected in ((100, "优"), (85, "优"), (84, "良"), (70, "良"), (69, "中"), (55, "中"), (54, "差"), (0, "差")):
        check.check(f"{score} 分等级", grade_of(score), expected)


def api_checks(check: Checker, elder_id: int, base_url: str) -> None:
    client = httpx.Client(timeout=30, base_url=base_url)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    print(f"\n== 平台接口（老人 #{elder_id}）==")
    score = client.get("/health/score", params={"elder_id": elder_id}).json()
    print(f"  评分 {score['score']} 分（{score['grade']}），覆盖 {score['coverage']}，置信度 {score['confidence']}"
          f"{'，参考值' if score['is_reference'] else ''}{'，' + score['cap_reason'] if score['cap_reason'] else ''}")
    for item in score["dimensions"]:
        shown = "缺项" if item["score"] is None else f"{item['score']:.0f} 分"
        reasons = "；".join(item["reasons"]) or "—"
        print(f"    {item['label']:<7} 权重 {item['weight']:<3} {shown:<6} {reasons}")
    print(f"  {score['summary']}")
    print(f"  {score['friendly_text']}")
    check.check_true("评分接口返回分数", score["score"] is not None)
    check.check_true("评分在 0~100", 0 <= (score["score"] or 0) <= 100)
    check.check("维度数为 7", len(score["dimensions"]), 7)
    check.check_true("带置信度", 0 <= score["confidence"] <= 1)

    assessment = client.get("/health/assessment", params={"elder_id": elder_id}).json()
    check.check("评估里带同样的分数", (assessment.get("health_score") or {}).get("score"), score["score"])

    live = client.get("/health/live").json()
    mine = next((item for item in live if item["elder_id"] == elder_id), None)
    if mine:
        print(f"  首页快照：{mine.get('health_score')} 分（{mine.get('health_grade')}），较昨日 {mine.get('score_delta')}，参考 {mine.get('score_reference')}")
        if mine.get("health_score") is not None and score["score"] is not None:
            check.check_true("首页快照分数与即时评分接近", abs(mine["health_score"] - score["score"]) <= 5)

    summaries = client.get("/health/summaries", params={"elder_id": elder_id, "days": 7}).json()
    check.check_true("日摘要接口带静息心率字段", all("resting_hr" in s for s in summaries))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elder-id", type=int, default=1)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()

    check = Checker()
    logic_checks(check)
    if not args.skip_api:
        api_checks(check, args.elder_id, args.base_url)
    return check.report()


if __name__ == "__main__":
    sys.exit(main())
