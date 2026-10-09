"""天气能力演示脚本: 把和风天气接入的每一层摊开给人看。

不需要启动后端服务, 直接调用 backend 内的天气服务, 依次展示
"原始 API 响应 -> 系统内的天气快照 -> 外出规则判定 -> 机器狗播报文案"。

用法:
    python scripts/demo_weather.py                       # 交互式: 输入调用参数, 返回该地各项气象数据
    python scripts/demo_weather.py --batch               # 直接跑一遍老人档案所在地的完整演示
    python scripts/demo_weather.py --city 北京 三亚 哈尔滨  # 多地对比, 演示极端天气拦截
    python scripts/demo_weather.py --lat 30.27 --lng 120.15
    python scripts/demo_weather.py --raw                 # 附带和风天气原始 JSON
    python scripts/demo_weather.py --fresh               # 跳过缓存, 强制真实调用
    python scripts/demo_weather.py --json                # 输出 JSON, 便于接到其他脚本

现场手敲演示走这个模式, 会进到一个预置好 weather() 的交互环境:
    python scripts/demo_weather.py --repl

下面的"现场手敲区"是为当众写代码准备的封装, 每个入口都做了容错:
城市名拼错、经纬度写反、字段名敲错、接口抖动, 都不会打断演示。
"""

import argparse
import asyncio
import code
import difflib
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 演示脚本不经过 HTTP 接口, 直接复用后端的天气服务
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.rules.definitions import DEFAULT_RULES  # noqa: E402
from app.rules.engine import OutingRuleEngine  # noqa: E402
from app.services import weather_service  # noqa: E402
from app.services.evaluation_service import _weather_text  # noqa: E402

DIVIDER = "=" * 78

# 下面两项都放模块级, 这样被 import 进 REPL 手敲时也已经生效。
# Windows 控制台默认 GBK, 不切换编码打中文会抛 UnicodeEncodeError。
# stderr 也要切: 交互环境的提示语和报错都走 stderr
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# httpx 每次调用都会打一行请求日志, 手敲时会盖住输出; 想看真实请求用 --raw。
# 缓存只屏蔽连接成功那条 INFO, Redis 不可用的告警仍会打出来
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("app.core.cache").setLevel(logging.WARNING)

# 演示常用地点, 免得现场查经纬度; 冷热两极的城市用来触发气温红线
DEMO_CITIES: dict[str, tuple[float, float]] = {
    "杭州": (30.27, 120.15),
    "北京": (39.92, 116.41),
    "上海": (31.23, 121.47),
    "广州": (23.13, 113.26),
    "成都": (30.66, 104.07),
    "哈尔滨": (45.80, 126.53),
    "三亚": (18.25, 109.51),
    "拉萨": (29.65, 91.14),
    "乌鲁木齐": (43.83, 87.62),
}


# 真实天气好的时候演示不出"恶劣天气拦截", 这里备几组假设数据专门走规则判定。
# 用的是和风天气真实的现象描述, 因此能验证关键词匹配是否覆盖到位。
SCENARIOS: dict[str, dict] = {
    "暴雨": {
        "condition": "大暴雨",
        "temperature": 24,
        "feels_like": 26,
        "humidity": 96,
        "wind_level": 7,
        "aqi": 35,
        "aqi_category": "优",
    },
    "雷阵雨": {
        "condition": "强雷阵雨",
        "temperature": 27,
        "feels_like": 30,
        "humidity": 88,
        "wind_level": 6,
        "aqi": 42,
        "aqi_category": "优",
    },
    "重度污染": {
        "condition": "中度霾",
        "temperature": 11,
        "feels_like": 9,
        "humidity": 62,
        "wind_level": 1,
        "aqi": 268,
        "aqi_category": "重度污染",
    },
    "高温": {
        "condition": "晴",
        "temperature": 39,
        "feels_like": 43,
        "humidity": 55,
        "wind_level": 2,
        "aqi": 78,
        "aqi_category": "良",
    },
    "严寒": {
        "condition": "中雪",
        "temperature": -6,
        "feels_like": -12,
        "humidity": 72,
        "wind_level": 5,
        "aqi": 66,
        "aqi_category": "良",
    },
}


def build_scenario(name: str, city: str) -> dict:
    return {
        "city": city,
        "condition_code": "",
        "uv_index": None,
        "provider": "scenario",
        "updated_at": datetime.now().isoformat(),
        **SCENARIOS[name],
    }


def fetch_weather(city: str, lat: float, lng: float, fresh: bool = False) -> dict:
    """同步取一次天气快照, 供其他脚本直接 import 使用。"""
    return asyncio.run(
        weather_service.get_weather(city, lat, lng, force_refresh=fresh)
    )


# ===========================================================================
# 现场手敲区
# ---------------------------------------------------------------------------
# 这一段是当众写代码时敲的封装。演示台上最怕的是敲错一个字母就报红,
# 所以这里的每个入口都刻意容错: 城市名可以拼错或写拼音, 经纬度可以写反,
# 字段名可以敲错, 接口失败会自动接上一次的数据。真实调用照旧, 只是不会中断。
#
# 现场可以这样敲:
#     weather()                    -> 默认城市的实时天气
#     weather("北京")               -> 指定城市
#     weather("beijng")            -> 拼错也能命中北京
#     weather(120.15, 30.27)       -> 经纬度写反了会自动纠正
#     w = weather("杭州")
#     w.temperature / w.温度 / w["tempreature"]   -> 都取得到气温
#     w.can_go_out()               -> 外出判定
#     scenario("暴雨").can_go_out() -> 假设恶劣天气, 演示拦截
# ===========================================================================

# 城市别名: 拼音、缩写、常见错别字, 都指回内置坐标表
CITY_ALIASES = {
    "hangzhou": "杭州", "hz": "杭州", "杭洲": "杭州",
    "beijing": "北京", "bj": "北京", "peking": "北京",
    "shanghai": "上海", "sh": "上海",
    "guangzhou": "广州", "gz": "广州",
    "chengdu": "成都", "cd": "成都",
    "harbin": "哈尔滨", "haerbin": "哈尔滨",
    "sanya": "三亚",
    "lhasa": "拉萨", "lasa": "拉萨",
    "urumqi": "乌鲁木齐", "wulumuqi": "乌鲁木齐",
}

# 场景别名: 台上懒得切输入法时可以直接敲拼音或英文
SCENARIO_ALIASES = {
    "baoyu": "暴雨", "rain": "暴雨", "storm": "暴雨",
    "leizhenyu": "雷阵雨", "thunder": "雷阵雨",
    "wuran": "重度污染", "haze": "重度污染", "smog": "重度污染",
    "gaowen": "高温", "hot": "高温",
    "yanhan": "严寒", "cold": "严寒", "snow": "严寒",
}

# 字段别名: 中文、简写、常用叫法都映射到天气快照里的真实字段
FIELD_ALIASES = {
    "温度": "temperature", "气温": "temperature", "temp": "temperature",
    "体感": "feels_like", "feels": "feels_like", "feel": "feels_like",
    "天气": "condition", "现象": "condition", "text": "condition",
    "湿度": "humidity", "humid": "humidity",
    "风力": "wind_level", "风": "wind_level", "wind": "wind_level",
    "空气": "aqi", "空气质量": "aqi", "air": "aqi", "pm": "aqi",
    "紫外线": "uv_index", "uv": "uv_index",
    "城市": "city", "来源": "provider",
}

# 按城市记住最近一次的真实数据。接口万一在台上抽了, 拿它顶上,
# 比让服务层的模拟数据冒充实时值要诚实
_LAST_GOOD: dict[str, dict] = {}

# 服务层默认等 8 秒, 台上网络不通时会像卡死。只改本进程, 不影响后端。
# 实测真实调用约 0.6 秒, 4 秒留足余量
weather_service._QWEATHER_TIMEOUT = 4.0


def _resolve(name, candidates: list[str]) -> str | None:
    """把敲错的名字纠到最接近的候选上。"""
    key = str(name).strip().lower().replace(" ", "").replace("_", "")
    table = {c.lower().replace("_", ""): c for c in candidates}
    if key in table:
        return table[key]
    close = difflib.get_close_matches(key, list(table), n=1, cutoff=0.5)
    return table[close[0]] if close else None


def _city_of(name) -> str:
    """城市名: 支持中文、拼音、缩写与拼错, 认不出就退到默认城市。"""
    default = settings.default_city if settings.default_city in DEMO_CITIES else "杭州"
    if name is None:
        return default
    text = str(name).strip()
    if text in DEMO_CITIES:
        return text
    hit = _resolve(text, list(DEMO_CITIES) + list(CITY_ALIASES))
    if hit is None:
        print(f"[提示] 不认识 {text}, 内置城市: {' '.join(DEMO_CITIES)}；本次用{default}")
        return default
    return CITY_ALIASES.get(hit, hit)


def _city_for_point(lat: float, lng: float) -> str:
    """坐标反查地点名, 认不出就用坐标本身当标签, 免得套上别的城市名。"""
    for name, (city_lat, city_lng) in DEMO_CITIES.items():
        if abs(city_lat - lat) < 0.5 and abs(city_lng - lng) < 0.5:
            return name
    # 播报文案会直接用这个名字, 所以给个能读通的说法
    return "该位置"


def _point_of(city: str, numbers: list[float]) -> tuple[float, float]:
    if len(numbers) < 2:
        return DEMO_CITIES.get(city, DEMO_CITIES["杭州"])
    lat, lng = numbers[0], numbers[1]
    # 纬度不可能超过 90, 一旦超了就是经纬度写反了
    if abs(lat) > 90 >= abs(lng):
        print(f"[提示] 经纬度写反了, 已按 lat={lng} lng={lat} 处理")
        lat, lng = lng, lat
    return lat, lng


class Weather:
    """一次天气结果。属性、下标、中文别名、拼错的字段名都能取到值。"""

    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def __repr__(self) -> str:
        return self.summary()

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lookup(name)

    def __getitem__(self, name: str):
        return self._lookup(name)

    def _lookup(self, name: str):
        alias = FIELD_ALIASES.get(str(name).strip().lower())
        if alias:
            return self._data.get(alias)
        methods = [m for m in dir(type(self)) if not m.startswith("_")]
        hit = _resolve(name, list(self._data) + methods)
        if hit in self._data:
            return self._data[hit]
        if hit:
            return getattr(self, hit)
        print(f"[提示] 没有 {name} 这个字段, 可用: {' '.join(self._data)}")
        return None

    def summary(self) -> str:
        """一行中文摘要, 直接就是机器狗要播报的话。"""
        text = _weather_text(self._data)
        if self._data.get("provider") == "scenario":
            return f"{text}（演示假设数据）"
        return text

    def can_go_out(self) -> str:
        """外出判定: 这条就是系统里拦不拦老人出门的依据。"""
        hits = judge(self._data)
        blocks = [h for h in hits if h.level == "block"]
        warns = [h for h in hits if h.level == "warn"]
        if blocks:
            return "不建议外出：" + "；".join(h.detail for h in blocks)
        if warns:
            return "可以外出，但要注意：" + "；".join(h.detail for h in warns)
        return "可以外出：天气与空气质量均未命中风险规则"

    def data(self) -> dict:
        """完整天气快照, 就是存进决策留痕的那份。"""
        return dict(self._data)


def weather(*args, fresh: bool = False, **kwargs) -> Weather:
    """取天气。参数怎么写都行: 城市名、坐标、或者什么都不传。"""
    city_arg, numbers = None, []
    lat_kw = lng_kw = None

    # 关键字参数只认城市和经纬度, 别的一概忽略,
    # 否则台上多传一个参数就会把城市顶掉
    for key, value in kwargs.items():
        name = str(key).strip().lower()
        if name.startswith("lat") or "纬" in name:
            lat_kw = value
        elif name.startswith(("lng", "lon")) or "经" in name:
            lng_kw = value
        elif "city" in name or "城市" in name or "地点" in name or name == "name":
            city_arg = value

    for value in args:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            if isinstance(value, str) and value.strip():
                city_arg = value

    if lat_kw is not None and lng_kw is not None:
        try:
            numbers = [float(lat_kw), float(lng_kw)]
        except (TypeError, ValueError):
            pass

    city = _city_of(city_arg)
    return _weather_for(city, *_point_of(city, numbers), fresh=fresh)


def _weather_for(city: str, lat: float, lng: float, fresh: bool = False) -> Weather:
    """真正发起调用的一层, 地点已经解析好, 只负责取数与兜底。"""
    try:
        data = fetch_weather(city, lat, lng, fresh=fresh)
    except Exception as exc:
        print(f"[提示] 取天气失败({type(exc).__name__}), 转用兜底数据继续演示")
        data = {}

    # 按坐标记, 免得"该位置"这种通用标签把不同地点的数据混在一起
    key = f"{city}|{round(lat, 2)},{round(lng, 2)}"
    if data.get("provider") == "qweather":
        _LAST_GOOD[key] = data
        return Weather(data)

    # 服务层在接口失败时会自己回落到模拟天气, 这里不让模拟值冒充实时数据
    if key in _LAST_GOOD:
        print("[提示] 本次没取到实时数据, 用该地点最近一次的真实数据继续演示")
        return Weather(_LAST_GOOD[key])
    print(f"[提示] 本次是 {data.get('provider') or 'mock'} 兜底数据, 不是和风天气实时值")
    return Weather(data or weather_service._mock_weather(city))


def scenario(name="暴雨", city=None) -> Weather:
    """假设一种天气, 用来演示恶劣天气怎么拦下外出。"""
    hit = _resolve(name, list(SCENARIOS) + list(SCENARIO_ALIASES))
    if hit is None:
        print(f"[提示] 没有 {name} 这个场景, 可选: {' '.join(SCENARIOS)}")
        hit = "暴雨"
    return Weather(build_scenario(SCENARIO_ALIASES.get(hit, hit), _city_of(city)))


# 台上手滑写成中文或另一个习惯名字也能跑
天气 = weather
假设 = scenario
get_weather = weather


async def fetch_raw(lat: float, lng: float) -> dict:
    """按官方文档直接发两个 HTTP 请求, 用来证明数据确实来自和风天气。"""
    host = weather_service._api_host(settings.qweather_api_host)
    key = settings.qweather_api_key or settings.weather_api_key
    if not host or not key:
        return {}
    point = f"{round(lat, 2)}/{round(lng, 2)}"
    params = {"lang": "zh-hans"}
    result: dict = {}
    async with httpx.AsyncClient(timeout=10.0, headers={"X-QW-Api-Key": key}) as client:
        for name, path in (
            ("实时天气", f"weather/v1/current/{point}"),
            ("实时空气质量", f"airquality/v1/current/{point}"),
        ):
            url = f"https://{host}/{path}"
            resp = await client.get(url, params=params)
            result[name] = {
                "url": f"{url}?lang=zh-hans",
                "status": resp.status_code,
                "body": resp.json() if resp.status_code == 200 else resp.text[:200],
            }
    return result


def judge(snapshot: dict) -> list:
    """跑一遍外出规则里的天气这一条, 返回命中项。"""
    engine = OutingRuleEngine(elder=None, rules=DEFAULT_RULES)
    engine.check_weather(snapshot)
    return engine.hits


async def load_elder_location() -> tuple[str, float, float] | None:
    """默认演示对象取档案里的第一位老人, 与线上判定用的坐标完全一致。"""
    try:
        from sqlalchemy import select

        from app.core.db import SessionLocal
        from app.models import Elder

        async with SessionLocal() as db:
            elder = (
                await db.execute(select(Elder).order_by(Elder.id).limit(1))
            ).scalars().first()
        if elder is None:
            return None
        return elder.city, elder.home_lat, elder.home_lng
    except Exception as exc:
        print(f"[提示] 读取老人档案失败({type(exc).__name__}), 改用杭州坐标演示")
        return None


def describe(snapshot: dict) -> tuple[str, str, str]:
    temp = snapshot.get("temperature")
    feels = snapshot.get("feels_like")
    temp_text = f"{temp}℃" if temp is not None else "温度未知"
    if feels is not None:
        temp_text += f"（体感 {feels}℃）"

    details = [
        f"湿度 {snapshot['humidity']}%" if snapshot.get("humidity") is not None else "",
        f"{snapshot['wind_level']} 级风" if snapshot.get("wind_level") is not None else "",
        f"紫外线 {snapshot['uv_index']}" if snapshot.get("uv_index") is not None else "",
    ]
    aqi = snapshot.get("aqi")
    aqi_text = (
        f"AQI {aqi} {snapshot.get('aqi_category', '')}".strip()
        if aqi is not None
        else "未取空气质量（QWEATHER_AIR_QUALITY=false 或接口失败）"
    )
    return temp_text, "  ".join(d for d in details if d), aqi_text


def print_conditions(snapshot: dict) -> None:
    temp_text, detail_text, aqi_text = describe(snapshot)
    print(f"  实时天气   {snapshot['condition'] or '未知'} {temp_text}")
    if detail_text:
        print(f"             {detail_text}")
    print(f"  空气质量   {aqi_text}")


def print_verdict(snapshot: dict) -> list:
    hits = judge(snapshot)
    if not hits:
        print("  外出判定   通过（天气与空气质量均未命中风险规则）")
    for hit in hits:
        flag = "拦截" if hit.level == "block" else "提醒"
        line = f"  外出判定   [{flag}] {hit.detail}"
        if hit.threshold:
            line += f"（阈值 {hit.threshold}）"
        print(line)
    print(f"  播报文案   {_weather_text(snapshot)}")
    return hits


def show_scenario(name: str, city: str) -> dict:
    """用假设天气走一遍规则判定, 演示恶劣天气如何拦截外出。"""
    print(f"\n{DIVIDER}\n>> 假设场景：{name}（{city}）\n{DIVIDER}")
    print("  说明       以下为演示用假设数据, 不是和风天气实时返回")
    snapshot = build_scenario(name, city)
    print_conditions(snapshot)
    hits = print_verdict(snapshot)
    return {
        "city": city,
        "scenario": name,
        "snapshot": snapshot,
        "hits": [hit.model_dump() for hit in hits],
    }


async def show(city: str, lat: float, lng: float, args) -> dict:
    print(f"\n{DIVIDER}\n>> {city}  ({lat}, {lng})\n{DIVIDER}")

    if args.raw:
        for name, item in (await fetch_raw(lat, lng)).items():
            print(f"[原始响应] {name}  HTTP {item['status']}")
            print(f"  GET {item['url']}")
            print(f"  {json.dumps(item['body'], ensure_ascii=False)[:400]}")
        print()

    started = time.perf_counter()
    snapshot = await weather_service.get_weather(
        city, lat, lng, force_refresh=args.fresh
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    # 快照里的 updated_at 是数据成型时刻, 明显早于当下就说明这次读的是缓存
    age = (datetime.now() - datetime.fromisoformat(snapshot["updated_at"])).total_seconds()
    origin = f"缓存命中（数据 {int(age)}s 前取得）" if age > 2 else "本次实时调用"

    if snapshot["provider"] != "qweather":
        print(
            f"[注意] 未取到和风天气数据, 已回落到 {snapshot['provider']}。"
            "演示前请先跑 python scripts/probe_apis.py 确认密钥与网络"
        )

    print_conditions(snapshot)
    print(f"  数据来源   {snapshot['provider']} · {elapsed}ms · {origin}")

    # 紧接着再取一次, 演示同城同时段只消耗一次调用量
    started = time.perf_counter()
    await weather_service.get_weather(city, lat, lng)
    print(
        f"  缓存复核   再查一次 {int((time.perf_counter() - started) * 1000)}ms, "
        f"{settings.weather_cache_seconds}s 内不再请求和风天气"
    )

    hits = print_verdict(snapshot)
    return {
        "city": city,
        "lat": lat,
        "lng": lng,
        "elapsed_ms": elapsed,
        "snapshot": snapshot,
        "hits": [hit.model_dump() for hit in hits],
    }


async def resolve_targets(args) -> list[tuple[str, float, float]]:
    if args.lat is not None and args.lng is not None:
        return [(args.city[0] if args.city else "自定义坐标", args.lat, args.lng)]

    targets: list[tuple[str, float, float]] = []
    for name in args.city:
        if name in DEMO_CITIES:
            targets.append((name, *DEMO_CITIES[name]))
        else:
            print(f"[跳过] {name} 不在内置地点里, 请用 --lat/--lng 指定坐标")
    if targets:
        return targets

    elder = await load_elder_location()
    return [elder] if elder else [("杭州", *DEMO_CITIES["杭州"])]


async def run(args) -> int:
    # 与后端启动时一致: 有 Redis 就用 Redis, 没有就退化为进程内缓存
    await weather_service.cache.connect()

    host = weather_service._api_host(settings.qweather_api_host)
    print(DIVIDER)
    print("天气能力演示 · 和风天气 API v1")
    print(
        f"provider={settings.weather_provider}  host={host or '未配置'}  "
        f"空气质量 {'开' if settings.qweather_air_quality else '关'}  "
        f"缓存 {settings.weather_cache_seconds}s（{weather_service.cache.backend}）"
    )
    print(DIVIDER)

    results = []
    if not args.scenario_only:
        for city, lat, lng in await resolve_targets(args):
            results.append(await show(city, lat, lng, args))
    await weather_service.cache.close()

    demo_city = results[0]["city"] if results else settings.default_city
    for name in args.scenario:
        results.append(show_scenario(name, demo_city))

    if args.json:
        print(f"\n{DIVIDER}")
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))

    print(f"\n{DIVIDER}")
    live = [r for r in results if r["snapshot"]["provider"] == "qweather"]
    scenarios = [r for r in results if r.get("scenario")]
    blocked = [
        r.get("scenario") or r["city"]
        for r in results
        if any(h["level"] == "block" for h in r["hits"])
    ]
    print(
        f"演示完成: {len(live)} 个地点取到和风天气实时数据"
        + (f", 另跑了 {len(scenarios)} 个假设场景" if scenarios else "")
    )
    if blocked:
        print(f"判定为禁止外出: {'、'.join(blocked)}")
    return 0


def start_repl() -> int:
    """进入预置好封装的交互环境, 供现场手敲。"""
    host = weather_service._api_host(settings.qweather_api_host)
    banner = "\n".join(
        [
            DIVIDER,
            "天气封装 · 现场手敲环境（真实调用和风天气）",
            f"host={host or '未配置'}  空气质量 {'开' if settings.qweather_air_quality else '关'}",
            DIVIDER,
            "  weather()                      默认城市的实时天气",
            "  weather('北京')                 指定城市（拼音、缩写、拼错都认）",
            "  weather(120.15, 30.27)         经纬度写反会自动纠正",
            "  weather('杭州', fresh=True)     跳过缓存, 强制真实调用",
            "  w = weather('杭州')",
            "  w.温度  w.temperature  w['tempreature']    字段名怎么敲都取得到",
            "  w.can_go_out()                 外出判定",
            "  w.summary()                    机器狗播报文案",
            "  w.data()                       完整天气快照",
            "  scenario('暴雨').can_go_out()   假设恶劣天气, 演示拦截",
            "  exit()                         退出",
            DIVIDER,
        ]
    )
    code.interact(
        banner=banner,
        exitmsg="手敲演示结束",
        local={
            "weather": weather,
            "scenario": scenario,
            "天气": weather,
            "假设": scenario,
            "Weather": Weather,
            "fetch_weather": fetch_weather,
            "judge": judge,
            "DEMO_CITIES": DEMO_CITIES,
            "SCENARIOS": SCENARIOS,
            "settings": settings,
        },
    )
    return 0


# 交互演示时逐项列出的气象要素: (快照字段, 中文标签, 单位)
PRECIP_LABELS = {
    "none": "无",
    "rain": "雨",
    "snow": "雪",
    "sleet": "雨夹雪",
    "hail": "冰雹",
    "": "无",
}


def print_fields(snapshot: dict) -> None:
    """各项气象要素压成两行, 台上一眼看得完。"""
    parts = []
    if snapshot.get("temperature") is not None:
        parts.append(f"{snapshot['temperature']}℃")
    if snapshot.get("feels_like") is not None:
        parts.append(f"体感 {snapshot['feels_like']}℃")
    if snapshot.get("humidity") is not None:
        parts.append(f"湿度 {snapshot['humidity']}%")
    if snapshot.get("wind_level") is not None:
        parts.append(f"风力 {snapshot['wind_level']} 级")
    if snapshot.get("precipitation_mm") is not None:
        kind = PRECIP_LABELS.get(str(snapshot.get("precipitation_type") or ""), "")
        note = f" {kind}" if snapshot["precipitation_mm"] and kind not in ("", "无") else ""
        parts.append(f"降水 {snapshot['precipitation_mm']}mm{note}")
    if snapshot.get("uv_index") is not None:
        parts.append(f"紫外线 {snapshot['uv_index']}")

    print(f"  天气  {snapshot.get('condition') or '未知'}  {'   '.join(parts)}")
    if snapshot.get("aqi") is not None:
        print(f"  空气  AQI {snapshot['aqi']} {snapshot.get('aqi_category', '')}".rstrip())


def call_weather(text: str, fresh: bool = False) -> Weather | None:
    """把一行输入当成函数入参, 真调一次天气函数并展示结果。"""
    tokens = [t for t in text.replace(",", " ").replace("，", " ").split() if t]
    numbers = []
    for token in tokens:
        try:
            numbers.append(float(token))
        except ValueError:
            pass

    # 两个数字就是经纬度, 否则按地点名; 场景名单独走假设数据
    started = time.perf_counter()
    if len(numbers) >= 2:
        lat, lng = _point_of("", numbers)
        city = _city_for_point(lat, lng)
        call = f'get_weather(city="{city}", lat={lat}, lng={lng})'
        result = _weather_for(city, lat, lng, fresh=fresh)
    elif tokens and _resolve(tokens[0], list(SCENARIOS) + list(SCENARIO_ALIASES)):
        call = f"scenario({tokens[0]!r})"
        result = scenario(tokens[0])
    else:
        city = _city_of(tokens[0] if tokens else None)
        lat, lng = _point_of(city, [])
        call = f'get_weather(city="{city}", lat={lat}, lng={lng})'
        if not tokens:
            call += "   # 未传参数"
        result = weather(city, lat, lng, fresh=fresh)
    elapsed = int((time.perf_counter() - started) * 1000)

    snapshot = result.data()
    if snapshot.get("provider") == "scenario":
        source = "假设数据"
    else:
        age = (
            datetime.now() - datetime.fromisoformat(snapshot["updated_at"])
        ).total_seconds()
        source = "缓存命中" if age > 2 else "真实调用"

    print(f"  调用  {call}")
    print(f"  返回  {snapshot.get('provider')} · {source} · {elapsed}ms")
    print_fields(snapshot)
    print(f"  判定  {result.can_go_out()}")
    print(f"  播报  {result.summary()}")
    return result


def start_console() -> int:
    """交互式演示: 输入调用参数, 当场返回该地点的各项气象数据。"""
    host = weather_service._api_host(settings.qweather_api_host)
    print(DIVIDER)
    print(f"天气函数调用演示 · 真实调用和风天气（{host or '未配置'}）")
    print(f"参数: 地点名（{' '.join(list(DEMO_CITIES)[:3])}… / beijing / bj）｜经纬度（39.92 116.41）")
    print(f"      假设天气（{' '.join(SCENARIOS)}）｜回车用默认城市｜json｜退出")
    print(DIVIDER)

    last: Weather | None = None
    while True:
        try:
            text = input("\n请输入调用参数带入天气函数 >>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n演示结束")
            return 0

        if text.lower() in ("exit", "quit", "q", "退出"):
            print("演示结束")
            return 0

        if text.lower() == "json":
            if last is None:
                print("  还没调用过, 先输入一个地点")
            else:
                print(json.dumps(last.data(), ensure_ascii=False, indent=2))
            continue

        fresh = False
        if text.lower().endswith(("!", "！", " fresh")):
            # 末尾加感叹号就跳过缓存, 现场想强调"真的在调接口"时用
            fresh = True
            text = text.rstrip("!！").removesuffix(" fresh").strip()

        try:
            last = call_weather(text, fresh=fresh) or last
        except Exception as exc:
            # 台上不能因为一次异常就退出演示
            print(f"  [提示] 本次调用出错({type(exc).__name__}: {exc}), 换个参数再试")


def main() -> int:
    parser = argparse.ArgumentParser(description="天气接入演示")
    parser.add_argument(
        "--city",
        nargs="*",
        default=[],
        help=f"演示地点, 可多个。内置: {' '.join(DEMO_CITIES)}",
    )
    parser.add_argument("--lat", type=float, help="自定义纬度")
    parser.add_argument("--lng", type=float, help="自定义经度")
    parser.add_argument(
        "--scenario",
        nargs="*",
        default=[],
        choices=list(SCENARIOS),
        help="追加假设天气场景, 演示恶劣天气拦截外出",
    )
    parser.add_argument(
        "--scenario-only",
        action="store_true",
        help="只跑假设场景, 不调用和风天气",
    )
    parser.add_argument("--raw", action="store_true", help="附带和风天气原始响应")
    parser.add_argument("--fresh", action="store_true", help="跳过缓存, 强制真实调用")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument(
        "--repl", action="store_true", help="进入现场手敲环境, 预置好 weather()"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="不进入交互, 直接跑一遍老人档案所在地的完整演示",
    )
    args = parser.parse_args()

    # .env 里的 SQLite 路径是相对的, 从仓库根目录跑本脚本会指向不存在的库
    if settings.database_url.startswith("sqlite") and "///./" in settings.database_url:
        db_file = BACKEND_DIR / settings.database_url.split("///./", 1)[1]
        settings.database_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    # 交互模式下每次调用各起一个事件循环, 连了 Redis 反而会跨循环失效,
    # 因此这两个分支都不连缓存, 走进程内缓存
    if args.repl:
        return start_repl()

    # 不带参数运行就是交互式的函数调用演示; 指定了任何参数才走批量演示
    if not any(
        [
            args.city,
            args.lat is not None,
            args.lng is not None,
            args.scenario,
            args.scenario_only,
            args.raw,
            args.fresh,
            args.json,
            args.batch,
        ]
    ):
        return start_console()

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
