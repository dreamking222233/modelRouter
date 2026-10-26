"""对真实平台的端到端冒烟脚本（通过本地 modelRouter 调用）。

会在平台上真实创建：一个测试用户、一个模型 API Key、一笔 1 元未支付订单。
只在联调环境使用。

用法：
    python scripts/e2e_smoke.py --base http://127.0.0.1:8000 \
        --token <SERVICE_API_TOKENS 之一> [--wait]

--wait 会额外调用等待支付接口：阻塞约 90–120 秒，平台会在此期间主动关闭未支付订单。
输出会对 JWT 和模型 Key 做掩码。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import uuid
from typing import Any

import httpx


def mask(value: Any, keep: int = 6) -> Any:
    if not isinstance(value, str):
        return value
    return f"{value[:keep]}…({len(value)} chars)"


def show(title: str, resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    retry_after = resp.headers.get("retry-after")
    suffix = f" Retry-After={retry_after}" if retry_after else ""
    print(f"\n=== {title} -> HTTP {resp.status_code}{suffix}")
    printable = dict(body) if isinstance(body, dict) else {"body": body}
    data = printable.get("data")
    if isinstance(data, dict):
        printable["data"] = {
            k: (mask(v) if k in {"token", "key", "api_key"} else v) for k, v in data.items()
        }
    print(json.dumps(printable, ensure_ascii=False, indent=2)[:2000])
    return body if isinstance(body, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default=os.environ.get("MR_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("MR_SERVICE_TOKEN", ""))
    parser.add_argument("--wait", action="store_true", help="调用等待支付接口（阻塞约 90–120 秒）")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d%H%M%S")
    username = f"mr_test_{stamp}"
    password = secrets.token_urlsafe(12)
    print(f"测试账号: {username}")

    client = httpx.Client(
        base_url=args.base, headers={"X-Service-Token": args.token}, timeout=30
    )

    show("healthz", client.get("/healthz"))

    body = show(
        "注册",
        client.post(
            "/api/v1/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": password},
        ),
    )
    body = show(
        "登录",
        client.post("/api/v1/auth/login", json={"username": username, "password": password}),
    )
    jwt = (body.get("data") or {}).get("token")
    if not jwt:
        print("\n没有拿到用户 JWT，终止。")
        return
    user = {"Authorization": f"Bearer {jwt}"}

    body = show("分组选项", client.get("/api/v1/user/api-keys/group-options", headers=user))
    options = body.get("data") or {}
    if isinstance(options, dict):
        series = [s.get("value") for s in options.get("series", []) if isinstance(s, dict)]
        print("series:", series, "| groups:", len(options.get("groups", [])))

    show(
        "创建模型 Key（unified，日限额 1 USD）",
        client.post(
            "/api/v1/user/api-keys",
            json={"name": f"e2e-{stamp}", "group_mode": "unified", "daily_limit_usd": 1},
            headers=user,
        ),
    )

    idem = f"mr-e2e-{stamp}-{secrets.token_hex(3)}"
    order_req = {"amount_cny": 1, "payment_channel": "wechat"}
    body = show(
        "创建充值订单 1 CNY / wechat",
        client.post(
            "/api/v1/user/recharge-orders",
            json=order_req,
            headers={**user, "Idempotency-Key": idem},
        ),
    )
    order_no = (body.get("data") or {}).get("order_no")
    show(
        "同一幂等键重复创建（预期同一订单）",
        client.post(
            "/api/v1/user/recharge-orders",
            json=order_req,
            headers={**user, "Idempotency-Key": idem},
        ),
    )
    show(
        "同一幂等键改金额（预期 409 IDEMPOTENCY_CONFLICT）",
        client.post(
            "/api/v1/user/recharge-orders",
            json={**order_req, "amount_cny": 2},
            headers={**user, "Idempotency-Key": idem},
        ),
    )

    if order_no and args.wait:
        started = time.perf_counter()
        resp = client.post(
            f"/api/v1/user/recharge-orders/{order_no}/wait", headers=user, timeout=170
        )
        show(f"等待支付结果（耗时 {time.perf_counter() - started:.0f}s）", resp)

    show(
        "充值记录 pending 前 5 条",
        client.get("/api/v1/agent/payments", params={"page_size": 5, "status": "pending"}),
    )
    show("请求详情 随机 UUID（预期 404）", client.get(f"/api/v1/agent/requests/{uuid.uuid4()}"))
    show(
        "日期范围颠倒（预期本地 400）",
        client.get(
            "/api/v1/agent/payments",
            params={"start_date": "2026-10-31", "end_date": "2026-10-01"},
        ),
    )

    print(f"\n本次创建：用户 {username}，订单 {order_no}，模型 Key e2e-{stamp}")


if __name__ == "__main__":
    main()
