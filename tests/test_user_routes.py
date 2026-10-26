from __future__ import annotations

import httpx

from .conftest import OPEN_API_BASE, USER_JWT

ORDER_BODY = {"amount_cny": 100.5, "payment_channel": "alipay"}
IDEM = {"Idempotency-Key": "desktop-order-0001"}


def test_user_routes_require_bearer_jwt(client, platform):
    resp = client.get("/api/v1/user/api-keys/group-options")
    assert resp.status_code == 401
    assert "Authorization" in resp.json()["message"]
    assert platform.requests == []


def test_malformed_authorization_is_rejected(client, platform):
    resp = client.get(
        "/api/v1/user/api-keys/group-options", headers={"Authorization": "Token abc"}
    )
    assert resp.status_code == 401
    assert platform.requests == []


def test_group_options_forwards_jwt(client, platform, user_headers):
    platform.respond(200, {"code": 200, "message": "ok", "data": {"series": []}})
    resp = client.get("/api/v1/user/api-keys/group-options", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"series": []}
    assert platform.last.method == "GET"
    assert str(platform.last.url) == f"{OPEN_API_BASE}/user/api-keys/group-options"
    assert platform.last.headers["Authorization"] == f"Bearer {USER_JWT}"


def test_create_api_key_special_requires_series_and_group(client, platform, user_headers):
    resp = client.post(
        "/api/v1/user/api-keys",
        json={"name": "工作台", "group_mode": "special", "group_id": 12},
        headers=user_headers,
    )
    assert resp.status_code == 422
    assert platform.requests == []


def test_create_api_key_unified_rejects_group_fields(client, platform, user_headers):
    resp = client.post(
        "/api/v1/user/api-keys",
        json={"name": "工作台", "group_mode": "unified", "group_id": 12},
        headers=user_headers,
    )
    assert resp.status_code == 422
    assert platform.requests == []


def test_create_api_key_forwards_special_payload(client, platform, user_headers):
    client.post(
        "/api/v1/user/api-keys",
        json={
            "name": "AI 工作台",
            "group_mode": "special",
            "group_model_series": "gpt",
            "group_id": 12,
            "daily_limit_usd": "20.5",
        },
        headers=user_headers,
    )
    assert str(platform.last.url) == f"{OPEN_API_BASE}/user/api-keys"
    assert platform.last_json() == {
        "name": "AI 工作台",
        "group_mode": "special",
        "group_model_series": "gpt",
        "group_id": 12,
        "daily_limit_usd": 20.5,
    }


def test_create_api_key_unified_omits_group_fields(client, platform, user_headers):
    client.post(
        "/api/v1/user/api-keys",
        json={"name": "默认", "group_mode": "unified"},
        headers=user_headers,
    )
    assert platform.last_json() == {"name": "默认", "group_mode": "unified"}


def test_create_order_requires_idempotency_key(client, platform, user_headers):
    resp = client.post("/api/v1/user/recharge-orders", json=ORDER_BODY, headers=user_headers)
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["message"]
    assert platform.requests == []


def test_create_order_validates_idempotency_key_format(client, platform, user_headers):
    resp = client.post(
        "/api/v1/user/recharge-orders",
        json=ORDER_BODY,
        headers={**user_headers, "Idempotency-Key": "short"},
    )
    assert resp.status_code == 400
    assert platform.requests == []


def test_create_order_validates_amount(client, platform, user_headers):
    for amount in (0.5, 10000.01, 1.234):
        resp = client.post(
            "/api/v1/user/recharge-orders",
            json={**ORDER_BODY, "amount_cny": amount},
            headers={**user_headers, **IDEM},
        )
        assert resp.status_code == 422, amount
    assert platform.requests == []


def test_create_order_forwards_headers_and_body(client, platform, user_headers):
    platform.respond(
        200,
        {"code": 200, "message": "ok", "data": {"order_no": "R1", "pay_url": "https://pay"}},
    )
    resp = client.post(
        "/api/v1/user/recharge-orders",
        json={**ORDER_BODY, "recharge_rate_code": "a"},
        headers={**user_headers, **IDEM},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["order_no"] == "R1"
    sent = platform.last
    assert str(sent.url) == f"{OPEN_API_BASE}/users/recharge-orders"
    assert sent.headers["Idempotency-Key"] == IDEM["Idempotency-Key"]
    assert sent.headers["Authorization"] == f"Bearer {USER_JWT}"
    assert platform.last_json() == {
        "amount_cny": 100.5,
        "payment_channel": "alipay",
        "recharge_rate_code": "a",
    }


def test_create_order_passes_through_409_with_retry_after(client, platform, user_headers):
    platform.respond(409, {"code": 409, "message": "处理中", "data": None}, **{"Retry-After": "3"})
    resp = client.post(
        "/api/v1/user/recharge-orders", json=ORDER_BODY, headers={**user_headers, **IDEM}
    )
    assert resp.status_code == 409
    assert resp.headers["Retry-After"] == "3"
    assert resp.json()["message"] == "处理中"


def test_wait_uses_long_timeout_and_passes_through_result(client, platform, user_headers):
    platform.respond(
        200,
        {
            "code": 200,
            "message": "ok",
            "data": {
                "wait_result": "wait_timeout",
                "close_result": "uncertain",
                "order_status": "pending",
                "order_no": "R1",
            },
        },
    )
    resp = client.post("/api/v1/user/recharge-orders/R1/wait", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["wait_result"] == "wait_timeout"
    sent = platform.last
    assert sent.method == "POST"
    assert str(sent.url) == f"{OPEN_API_BASE}/users/recharge-orders/R1/wait"
    assert sent.extensions["timeout"]["read"] == 150.0


def test_regular_calls_use_default_timeout(client, platform, user_headers):
    client.get("/api/v1/user/api-keys/group-options", headers=user_headers)
    assert platform.last.extensions["timeout"]["read"] == 15.0


def test_wait_rejects_invalid_order_no(client, platform, user_headers):
    resp = client.post("/api/v1/user/recharge-orders/bad%20no/wait", headers=user_headers)
    assert resp.status_code == 422
    assert platform.requests == []


def test_platform_timeout_becomes_504(client, platform, user_headers):
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    platform.responder = responder
    resp = client.post("/api/v1/user/recharge-orders/R1/wait", headers=user_headers)
    assert resp.status_code == 504
    assert resp.json()["code"] == 504


def test_platform_connection_error_becomes_502(client, platform, user_headers):
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    platform.responder = responder
    resp = client.get("/api/v1/user/api-keys/group-options", headers=user_headers)
    assert resp.status_code == 502
    assert resp.json()["data"]["error"] == "ConnectError"


def test_non_json_platform_response_becomes_502(client, platform, user_headers):
    platform.responder = lambda _: httpx.Response(502, text="<html>bad gateway</html>")
    resp = client.get("/api/v1/user/api-keys/group-options", headers=user_headers)
    assert resp.status_code == 502
    assert resp.json()["data"]["status"] == 502
