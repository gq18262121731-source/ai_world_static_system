from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Alarm, Elder, FallEvent
from app.models.enums import AlarmStatus, AlarmType
from app.rules.config_store import get_effective_rules
from app.core.formatting import format_vital
from app.rules.definitions import MOBILITY_TIGHTEN, RULE_TITLES
from app.schemas.health import HealthAssessment
from app.schemas.outing import OutingStats, RuleHit, RuleReport

logger = get_logger(__name__)

ENGINE_VERSION = "v1"


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _match_condition(condition: str, keywords: list[str]) -> bool:
    """天气服务给出的是"大暴雨""雷阵雨""强浓雾"这类组合词，按关键词包含判断。"""
    return any(word in condition for word in keywords)


class OutingRuleEngine:
    """外出评估的确定性规则层。

    输出的 RuleReport 是不可被大模型推翻的事实依据:
    命中任意一条 block 规则即禁止外出, 命中 warn 规则则收紧外出限制。
    """

    def __init__(self, elder: Elder, rules: dict, now: datetime | None = None) -> None:
        self.elder = elder
        self.rules = rules
        self.now = now or datetime.now()
        self.hits: list[RuleHit] = []
        self.passed: list[str] = []
        # 不改变结论、但要告诉老人的事（带伞、天黑前回家、带糖）
        self.advisories: list[str] = []
        # 预报里的降水会把时长压短：(分钟上限, 原因)
        self.duration_caps: list[tuple[int, str]] = []
        self.best_time_hint = ""
        self.sunset = ""

    def _advise(self, text: str) -> None:
        if text and text not in self.advisories:
            self.advisories.append(text)

    def _cap_duration(self, minutes: int, reason: str) -> None:
        self.duration_caps.append((max(0, minutes), reason))

    def _has_disease(self, *names: str) -> bool:
        diseases = {str(d).strip() for d in (self.elder.chronic_diseases or [])}
        return any(name in diseases for name in names)

    def _limited_mobility(self) -> bool:
        return self.elder.mobility_level in ("limited", "poor")

    def _lookahead_minutes(self) -> int:
        """外出时段 = 基准时长 + 缓冲，预报只看这么远。"""
        return int(
            self.rules["outing_limits"]["base_duration_minutes"]
            + self.rules["forecast"]["lookahead_buffer_minutes"]
        )

    def _hours_within(self, weather: dict, minutes: int) -> list[dict]:
        end = self.now + timedelta(minutes=minutes)
        result = []
        for hour in weather.get("hourly") or []:
            try:
                at = datetime.fromisoformat(hour["at"])
            except (KeyError, TypeError, ValueError):
                continue
            # 逐小时预报代表整点起的一小时，覆盖到外出时段就算
            if at + timedelta(hours=1) > self.now and at <= end:
                result.append(hour)
        return result

    def _block(self, key: str, detail: str, value: str = "", threshold: str = "") -> None:
        self.hits.append(
            RuleHit(
                key=key,
                level="block",
                title=RULE_TITLES.get(key, key),
                detail=detail,
                value=value,
                threshold=threshold,
            )
        )

    def _warn(self, key: str, detail: str, value: str = "", threshold: str = "") -> None:
        self.hits.append(
            RuleHit(
                key=key,
                level="warn",
                title=RULE_TITLES.get(key, key),
                detail=detail,
                value=value,
                threshold=threshold,
            )
        )

    def _pass(self, key: str) -> None:
        self.passed.append(RULE_TITLES.get(key, key))

    # ---------- 各类规则 ----------

    def check_data_freshness(self, health: HealthAssessment) -> None:
        cfg = self.rules["data_freshness"]
        if not health.has_data or health.latest_at is None:
            self._block("data_freshness", "近期没有手表健康数据，无法判断当前身体状况")
            return
        age_minutes = (self.now - health.latest_at).total_seconds() / 60
        if age_minutes > cfg["block_max_age_minutes"]:
            self._block(
                "data_freshness",
                f"最近一次健康数据是 {int(age_minutes)} 分钟前，数据过旧无法评估",
                value=f"{int(age_minutes)}分钟",
                threshold=f"{cfg['block_max_age_minutes']}分钟",
            )
        elif age_minutes > cfg["warn_max_age_minutes"]:
            self._warn(
                "data_freshness",
                f"健康数据已有 {int(age_minutes)} 分钟未更新，请确认手表佩戴正常",
                value=f"{int(age_minutes)}分钟",
                threshold=f"{cfg['warn_max_age_minutes']}分钟",
            )
        else:
            self._pass("data_freshness")

    def check_vitals(self, health: HealthAssessment) -> None:
        if not health.has_data:
            return
        checks = [
            ("heart_rate", "heart_rate"),
            ("spo2", "spo2"),
            ("systolic", "blood_pressure"),
            ("diastolic", "blood_pressure"),
            ("temperature", "temperature"),
        ]
        clean = True
        for vital_key, rule_key in checks:
            stat = health.vitals.get(vital_key)
            if not stat or stat.latest is None:
                continue
            value_text = f"{format_vital(vital_key, stat.latest)}{stat.unit}"
            if stat.status == "danger":
                clean = False
                self._block(
                    rule_key,
                    f"{stat.label}为 {value_text}，已超出安全范围，不适合外出",
                    value=value_text,
                    threshold=self._threshold_text(vital_key),
                )
            elif stat.status == "warning":
                clean = False
                self._warn(
                    rule_key,
                    # 当前值正常但近期出现过异常时, 用 note 说明真实原因
                    stat.note or f"{stat.label}为 {value_text}，略微偏离正常范围",
                    value=value_text,
                    threshold=self._threshold_text(vital_key),
                )
        if clean:
            self._pass("heart_rate")
            self._pass("spo2")
            self._pass("blood_pressure")

    def _threshold_text(self, vital_key: str) -> str:
        if vital_key == "heart_rate":
            cfg = self.rules["heart_rate"]
            return f"安全区间 {cfg['block_min']}~{cfg['block_max']} 次/分"
        if vital_key == "spo2":
            return f"不低于 {self.rules['spo2']['block_min']}%"
        if vital_key == "systolic":
            cfg = self.rules["blood_pressure"]
            return f"收缩压 {cfg['systolic_block_min']}~{cfg['systolic_block_max']} mmHg"
        if vital_key == "diastolic":
            return f"舒张压不高于 {self.rules['blood_pressure']['diastolic_block_max']} mmHg"
        if vital_key == "temperature":
            cfg = self.rules["temperature"]
            return f"{cfg['block_min']}~{cfg['block_max']} ℃"
        return ""

    def check_sleep(self, health: HealthAssessment) -> None:
        cfg = self.rules["sleep"]
        hours = health.sleep_hours_last_night
        if hours is None:
            return
        if hours < cfg["warn_min_hours"]:
            self._warn(
                "sleep",
                f"昨夜只睡了 {hours} 小时，体力可能不足",
                value=f"{hours}小时",
                threshold=f"不少于 {cfg['warn_min_hours']} 小时",
            )
        else:
            self._pass("sleep")

    def check_health_trend(self, health: HealthAssessment) -> None:
        cfg = self.rules["health_trend"]
        if health.risk_score >= cfg["block_risk_score"]:
            self._block(
                "health_trend",
                f"综合健康风险评分 {health.risk_score}，处于高风险状态",
                value=str(health.risk_score),
                threshold=f"低于 {cfg['block_risk_score']}",
            )
        elif health.risk_score >= cfg["warn_risk_score"]:
            self._warn(
                "health_trend",
                f"综合健康风险评分 {health.risk_score}，近期身体状态一般",
                value=str(health.risk_score),
                threshold=f"低于 {cfg['warn_risk_score']}",
            )
        for note in health.trend_notes:
            self._warn("health_trend", note)

    def check_outing_frequency(self, stats: OutingStats) -> None:
        cfg = self.rules["outing_frequency"]
        if stats.active_task_id:
            self._block(
                "outing_frequency",
                f"当前外出任务 #{stats.active_task_id} 尚未结束",
            )
            return
        if stats.today_count >= cfg["max_per_day"]:
            self._block(
                "outing_frequency",
                f"今天已经外出 {stats.today_count} 次，达到每日上限",
                value=f"{stats.today_count}次",
                threshold=f"每日不超过 {cfg['max_per_day']} 次",
            )
        elif stats.today_count >= cfg["warn_per_day"]:
            self._warn(
                "outing_frequency",
                f"今天已经外出 {stats.today_count} 次，注意不要过度劳累",
                value=f"{stats.today_count}次",
                threshold=f"建议每日不超过 {cfg['warn_per_day']} 次",
            )
        if stats.week_count >= cfg["max_per_week"]:
            self._block(
                "outing_frequency",
                f"本周已外出 {stats.week_count} 次，超过每周上限",
                value=f"{stats.week_count}次",
                threshold=f"每周不超过 {cfg['max_per_week']} 次",
            )
        if (
            stats.minutes_since_last_return is not None
            and stats.minutes_since_last_return < cfg["min_interval_minutes"]
        ):
            self._block(
                "outing_frequency",
                f"距上次回家仅 {stats.minutes_since_last_return} 分钟，建议先休息一会儿",
                value=f"{stats.minutes_since_last_return}分钟",
                threshold=f"至少间隔 {cfg['min_interval_minutes']} 分钟",
            )
        if not [h for h in self.hits if h.key == "outing_frequency"]:
            self._pass("outing_frequency")

    def check_time_window(self, now: datetime | None = None) -> None:
        cfg = self.rules["time_window"]
        current = (now or datetime.now()).time()
        start, end = _parse_hhmm(cfg["start"]), _parse_hhmm(cfg["end"])
        if current < start or current > end:
            self._block(
                "time_window",
                f"现在是 {current.strftime('%H:%M')}，不在建议外出时段内",
                value=current.strftime("%H:%M"),
                threshold=f"{cfg['start']}~{cfg['end']}",
            )
            return
        warn_before, warn_after = _parse_hhmm(cfg["warn_before"]), _parse_hhmm(cfg["warn_after"])
        if current < warn_before or current > warn_after:
            self._warn(
                "time_window",
                f"现在是 {current.strftime('%H:%M')}，光线和气温不太理想，建议缩短外出时间",
                value=current.strftime("%H:%M"),
                threshold=f"最佳 {cfg['warn_before']}~{cfg['warn_after']}",
            )
        else:
            self._pass("time_window")

    def check_devices(self, devices: dict) -> None:
        online_cfg = self.rules["device_online"]
        battery_cfg = self.rules["device_battery"]
        watch, dog = devices.get("watch"), devices.get("dog")

        if online_cfg["require_watch"]:
            if not watch:
                self._block("device_online", "尚未绑定健康手表，无法在外出时监测身体状况")
            elif not watch["online"]:
                self._block(
                    "device_online",
                    "健康手表当前离线，外出期间无法监测心率血氧",
                    value="离线",
                )
            elif watch["battery"] < battery_cfg["watch_block_min"]:
                self._block(
                    "device_battery",
                    f"手表电量仅剩 {watch['battery']}%，请先充电",
                    value=f"{watch['battery']}%",
                    threshold=f"不低于 {battery_cfg['watch_block_min']}%",
                )
            elif watch["battery"] < battery_cfg["watch_warn_min"]:
                self._warn(
                    "device_battery",
                    f"手表电量 {watch['battery']}%，建议缩短外出时间",
                    value=f"{watch['battery']}%",
                    threshold=f"建议不低于 {battery_cfg['watch_warn_min']}%",
                )

        if online_cfg["require_dog"]:
            if not dog:
                self._block("device_online", "尚未绑定陪护机器狗，外出无人跟随不安全")
            elif not dog["online"]:
                self._block(
                    "device_online", "陪护机器狗当前离线，无法跟随保护", value="离线"
                )
            elif dog["battery"] < battery_cfg["dog_block_min"]:
                self._block(
                    "device_battery",
                    f"机器狗电量仅剩 {dog['battery']}%，不足以完成本次陪同",
                    value=f"{dog['battery']}%",
                    threshold=f"不低于 {battery_cfg['dog_block_min']}%",
                )
            elif dog["battery"] < battery_cfg["dog_warn_min"]:
                self._warn(
                    "device_battery",
                    f"机器狗电量 {dog['battery']}%，建议就近活动",
                    value=f"{dog['battery']}%",
                    threshold=f"建议不低于 {battery_cfg['dog_warn_min']}%",
                )

        if not [h for h in self.hits if h.key in ("device_online", "device_battery")]:
            self._pass("device_online")
            self._pass("device_battery")

    def check_weather(self, weather: dict) -> None:
        cfg = self.rules["weather"]
        condition = weather.get("condition", "")
        temperature = weather.get("temperature")
        aqi = weather.get("aqi")

        if _match_condition(condition, cfg["block_conditions"]):
            self._block(
                "weather", f"当前天气为{condition}，外出存在安全风险", value=condition
            )
        elif _match_condition(condition, cfg["warn_conditions"]):
            self._warn(
                "weather", f"当前天气为{condition}，路面湿滑或能见度较低", value=condition
            )

        if temperature is not None:
            if temperature < cfg["block_temp_min"] or temperature > cfg["block_temp_max"]:
                self._block(
                    "weather",
                    f"当前气温 {temperature}℃，对老人身体负担过大",
                    value=f"{temperature}℃",
                    threshold=f"{cfg['block_temp_min']}~{cfg['block_temp_max']}℃",
                )
            elif temperature < cfg["warn_temp_min"] or temperature > cfg["warn_temp_max"]:
                self._warn(
                    "weather",
                    f"当前气温 {temperature}℃，注意增减衣物",
                    value=f"{temperature}℃",
                    threshold=f"舒适区间 {cfg['warn_temp_min']}~{cfg['warn_temp_max']}℃",
                )

        if aqi is not None:
            if aqi >= cfg["block_aqi"]:
                self._block(
                    "weather", f"空气质量指数 {aqi}，属于严重污染", value=str(aqi)
                )
            elif aqi >= cfg["warn_aqi"]:
                self._warn(
                    "weather", f"空气质量指数 {aqi}，建议佩戴口罩并缩短外出", value=str(aqi)
                )

        if not [h for h in self.hits if h.key == "weather"]:
            self._pass("weather")

    def check_weather_alerts(self, weather: dict) -> None:
        """官方预警是最权威的"别出门"信号，比任何阈值判断可靠。"""
        alerts = weather.get("alerts")
        if alerts is None:
            # 预警接口这次没拿到，不能当作"没有预警"
            self._advise("气象预警信息暂时获取不到，出门前留意一下天气提示")
            return
        cfg = self.rules["weather_alerts"]
        for alert in alerts:
            color = str(alert.get("color", "")).lower()
            headline = alert.get("headline") or f"{alert.get('event', '天气')}预警"
            if color in cfg["block_colors"]:
                self._block("weather_alerts", f"{headline}，官方预警生效期间不宜外出", value=color)
            elif color in cfg["warn_colors"]:
                self._warn("weather_alerts", f"{headline}，外出请缩短时间并注意防范", value=color)
            else:
                self._advise(f"当前有{alert.get('event', '天气')}预警（{color or '蓝色'}），出门多留意")
        if not [h for h in self.hits if h.key == "weather_alerts"]:
            self._pass("weather_alerts")

    def check_forecast(self, weather: dict) -> None:
        """看外出这段时间会不会下雨，而不是只看此刻。"""
        cfg = self.rules["forecast"]
        lookahead = self._lookahead_minutes()
        hours = self._hours_within(weather, lookahead)
        minutely = weather.get("minutely") or {}
        rain_in = minutely.get("rain_starts_in_minutes")

        heavy = [
            h for h in hours
            if (h.get("precip_prob") or 0) >= cfg["block_precip_prob"]
            and (h.get("precip_mm") or 0) >= cfg["block_precip_mm"]
        ]
        light = [
            h for h in hours
            if h not in heavy
            and (
                (h.get("precip_prob") or 0) >= cfg["warn_precip_prob"]
                or (h.get("precip_mm") or 0) >= cfg["warn_precip_mm"]
            )
        ]
        if heavy:
            first = heavy[0]
            self._block(
                "forecast",
                f"预报 {first['time']} 前后有{first.get('condition') or '较大降雨'}"
                f"（概率 {first.get('precip_prob')}%），外出途中很可能遇上",
                value=f"{first.get('precip_mm')}mm/h",
                threshold=f"概率≥{cfg['block_precip_prob']}% 且雨量≥{cfg['block_precip_mm']}mm",
            )
        elif rain_in is not None and rain_in <= lookahead:
            summary = minutely.get("summary") or f"约 {rain_in} 分钟后开始下雨"
            if rain_in <= 10:
                self._warn("forecast", f"{summary}，现在出门大概率会淋雨", value=f"{rain_in}分钟")
                self._cap_duration(0, summary)
            else:
                self._warn("forecast", f"{summary}，只能就近走一小圈", value=f"{rain_in}分钟")
                self._cap_duration(rain_in - 10, summary)
            self._advise("带上雨伞")
        elif light:
            first = light[0]
            self._warn(
                "forecast",
                f"预报 {first['time']} 前后可能有{first.get('condition') or '小雨'}"
                f"（概率 {first.get('precip_prob')}%），建议缩短外出",
                value=f"{first.get('precip_prob')}%",
            )
            self._advise("带上雨伞")
        else:
            self._pass("forecast")

    def check_feels_like(self, weather: dict) -> None:
        cfg = self.rules["feels_like"]
        feels = weather.get("feels_like")
        if feels is None:
            feels = weather.get("temperature")
        if feels is None:
            return
        humidity = weather.get("humidity")
        if feels < cfg["block_min"] or feels > cfg["block_max"]:
            self._block(
                "feels_like",
                f"体感温度 {feels}℃，对老人身体负担过大",
                value=f"{feels}℃",
                threshold=f"{cfg['block_min']}~{cfg['block_max']}℃",
            )
        elif feels < cfg["warn_min"] or feels > cfg["warn_max"]:
            self._warn(
                "feels_like",
                f"体感温度 {feels}℃，" + ("注意保暖，戴好帽子围巾" if feels < cfg["warn_min"] else "尽量走阴凉处，多喝水"),
                value=f"{feels}℃",
                threshold=f"舒适区间 {cfg['warn_min']}~{cfg['warn_max']}℃",
            )
        elif (
            humidity is not None
            and feels >= cfg["muggy_temp"]
            and humidity >= cfg["muggy_humidity"]
        ):
            self._warn(
                "feels_like",
                f"体感 {feels}℃ 且湿度 {humidity}%，闷热天容易中暑，随身带水",
                value=f"{feels}℃/{humidity}%",
            )
        else:
            self._pass("feels_like")
        if feels >= cfg["muggy_temp"]:
            self._advise("带一瓶水，途中多歇一歇")
        if feels <= cfg["warn_min"]:
            self._advise("多穿一件外套，戴好帽子")

    def check_wind(self, weather: dict) -> None:
        cfg = self.rules["wind"]
        level = weather.get("wind_level")
        if level is None:
            return
        block_level = cfg["block_level_limited_mobility"] if self._limited_mobility() else cfg["block_level"]
        if level >= block_level:
            self._block(
                "wind",
                f"当前风力 {level} 级，" + ("行动不便时" if self._limited_mobility() else "") + "容易站不稳",
                value=f"{level}级",
                threshold=f"低于 {block_level} 级",
            )
        elif level >= cfg["warn_level"]:
            self._warn("wind", f"当前风力 {level} 级，走路注意扶稳，别去开阔的地方", value=f"{level}级")
        else:
            self._pass("wind")

    def check_sun_uv(self, weather: dict) -> None:
        cfg = self.rules["sun_uv"]
        uv = weather.get("uv_index")
        current = self.now.time()
        if (
            uv is not None
            and uv >= cfg["warn_uv_index"]
            and _parse_hhmm(cfg["uv_start"]) <= current <= _parse_hhmm(cfg["uv_end"])
        ):
            self._warn("sun_uv", f"紫外线指数 {uv}，正午前后阳光太强，建议避开或戴帽子", value=str(uv))
            self._advise("戴顶遮阳帽")
        visibility = None
        for hour in self._hours_within(weather, 60)[:1]:
            visibility = hour.get("visibility_m")
        if visibility is not None and visibility < cfg["warn_visibility_m"]:
            self._warn("sun_uv", f"能见度只有 {visibility} 米，过马路要格外小心", value=f"{visibility}米")

        sunset = (weather.get("today") or {}).get("sunset") or ""
        self.sunset = sunset
        if sunset:
            try:
                sunset_time = datetime.combine(self.now.date(), _parse_hhmm(sunset))
            except ValueError:
                sunset_time = None
            if sunset_time is not None:
                latest_return = sunset_time - timedelta(minutes=cfg["sunset_margin_minutes"])
                planned_return = self.now + timedelta(
                    minutes=self.rules["outing_limits"]["base_duration_minutes"]
                )
                if planned_return > latest_return:
                    self._advise(f"今天 {sunset} 日落，尽量在天黑前回到家")
        if not [h for h in self.hits if h.key == "sun_uv"]:
            self._pass("sun_uv")

    def check_life_indices(self, weather: dict) -> None:
        cfg = self.rules["life_indices"]
        indices = weather.get("indices") or {}
        cold = indices.get("cold") or {}
        sport = indices.get("sport") or {}
        dressing = indices.get("dressing") or {}
        if cold.get("level") is not None and cold["level"] >= cfg["warn_cold_level"]:
            self._warn(
                "life_indices",
                f"感冒指数「{cold.get('category', '易发')}」，{cold.get('text') or '注意保暖'}",
                value=cold.get("category", ""),
            )
        if sport.get("category") in cfg["warn_sport_categories"]:
            self._warn(
                "life_indices",
                f"运动指数「{sport['category']}」，{sport.get('text') or '不太适合户外活动'}",
                value=sport["category"],
            )
        # 和风的穿衣文案是面向大众的（"短裙、短裤"），换成老人听得进去的说法
        level = dressing.get("level")
        if level is not None:
            if level <= 2:
                self._advise("天冷，穿厚外套，戴好帽子围巾")
            elif level <= 4:
                self._advise("有点凉，加一件外套")
            elif level == 5:
                self._advise("穿长袖或带件薄外套")
            else:
                self._advise("穿轻薄透气的衣服，戴顶帽子")
        if not [h for h in self.hits if h.key == "life_indices"]:
            self._pass("life_indices")

    def check_health_score(self, health: HealthAssessment) -> None:
        cfg = self.rules["health_score"]
        score = getattr(health, "health_score", None)
        if score is None or score.score is None:
            return
        reasons = "、".join(score.deductions[:2]) if score.deductions else "多项指标偏离"
        if score.score < cfg["block_below"]:
            self._block(
                "health_score",
                f"今日健康评分 {score.score} 分（{score.grade}），{reasons}，今天先休息",
                value=str(score.score),
                threshold=f"不低于 {cfg['block_below']} 分",
            )
        elif score.score < cfg["warn_below"]:
            self._warn(
                "health_score",
                f"今日健康评分 {score.score} 分（{score.grade}），{reasons}",
                value=str(score.score),
                threshold=f"建议不低于 {cfg['warn_below']} 分",
            )
        else:
            self._pass("health_score")

    def check_chronic_weather(self, weather: dict) -> None:
        """慢病和天气叠加时收紧，只有慢阻肺/哮喘遇重度污染才直接拦截。"""
        cfg = self.rules["chronic_weather"]
        condition = weather.get("condition", "") or ""
        aqi = weather.get("aqi")
        humidity = weather.get("humidity")
        feels = weather.get("feels_like") if weather.get("feels_like") is not None else weather.get("temperature")
        today = weather.get("today") or {}
        hit = False

        if self._has_disease("慢阻肺", "哮喘"):
            if aqi is not None and aqi >= cfg["copd_block_aqi"]:
                hit = True
                self._block(
                    "chronic_weather",
                    f"有慢阻肺/哮喘病史，空气质量指数 {aqi} 属重度污染，今天不适合出门",
                    value=str(aqi),
                    threshold=f"低于 {cfg['copd_block_aqi']}",
                )
            elif (aqi is not None and aqi >= cfg["copd_warn_aqi"]) or _match_condition(condition, ["雾", "霾"]):
                hit = True
                self._warn("chronic_weather", "有慢阻肺/哮喘病史，今天空气不好，戴好口罩、少走一会儿", value=str(aqi or condition))
                self._advise("戴上口罩")
            elif humidity is not None and humidity >= cfg["copd_warn_humidity"]:
                hit = True
                self._warn("chronic_weather", f"有慢阻肺/哮喘病史，湿度 {humidity}% 偏闷，胸口发紧就马上回来", value=f"{humidity}%")

        if self._has_disease("高血压", "冠心病", "心律失常"):
            swing = None
            if today.get("temp_max") is not None and today.get("temp_min") is not None:
                swing = today["temp_max"] - today["temp_min"]
            if feels is not None and feels <= cfg["cardio_warn_feels_like"]:
                hit = True
                self._warn("chronic_weather", f"有心血管病史，体感 {feels}℃ 偏冷，血管受寒容易出问题，务必穿暖", value=f"{feels}℃")
                self._advise("穿暖再出门，围巾护住脖子")
            elif swing is not None and swing >= cfg["cardio_warn_temp_swing"]:
                hit = True
                self._warn("chronic_weather", f"有心血管病史，今天昼夜温差 {swing}℃，注意随时增减衣物", value=f"{swing}℃")

        if self._has_disease("帕金森") or self.elder.mobility_level == "poor":
            slippery = _match_condition(condition, ["雨", "雪", "冰"]) or any(
                (h.get("precip_mm") or 0) > 0 for h in self._hours_within(weather, 60)
            )
            if slippery:
                hit = True
                self._warn("chronic_weather", "腿脚不太方便，雨雪天路面滑，只在楼下平地走走", value=condition)

        if self._has_disease("糖尿病", "2型糖尿病"):
            if self.rules["outing_limits"]["base_duration_minutes"] >= cfg["diabetes_snack_minutes"]:
                self._advise("随身带点糖或饼干，防止低血糖")

        if not hit:
            self._pass("chronic_weather")

    def find_best_time(self, weather: dict) -> str:
        """现在不合适时，从预报里挑下一个合适的整点：没雨、体感舒适、风不大、还在允许时段内。"""
        forecast_cfg = self.rules["forecast"]
        feel_cfg = self.rules["feels_like"]
        wind_cfg = self.rules["wind"]
        window = self.rules["time_window"]
        start, end = _parse_hhmm(window["start"]), _parse_hhmm(window["end"])
        for hour in weather.get("hourly") or []:
            try:
                at = datetime.fromisoformat(hour["at"])
            except (KeyError, TypeError, ValueError):
                continue
            if at <= self.now or not (start <= at.time() <= end):
                continue
            feels = hour.get("feels_like") if hour.get("feels_like") is not None else hour.get("temperature")
            if (hour.get("precip_prob") or 0) >= forecast_cfg["warn_precip_prob"]:
                continue
            if feels is not None and not (feel_cfg["warn_min"] <= feels <= feel_cfg["warn_max"]):
                continue
            if (hour.get("wind_level") or 0) >= wind_cfg["warn_level"]:
                continue
            return hour["time"]
        return ""

    def check_fall_history(self, recent_falls: list[FallEvent], unresolved: int) -> None:
        cfg = self.rules["fall_history"]
        block_since = self.now - timedelta(hours=cfg["block_lookback_hours"])
        warn_since = self.now - timedelta(hours=cfg["warn_lookback_hours"])

        if unresolved:
            self._block(
                "fall_history", f"仍有 {unresolved} 条未处置的紧急告警，请先联系家人确认"
            )
        blocking = [f for f in recent_falls if f.detected_at >= block_since]
        warning = [f for f in recent_falls if warn_since <= f.detected_at < block_since]
        if blocking:
            self._block(
                "fall_history",
                f"最近 {cfg['block_lookback_hours']} 小时内发生过跌倒，需要观察恢复情况",
                value=f"{len(blocking)}次",
            )
        elif warning:
            self._warn(
                "fall_history",
                f"最近 {cfg['warn_lookback_hours']} 小时内有跌倒记录，外出请格外小心",
                value=f"{len(warning)}次",
            )
        else:
            self._pass("fall_history")

    # ---------- 汇总 ----------

    def build_report(self) -> RuleReport:
        limits_cfg = self.rules["outing_limits"]
        mobility = MOBILITY_TIGHTEN.get(
            self.elder.mobility_level, MOBILITY_TIGHTEN["good"]
        )
        warn_count = len([h for h in self.hits if h.level == "warn"])
        penalty = max(0.3, 1 - warn_count * limits_cfg["warn_penalty_ratio"])

        duration = int(
            limits_cfg["base_duration_minutes"] * mobility["duration_ratio"] * penalty
        )
        radius = int(limits_cfg["base_radius_m"] * mobility["radius_ratio"] * penalty)
        duration = max(limits_cfg["min_duration_minutes"], duration)
        radius = max(limits_cfg["min_radius_m"], radius)

        # 预报里马上要下雨时，时长压到雨前；压到比最短时长还短就干脆不出门
        cap_reason = ""
        if self.duration_caps:
            cap_minutes, cap_reason = min(self.duration_caps, key=lambda item: item[0])
            if cap_minutes < limits_cfg["min_duration_minutes"]:
                self._block("forecast", f"{cap_reason}，来不及走一圈就得往回赶，建议等雨停再出门")
            else:
                duration = min(duration, cap_minutes)

        block_count = len([h for h in self.hits if h.level == "block"])
        risk = min(100.0, block_count * 40 + warn_count * 12)

        return RuleReport(
            elder_id=self.elder.id,
            blocked=block_count > 0,
            hits=self.hits,
            passed_rules=sorted(set(self.passed) - {h.title for h in self.hits}),
            suggested_duration_minutes=duration,
            suggested_radius_m=radius,
            risk_score=round(risk, 1),
            evaluated_at=datetime.now(),
            advisories=self.advisories,
            duration_cap_reason=cap_reason if self.duration_caps else "",
            best_time_hint=self.best_time_hint,
            sunset=self.sunset,
        )


def run_checks(
    engine: OutingRuleEngine,
    health: HealthAssessment,
    stats: OutingStats,
    weather: dict,
    devices: dict,
) -> None:
    """不依赖数据库的全部检查，离线自测也走这一条路径。"""
    engine.check_data_freshness(health)
    engine.check_vitals(health)
    engine.check_sleep(health)
    engine.check_health_trend(health)
    engine.check_health_score(health)
    engine.check_outing_frequency(stats)
    engine.check_time_window(engine.now)
    engine.check_devices(devices)
    engine.check_weather(weather)
    engine.check_weather_alerts(weather)
    engine.check_forecast(weather)
    engine.check_feels_like(weather)
    engine.check_wind(weather)
    engine.check_sun_uv(weather)
    engine.check_life_indices(weather)
    engine.check_chronic_weather(weather)
    # 现在不合适（被拦或被压短）时才需要告诉老人几点再看看
    if any(h.level == "block" for h in engine.hits) or engine.duration_caps:
        engine.best_time_hint = engine.find_best_time(weather)


async def _recent_falls(db: AsyncSession, elder_id: int, hours: int) -> list[FallEvent]:
    since = datetime.now() - timedelta(hours=hours)
    result = await db.execute(
        select(FallEvent).where(
            FallEvent.elder_id == elder_id,
            FallEvent.detected_at >= since,
            FallEvent.merged.is_(False),
        )
    )
    return list(result.scalars().all())


async def _unresolved_critical(db: AsyncSession, elder_id: int) -> int:
    result = await db.execute(
        select(Alarm).where(
            Alarm.elder_id == elder_id,
            Alarm.alarm_type.in_([AlarmType.FALL, AlarmType.SOS]),
            Alarm.status.in_([AlarmStatus.PENDING, AlarmStatus.CONFIRMING]),
        )
    )
    return len(list(result.scalars().all()))


async def evaluate_rules(
    db: AsyncSession,
    elder: Elder,
    health: HealthAssessment,
    stats: OutingStats,
    weather: dict,
    devices: dict,
) -> tuple[RuleReport, dict]:
    """执行全部规则, 返回 (规则报告, 生效阈值)。"""
    rules, _ = await get_effective_rules(db, elder)
    engine = OutingRuleEngine(elder, rules)
    run_checks(engine, health, stats, weather, devices)
    engine.check_fall_history(
        await _recent_falls(db, elder.id, rules["fall_history"]["warn_lookback_hours"]),
        await _unresolved_critical(db, elder.id),
    )

    report = engine.build_report()
    logger.info(
        "老人 %s 外出规则评估: blocked=%s, 命中 %d 条",
        elder.name,
        report.blocked,
        len(report.hits),
    )
    return report, rules
