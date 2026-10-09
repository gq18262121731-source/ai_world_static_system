import hashlib
import json
import math
import struct
import wave
from datetime import datetime

from app.ai.base import ASRProvider, ChatRequest, LLMProvider, TTSProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TASK_OUTING = "outing_eval"
TASK_QA = "general_qa"
TASK_INTENT = "intent"

# 养老场景常见问题的本地知识库, 无大模型密钥时也能给出可用回答
QA_KNOWLEDGE: list[tuple[tuple[str, ...], str]] = [
    (
        ("天气", "下雨", "气温", "冷不冷", "热不热"),
        "我看了一下最新的天气，{weather_text}。出门前记得看看衣服穿够没有。",
    ),
    (
        ("心率", "心跳"),
        "您最近的心率情况是这样的：{heart_text}。心率平稳的时候适合慢走，别做剧烈运动。",
    ),
    (
        ("血压",),
        "您的血压情况：{bp_text}。测血压最好在安静休息五分钟以后测，早晚各一次比较准。",
    ),
    (
        ("血氧",),
        "您的血氧情况：{spo2_text}。血氧一般保持在95%以上就比较正常。",
    ),
    (
        ("睡眠", "睡得", "失眠"),
        "您昨晚的睡眠情况：{sleep_text}。睡前少喝浓茶，泡泡脚会好一些。",
    ),
    (
        ("步数", "走了多少", "运动量"),
        "您今天的活动情况：{steps_text}。老年人每天六千步左右比较合适，分几次走完更轻松。",
    ),
    (
        ("身体", "健康", "怎么样", "状况"),
        "我帮您看了一下手表数据：{health_text}。整体{health_verdict}。",
    ),
    (
        ("吃药", "药", "服药"),
        "吃药这件事一定要按医生嘱咐来。我可以提醒您时间，但具体吃什么、吃多少，还是要听医生的。",
    ),
    (
        ("摔", "跌倒", "摔跤"),
        "外出的时候我会一直跟着您，如果检测到摔倒会马上语音确认，并第一时间通知您的家人。",
    ),
    (
        ("你是谁", "叫什么", "干什么的"),
        "我是您的陪护机器狗，可以陪您散步、监测安全，也能帮您看手表上的健康数据。",
    ),
    (
        ("几点", "时间", "现在"),
        "现在是{now_text}。",
    ),
    (
        ("孤单", "无聊", "闷", "想聊天"),
        "我陪着您呢。要不我们出去走一小圈？我先帮您看看今天适不适合出门。",
    ),
    (
        ("谢谢", "辛苦"),
        "不用客气，照顾好您是我的任务。",
    ),
]

FALLBACK_ANSWER = (
    "这个问题我还回答不好。您可以问我天气、身体数据，或者告诉我想出去走走，"
    "我帮您看看今天适不适合外出。"
)


class MockLLM(LLMProvider):
    """无密钥可用的本地推理实现。

    外出评估任务完全基于规则报告生成结论与话术, 保证演示环境行为可复现;
    问答任务走关键词知识库并注入真实的健康/天气数据。
    """

    name = "mock"

    async def chat(self, request: ChatRequest) -> str:
        if request.task == TASK_OUTING:
            return json.dumps(
                self._outing_decision(request.context), ensure_ascii=False
            )
        if request.task == TASK_QA:
            return self._answer(request.context)
        if request.task == TASK_INTENT:
            return json.dumps({"intent": "general_qa"}, ensure_ascii=False)
        return request.context.get("fallback_answer") or FALLBACK_ANSWER

    async def chat_stream(self, request: ChatRequest):
        """离线也要有"逐字流出"的效果，几个字一段地吐。"""
        import asyncio

        text = await self.chat(request)
        for index in range(0, len(text), 4):
            yield text[index : index + 4]
            await asyncio.sleep(0.03)

    @staticmethod
    def _salutation(ctx: dict) -> str:
        """张三 + 男 -> 张爷爷；没有姓名信息就叫"您"。"""
        name = str(ctx.get("elder_name") or "").strip()
        if not name:
            return "您"
        gender = str((ctx.get("elder_profile") or {}).get("性别") or "")
        return name[0] + ("奶奶" if gender == "女" else "爷爷")

    def _outing_decision(self, ctx: dict) -> dict:
        report = ctx.get("rule_report", {})
        elder_name = self._salutation(ctx)
        blocks = [h for h in report.get("hits", []) if h.get("level") == "block"]
        warns = [h for h in report.get("hits", []) if h.get("level") == "warn"]
        duration = report.get("suggested_duration_minutes", 30)
        radius = report.get("suggested_radius_m", 500)
        purpose, expected = self._purpose(ctx.get("question", ""))
        preparation = list(report.get("advisories") or [])[:4]
        best_time = report.get("best_time_hint") or ""
        best_time_text = f"{best_time} 以后再看看" if best_time else ""
        care_note = "有什么不舒服随时叫我，我一直在。"

        if blocks:
            reasons = [h["detail"] for h in blocks]
            advice = self._deny_advice(blocks)
            later = f"等到{best_time}左右咱们再看看。" if best_time else advice[0]
            speech = f"{elder_name}，今天先不出去了。" + reasons[0] + "。" + later + care_note
            return {
                "decision": "deny",
                "purpose": purpose,
                "expected_minutes": expected,
                "reasons": reasons,
                "advice": advice,
                "limits": {
                    "duration_minutes": 0,
                    "radius_meters": 0,
                    "need_follow": False,
                    "return_before": "",
                },
                "preparation": [],
                "route_hint": "",
                "best_time_hint": best_time_text,
                "care_note": care_note,
                "speech_text": speech,
                "confidence": 0.95,
            }

        return_before = ctx.get("return_before", "")
        route_hint = "走小区里平整、有树荫的路，累了就找长椅坐一坐"
        if warns:
            reasons = [h["detail"] for h in warns]
            advice = [
                f"建议本次外出控制在 {duration} 分钟以内",
                f"活动范围不要超过离家 {radius} 米",
                "让机器狗全程跟随，有不舒服随时叫我",
            ] + self._warn_advice(warns)
            prep_text = f"记得带上{'、'.join(preparation[:2])}。" if preparation else ""
            speech = (
                f"{elder_name}可以出去走走，不过要注意：{reasons[0]}。"
                f"建议{duration}分钟内回来，别走太远。{prep_text}{care_note}"
            )
            return {
                "decision": "conditional",
                "purpose": purpose,
                "expected_minutes": expected,
                "reasons": reasons,
                "advice": advice,
                "limits": {
                    "duration_minutes": duration,
                    "radius_meters": radius,
                    "need_follow": True,
                    "return_before": return_before,
                },
                "preparation": preparation,
                "route_hint": route_hint,
                "best_time_hint": best_time_text,
                "care_note": care_note,
                "speech_text": speech,
                "confidence": 0.88,
            }

        health_text = ctx.get("health_summary", "各项指标平稳")
        weather_text = ctx.get("weather_text", "")
        advice = [
            f"建议外出 {duration} 分钟左右",
            f"在离家 {radius} 米范围内活动",
            "慢走为主，感觉累了就找地方歇一歇",
        ]
        if weather_text:
            advice.append(weather_text)
        prep_text = f"带上{'、'.join(preparation[:2])}就行。" if preparation else ""
        speech = (
            f"{elder_name}今天身体状况不错，可以出去走走。"
            f"建议{duration}分钟左右回来，我跟着您一起去。{prep_text}{care_note}"
        )
        return {
            "decision": "allow",
            "purpose": purpose,
            "expected_minutes": expected,
            "reasons": ["各项健康指标正常", "外出条件满足"],
            "advice": advice,
            "limits": {
                "duration_minutes": duration,
                "radius_meters": radius,
                "need_follow": True,
                "return_before": return_before,
            },
            "preparation": preparation,
            "route_hint": route_hint,
            "best_time_hint": "",
            "care_note": care_note,
            "speech_text": speech,
            "confidence": 0.92,
        }

    @staticmethod
    def _purpose(question: str) -> tuple[str, int | None]:
        """从老人的话里粗略听出要去干什么、大概多久。"""
        table = [
            (("买菜", "菜场", "菜市场", "超市"), "买菜", 40),
            (("医院", "看病", "拿药", "配药"), "看病取药", 90),
            (("快递", "取件"), "取快递", 15),
            (("公园", "散步", "走走", "溜溜", "转转", "透气"), "散步", 30),
            (("下棋", "老伙伴", "朋友", "邻居"), "找老伙伴", 60),
        ]
        for keywords, label, minutes in table:
            if any(word in question for word in keywords):
                return label, minutes
        return "", None

    def _deny_advice(self, blocks: list[dict]) -> list[str]:
        keys = {h.get("key") for h in blocks}
        advice: list[str] = []
        if {"heart_rate", "spo2", "blood_pressure", "temperature"} & keys:
            advice.append("先在家坐着休息，半小时后我再帮您测一次")
            advice.append("如果感觉胸闷、头晕，马上告诉我，我帮您联系家人")
        if "time_window" in keys:
            advice.append("等天亮一些、光线好的时候再出门更安全")
        if "weather" in keys:
            advice.append("等天气转好我第一时间提醒您")
        if "outing_frequency" in keys:
            advice.append("今天活动量已经够了，在家做做伸展就好")
        if {"device_online", "device_battery"} & keys:
            advice.append("先把设备充上电，等设备恢复正常我们再出门")
        if "fall_history" in keys:
            advice.append("最近摔过一次，先在家里活动观察一两天")
        if not advice:
            advice.append("先在家休息，我会继续帮您盯着身体数据")
        return advice

    def _warn_advice(self, warns: list[dict]) -> list[str]:
        keys = {h.get("key") for h in warns}
        advice: list[str] = []
        if "weather" in keys:
            advice.append("路上注意脚下，走平整的路")
        if "sleep" in keys:
            advice.append("昨晚没睡好，走一小段就回来休息")
        if "device_battery" in keys:
            advice.append("设备电量不多，尽量在家附近活动")
        return advice

    def _answer(self, ctx: dict) -> str:
        question = ctx.get("question", "")
        # 问身体的，直接按体征讲解逐条报数、说高低，和真实模型的要求一致
        digest = ctx.get("digest") or {}
        health_words = ("身体", "健康", "指标", "数据", "怎么样", "心率", "心跳", "血压", "血氧", "体温", "发烧")
        if digest.get("has_data") and any(word in question for word in health_words):
            from app.agent.grounding import spoken_health_report

            return spoken_health_report(ctx.get("elder_name", ""), ctx.get("elder_gender", ""), digest)
        values = {
            "weather_text": ctx.get("weather_text", "暂时没拿到天气数据"),
            "heart_text": ctx.get("heart_text", "暂时没有心率数据"),
            "bp_text": ctx.get("bp_text", "暂时没有血压数据"),
            "spo2_text": ctx.get("spo2_text", "暂时没有血氧数据"),
            "sleep_text": ctx.get("sleep_text", "暂时没有睡眠数据"),
            "steps_text": ctx.get("steps_text", "暂时没有步数数据"),
            "health_text": ctx.get("health_summary", "暂时没有健康数据"),
            "health_verdict": ctx.get("health_verdict", "还算平稳"),
            "now_text": datetime.now().strftime("%H点%M分"),
        }
        for keywords, template in QA_KNOWLEDGE:
            if any(word in question for word in keywords):
                return template.format(**values)
        # 兜底话术允许后台改写, 未传入时用内置默认值
        return ctx.get("fallback_answer") or FALLBACK_ANSWER


class MockASR(ASRProvider):
    """无 ASR 密钥时的占位实现。

    真实链路是 音频 -> 文本; 演示环境下机器狗模拟器直接上行文本,
    这里只在收到音频文件时返回一句默认指令, 保证接口形状一致。
    """

    name = "mock"
    default_text = "我想出去走走"

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        logger.info("MockASR 收到音频 %s (%d 字节)", filename, len(audio))
        return self.default_text


class MockTTS(TTSProvider):
    """生成一段占位音频文件, 前端可直接用浏览器语音合成朗读文本。"""

    name = "mock"

    async def synthesize(self, text: str) -> str:
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
        path = settings.media_dir / f"tts_{digest}.wav"
        if not path.exists():
            self._write_placeholder(path, text)
        return f"/media/{path.name}"

    @staticmethod
    def _write_placeholder(path, text: str) -> None:
        sample_rate = 16000
        duration = min(6.0, max(0.6, len(text) * 0.18))
        frames = int(sample_rate * duration)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            data = bytearray()
            for i in range(frames):
                # 轻微的提示音, 避免完全静音让人以为链路没通
                envelope = math.exp(-3.0 * i / frames)
                value = int(2600 * envelope * math.sin(2 * math.pi * 520 * i / sample_rate))
                data += struct.pack("<h", value)
            wav.writeframes(bytes(data))
