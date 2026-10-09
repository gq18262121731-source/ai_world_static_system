import json
import re

from app.agent.prompts import INTENT_SYSTEM_PROMPT
from app.ai.base import ChatMessage, ChatRequest
from app.ai.factory import get_llm
from app.ai.mock import TASK_INTENT
from app.core.logging import get_logger
from app.models.enums import IntentType

logger = get_logger(__name__)

# 关键词优先, 命中即返回, 无需调用大模型, 保证低时延与可预测
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (
        IntentType.SOS,
        ["救命", "帮我叫人", "叫救护车", "打120", "不行了", "很难受", "疼得厉害", "喘不上气", "呼救"],
    ),
    (
        IntentType.END_OUTING,
        ["回家了", "我要回去", "回去了", "结束外出", "不走了", "到家了", "散步结束", "遛完了"],
    ),
    (
        IntentType.OUTING_REQUEST,
        [
            "出去", "出门", "外出", "散步", "遛弯", "溜达", "走走", "逛逛",
            "买菜", "下楼", "去公园", "能不能出", "可以出", "想去",
        ],
    ),
    (
        IntentType.HEALTH_QUERY,
        [
            "心率", "心跳", "血压", "血氧", "体温", "睡眠", "睡得", "步数",
            "走了多少", "身体怎么样", "健康", "指标", "数据",
        ],
    ),
    (
        IntentType.CHITCHAT,
        ["你好", "早上好", "晚上好", "谢谢", "辛苦了", "无聊", "陪我", "聊聊"],
    ),
]

OK_PATTERNS = ["没事", "还好", "不用", "不要紧", "好着呢", "能起来", "我很好", "没摔", "不碍事"]
HELP_PATTERNS = ["帮我", "起不来", "疼", "叫人", "救", "不行", "难受", "扶我"]


def classify_by_keyword(text: str) -> str | None:
    cleaned = re.sub(r"\s+", "", text or "")
    if not cleaned:
        return None
    for intent, keywords in KEYWORD_RULES:
        if any(word in cleaned for word in keywords):
            return intent
    return None


def is_positive_reply(text: str) -> bool:
    """判断跌倒确认场景下老人是否表示自己没事。"""
    cleaned = re.sub(r"\s+", "", text or "")
    if any(word in cleaned for word in HELP_PATTERNS):
        return False
    return any(word in cleaned for word in OK_PATTERNS)


async def classify(text: str, in_fall_confirm: bool = False) -> tuple[str, float]:
    """返回 (意图, 置信度)。"""
    if in_fall_confirm:
        return IntentType.FALL_CONFIRM, 0.99

    keyword_intent = classify_by_keyword(text)
    if keyword_intent:
        return keyword_intent, 0.95

    llm = get_llm()
    try:
        raw = await llm.chat(
            ChatRequest(
                messages=[
                    ChatMessage("system", INTENT_SYSTEM_PROMPT),
                    ChatMessage("user", text),
                ],
                temperature=0.0,
                json_mode=True,
                task=TASK_INTENT,
                context={"question": text},
            )
        )
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            intent = data.get("intent", IntentType.GENERAL_QA)
            if intent in set(IntentType):
                return intent, float(data.get("confidence", 0.7))
    except Exception as exc:
        logger.warning("意图识别调用模型失败: %s", exc)
    return IntentType.GENERAL_QA, 0.5
