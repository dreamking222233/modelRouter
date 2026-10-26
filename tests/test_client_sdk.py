"""客户端 SDK 测试。

第一部分：异步 SDK 通过 ASGITransport 直接打到真实的 modelRouter 应用（平台用 mock），
验证全链路；第二部分：同步 SDK 对着模拟的 modelRouter 响应，验证解析、异常映射与重试。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest

from modelrouter.main import create_app
from modelrouter_client import (
    AsyncModelRouterClient,
    AuthenticationError,
    ConflictError,
    ModelRouterClient,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    TransportError,
    ValidationError,
)
from modelrouter_client import client as client_module

from .conftest import SERVICE_TOKEN, make_settings
from .test_order_lookup import AGENT_ROW, make_jwt

USER_JWT = make_jwt(sub=1)


def ok(data: object) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "message": "success", "data": data})


def fake_platform(request: httpx.Request) -> httpx.Response:
    """按路径模拟平台开放接口。"""
    path = request.url.path
    body = json.loads(request.content) if request.content else {}
    if path.endswith("/auth/register"):
        return ok({"token": USER_JWT, "user": {"id": 1, "username": body["username"]}})
    if path.endswith("/auth/login"):
        return ok(
            {
                "token": USER_JWT,
                "user": {"id": 1, "username": body["username"], "agent_id": 8},
                "is_first_login": False,
                "balance": 12.5,
            }
        )
    if path.endswith("/user/api-keys/group-options"):
        group = {
            "id": 2,
            "name": "GPT默认",
            "code": "default",
            "model_series": "gpt",
            "multiplier": 1.0,
            "enabled": 1,
            "is_default": 1,
            "key_count": 3,
        }
        series = {"value": "gpt", "label": "GPT", "default_group_id": 2, "groups": [group]}
        return ok({"series": [series], "model_series": [series], "groups": [group]})
    if path.endswith("/user/api-keys"):
        return ok({"id": 5, "name": body["name"], "key": "sk-secret", "key_prefix": "sk-sec"})
    if path.endswith("/users/recharge-orders"):
        return ok(
            {
                "order_no": "WXP1",
                "status": "pending",
                "amount_cny": body["amount_cny"],
                "credited_usd": 5.0,
                "user_recharge_rate_code": "e",
                "payment_channel": "wechat",
                "wechat_code_url": "weixin://wxpay/bizpayurl?pr=x",
            }
        )
    if path.endswith("/wait"):
        return ok(
            {
                "wait_result": "closed",
                "close_result": "confirmed",
                "order_status": "closed",
                "order_no": "WXP1",
            }
        )
    if path.endswith("/agent/payments"):
        return ok({"list": [{**AGENT_ROW, "user_id": 1}], "total": 1, "page": 1, "page_size": 100})
    if "/agent/requests/" in path:
        return httpx.Response(
            404, json={"code": "REQUEST_LOG_NOT_FOUND", "message": "请求记录不存在", "data": None}
        )
    raise AssertionError(f"未模拟的平台路径: {path}")


def test_async_sdk_end_to_end_against_real_app():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return fake_platform(request)

    app = create_app(make_settings(), platform_transport=httpx.MockTransport(handler))

    async def flow() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with AsyncModelRouterClient(
                "http://testserver", SERVICE_TOKEN, transport=transport
            ) as client:
                assert await client.ready()

                reg = await client.register("alice", "alice@example.com", "pw")
                assert reg.token == USER_JWT and reg.user.username == "alice"

                auth = await client.login("alice", "pw")
                assert auth.balance == 12.5 and auth.is_first_login is False
                user = client.as_user(auth.token)
                assert user.token == USER_JWT

                options = await user.group_options()
                gpt = options.find_series("gpt")
                assert gpt is not None and gpt.default_group_id == 2
                assert gpt.groups[0].name == "GPT默认"
                assert "key_count" not in gpt.groups[0].raw  # modelRouter 已裁剪

                key = await user.create_api_key("工作台", daily_limit_usd="1.5")
                assert key.key == "sk-secret"

                order = await user.create_recharge_order("100.50", "wechat")
                assert order.order_no == "WXP1"
                assert order.payment_target == "weixin://wxpay/bizpayurl?pr=x"
                assert order.idempotency_key.startswith("mrc-")

                record = await user.get_recharge_order("WXP1")
                assert record.status == "pending" and record.credited_usd == 5.0
                assert "agent_income_cny" not in record.raw

                result = await user.wait_for_payment("WXP1")
                assert result.closed and not result.paid and not result.can_wait_again

                page = await client.payments(page_size=5)
                assert page.total == 1 and page.items[0].order_no == "WXP1"

                with pytest.raises(NotFoundError) as exc_info:
                    await client.request_detail("4f1c2a4e-6c1b-4c89-9d1e-9d9f3f2d1a10")
                assert exc_info.value.code == "REQUEST_LOG_NOT_FOUND"

    asyncio.run(flow())

    create_order = next(r for r in seen if r.url.path.endswith("/users/recharge-orders"))
    assert create_order.headers["Idempotency-Key"].startswith("mrc-")
    assert create_order.headers["Authorization"] == f"Bearer {USER_JWT}"
    assert json.loads(create_order.content)["amount_cny"] == 100.5
    wait = next(r for r in seen if r.url.path.endswith("/wait"))
    assert wait.extensions["timeout"]["read"] == 150.0


# ------------------------------------------------------------- 同步 SDK
class FakeRouter:
    """模拟 modelRouter 的响应序列。"""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def sync_client(router: FakeRouter, **kwargs: object) -> ModelRouterClient:
    return ModelRouterClient(
        "http://router", "tok", transport=httpx.MockTransport(router), **kwargs
    )


def test_sync_client_sends_service_token_and_parses(monkeypatch):
    router = FakeRouter(ok({"token": "jwt", "user": {"id": 1}, "balance": 3.0}))
    with sync_client(router) as client:
        auth = client.login("a", "b")
    assert auth.token == "jwt" and auth.balance == 3.0
    sent = router.requests[0]
    assert sent.headers["X-Service-Token"] == "tok"
    assert str(sent.url) == "http://router/api/v1/auth/login"


def test_sync_client_retries_on_429_with_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    limited = httpx.Response(
        429,
        json={"code": "WAIT_CONCURRENCY_LIMIT", "message": "busy", "data": None},
        headers={"Retry-After": "2"},
    )
    router = FakeRouter(limited, ok({"wait_result": "paid", "order_status": "paid"}))
    with sync_client(router) as client:
        result = client.as_user("jwt").wait_for_payment("R1")
    assert result.paid
    assert sleeps == [2.0]
    assert len(router.requests) == 2
    assert all(r.extensions["timeout"]["read"] == 170.0 for r in router.requests)


def test_sync_client_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    limited = httpx.Response(429, json={"code": "RATE", "message": "busy", "data": None})
    router = FakeRouter(limited, limited, limited)
    with sync_client(router, max_retries=2) as client:
        with pytest.raises(RateLimitedError):
            client.as_user("jwt").wait_for_payment("R1")
    assert len(router.requests) == 3


def test_create_order_retries_in_progress_conflict_with_same_key(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    in_progress = httpx.Response(
        409,
        json={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "处理中", "data": None},
        headers={"Retry-After": "1"},
    )
    router = FakeRouter(in_progress, ok({"order_no": "R1", "status": "pending"}))
    with sync_client(router) as client:
        order = client.as_user("Bearer jwt").create_recharge_order(1, "alipay", rate_code="a")
    assert order.order_no == "R1" and sleeps == [1.0]
    keys = {r.headers["Idempotency-Key"] for r in router.requests}
    assert keys == {order.idempotency_key}
    assert router.requests[0].headers["Authorization"] == "Bearer jwt"
    assert json.loads(router.requests[0].content) == {
        "amount_cny": 1.0,
        "payment_channel": "alipay",
        "recharge_rate_code": "a",
    }


def test_conflict_without_retry_after_is_raised():
    conflict = httpx.Response(
        409, json={"code": "IDEMPOTENCY_CONFLICT", "message": "参数不一致", "data": None}
    )
    router = FakeRouter(conflict)
    with sync_client(router) as client:
        with pytest.raises(ConflictError) as exc_info:
            client.as_user("jwt").create_recharge_order(1, "wechat", idempotency_key="fixed-key-1")
    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc_info.value.retry_after is None
    assert len(router.requests) == 1


def test_error_mapping_by_status():
    cases = [
        (401, "SERVICE_TOKEN_INVALID", AuthenticationError),
        (422, "VALIDATION_ERROR", ValidationError),
        (404, "ORDER_NOT_FOUND", NotFoundError),
        (504, "UPSTREAM_TIMEOUT", ServiceUnavailableError),
    ]
    for status, code, exc_type in cases:
        router = FakeRouter(
            httpx.Response(status, json={"code": code, "message": "m", "data": None})
        )
        with sync_client(router) as client:
            with pytest.raises(exc_type) as exc_info:
                client.health()
        assert exc_info.value.code == code
        assert exc_info.value.status_code == status
        assert str(exc_info.value) == f"[{status} {code}] m"


def test_non_json_and_transport_errors():
    router = FakeRouter(httpx.Response(502, text="<html>bad gateway</html>"))
    with sync_client(router) as client:
        with pytest.raises(ServiceUnavailableError) as exc_info:
            client.health()
    assert exc_info.value.code == "BAD_RESPONSE"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with ModelRouterClient("http://router", "tok", transport=httpx.MockTransport(down)) as client:
        with pytest.raises(TransportError):
            client.health()
        assert client.ready() is False


def test_ready_false_on_503():
    router = FakeRouter(
        httpx.Response(503, json={"code": "PLATFORM_UNAVAILABLE", "message": "m", "data": None})
    )
    with sync_client(router) as client:
        assert client.ready() is False


def test_payments_params_and_view():
    router = FakeRouter(ok({"list": [], "total": 0, "page": 2, "page_size": 10}))
    with sync_client(router) as client:
        page = client.payments(
            page=2, page_size=10, status="paid", start_date=date(2026, 10, 1), view="agent"
        )
    assert page.page == 2 and page.items == []
    assert dict(router.requests[0].url.params) == {
        "page": "2",
        "page_size": "10",
        "status": "paid",
        "start_date": "2026-10-01",
        "view": "agent",
    }
