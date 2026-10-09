"""校验外出评估的天气与智能体升级。

前半部分不连数据库：给规则引擎喂合成的天气材料，逐条确认新增条件的拦截/提醒/建议行为。
后半部分连运行中的平台：用真实天气跑一次评估，确认预报、预警、生活指数进了留痕，
智能体给出了目的、准备清单、路线提示等补充输出。

用法: python scripts/check_outing_conditions.py [--elder-id 1] [--skip-api]
"""

import argparse
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.models import Elder  # noqa: E402
from app.rules.definitions import CHRONIC_TIGHTEN, DEFAULT_RULES, deep_merge  # noqa: E402
from app.rules.engine import OutingRuleEngine, run_checks  # noqa: E402
from app.schemas.health import HealthAssessment, HealthScoreOut, VitalStat  # noqa: E402
from app.schemas.outing import OutingStats  # noqa: E402

BASE = "http://127.0.0.1:8080/api/v1"
NOW = datetime(2026, 9, 6, 10, 0, 0)


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


# ---------- 合成材料 ----------


def make_elder(**overrides) -> Elder:
    values = {
        "id": 1,
        "name": "张三",
        "gender": "男",
        "chronic_diseases": [],
        "mobility_level": "good",
        "live_alone": True,
    }
    values.update(overrides)
    return Elder(**values)


def make_health(score: int | None = 88) -> HealthAssessment:
    health = HealthAssessment(elder_id=1, has_data=True, latest_at=NOW - timedelta(minutes=2))
    for key, label, unit, value in (
        ("heart_rate", "心率", "次/分", 72.0),
        ("spo2", "血氧", "%", 98.0),
        ("systolic", "收缩压", "mmHg", 122.0),
        ("diastolic", "舒张压", "mmHg", 76.0),
        ("temperature", "体温", "℃", 36.5),
    ):
        health.vitals[key] = VitalStat(latest=value, unit=unit, status="normal", label=label)
    health.sleep_hours_last_night = 7.5
    health.steps_today = 1200
    if score is not None:
        health.health_score = HealthScoreOut(
            score=score,
            grade="优" if score >= 85 else "良" if score >= 70 else "中" if score >= 55 else "差",
            coverage="5/5",
            deductions=[] if score >= 85 else ["昨夜睡眠仅 5.0 小时"],
        )
    return health


def make_weather(**overrides) -> dict:
    hourly = []
    for offset in range(1, 9):
        at = NOW.replace(minute=0) + timedelta(hours=offset)
        hourly.append(
            {
                "time": at.strftime("%H:%M"),
                "at": at.isoformat(timespec="minutes"),
                "condition": "晴",
                "temperature": 25,
                "feels_like": 26,
                "humidity": 60,
                "wind_level": 2,
                "precip_prob": 5,
                "precip_mm": 0.0,
                "precip_type": "none",
                "uv_index": 4,
                "visibility_m": 20000,
            }
        )
    weather = {
        "city": "杭州",
        "condition": "晴",
        "temperature": 24,
        "feels_like": 25,
        "humidity": 60,
        "wind_level": 2,
        "uv_index": 4,
        "aqi": 40,
        "aqi_category": "优",
        "provider": "test",
        "hourly": hourly,
        "today": {"temp_max": 29, "temp_min": 21, "uv_index_max": 6, "sunrise": "05:40", "sunset": "18:19"},
        "alerts": [],
        "minutely": {"summary": "未来两小时无降水", "rain_starts_in_minutes": None, "rain_ends_in_minutes": None},
        "indices": {
            "sport": {"name": "运动指数", "level": 2, "category": "较适宜", "text": "天气较好，适合户外慢走。"},
            "dressing": {"name": "穿衣指数", "level": 5, "category": "舒适", "text": "建议穿长袖衬衫或薄外套。"},
            "uv": {"name": "紫外线指数", "level": 3, "category": "中等", "text": ""},
            "comfort": {"name": "舒适度指数", "level": 2, "category": "较舒适", "text": ""},
            "cold": {"name": "感冒指数", "level": 1, "category": "少发", "text": "感冒机率较低。"},
        },
    }
    weather.update(overrides)
    return weather


DEVICES = {"watch": {"online": True, "battery": 80}, "dog": {"online": True, "battery": 90}}


def evaluate(elder: Elder, weather: dict, health: HealthAssessment | None = None, now: datetime = NOW):
    rules = copy.deepcopy(DEFAULT_RULES)
    for disease in elder.chronic_diseases or []:
        rules = deep_merge(rules, CHRONIC_TIGHTEN.get(disease, {}))
    engine = OutingRuleEngine(elder, rules, now=now)
    run_checks(engine, health or make_health(), OutingStats(), weather, DEVICES)
    engine.check_fall_history([], 0)
    return engine.build_report()


def keys_of(report, level: str) -> set[str]:
    return {h.key for h in report.hits if h.level == level}


def logic_checks(check: Checker) -> None:
    print("好天气基线")
    report = evaluate(make_elder(), make_weather())
    check.check("好天气不拦截", report.blocked, False)
    check.check("好天气没有提醒", [h.detail for h in report.hits], [])
    for title in ("气象预警", "外出时段预报", "体感温度", "风力", "健康评分", "生活指数", "慢病与天气"):
        check.check_true(f"{title}通过检查", title in report.passed_rules)
    check.check_true("穿衣建议进入准备提示", any("长袖" in a or "外套" in a for a in report.advisories))
    check.check_true("不照搬和风的大众穿衣文案", not any("短裙" in a for a in report.advisories))
    check.check("好天气不需要最佳时段", report.best_time_hint, "")

    print("官方气象预警")
    orange = [{"event": "暴雨", "severity": "severe", "color": "orange", "headline": "杭州市气象台发布暴雨橙色预警", "description": "", "expires": "18:00"}]
    report = evaluate(make_elder(), make_weather(alerts=orange))
    check.check("橙色预警拦截", "weather_alerts" in keys_of(report, "block"), True)
    yellow = [dict(orange[0], color="yellow", headline="大风黄色预警")]
    report = evaluate(make_elder(), make_weather(alerts=yellow))
    check.check("黄色预警只提醒", ("weather_alerts" in keys_of(report, "warn"), report.blocked), (True, False))
    blue = [dict(orange[0], color="blue", event="大雾", headline="大雾蓝色预警")]
    report = evaluate(make_elder(), make_weather(alerts=blue))
    check.check("蓝色预警不进规则", "weather_alerts" in {h.key for h in report.hits}, False)
    check.check_true("蓝色预警进入建议", any("大雾" in a for a in report.advisories))
    report = evaluate(make_elder(), make_weather(alerts=None))
    check.check_true("预警接口不可用时说明而不是当作没有", any("暂时获取不到" in a for a in report.advisories))

    print("外出时段内的降水预报")
    heavy = make_weather()
    heavy["hourly"][0].update({"condition": "中雨", "precip_prob": 80, "precip_mm": 5.0})
    report = evaluate(make_elder(), heavy)
    check.check("一小时后中雨拦截", "forecast" in keys_of(report, "block"), True)
    check.check_true("拦截时给出下一个合适时段", report.best_time_hint != "", report.best_time_hint)
    light = make_weather()
    light["hourly"][0].update({"condition": "小雨", "precip_prob": 50, "precip_mm": 0.5})
    report = evaluate(make_elder(), light)
    check.check("小雨只提醒", ("forecast" in keys_of(report, "warn"), report.blocked), (True, False))
    check.check_true("小雨提醒带伞", "带上雨伞" in report.advisories)
    far = make_weather()
    far["hourly"][5].update({"condition": "大雨", "precip_prob": 90, "precip_mm": 9.0})
    report = evaluate(make_elder(), far)
    check.check("六小时后的雨不影响本次外出", "forecast" in {h.key for h in report.hits}, False)

    print("分钟级降水压缩时长")
    soon = make_weather(minutely={"summary": "25分钟后开始下小雨", "rain_starts_in_minutes": 25, "rain_ends_in_minutes": 60})
    report = evaluate(make_elder(), soon)
    check.check("25 分钟后下雨：不拦截", report.blocked, False)
    check.check("时长压到雨前 10 分钟", report.suggested_duration_minutes, 15)
    check.check_true("说明时长为什么被压短", "25分钟后" in report.duration_cap_reason)
    imminent = make_weather(minutely={"summary": "5分钟后开始下小雨", "rain_starts_in_minutes": 5, "rain_ends_in_minutes": 40})
    report = evaluate(make_elder(), imminent)
    check.check("5 分钟后下雨：来不及走一圈就拦截", report.blocked, True)
    check.check_true("拦截原因提到等雨停", any("等雨停" in h.detail for h in report.hits))

    print("体感温度")
    report = evaluate(make_elder(), make_weather(feels_like=36, temperature=33))
    check.check("体感 36℃ 拦截", "feels_like" in keys_of(report, "block"), True)
    report = evaluate(make_elder(), make_weather(feels_like=33, temperature=31))
    check.check("体感 33℃ 提醒", "feels_like" in keys_of(report, "warn"), True)
    check.check_true("高温提醒带水", any("带一瓶水" in a for a in report.advisories))
    report = evaluate(make_elder(), make_weather(feels_like=31, temperature=29, humidity=78))
    check.check_true("闷热天提醒中暑风险", any("中暑" in h.detail for h in report.hits))
    report = evaluate(make_elder(), make_weather(feels_like=-2, temperature=1))
    check.check("体感 -2℃ 拦截", "feels_like" in keys_of(report, "block"), True)
    check.check_true("规则用体感而不是气温", not any("1℃" in h.value for h in report.hits if h.key == "feels_like"))

    print("风力")
    check.check("6 级风拦截", "wind" in keys_of(evaluate(make_elder(), make_weather(wind_level=6)), "block"), True)
    check.check("5 级风行动良好者只提醒", "wind" in keys_of(evaluate(make_elder(), make_weather(wind_level=5)), "warn"), True)
    check.check(
        "5 级风行动不便者拦截",
        "wind" in keys_of(evaluate(make_elder(mobility_level="limited"), make_weather(wind_level=5)), "block"),
        True,
    )

    print("紫外线、能见度与日落")
    report = evaluate(make_elder(), make_weather(uv_index=8), now=NOW.replace(hour=12))
    check.check("正午紫外线 8 提醒", "sun_uv" in keys_of(report, "warn"), True)
    check.check_true("提醒戴帽子", "戴顶遮阳帽" in report.advisories)
    report = evaluate(make_elder(), make_weather(uv_index=8), now=NOW.replace(hour=16))
    check.check("下午四点紫外线不提醒", "sun_uv" in {h.key for h in report.hits}, False)
    foggy = make_weather()
    foggy["hourly"][0]["visibility_m"] = 300
    report = evaluate(make_elder(), foggy)
    check.check_true("能见度 300 米提醒", any("能见度" in h.detail for h in report.hits))
    evening = NOW.replace(hour=17, minute=50)
    late = make_weather()
    for hour in late["hourly"]:
        at = evening.replace(minute=0) + timedelta(hours=late["hourly"].index(hour) + 1)
        hour["at"], hour["time"] = at.isoformat(timespec="minutes"), at.strftime("%H:%M")
    report = evaluate(make_elder(), late, now=evening)
    check.check_true("接近日落时建议天黑前回家（不拦截）", any("日落" in a for a in report.advisories) and "sun_uv" not in {h.key for h in report.hits})
    check.check("报告里带日落时间", report.sunset, "18:19")

    print("健康评分")
    check.check("评分 40 拦截", "health_score" in keys_of(evaluate(make_elder(), make_weather(), make_health(40)), "block"), True)
    check.check("评分 55 提醒", "health_score" in keys_of(evaluate(make_elder(), make_weather(), make_health(55)), "warn"), True)
    check.check("评分 80 通过", "health_score" in {h.key for h in evaluate(make_elder(), make_weather(), make_health(80)).hits}, False)
    check.check("没有评分时不判", "health_score" in {h.key for h in evaluate(make_elder(), make_weather(), make_health(None)).hits}, False)

    print("慢病与天气交叉")
    copd = make_elder(chronic_diseases=["慢阻肺"])
    report = evaluate(copd, make_weather(aqi=210))
    check.check("慢阻肺遇重度污染拦截", "chronic_weather" in keys_of(report, "block"), True)
    report = evaluate(copd, make_weather(aqi=120))
    check.check("慢阻肺遇轻度污染只提醒", ("chronic_weather" in keys_of(report, "warn"), report.blocked), (True, False))
    check.check_true("提醒戴口罩", "戴上口罩" in report.advisories)
    check.check("普通老人 AQI 120 不受慢病规则影响", "chronic_weather" in {h.key for h in evaluate(make_elder(), make_weather(aqi=120)).hits}, False)
    cardio = make_elder(chronic_diseases=["高血压"])
    report = evaluate(cardio, make_weather(feels_like=3, temperature=4))
    check.check_true("高血压遇低温提醒", any("血管" in h.detail for h in report.hits))
    report = evaluate(cardio, make_weather(today={"temp_max": 30, "temp_min": 17, "sunrise": "05:40", "sunset": "18:19"}))
    check.check_true("高血压遇大温差提醒", any("温差" in h.detail for h in report.hits))
    report = evaluate(make_elder(chronic_diseases=["帕金森"]), make_weather(condition="小雨"))
    check.check_true("帕金森遇雨天提醒路滑", any("路面滑" in h.detail for h in report.hits))
    report = evaluate(make_elder(chronic_diseases=["2型糖尿病"]), make_weather())
    check.check_true("糖尿病提醒带糖", any("低血糖" in a for a in report.advisories))
    check.check("糖尿病基准时长被慢病收紧为 40", report.suggested_duration_minutes, 40)

    print("生活指数")
    indices = make_weather()["indices"]
    indices["cold"] = {"name": "感冒指数", "level": 3, "category": "易发", "text": "昼夜温差大，注意保暖。"}
    report = evaluate(make_elder(), make_weather(indices=indices))
    check.check_true("感冒指数易发提醒", any("感冒指数" in h.detail for h in report.hits))
    indices = make_weather()["indices"]
    indices["sport"] = {"name": "运动指数", "level": 5, "category": "不宜", "text": "天气不好，不适合户外运动。"}
    report = evaluate(make_elder(), make_weather(indices=indices))
    check.check_true("运动指数不宜提醒", any("运动指数" in h.detail for h in report.hits))

    print("多条提醒叠加收紧")
    report = evaluate(make_elder(), make_weather(feels_like=33, temperature=31, wind_level=4))
    check.check("两条提醒收紧到一半", report.suggested_duration_minutes, 22)
    check.check_true("提醒多但仍可有条件外出", not report.blocked)


def api_checks(check: Checker, elder_id: int, base_url: str) -> None:
    client = httpx.Client(timeout=90, base_url=base_url)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    print(f"\n== 真实天气 + 智能体评估（老人 #{elder_id}）==")
    result = client.post("/outings/evaluate", json={"elder_id": elder_id, "text": "我想出去买点菜"}).json()
    report = result["rule_report"]
    evaluation = result["evaluation"]
    weather = result["snapshots"]["weather"]

    print(f"  天气来源 {weather.get('provider')}：{weather.get('condition')} 体感 {weather.get('feels_like')}℃ "
          f"AQI {weather.get('aqi')}，日落 {weather.get('today', {}).get('sunset')}，"
          f"预警 {len(weather.get('alerts') or [])} 条，预报 {len(weather.get('hourly') or [])} 小时")
    print(f"  分钟级降水：{(weather.get('minutely') or {}).get('summary', '—')}")
    print(f"  结论 {evaluation['decision']}（{evaluation['llm_provider']}），"
          f"目的「{evaluation.get('purpose') or '未说明'}」，期望 {evaluation.get('expected_minutes')} 分钟")
    print(f"  建议时长 {report['suggested_duration_minutes']} 分钟 / 半径 {report['suggested_radius_m']} 米")
    for hit in report["hits"]:
        print(f"    [{'拦截' if hit['level'] == 'block' else '提醒'}] {hit['title']}：{hit['detail']}")
    print(f"  准备清单：{evaluation.get('preparation')}")
    print(f"  路线提示：{evaluation.get('route_hint') or '—'}")
    print(f"  最佳时段：{evaluation.get('best_time_hint') or '—'}")
    print(f"  关心的话：{evaluation.get('care_note') or '—'}")
    print(f"  播报：{evaluation['speech_text']}")

    check.check_true("天气留痕含逐小时预报", bool(weather.get("hourly")))
    check.check_true("天气留痕含预警字段", "alerts" in weather)
    check.check_true("天气留痕含生活指数", bool(weather.get("indices")))
    check.check_true("天气留痕含日落时间", bool((weather.get("today") or {}).get("sunset")))
    check.check_true("规则报告带 advisories 字段", "advisories" in report)
    check.check_true("评估带准备清单字段", isinstance(evaluation.get("preparation"), list))
    check.check_true("播报不超过 120 字", len(evaluation["speech_text"]) <= 120, str(len(evaluation["speech_text"])))
    check.check_true("播报里有称呼或关心", any(w in evaluation["speech_text"] for w in ("爷爷", "奶奶", "叔叔", "阿姨", "您")))
    if evaluation["decision"] != "deny":
        check.check_true("非拒绝时时长不超过规则建议", evaluation["limits"]["duration_minutes"] <= report["suggested_duration_minutes"])


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
