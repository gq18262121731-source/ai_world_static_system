"""前端联调冒烟测试: 校验 Vite 开发服务器与 API 代理是否正常。

用法: python scripts/smoke_web.py [--base-url http://127.0.0.1:5173]
"""

import argparse
import sys

import httpx

CHECKS = [
    "/api/v1/dashboard/overview",
    "/api/v1/dashboard/outing-trend",
    "/api/v1/dashboard/audit-logs",
    "/api/v1/elders",
    "/api/v1/devices",
    "/api/v1/devices/mqtt/status",
    "/api/v1/devices/gateways",
    "/api/v1/alarms",
    "/api/v1/rules/defaults",
    "/api/v1/rules/effective",
    "/api/v1/outings/requests",
    "/api/v1/outings/tasks",
    "/api/v1/agent/status",
    "/api/v1/agent/turns",
    "/api/v1/agent/metrics",
    "/api/v1/agent/intents",
    "/api/v1/agent/templates",
    "/api/v1/simulator/state?elder_id=1",
    "/api/v1/health/live",
    "/api/v1/health/assessment?elder_id=1",
    "/api/v1/health/summaries?elder_id=1&days=7",
    "/api/v1/outings/rule-report?elder_id=1",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    client = httpx.Client(timeout=30)
    failures = 0

    page = client.get(f"{base}/")
    ok = page.status_code == 200 and 'id="app"' in page.text
    print(f"{'页面入口 index.html':38s} -> {page.status_code} {'OK' if ok else 'FAIL'}")
    failures += 0 if ok else 1

    entry = client.get(f"{base}/src/main.js")
    print(f"{'前端入口 main.js':38s} -> {entry.status_code}")
    failures += 0 if entry.status_code == 200 else 1

    login = client.post(
        f"{base}/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    if login.status_code != 200:
        print(f"登录失败: {login.status_code} {login.text}")
        return 1
    token = login.json()["access_token"]
    print(f"{'API 代理登录':38s} -> 200 {login.json()['user']['real_name']}")

    headers = {"Authorization": f"Bearer {token}"}
    for path in CHECKS:
        resp = client.get(f"{base}{path}", headers=headers)
        status = "OK" if resp.status_code == 200 else "FAIL"
        print(f"{path:38s} -> {resp.status_code} {status}")
        failures += 0 if resp.status_code == 200 else 1

    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
