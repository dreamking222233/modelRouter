from __future__ import annotations

import base64
import json
import time

import httpx

from .conftest import OPEN_API_BASE

AGENT_ROW = {
    "id": 3800,
    "order_no": "WXP1",
    "user_id": 3418,
    "username": "alice",
    "agent_id": 8,
    "agent_name": "阿乐",
    "site_scope": "agent",
    "source_host": "ale.example.com",
    "payment_channel": "wechat",
    "payment_channel_text": "微信",
    "recharge_type": "balance",
    "recharge_type_text": "余额",
    "customer_type": "agent",
    "amount_cny": 1.0,
    "credited_usd": 5.0,
    "credited_image_credits": 0.0,
    "user_recharge_rate": 5.0,
    "agent_settlement_rate": 7.0,
    "agent_income_cny": 0.29,
    "status": "pending",
    "trade_status": None,
    "subject": "AI 平台余额充值",
    "alipay_trade_no": None,
    "wechat_transaction_id": None,
    "channel_trade_no": None,
    "paid_at": None,
    "created_at": "2026-10-27T01:32:09+08:00",
}
SENSITIVE = {"agent_name", "source_host", "agent_settlement_rate", "agent_income_cny", "agent_id"}


def make_jwt(sub: str | int | None = 3418, exp: int | None = None) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    payload: dict = {"jti": "x"}
    if sub is not None:
        payload["sub"] = str(sub)
    if exp is not None:
        payload["exp"] = exp
    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(payload)}.sig"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def page_response(rows: list[dict], total: int) -> dict:
    return {
        "code": 200,
        "message": "ok",
        "data": {"list": rows, "total": total, "page": 1, "page_size": 100},
    }


def test_lookup_filters_by_jwt_subject_and_projects_fields(client, platform):
    platform.respond(200, page_response([{**AGENT_ROW, "order_no": "OTHER"}, AGENT_ROW], 2))
    resp = client.get("/api/v1/user/recharge-orders/WXP1", headers=auth(make_jwt()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["order_no"] == "WXP1"
    assert body["data"]["credited_usd"] == 5.0
    assert not SENSITIVE & set(body["data"])

    sent = platform.last
    assert str(sent.url).startswith(f"{OPEN_API_BASE}/agent/payments")
    assert dict(sent.url.params) == {"user_id": "3418", "page": "1", "page_size": "100"}
    assert "Authorization" not in sent.headers


def test_lookup_pages_until_found(client, platform):
    def responder(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        rows = [{**AGENT_ROW, "order_no": f"P{page}-{i}"} for i in range(100)]
        if page == "2":
            rows[50] = {**AGENT_ROW, "order_no": "TARGET"}
        return httpx.Response(200, json=page_response(rows, 250))

    platform.responder = responder
    resp = client.get("/api/v1/user/recharge-orders/TARGET", headers=auth(make_jwt()))
    assert resp.status_code == 200
    assert [r.url.params["page"] for r in platform.requests] == ["1", "2"]


def test_lookup_stops_at_last_page_and_returns_404(client, platform):
    platform.respond(200, page_response([AGENT_ROW], 1))
    resp = client.get("/api/v1/user/recharge-orders/NOPE", headers=auth(make_jwt()))
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"
    assert len(platform.requests) == 1


def test_lookup_respects_max_pages(client, platform):
    rows = [{**AGENT_ROW, "order_no": "X"} for _ in range(100)]
    platform.respond(200, page_response(rows, 10_000))
    resp = client.get("/api/v1/user/recharge-orders/NOPE", headers=auth(make_jwt()))
    assert resp.status_code == 404
    assert len(platform.requests) == 5


def test_lookup_passes_through_platform_errors(client, platform):
    platform.respond(403, {"code": "SCOPE_DENIED", "message": "缺少 scope", "data": None})
    resp = client.get("/api/v1/user/recharge-orders/WXP1", headers=auth(make_jwt()))
    assert resp.status_code == 403
    assert resp.json()["code"] == "SCOPE_DENIED"


def test_lookup_rejects_unreadable_jwt(client, platform):
    resp = client.get("/api/v1/user/recharge-orders/WXP1", headers=auth("not.a.jwt.at.all"))
    assert resp.status_code == 401
    assert resp.json()["code"] == "USER_TOKEN_UNREADABLE"

    resp = client.get("/api/v1/user/recharge-orders/WXP1", headers=auth(make_jwt(sub=None)))
    assert resp.status_code == 401
    assert resp.json()["code"] == "USER_TOKEN_UNREADABLE"
    assert platform.requests == []


def test_lookup_rejects_expired_jwt(client, platform):
    token = make_jwt(exp=int(time.time()) - 60)
    resp = client.get("/api/v1/user/recharge-orders/WXP1", headers=auth(token))
    assert resp.status_code == 401
    assert resp.json()["code"] == "USER_TOKEN_EXPIRED"
    assert platform.requests == []
