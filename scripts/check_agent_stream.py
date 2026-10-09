"""校验智能体的两条流式通道与"说话必须扣数据"。

- 控制台问答 /agent/chat/stream：只回答，不播报、不开跟随；健康问题的回答必须带数字与高低判断
- 机器狗对话 /agent/talk/stream：阶段事件 -> 逐段回复 -> 最终结果（含 audio_url）
- 体征讲解的离线断言：按个人阈值把读数翻译成"值 + 高低 + 程度"

用法: python scripts/check_agent_stream.py [--elder-id 1] [--skip-api]
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agent.grounding import (  # noqa: E402
    explain_vital,
    salutation,
    spoken_health_report,
    vitals_digest,
)
from app.rules.definitions import DEFAULT_RULES  # noqa: E402
from app.schemas.health import HealthAssessment, VitalStat  # noqa: E402

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

    def check_true(self, label: str, condition, note: str = "") -> None:
        self.check(f"{label}{f'（{note}）' if note else ''}", bool(condition), True)

    def report(self) -> int:
        print(f"\n断言 {self.passed}/{self.passed + len(self.failed)} 通过")
        for item in self.failed:
            print(f"  失败 · {item}")
        return 0 if not self.failed else 1


def logic_checks(check: Checker) -> None:
    print("体征讲解")
    rules = DEFAULT_RULES
    item = explain_vital("temperature", 39.0, rules)
    check.check("39℃ 判为过高", (item["status"], item["direction"]), ("danger", "high"))
    check.check_true("讲出比正常上限高了多少", "高 1.7℃" in item["sentence"], item["sentence"])
    check.check_true("发热用老人听得懂的说法", "发热" in item["sentence"])
    item = explain_vital("heart_rate", 105, rules)
    check.check("心率 105 判为偏高", (item["status"], item["direction"]), ("warning", "high"))
    check.check_true("说明正常上限", "不超过 100" in item["sentence"], item["sentence"])
    item = explain_vital("spo2", 88, rules)
    check.check("血氧 88 判为过低", (item["status"], item["direction"]), ("danger", "low"))
    check.check_true("说明比安全下限低了多少", "低 2%" in item["sentence"], item["sentence"])
    item = explain_vital("systolic", 122, rules)
    check.check("收缩压 122 正常", item["sentence"], "收缩压 122mmHg 正常")

    health = HealthAssessment(elder_id=1, has_data=True)
    for key, label, unit, value, status in (
        ("temperature", "体温", "℃", 39.0, "danger"),
        ("heart_rate", "心率", "次/分", 72.0, "normal"),
        ("spo2", "血氧", "%", 97.0, "normal"),
        ("systolic", "收缩压", "mmHg", 122.0, "normal"),
        ("diastolic", "舒张压", "mmHg", 76.0, "normal"),
    ):
        health.vitals[key] = VitalStat(latest=value, unit=unit, status=status, label=label)
    health.sleep_hours_last_night = 7.5
    health.steps_today = 2100
    digest = vitals_digest(health, rules)
    check.check("异常项只有体温", len(digest["abnormal_lines"]), 1)
    check.check_true("正常项合成一句，血压合并", "血压 122/76 mmHg" in digest["normal_summary"] and "收缩压" not in digest["normal_summary"], digest["normal_summary"])
    check.check("最差状态为危险", digest["worst_status"], "danger")

    check.check("张三 男 -> 张爷爷", salutation("张三", "男"), "张爷爷")
    check.check("李四 女 -> 李奶奶", salutation("李四", "女"), "李奶奶")
    text = spoken_health_report("张三", "男", digest)
    print(f"  兜底话术：{text}")
    check.check_true("兜底话术以称呼开头", text.startswith("李爷爷"))
    check.check_true("兜底话术报出体温与高低", "39.0℃" in text and "过高" in text)
    check.check_true("兜底话术带过正常项", "都正常" in text)
    check.check_true("危险读数建议联系家人或就医", "家人" in text or "医院" in text)
    empty = vitals_digest(HealthAssessment(elder_id=1, has_data=False), rules)
    check.check_true("没有数据时说没测到", "没测到" in spoken_health_report("张三", "男", empty))

    # 现在正常、一小时内曾到 157：峰值也要按阈值讲清高了多少，模型就不会自己瞎算
    recovered = HealthAssessment(elder_id=1, has_data=True)
    recovered.vitals["heart_rate"] = VitalStat(latest=82.0, unit="次/分", status="warning", label="心率", note="曾出现", recent_extreme=157.0)
    recovered.vitals["spo2"] = VitalStat(latest=97.0, unit="%", status="normal", label="血氧")
    digest = vitals_digest(recovered, rules)
    check.check("当前值正常不进异常项", digest["abnormal_lines"], [])
    check.check_true("峰值单独成句并算好差值", any("曾到 157" in line and "高 37" in line for line in digest["recent_lines"]), str(digest["recent_lines"]))
    text = spoken_health_report("张三", "男", digest)
    check.check_true("兜底话术也会提到峰值", "157" in text and "已回到 82" in text)

    print("闲聊判定与语气词过滤")
    from app.agent.prompts import build_qa_user_prompt, is_casual_question
    from app.api.v1.dog_audio import is_filler

    check.check_true("闲聊算闲聊", is_casual_question("chitchat", "今天真热"))
    check.check_true("一般问题不带身体词算闲聊", is_casual_question("general_qa", "现在几点了"))
    check.check_true("一般问题带身体词不算闲聊", not is_casual_question("general_qa", "我今天走了多少步"))
    check.check_true("健康问询不算闲聊", not is_casual_question("health_query", "你好"))
    casual_prompt = build_qa_user_prompt({"question": "今天真热", "intent": "chitchat", "digest": digest})
    check.check_true("闲聊材料里不给评分和峰值", "157" not in casual_prompt and "闲聊" in casual_prompt)
    health_prompt = build_qa_user_prompt({"question": "我心率怎么样", "intent": "health_query", "digest": digest})
    check.check_true("健康问询材料里有峰值", "157" in health_prompt)
    for text in ("嗯嗯。", "啊", "我们。", "好的", ""):
        check.check_true(f"“{text}”被忽略", is_filler(text))
    for text in ("疼", "救命", "你还好吗", "我想出去走走"):
        check.check_true(f"“{text}”要回答", not is_filler(text))

    print("合成分句")
    from app.ai.xfyun import split_sentences

    check.check("短句不切", split_sentences("李爷爷，您的体温正常。"), ["李爷爷，您的体温正常。"])
    long = "李爷爷，您昨晚睡了8小时，时间够的。今天健康评分54分，主要是因为刚才心率曾到157次每分，过高了。血氧降到87%，过低了。您先坐下歇会儿，别太累着，喝口水缓缓。"
    parts = split_sentences(long)
    check.check_true("长文本按句切成多段", 2 <= len(parts) <= 5, str(len(parts)))
    check.check("切完拼回原文", "".join(parts), long)


def stream(client: httpx.Client, path: str, body: dict) -> list[dict]:
    events = []
    with client.stream("POST", path, json=body, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def api_checks(check: Checker, elder_id: int, base_url: str) -> None:
    client = httpx.Client(timeout=120, base_url=base_url)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    print(f"\n== 健康咨询（老人 #{elder_id} 自己在屏幕上问）==")
    assessment = client.get("/health/assessment", params={"elder_id": elder_id}).json()
    has_vitals = any((v or {}).get("latest") is not None for v in assessment.get("vitals", {}).values())
    print(f"  当前是否有实时体征读数：{has_vitals}")
    events = stream(client, "/agent/chat/stream", {"elder_id": elder_id, "question": "我现在身体怎么样，有没有指标不正常"})
    kinds = [e["event"] for e in events]
    tokens = "".join(e["text"] for e in events if e["event"] == "token")
    final = next((e["reply"] for e in events if e["event"] == "final"), None)
    print(f"  事件序列：{kinds[:6]}…（共 {len(events)} 条，token {kinds.count('token')} 段）")
    print(f"  回答：{final['reply_text'] if final else '（无）'}")
    check.check_true("有意图事件", "intent" in kinds)
    check.check_true("有逐段 token", kinds.count("token") >= 2)
    check.check_true("有最终结果", final is not None)
    if final:
        check.check("健康咨询通道标记", final["channel"], "console")
        check.check("健康咨询不合成语音", final["audio_url"], "")
        check.check_true("健康咨询不下发播报动作", not any(a["type"] == "speak" for a in final["actions"]))
        check.check_true("回答引用了数字", any(ch.isdigit() for ch in final["reply_text"]), final["reply_text"][:60])
        if has_vitals:
            check.check_true("有读数时回答提到高低或正常", any(w in final["reply_text"] for w in ("偏高", "偏低", "过高", "过低", "正常")))
        else:
            check.check_true("没有读数时如实说明", any(w in final["reply_text"] for w in ("暂无", "没有", "没测到", "无读数", "暂时")), final["reply_text"][:60])
        check.check_true("健康咨询直接对老人说话", any(w in final["reply_text"] for w in ("爷爷", "奶奶", "您")), final["reply_text"][:40])
        check.check_true("流式内容与最终回答一致或被兜底替换", tokens.strip() == final["reply_text"] or not any(ch.isdigit() for ch in tokens))

    events = stream(client, "/agent/chat/stream", {"elder_id": elder_id, "question": "我今天能出门吗"})
    final = next((e["reply"] for e in events if e["event"] == "final"), None)
    print(f"  外出问题：{(final or {}).get('reply_text', '')[:120]}")
    if final:
        check.check("健康咨询里的外出问题只预演", (final.get("decision") or {}).get("preview"), True)
        check.check("健康咨询不开启跟随", final["task_id"], None)
        check.check_true("外出回答是对老人说的话", any(w in final["reply_text"] for w in ("爷爷", "奶奶", "您")))

    print(f"\n== 机器狗对话流式（老人 #{elder_id}）==")
    assessment = client.get("/health/assessment", params={"elder_id": elder_id}).json()
    has_bp = (assessment.get("vitals", {}).get("systolic") or {}).get("latest") is not None
    events = stream(client, "/agent/talk/stream", {"elder_id": elder_id, "text": "我最近血压怎么样"})
    kinds = [e["event"] for e in events]
    stages = [e["stage"] for e in events if e["event"] == "stage"]
    final = next((e["reply"] for e in events if e["event"] == "final"), None)
    print(f"  阶段：{stages}")
    print(f"  播报：{final['reply_text'] if final else '（无）'}")
    check.check_true("机器狗通道有推理与合成阶段", "reasoning" in stages and "tts" in stages)
    check.check_true("机器狗通道逐段回复", kinds.count("token") >= 2)
    if final:
        check.check("机器狗通道标记", final["channel"], "dog")
        if has_bp:
            check.check_true("有血压读数时回答报出数值", any(ch.isdigit() for ch in final["reply_text"]), final["reply_text"][:60])
        else:
            check.check_true("没有血压读数时如实说没测到", any(w in final["reply_text"] for w in ("没测到", "暂无", "没有测", "暂时没")), final["reply_text"][:60])
        check.check_true("带称呼", any(w in final["reply_text"] for w in ("爷爷", "奶奶")))


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
