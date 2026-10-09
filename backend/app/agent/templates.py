"""智能体提示词与机器狗话术的默认值与元信息。

后台可以在「智能体」页面覆盖这里的任意一条; 覆盖被删除或禁用后自动回落到默认值。
占位符使用 {name} 形式, 渲染时缺失的占位符会原样保留而不是抛异常。
"""

from app.agent.prompts import (
    CONSULT_QA_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    OUTING_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
)

CATEGORY_SYSTEM = "system_prompt"
CATEGORY_SPEECH = "speech"

CATEGORY_LABELS = {
    CATEGORY_SYSTEM: "大模型提示词",
    CATEGORY_SPEECH: "机器狗话术",
}

# key -> 元信息 + 默认内容
TEMPLATES: dict[str, dict] = {
    "outing_system_prompt": {
        "name": "外出评估提示词",
        "category": CATEGORY_SYSTEM,
        "description": "决定大模型如何结合规则报告给出外出结论与播报话术。规则层结论不可被推翻的约束写在这里。",
        "placeholders": [],
        "content": OUTING_SYSTEM_PROMPT,
    },
    "qa_system_prompt": {
        "name": "机器狗问答提示词",
        "category": CATEGORY_SYSTEM,
        "description": "老人对机器狗询问健康数据、天气、常识时的回答风格与安全边界：必须引用具体读数并说明高低。",
        "placeholders": [],
        "content": QA_SYSTEM_PROMPT,
    },
    "consult_qa_system_prompt": {
        "name": "健康咨询提示词",
        "category": CATEGORY_SYSTEM,
        "description": "老人在系统里打字咨询自己身体情况时的回答风格：直接对老人说、逐项引用读数并说明高低。",
        "placeholders": [],
        "content": CONSULT_QA_SYSTEM_PROMPT,
    },
    "intent_system_prompt": {
        "name": "意图识别提示词",
        "category": CATEGORY_SYSTEM,
        "description": "关键词未命中时，交给大模型判断老人这句话属于哪种意图。",
        "placeholders": [],
        "content": INTENT_SYSTEM_PROMPT,
    },
    "fall_confirm_question": {
        "name": "跌倒后的确认询问",
        "category": CATEGORY_SPEECH,
        "description": "检测到疑似跌倒后，机器狗立即播报这句话确认老人状态。",
        "placeholders": [],
        "content": (
            "我检测到您可能摔倒了，您还好吗？"
            "如果没事请说“我没事”，需要帮忙请说“帮我叫人”。"
        ),
    },
    "fall_reply_ok": {
        "name": "老人回应没事时的答复",
        "category": CATEGORY_SPEECH,
        "description": "老人确认自己无碍后机器狗的回应，告警会同时自动关闭。",
        "placeholders": [],
        "content": "太好了，那我放心了。您先慢慢起来，扶着东西站稳，我在旁边陪着您。",
    },
    "fall_reply_help": {
        "name": "老人需要帮助时的答复",
        "category": CATEGORY_SPEECH,
        "description": "老人表示起不来或需要救助时的回应，告警会立即升级通知家属。",
        "placeholders": [],
        "content": "别着急，我已经通知您的家人了，您先别乱动，我一直在您身边。",
    },
    "fall_no_response_broadcast": {
        "name": "跌倒无应答时的现场播报",
        "category": CATEGORY_SPEECH,
        "description": "语音确认超时无应答时，机器狗向周围人求助的播报。",
        "placeholders": [],
        "content": "老人可能发生跌倒且无应答，已通知家属，请附近的人帮忙查看。",
    },
    "sos_reply": {
        "name": "老人主动求助时的答复",
        "category": CATEGORY_SPEECH,
        "description": "老人说“救命”“帮我叫人”等求助内容时的回应。",
        "placeholders": [],
        "content": "我已经帮您通知家人了，您先坐下别动，有什么不舒服随时告诉我。",
    },
    "sos_broadcast": {
        "name": "求助后的现场播报",
        "category": CATEGORY_SPEECH,
        "description": "触发 SOS 后机器狗持续播报，安抚老人并提示周围人。",
        "placeholders": [],
        "content": "已经帮您联系家人了，请原地等待，我陪着您。",
    },
    "outing_end_reply": {
        "name": "结束外出时的答复",
        "category": CATEGORY_SPEECH,
        "description": "老人说要回家时的回应，{summary} 会替换为本次外出小结。",
        "placeholders": ["summary"],
        "content": "好的，那我们回家。{summary}回家以后先坐下歇一会儿。",
    },
    "outing_end_no_task": {
        "name": "没有外出任务时的答复",
        "category": CATEGORY_SPEECH,
        "description": "老人说回家但当前并没有进行中的外出任务时的回应。",
        "placeholders": [],
        "content": "您现在没有在外出中哦。需要出去走走的话跟我说一声就行。",
    },
    "outing_remind_return": {
        "name": "外出时间过半的提醒",
        "category": CATEGORY_SPEECH,
        "description": "外出达到计划时长的 80% 时提醒返程，{minutes} 为已外出分钟数。",
        "placeholders": ["minutes"],
        "content": "提醒一下，您已经外出 {minutes} 分钟了，建议慢慢往回走。",
    },
    "outing_timeout": {
        "name": "外出超时未归的提醒",
        "category": CATEGORY_SPEECH,
        "description": "超过约定外出时长后的播报，同时会通知家属。",
        "placeholders": [],
        "content": "已经超过约定的外出时间了，我陪您回家吧，家人也收到了提醒。",
    },
    "outing_geofence": {
        "name": "超出安全范围的提醒",
        "category": CATEGORY_SPEECH,
        "description": "老人走出电子围栏时的劝返话术。",
        "placeholders": [],
        "content": "您已经走得比较远了，我们往回走一点好吗？",
    },
    "qa_fallback": {
        "name": "无法回答时的兜底话术",
        "category": CATEGORY_SPEECH,
        "description": "问题超出能力范围时的回复，用于引导老人问可以回答的内容。",
        "placeholders": [],
        "content": (
            "这个问题我还回答不好。您可以问我天气、身体数据，"
            "或者告诉我想出去走走，我帮您看看今天适不适合外出。"
        ),
    },
}


def default_content(key: str) -> str:
    return TEMPLATES.get(key, {}).get("content", "")
