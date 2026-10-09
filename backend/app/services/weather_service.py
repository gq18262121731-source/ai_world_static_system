import asyncio
import random
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.cache import cache
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MOCK_CONDITIONS = [
    ("晴", 0.30),
    ("多云", 0.28),
    ("阴", 0.15),
    ("小雨", 0.12),
    ("中雨", 0.06),
    ("雾", 0.04),
    ("大风", 0.03),
    ("暴雨", 0.02),
]


def _mock_weather(city: str) -> dict:
    """稳定的模拟天气: 同一天同一城市结果一致, 便于演示复现。"""
    seed = f"{city}-{datetime.now().strftime('%Y%m%d%H')}"
    rng = random.Random(seed)
    roll = rng.random()
    cumulative = 0.0
    condition = "晴"
    for name, weight in _MOCK_CONDITIONS:
        cumulative += weight
        if roll <= cumulative:
            condition = name
            break
    month = datetime.now().month
    base_temp = {12: 6, 1: 4, 2: 7, 3: 14, 4: 20, 5: 25, 6: 29, 7: 33, 8: 33, 9: 27, 10: 20, 11: 13}[month]
    temperature = base_temp + rng.randint(-3, 3)
    return {
        "city": city,
        "condition": condition,
        "temperature": temperature,
        "humidity": rng.randint(40, 90),
        "wind_level": rng.randint(1, 5),
        "aqi": rng.randint(30, 180),
        "provider": "mock",
        "updated_at": datetime.now().isoformat(),
    }


async def _open_meteo(city: str, lat: float, lng: float) -> dict | None:
    """使用免费的 Open-Meteo 接口(无需密钥)。"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "timezone": "Asia/Shanghai",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            current = resp.json().get("current", {})
    except Exception as exc:
        logger.warning("获取实时天气失败(%s), 回退到模拟天气", exc)
        return None

    code = current.get("weather_code", 0)
    condition = _WMO_MAP.get(code, "多云")
    wind = current.get("wind_speed_10m", 0) or 0
    return {
        "city": city,
        "condition": condition,
        "temperature": round(current.get("temperature_2m", 20)),
        "humidity": current.get("relative_humidity_2m", 60),
        "wind_level": min(8, int(wind // 6) + 1),
        "aqi": None,
        "provider": "open-meteo",
        "updated_at": datetime.now().isoformat(),
    }


_WMO_MAP = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾",
    51: "小雨", 53: "小雨", 55: "中雨",
    61: "小雨", 63: "中雨", 65: "暴雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "小雪",
    80: "阵雨", 81: "中雨", 82: "暴雨",
    85: "小雪", 86: "大雪",
    95: "雷暴", 96: "雷暴", 99: "雷暴",
}


_QWEATHER_TIMEOUT = 8.0
# 规则引擎的 AQI 阈值(150/250)按中国标准设定, 因此优先取同一把尺子的指数;
# 海外位置没有 cn-mee 时退到量纲相近的 us-epa, qaqi 只作兜底。
_AQI_INDEX_PRIORITY = ("cn-mee", "us-epa", "qaqi")


def _api_host(raw: str) -> str:
    """控制台里的 API Host 只是域名, 这里容忍用户连 scheme 一起粘贴。"""
    return raw.strip().rstrip("/").split("://", 1)[-1]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _qweather_get(
    client: httpx.AsyncClient, url: str, params: dict
) -> dict | None:
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "和风天气 %s 返回 %s: %s",
            url,
            exc.response.status_code,
            exc.response.text[:200],
        )
    except Exception as exc:
        logger.warning("和风天气 %s 调用失败: %s", url, exc)
    return None


def _pick_aqi(payload: dict | None) -> tuple[int | None, str]:
    if not payload:
        return None, ""
    indexes = [i for i in (payload.get("indexes") or []) if isinstance(i, dict)]
    by_code = {str(i.get("code", "")): i for i in indexes}
    chosen = next(
        (by_code[code] for code in _AQI_INDEX_PRIORITY if code in by_code),
        indexes[0] if indexes else None,
    )
    if chosen is None:
        return None, ""
    aqi = _number(chosen.get("aqi"))
    return (
        round(aqi) if aqi is not None else None,
        str(chosen.get("category") or ""),
    )


async def _qweather(city: str, lat: float, lng: float) -> dict | None:
    """和风天气 API v1: 实时天气 + 实时空气质量, API KEY 请求头鉴权。"""
    host = _api_host(settings.qweather_api_host)
    api_key = settings.qweather_api_key or settings.weather_api_key
    if not host or not api_key:
        logger.warning("QWEATHER_API_HOST 或 QWEATHER_API_KEY 未配置, 无法调用和风天气")
        return None

    # 官方限制经纬度最多两位小数, 多余位数会被判为无效参数
    point = f"{round(lat, 2)}/{round(lng, 2)}"
    params = {"lang": "zh-hans"}

    async with httpx.AsyncClient(
        timeout=_QWEATHER_TIMEOUT, headers={"X-QW-Api-Key": api_key}
    ) as client:
        requests = [
            _qweather_get(client, f"https://{host}/weather/v1/current/{point}", params)
        ]
        if settings.qweather_air_quality:
            requests.append(
                _qweather_get(
                    client, f"https://{host}/airquality/v1/current/{point}", params
                )
            )
        results = await asyncio.gather(*requests)

    current = results[0]
    if not current:
        return None
    aqi, aqi_category = _pick_aqi(results[1] if len(results) > 1 else None)

    condition = current.get("condition") or {}
    temperature = _number((current.get("temperature") or {}).get("value"))
    feels_like = _number((current.get("feelsLike") or {}).get("value"))
    uv_index = _number(current.get("uvIndex"))

    humidity = _number(current.get("humidity"))
    if humidity is not None and humidity <= 1:
        # v1 用 0~1 的比例表达相对湿度, 系统内统一用百分数
        humidity *= 100

    precipitation = current.get("precipitation") or {}
    # type 说的是降水形态(雨/雪), 天气晴朗时也可能带着上一时段的形态,
    # 因此判断"现在有没有在下"要看 amount
    precip_mm = _number((precipitation.get("amount") or {}).get("value"))

    wind = current.get("wind") or {}
    wind_level = _number(wind.get("scale"))
    if wind_level is None:
        speed = _number((wind.get("speed") or {}).get("value")) or 0.0
        wind_level = min(12, int(speed // 2) + 1)

    return {
        "city": city,
        "condition": str(condition.get("text") or "").strip(),
        "condition_code": str(condition.get("code") or ""),
        "temperature": round(temperature) if temperature is not None else None,
        "feels_like": round(feels_like) if feels_like is not None else None,
        "humidity": round(humidity) if humidity is not None else None,
        "wind_level": int(wind_level),
        "precipitation_type": str(precipitation.get("type") or ""),
        "precipitation_mm": round(precip_mm, 1) if precip_mm is not None else None,
        "uv_index": round(uv_index) if uv_index is not None else None,
        "aqi": aqi,
        "aqi_category": aqi_category,
        "provider": "qweather",
        "updated_at": datetime.now().isoformat(),
    }


# ---------- 外出评估用的扩展材料：预报、分钟级降水、预警、日出日落、生活指数 ----------

# 老人一次外出通常 30~60 分钟，再往后的预报对本次决策没有意义
FORECAST_HOURS = 8
# 生活指数类型：1 运动 / 3 穿衣 / 5 紫外线 / 8 舒适度 / 9 感冒
LIFE_INDEX_TYPES = {"1": "sport", "3": "dressing", "5": "uv", "8": "comfort", "9": "cold"}
LIFE_INDEX_NAMES = {"sport": "运动", "dressing": "穿衣", "uv": "紫外线", "comfort": "舒适度", "cold": "感冒"}
# 预警和分钟级降水变化快，缓存要比实况短
ALERT_CACHE_SECONDS = 300


def _local_hhmm(value: Any) -> str:
    """'2026-09-06T05:40+08:00' -> '05:40'；解析不了就原样截取。"""
    text = str(value or "")
    if "T" in text:
        return text.split("T", 1)[1][:5]
    return text[:5]


def _parse_local(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # 系统内部统一用本地 naive 时间
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _normalize_hourly(payload: dict | None) -> list[dict]:
    hours = []
    for item in (payload or {}).get("hours") or []:
        when = _parse_local(item.get("forecastTime"))
        if when is None:
            continue
        humidity = _number(item.get("humidity"))
        if humidity is not None and humidity <= 1:
            humidity *= 100
        precipitation = item.get("precipitation") or {}
        probability = _number(precipitation.get("probability"))
        if probability is not None and probability <= 1:
            probability *= 100
        wind = item.get("wind") or {}
        hours.append(
            {
                "time": when.strftime("%H:%M"),
                "at": when.isoformat(timespec="minutes"),
                "condition": str((item.get("condition") or {}).get("text") or ""),
                "temperature": _round(_number((item.get("temperature") or {}).get("value"))),
                "feels_like": _round(_number((item.get("feelsLike") or {}).get("value"))),
                "humidity": _round(humidity),
                "wind_level": _round(_number(wind.get("scale"))),
                "precip_prob": _round(probability),
                "precip_mm": _number((precipitation.get("amount") or {}).get("value")),
                "precip_type": str(precipitation.get("type") or ""),
                "uv_index": _round(_number(item.get("uvIndex"))),
                "visibility_m": _round(_number((item.get("visibility") or {}).get("value"))),
            }
        )
    return hours


def _normalize_daily(payload: dict | None) -> dict:
    days = (payload or {}).get("days") or []
    if not days:
        return {}
    day = days[0]
    astro = day.get("astro") or {}
    return {
        "temp_max": _round(_number((day.get("temperatureMax") or {}).get("value"))),
        "temp_min": _round(_number((day.get("temperatureMin") or {}).get("value"))),
        "uv_index_max": _round(_number(day.get("uvIndexMax"))),
        "sunrise": _local_hhmm(astro.get("sunrise")),
        "sunset": _local_hhmm(astro.get("sunset")),
    }


def _normalize_alerts(payload: dict | None) -> list[dict]:
    alerts = []
    for item in (payload or {}).get("alerts") or []:
        color = item.get("color") or {}
        alerts.append(
            {
                "event": str((item.get("eventType") or {}).get("name") or "天气预警"),
                "severity": str(item.get("severity") or ""),
                "color": str(color.get("code") or "").lower(),
                "headline": str(item.get("headline") or ""),
                "description": str(item.get("description") or "")[:200],
                "expires": _local_hhmm(item.get("expireTime")),
            }
        )
    return alerts


def _normalize_minutely(payload: dict | None) -> dict:
    """把 5 分钟粒度的降水预报压成"几分钟后开始下、几分钟后停"。"""
    if not payload or str(payload.get("code")) != "200":
        return {}
    now = datetime.now()
    starts = ends = None
    peak = 0.0
    raining = False
    for slot in payload.get("minutely") or []:
        when = _parse_local(slot.get("fxTime"))
        amount = _number(slot.get("precip")) or 0.0
        if when is None:
            continue
        offset = int((when - now).total_seconds() // 60)
        if amount > 0:
            peak = max(peak, amount)
            if starts is None:
                starts = max(0, offset)
            raining = True
        elif raining and ends is None:
            ends = max(0, offset)
    return {
        "summary": str(payload.get("summary") or ""),
        "rain_starts_in_minutes": starts,
        "rain_ends_in_minutes": ends,
        "peak_precip_mm_per_5min": round(peak, 2),
    }


def _normalize_indices(payload: dict | None) -> dict:
    result = {}
    if not payload or str(payload.get("code")) != "200":
        return result
    for item in payload.get("daily") or []:
        key = LIFE_INDEX_TYPES.get(str(item.get("type")))
        if not key:
            continue
        result[key] = {
            "name": str(item.get("name") or LIFE_INDEX_NAMES[key]),
            "level": _number(item.get("level")),
            "category": str(item.get("category") or ""),
            "text": str(item.get("text") or ""),
        }
    return result


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


async def _qweather_extras(lat: float, lng: float) -> dict:
    host = _api_host(settings.qweather_api_host)
    api_key = settings.qweather_api_key or settings.weather_api_key
    if not host or not api_key:
        return {}
    point = f"{round(lat, 2)}/{round(lng, 2)}"
    location = f"{round(lng, 2)},{round(lat, 2)}"
    async with httpx.AsyncClient(
        timeout=_QWEATHER_TIMEOUT, headers={"X-QW-Api-Key": api_key}
    ) as client:
        hourly, daily, alerts, minutely, indices = await asyncio.gather(
            _qweather_get(
                client,
                f"https://{host}/weather/v1/hourly/{point}",
                {"hours": FORECAST_HOURS, "lang": "zh-hans", "localTime": "true"},
            ),
            _qweather_get(
                client,
                f"https://{host}/weather/v1/daily/{point}",
                {"days": 1, "lang": "zh-hans", "localTime": "true"},
            ),
            _qweather_get(
                client, f"https://{host}/weatheralert/v1/current/{point}", {"lang": "zh-hans"}
            ),
            _qweather_get(
                client, f"https://{host}/v7/minutely/5m", {"location": location, "lang": "zh"}
            ),
            _qweather_get(
                client,
                f"https://{host}/v7/indices/1d",
                {"location": location, "type": ",".join(LIFE_INDEX_TYPES), "lang": "zh"},
            ),
        )
    return {
        "hourly": _normalize_hourly(hourly),
        "today": _normalize_daily(daily),
        "alerts": _normalize_alerts(alerts) if alerts is not None else None,
        "minutely": _normalize_minutely(minutely),
        "indices": _normalize_indices(indices),
    }


def _mock_extras(current: dict) -> dict:
    """离线演示用：没有降水、没有预警，指数按当前天气推一个合理值。"""
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    rainy = any(word in current.get("condition", "") for word in ("雨", "雪"))
    hourly = []
    for offset in range(1, FORECAST_HOURS + 1):
        when = now + timedelta(hours=offset)
        hourly.append(
            {
                "time": when.strftime("%H:%M"),
                "at": when.isoformat(timespec="minutes"),
                "condition": current.get("condition", "多云"),
                "temperature": current.get("temperature"),
                "feels_like": current.get("temperature"),
                "humidity": current.get("humidity"),
                "wind_level": current.get("wind_level"),
                "precip_prob": 70 if rainy else 10,
                "precip_mm": 1.5 if rainy else 0.0,
                "precip_type": "rain" if rainy else "none",
                "uv_index": 5 if 10 <= when.hour <= 14 else 1,
                "visibility_m": 12000,
            }
        )
    temperature = current.get("temperature") or 20
    return {
        "hourly": hourly,
        "today": {
            "temp_max": temperature + 4,
            "temp_min": temperature - 5,
            "uv_index_max": 6,
            "sunrise": "06:00",
            "sunset": "18:00",
        },
        "alerts": [],
        "minutely": {
            "summary": "未来两小时有小雨" if rainy else "未来两小时无降水",
            "rain_starts_in_minutes": 0 if rainy else None,
            "rain_ends_in_minutes": None,
            "peak_precip_mm_per_5min": 0.3 if rainy else 0.0,
        },
        "indices": {
            "sport": {"name": "运动指数", "level": 2, "category": "较适宜", "text": "天气较好，适合户外慢走。"},
            "dressing": {"name": "穿衣指数", "level": 4, "category": "舒适", "text": "建议穿薄外套或长袖。"},
            "uv": {"name": "紫外线指数", "level": 3, "category": "中等", "text": "外出可以戴顶帽子。"},
            "comfort": {"name": "舒适度指数", "level": 2, "category": "较舒适", "text": "早晚凉爽，午后偏热。"},
            "cold": {"name": "感冒指数", "level": 1, "category": "少发", "text": "感冒机率较低。"},
        },
    }


async def get_outing_weather(
    city: str | None = None, lat: float | None = None, lng: float | None = None
) -> dict:
    """实况 + 未来几小时预报 + 分钟级降水 + 官方预警 + 日出日落 + 生活指数。

    规则引擎和智能体评估外出都用这一份；只回答"现在天气怎样"的问答仍用 get_weather。
    任何一路接口失败都只让对应字段为空，不影响其余判断。
    """
    current = await get_weather(city, lat, lng)
    context = dict(current)
    if current.get("provider") == "qweather" and lat is not None and lng is not None:
        cache_key = f"weather:extras:{round(lat, 2)}:{round(lng, 2)}"
        extras = await cache.get_json(cache_key)
        if not extras:
            extras = await _qweather_extras(lat, lng)
            if extras:
                await cache.set_json(cache_key, extras, ex=ALERT_CACHE_SECONDS)
        # 预警接口调用失败时 alerts 为 None，规则层据此说明"预警信息暂不可用"而不是当作没有预警
        context.update(extras or {})
    else:
        context.update(_mock_extras(current))
    return context


async def get_weather(
    city: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    force_refresh: bool = False,
) -> dict:
    city = city or settings.default_city
    provider = (settings.weather_provider or "mock").strip().lower()
    # 缓存键带上坐标: 同城不同小区的老人坐标不同, 只按城市缓存会互相串数据
    point = f"{round(lat, 2)},{round(lng, 2)}" if lat is not None and lng is not None else "-"
    bucket = datetime.now().strftime("%Y%m%d%H%M")[:-1]
    cache_key = f"weather:{provider}:{city}:{point}:{bucket}"
    cached = None if force_refresh else await cache.get_json(cache_key)
    if cached:
        return cached

    data: dict | None = None
    if lat is not None and lng is not None:
        if provider == "qweather":
            data = await _qweather(city, lat, lng)
        elif provider == "open-meteo":
            data = await _open_meteo(city, lat, lng)

    if data is None:
        if provider != "mock":
            logger.warning("%s 天气数据不可用, 本次回退到模拟天气", provider)
        data = _mock_weather(city)
        # 真实数据源只是临时抖动时, 不要让模拟数据占满整个缓存周期
        ttl = 60
    else:
        ttl = settings.weather_cache_seconds

    await cache.set_json(cache_key, data, ex=ttl)
    return data
