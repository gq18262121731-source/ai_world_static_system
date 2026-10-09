"""校验智能体运营台的接口：对话检索、运行指标、话术模板覆盖与连通性测试。

用法: python scripts/check_agent.py
"""

import sys

import httpx

BASE = "http://127.0.0.1:8080/api/v1"
TEMPLATE_KEY = "outing_end_no_task"
CUSTOM = "您现在并没有在外面呀，想散步的话跟我讲一声，我先帮您看看身体状况。"


def main() -> int:
    client = httpx.Client(timeout=60, base_url=BASE)
    token = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    failures = 0

    elders = client.get("/elders").json()
    elder_id = elders[0]["id"]

    print("== 产生几轮对话 ==")
    for text in ["今天天气好不好", "我最近血压怎么样", "我们回家了"]:
        reply = client.post(
            "/simulator/speak", json={"elder_id": elder_id, "text": text}
        ).json()
        print(f"  {text} -> 意图 {reply['intent']}，耗时 {reply['latency_ms']}ms")

    print("\n== 对话轮次检索 ==")
    page = client.get("/agent/turns", params={"page_size": 5}).json()
    print(f"  共 {page['total']} 轮，本页 {len(page['items'])} 条")
    for turn in page["items"][:3]:
        stages = turn["stages"] or {}
        stage_text = " ".join(f"{k}={v}" for k, v in stages.items()) or "无分段数据"
        print(
            f"  [{turn['elder_name']}] {turn['intent']:15s} "
            f"问：{turn['question'][:14]:14s} 工具={turn['tools']} "
            f"总耗时={turn['latency_ms']}ms {stage_text}"
        )
    if not page["items"] or not page["items"][0]["stages"]:
        print("  FAIL 缺少阶段耗时数据")
        failures += 1

    filtered = client.get("/agent/turns", params={"intent": "health_query"}).json()
    print(f"  按意图 health_query 过滤 -> {filtered['total']} 条")
    keyword = client.get("/agent/turns", params={"keyword": "天气"}).json()
    print(f"  按关键词「天气」过滤 -> {keyword['total']} 条")

    print("\n== 运行指标 ==")
    metrics = client.get("/agent/metrics").json()
    print(
        f"  总轮次 {metrics['total_turns']}，平均耗时 {metrics['avg_latency_ms']}ms，"
        f"峰值 {metrics['max_latency_ms']}ms"
    )
    print(f"  意图分布 {metrics['intent_distribution']}")

    print("\n== 话术模板覆盖 ==")
    templates = client.get("/agent/templates").json()
    print(f"  共 {len(templates)} 条模板，已覆盖 {sum(t['overridden'] for t in templates)} 条")

    before = client.post(
        "/simulator/speak", json={"elder_id": elder_id, "text": "我们回家了"}
    ).json()["reply_text"]
    print(f"  改写前：{before}")

    client.put(
        f"/agent/templates/{TEMPLATE_KEY}", json={"content": CUSTOM, "enabled": True}
    )
    after = client.post(
        "/simulator/speak", json={"elder_id": elder_id, "text": "我们回家了"}
    ).json()["reply_text"]
    print(f"  改写后：{after}")
    if after != CUSTOM:
        print("  FAIL 话术改写未生效")
        failures += 1

    client.delete(f"/agent/templates/{TEMPLATE_KEY}")
    restored = client.post(
        "/simulator/speak", json={"elder_id": elder_id, "text": "我们回家了"}
    ).json()["reply_text"]
    print(f"  恢复默认：{restored}")
    if restored == CUSTOM:
        print("  FAIL 恢复默认未生效")
        failures += 1

    print("\n== AI 能力连通性 ==")
    for item in client.post("/agent/providers/test").json():
        flag = "OK" if item["ok"] else "FAIL"
        print(
            f"  {item['capability']:4s} {item['provider']:10s} {flag} "
            f"{item['latency_ms']}ms {item['detail']}"
        )
        if item["sample"]:
            print(f"       样例：{item['sample'][:60]}")

    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
