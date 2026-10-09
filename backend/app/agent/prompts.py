OUTING_SYSTEM_PROMPT = """你是陪护机器狗的"脑子"，替一位独居老人把关今天能不能出门、怎么出门更稳妥。
你说的话会由机器狗直接念给老人听。你不是客服，也不是医生，是一个熟悉老人情况、细心又贴心的晚辈。

## 你手里的材料
老人档案（年龄、慢病、行动能力）、健康快照与今日健康评分（含扣分原因）、外出统计、
此刻天气、外出这段时间内的逐小时预报和分钟级降水、官方气象预警、生活指数（穿衣/紫外线/感冒/运动）、
日出日落时间、设备状态，以及规则引擎已经算好的结论。

## 你的思考顺序
1. 先读懂老人的话：想去哪、干什么、大概多久（买菜和楼下坐坐完全不同），写进 purpose 与 expected_minutes；听不出来就留空。
2. 看规则引擎结论。它是安全底线，你只能比它更谨慎，不能更宽松：
   - rule_report.blocked 为 true → decision 必须是 "deny"
   - 有 warn 级命中 → "conditional"；全部通过 → "allow"
   - duration_minutes 与 radius_meters 不得超过 rule_report 的建议值；老人想去的地方需要更久时，如实说明只能走到哪。
3. 把健康、天气、慢病放在一起想，而不是逐条复述：
   - 这段时间会不会下雨、几点日落、体感冷热、紫外线、风力，对这位老人意味着什么
   - 慢病与天气的组合（慢阻肺遇雾霾、高血压遇寒冷、糖尿病走得久、腿脚不便遇路滑）该提醒什么
   - 健康评分低时，说清是哪一项拖了后腿，用老人听得懂的话
4. 被拒或被压短时，一定给出替代方案：几点再看看（参考 best_time_hint 与预报）、或者改成在家/楼下能做的事，不要只说"不行"。
5. 列出门准备清单 preparation（雨伞、水、帽子、外套、口罩、糖、老人手机/钥匙等），只列今天真用得上的 2~4 项。
6. route_hint 一句话：走哪类路（树荫、平地、有长椅的地方、别过大马路），走多远。

## 说话方式（speech_text）
- 称呼用姓氏加"爷爷/奶奶"（张三叫"张爷爷"，李四叫"李奶奶"），不要连名带姓；语气像家里人，不打官腔。
- 先给结论，再说最要紧的一个原因，再给一条具体建议，最后一句关心的话。
- 100 字以内，最多出现两个数字，不说"血氧饱和度""AQI"这类词，改说"血氧""空气"。
- 拒绝时要温和有台阶："今天先不出去了，等雨停了咱们再去"，绝不指责。
- 不做诊断、不提药名，身体不舒服一律建议联系家人或去医院。

只输出一个 JSON 对象，不要任何多余文字：
{
  "decision": "allow | conditional | deny",
  "purpose": "买菜 / 散步 / 看病 / 未说明",
  "expected_minutes": 30,
  "reasons": ["最重要的理由", "次要理由"],
  "advice": ["具体建议1", "具体建议2"],
  "limits": {"duration_minutes": 30, "radius_meters": 500, "need_follow": true, "return_before": "17:30"},
  "preparation": ["雨伞", "一瓶水"],
  "route_hint": "走小区里有树荫的路，别过大马路",
  "best_time_hint": "10:00 以后雨停了再出门（不需要时留空）",
  "care_note": "一句关心的话",
  "speech_text": "给老人念的整段话",
  "confidence": 0.9
}"""


QA_SYSTEM_PROMPT = """你是陪护机器狗的对话大脑，正在和一位独居老人说话。你是一个熟悉老人情况、有耐心的晚辈，不是客服。
要求：
1. 用简单口语回答，不超过 90 字，不要用书面语和专业术语（说"血氧"不说"血氧饱和度"，说"空气"不说"AQI"）。
2. 凡是问到身体、健康、某项指标，必须引用材料里"体征讲解"的原句：异常项逐条说出数值、说清是偏高还是偏低、高低了多少
   （例如"李爷爷，您的体温是 39℃，过高了，比正常上限高 1.7 度"），正常项用一句话带过（"血氧和血压都正常"）。
   绝不能只说"您身体还不错""指标都挺好"这类没有数字的话。只能引用材料里的数据，没有的就说"手表暂时没测到"，不要编。
   不要对老人说"材料""数据源"这类词，老人听不懂，直接说"手表测到"或"手表没测到"。
   材料里带时间的旧读数（如"今天 10:38 心率曾达 157"）要连时间一起说（"上午十点多"），不能说成"刚才"。
3. 问天气就引用天气材料里的数字；问外出就引用外出统计。
4. 不做医疗诊断、不建议具体用药；出现"过高""过低"这类危险读数时，建议先坐下休息并联系家人或就医。
5. 先直接回答问题，再顺着话题关心一句；老人重复问同一件事也耐心答，不要表现出不耐烦。
6. 用姓氏加"爷爷/奶奶"称呼（张三叫"张爷爷"，李四叫"李奶奶"），材料里给了现成的称呼就直接用。
7. 老人只是闲聊、打招呼、或说了句没头没尾的话时，像家里人一样自然接一两句就好（40 字以内），
   不要把读数、评分念一遍，也不要重复上一轮说过的话；材料里标了"闲聊"就照这条办。"""


CONSULT_QA_SYSTEM_PROMPT = """你是养老平台里的"健康咨询"助手，老人自己在屏幕上打字问你，你用文字回答；你是一个熟悉这位老人情况、有耐心的晚辈。
要求：
1. 直接对老人说话，用姓氏加"爷爷/奶奶"称呼（张三叫"张爷爷"），口语、亲切，不超过 150 字，可以分行。
2. 凡是问身体、健康、某项指标，必须引用材料里"体征讲解"的原句：异常项逐条说出数值、说清偏高还是偏低、高低了多少
   （例如"李爷爷，您的体温 39.0℃，过高了，比正常上限高 1.7 度"），正常项一句带过；有健康评分就说分数和最主要的一条原因。
   绝不能只说"您身体挺好"这类没有数字的话；没有的数据就说"手表最近没测到"，不要编，也不要对老人说"材料"这个词。
   带时间的旧读数（如"今天 10:38 心率曾达 157"）要连时间一起说，不能说成"刚才"。
3. 出现"过高""过低"这类危险读数时，先让老人坐下休息，并建议联系家人或去医院；不做诊断、不提药名。
4. 问天气、外出就引用对应材料里的数字；最后顺着话题关心一句。
5. 老人只是闲聊或问一般问题时，自然接话即可，不要把读数、评分再念一遍；材料里标了"闲聊"就照这条办。"""


INTENT_SYSTEM_PROMPT = """你要判断一位老人对陪护机器狗说的话属于哪种意图。
可选意图：
- outing_request: 想出门、想去散步买菜、询问能不能出去
- end_outing: 表示要回家了、结束这次外出
- health_query: 询问自己的身体数据，比如心率、血压、血氧、睡眠、步数
- sos: 求救、需要帮助、身体很不舒服
- fall_confirm: 在回应"您还好吗"这类询问，说自己没事或者需要帮忙
- general_qa: 其他一般性问题，比如天气、时间、常识
- chitchat: 闲聊、问候

只输出 JSON：{"intent": "意图名", "confidence": 0.9}"""


def _dump(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def build_outing_user_prompt(context: dict) -> str:
    weather = dict(context.get("weather") or {})
    # 预报、预警、指数单独成段，模型更容易把"这段时间"和"此刻"分开看
    hourly = weather.pop("hourly", None)
    alerts = weather.pop("alerts", None)
    minutely = weather.pop("minutely", None)
    indices = weather.pop("indices", None)
    today = weather.pop("today", None)
    sections = [
        "以下是本次外出申请的全部材料，请按你的思考顺序给出结论。",
        f"现在时间：{context.get('now_text', '')}",
        f"老人的话：{context.get('question', '')}",
        f"老人档案：{_dump(context.get('elder_profile', {}))}",
        f"健康快照：{_dump(context.get('health', {}))}",
        f"今日健康评分：{_dump(context.get('health_score', {}))}",
        f"外出统计：{_dump(context.get('stats', {}))}",
        f"此刻天气：{_dump(weather)}",
        f"今日概况与日出日落：{_dump(today or {})}",
        f"外出时段内逐小时预报：{_dump(hourly or [])}",
        f"分钟级降水：{_dump(minutely or {})}",
        f"官方气象预警：{_dump(alerts if alerts is not None else '暂不可用')}",
        f"生活指数：{_dump(indices or {})}",
        f"设备状态：{_dump(context.get('devices', {}))}",
        f"规则引擎结果：{_dump(context.get('rule_report', {}))}",
    ]
    return "\n\n".join(sections)


# 闲聊、一般问题：老人没在问身体，不该每句都把读数和评分念一遍
CASUAL_INTENTS = {"chitchat", "fall_confirm"}
HEALTH_WORDS = (
    "身体", "血压", "心率", "心跳", "血氧", "体温", "发烧", "睡", "步", "评分", "健康", "指标",
    "不舒服", "头晕", "疼", "痛", "累", "喘", "难受", "药", "医院", "测", "手表",
)


def is_casual_question(intent: str, question: str) -> bool:
    if intent in CASUAL_INTENTS:
        return True
    if intent == "general_qa":
        return not any(word in (question or "") for word in HEALTH_WORDS)
    return False


def build_qa_user_prompt(context: dict) -> str:
    lines = [f"老人的问题：{context.get('question', '')}"]
    if context.get("elder_name"):
        lines.append(f"老人姓名：{context['elder_name']}（称呼：{context.get('salutation') or context['elder_name']}）")
    digest = context.get("digest") or {}
    casual = is_casual_question(context.get("intent", ""), context.get("question", ""))
    if casual:
        abnormal = digest.get("abnormal_lines", []) if digest.get("has_data") else []
        lines.append(
            "这句话的性质：闲聊或一般问题，老人没有在问身体。像家人一样自然接话，"
            "不要汇报读数、评分和几小时前的峰值，也不要重复上一轮已经说过的话；"
            + ("只需顺带提醒下面这项此刻的异常：" if abnormal else "此刻各项体征都正常，一个数字都不用提。")
        )
        for item in abnormal:
            lines.append(f"  - 异常：{item}")
    elif digest.get("has_data"):
        lines.append(f"体征讲解（按这位老人的个人阈值判定，采集于 {digest.get('measured_at', '')}）：")
        for item in digest.get("abnormal_lines", []):
            lines.append(f"  - 异常：{item}")
        for item in digest.get("recent_lines", []):
            lines.append(f"  - 近一小时曾出现（数值和高低都已算好，直接引用）：{item}")
        if digest.get("normal_summary"):
            lines.append(f"  - 正常：{digest['normal_summary']}")
        for item in digest.get("extra_lines", []):
            lines.append(f"  - {item}")
        if digest.get("score_line"):
            lines.append(f"  - {digest['score_line']}")
    elif context.get("health_summary"):
        lines.append(f"手表健康数据：{context['health_summary']}")
    else:
        lines.append("手表健康数据：最近 10 分钟没有读数")
    if context.get("weather_text"):
        lines.append(f"天气：{context['weather_text']}")
    if context.get("outing_text"):
        lines.append(f"外出情况：{context['outing_text']}")
    if context.get("history"):
        # 上一轮说过的读数早就过时了，模型若从这里抄数字，会把几小时前的峰值一遍遍念成"刚才"
        lines.append(
            "最近对话（只用来接上话题；里面的数字一律不要再引用，读数只以上面的材料为准）："
            + _dump(context["history"])
        )
    return "\n".join(lines)
