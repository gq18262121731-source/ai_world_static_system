"""端到端演示脚本: 一条命令跑完"外出咨询 -> 跟随 -> 跌倒 -> 告警"全流程。

用法:
    python scripts/demo_e2e.py                       # 默认连 http://127.0.0.1:8080
    python scripts/demo_e2e.py --base-url http://127.0.0.1:8080
"""

import argparse
import sys
import time

import httpx

DIVIDER = "=" * 78


class DemoClient:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.api = f"{self.base}/api/v1"
        self.client = httpx.Client(timeout=60.0)
        self.token = ""

    def login(self, username: str = "admin", password: str = "admin123") -> None:
        resp = self.client.post(
            f"{self.api}/auth/login", json={"username": username, "password": password}
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        print(f"[登录] {username} 登录成功")

    def get(self, path: str, **params):
        resp = self.client.get(f"{self.api}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, json: dict | None = None, **params):
        resp = self.client.post(f"{self.api}{path}", json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, json: dict | None = None, **params):
        resp = self.client.put(f"{self.api}{path}", json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str):
        resp = self.client.delete(f"{self.api}{path}")
        resp.raise_for_status()
        return resp.json()


def step(title: str) -> None:
    print(f"\n{DIVIDER}\n>> {title}\n{DIVIDER}")


def show_decision(result: dict) -> str:
    decision = result["decision"]
    label = {"allow": "允许外出", "conditional": "有条件允许", "deny": "不建议外出"}[
        decision["decision"]
    ]
    print(f"决策结果: {label}")
    print(f"机器狗播报: {result['speech_text']}")
    print("理由:")
    for reason in decision["reasons"]:
        print(f"  - {reason}")
    if decision["advice"]:
        print("建议:")
        for advice in decision["advice"]:
            print(f"  - {advice}")
    hits = result["rule_report"]["hits"]
    if hits:
        print("命中规则:")
        for hit in hits:
            flag = "拦截" if hit["level"] == "block" else "提醒"
            print(f"  [{flag}] {hit['title']}: {hit['detail']}")
    if decision["limits"].get("duration_minutes"):
        print(
            f"外出限制: {decision['limits']['duration_minutes']} 分钟 / "
            f"{decision['limits']['radius_meters']} 米"
        )
    return decision["decision"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--elder-id", type=int, default=0)
    args = parser.parse_args()

    demo = DemoClient(args.base_url)
    demo.login()

    elders = demo.get("/elders")
    if not elders:
        print("没有老人档案，请先执行 python -m app.seed")
        return 1
    elder = next((e for e in elders if e["id"] == args.elder_id), elders[0])
    elder_id = elder["id"]
    print(f"[对象] {elder['name']}，{elder['age']} 岁，慢病：{elder['chronic_diseases']}")

    # 演示脚本可能在任意时刻运行，这里临时放开"外出时段"这条硬性红线，
    # 顺带演示按老人维度覆盖阈值的能力，脚本结束时会自动还原。
    time_window_config = demo.put(
        "/rules/configs",
        {
            "rule_key": "time_window",
            "value": {
                "start": "00:00",
                "end": "23:59",
                "warn_before": "00:00",
                "warn_after": "23:59",
            },
            "description": "演示脚本临时放开外出时段",
        },
        elder_id=elder_id,
    )
    print(f"[准备] 已为{elder['name']}临时放开外出时段限制（配置 #{time_window_config['id']}）")

    try:
        run_scenarios(demo, elder, elder_id)
    finally:
        demo.delete(f"/rules/configs/{time_window_config['id']}")
        print(f"\n[清理] 已还原{elder['name']}的外出时段限制")
    return 0


def run_scenarios(demo: DemoClient, elder: dict, elder_id: int) -> None:
    cleared = demo.post("/simulator/reset", {"elder_id": elder_id})["cleared"]
    print(
        f"[准备] 已重置演示数据（清理外出任务 {cleared['outing_tasks']} 条、"
        f"告警 {cleared['alarms']} 条、对话 {cleared['conversations']} 条）"
    )
    demo.post("/simulator/device-state", {"elder_id": elder_id, "device_type": "watch", "battery": 85, "online": True})
    demo.post("/simulator/device-state", {"elder_id": elder_id, "device_type": "dog", "battery": 90, "online": True})

    step("场景一：血氧偏低，老人想出门 —— 应被拒绝并说明原因")
    demo.post("/simulator/health-profile", {"elder_id": elder_id, "profile": "hypoxia", "points": 6})
    result = demo.post("/outings/request", {"elder_id": elder_id, "text": "我想下楼买点菜", "channel": "manual"})
    denied = show_decision(result)

    step("场景二：健康数据恢复正常，老人再次询问 —— 应通过并开启跟随")
    # 覆盖掉最近一小时的异常读数，让当前状态回到正常
    demo.post("/simulator/health-profile", {"elder_id": elder_id, "profile": "normal", "points": 14})
    reply = demo.post("/simulator/speak", {"elder_id": elder_id, "text": "我想出去走走"})
    print("老人: 我想出去走走")
    print(f"机器狗: {reply['reply_text']}")
    print(f"意图: {reply['intent']}，调用工具: {reply['tools_used']}")
    allowed = reply["decision"]["decision"] if reply.get("decision") else "unknown"
    task_id = reply.get("task_id")
    if task_id:
        print(f"已创建外出任务 #{task_id}，机器狗进入跟随模式")
    else:
        print("本次仍未通过评估，后续跟随环节将跳过")

    if task_id:
        step("场景三：机器狗跟随，生成轨迹并触发电子围栏")
        walk = demo.post("/simulator/walk", {"elder_id": elder_id, "steps": 6, "meters_per_step": 80})
        print(
            f"已走出 {walk['distance_from_origin_m']} 米，"
            f"围栏半径 {walk['geofence_radius_m']} 米，轨迹点 {len(walk['points'])} 个"
        )

        step("场景四：机器狗检测到跌倒，手表二次确认（双源融合去重）")
        first = demo.post("/simulator/fall", {"elder_id": elder_id, "source": "dog", "confidence": 0.88})
        print(f"机器狗上报跌倒 -> 告警 #{first['alarm_id']}，状态 {first['status']}（语音确认中）")
        time.sleep(1)
        second = demo.post("/simulator/fall", {"elder_id": elder_id, "source": "watch", "confidence": 0.93})
        print(
            f"手表上报跌倒 -> {'融合进同一条告警' if not second['is_new_alarm'] else '新建了告警'} "
            f"#{second['alarm_id']}，来源 {second['sources']}"
        )

        step("场景五：老人语音回应“我没事”，告警自动关闭")
        confirm = demo.post("/simulator/speak", {"elder_id": elder_id, "text": "我没事，能自己起来"})
        print("老人: 我没事，能自己起来")
        print(f"机器狗: {confirm['reply_text']}")
        alarm = demo.get(f"/alarms/{first['alarm_id']}")
        print(f"告警状态: {alarm['status']}，处置说明: {alarm['handle_note']}")
        print(f"通知记录: {[(n['channel'], n['target_name']) for n in alarm['notifications']]}")

        step("场景六：老人说要回家，结束外出并生成小结")
        end = demo.post("/simulator/speak", {"elder_id": elder_id, "text": "我们回家了"})
        print(f"机器狗: {end['reply_text']}")

        step("场景七：刚到家又想出门 —— 外出频次与跌倒史规则接管")
        result = demo.post(
            "/outings/request", {"elder_id": elder_id, "text": "我再出去转一圈", "channel": "manual"}
        )
        show_decision(result)
        if result.get("task_id"):
            demo.post(f"/outings/tasks/{result['task_id']}/end", reason="manual")

    step("场景八：日常健康问答")
    for question in ["我最近血压怎么样", "今天天气好不好", "我昨晚睡得怎么样"]:
        answer = demo.post("/simulator/speak", {"elder_id": elder_id, "text": question})
        print(f"老人: {question}")
        print(f"机器狗: {answer['reply_text']}\n")

    step("总览")
    overview = demo.get("/dashboard/overview")
    print(
        f"老人 {overview['elder_total']} 位 | 设备在线 {overview['device_online']}/{overview['device_total']} | "
        f"今日外出 {overview['outing_today']} 次 | 待处理告警 {overview['alarm_pending']} 条"
    )
    print(f"决策分布: {overview['decision_distribution']}")
    print(f"MQTT: {'已连接' if overview['mqtt']['connected'] else '未连接'} | AI: {overview['ai']['llm']['provider']}")

    print(f"\n{DIVIDER}")
    label = {"allow": "允许外出", "conditional": "有条件允许", "deny": "不建议外出"}
    print(f"演示完成。血氧偏低时判定为「{label.get(denied, denied)}」，")
    if allowed == "deny":
        # 天气、设备等外部条件也可能拦截，此时如实说明而不是宣称对比成立
        print(
            "  健康数据恢复正常后仍被拦截，说明还有健康之外的条件没满足"
            "（常见原因是当前时段的天气或设备状态），具体见上面场景二的命中规则。"
        )
    else:
        print(f"  健康数据恢复正常后判定为「{label.get(allowed, allowed)}」，对比成立。")


if __name__ == "__main__":
    sys.exit(main())
