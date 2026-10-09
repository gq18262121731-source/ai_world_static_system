"""校验角色权限边界与可空返回的接口。

用法: python scripts/check_access.py
"""

import sys

import httpx

BASE = "http://127.0.0.1:8080/api/v1"


def client_for(username: str, password: str) -> tuple[httpx.Client, dict]:
    client = httpx.Client(timeout=30, base_url=BASE)
    resp = client.post("/auth/login", json={"username": username, "password": password})
    resp.raise_for_status()
    data = resp.json()
    client.headers["Authorization"] = f"Bearer {data['access_token']}"
    return client, data["user"]


def main() -> int:
    failures = 0

    admin, admin_user = client_for("admin", "admin123")
    print(f"[admin] 角色 {admin_user['role']}")
    elders = admin.get("/elders").json()
    print(f"  可见老人 {len(elders)} 位：{[e['name'] for e in elders]}")

    # 可空返回的接口
    for path, params in [
        ("/health/latest", {"elder_id": elders[0]["id"]}),
        ("/outings/tasks/active", {"elder_id": elders[0]["id"]}),
    ]:
        resp = admin.get(path, params=params)
        ok = resp.status_code == 200
        print(f"  {path:28s} -> {resp.status_code} {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

    family, family_user = client_for("family", "family123")
    print(f"[family] 角色 {family_user['role']}，绑定老人 {family_user['elder_ids']}")
    family_elders = family.get("/elders").json()
    print(f"  可见老人 {len(family_elders)} 位：{[e['name'] for e in family_elders]}")
    if len(family_elders) >= len(elders):
        print("  FAIL 家属账号应只看到绑定的老人")
        failures += 1

    # 访问未绑定老人应被拒绝
    forbidden = [e for e in elders if e["id"] not in family_user["elder_ids"]]
    if forbidden:
        resp = family.get(f"/elders/{forbidden[0]['id']}")
        ok = resp.status_code == 403
        print(f"  访问未绑定老人 -> {resp.status_code} {'OK(已拒绝)' if ok else 'FAIL'}")
        failures += 0 if ok else 1

    # 家属不应能创建老人档案
    resp = family.post("/elders", json={"name": "越权测试"})
    ok = resp.status_code == 403
    print(f"  尝试新建档案 -> {resp.status_code} {'OK(已拒绝)' if ok else 'FAIL'}")
    failures += 0 if ok else 1

    # 家属不应能调整规则阈值
    resp = family.put("/rules/configs", json={"rule_key": "spo2", "value": {"block_min": 60}})
    ok = resp.status_code == 403
    print(f"  尝试改规则阈值 -> {resp.status_code} {'OK(已拒绝)' if ok else 'FAIL'}")
    failures += 0 if ok else 1

    # 未登录访问
    anon = httpx.Client(timeout=30, base_url=BASE)
    resp = anon.get("/elders")
    ok = resp.status_code == 401
    print(f"[匿名] 访问老人列表 -> {resp.status_code} {'OK(已拒绝)' if ok else 'FAIL'}")
    failures += 0 if ok else 1

    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
