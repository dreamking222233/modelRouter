from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from modelrouter.concurrency import WaitSlots, run_until_disconnect
from modelrouter.errors import ServiceError
from modelrouter.main import create_app
from modelrouter.security import read_user_claims

from .conftest import OK_BODY, FakePlatform, make_settings

WAIT_BODY = {"code": 200, "message": "ok", "data": {"wait_result": "closed"}}


# ---------------------------------------------------------------- WaitSlots
def test_wait_slots_reject_when_full():
    slots = WaitSlots(limit=1, retry_after_seconds=7)
    with slots.acquire():
        assert slots.in_use == 1
        with pytest.raises(ServiceError) as exc_info:
            with slots.acquire():
                pass
    err = exc_info.value
    assert err.status_code == 429
    assert err.code == "WAIT_CONCURRENCY_LIMIT"
    assert err.headers["Retry-After"] == "7"
    assert slots.in_use == 0


def test_wait_route_returns_429_when_slots_are_full(platform):
    platform.respond(200, WAIT_BODY)
    settings = make_settings(wait_max_concurrency=1, wait_retry_after_seconds=3)
    app = create_app(settings, platform_transport=httpx.MockTransport(platform.handler))
    with TestClient(app) as client:
        client.headers["X-Service-Token"] = settings.service_token_list[0]
        headers = {"Authorization": "Bearer jwt"}
        slots: WaitSlots = app.state.wait_slots
        with slots.acquire():
            resp = client.post("/api/v1/user/recharge-orders/R1/wait", headers=headers)
            assert resp.status_code == 429
            assert resp.json()["code"] == "WAIT_CONCURRENCY_LIMIT"
            assert resp.headers["Retry-After"] == "3"
            assert platform.requests == []
        resp = client.post("/api/v1/user/recharge-orders/R1/wait", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["wait_result"] == "closed"
        assert slots.in_use == 0


# ------------------------------------------------------- run_until_disconnect
class FakeRequest:
    def __init__(self, disconnect_on_poll: int | None) -> None:
        self.polls = 0
        self._disconnect_on_poll = disconnect_on_poll

    async def is_disconnected(self) -> bool:
        self.polls += 1
        return self._disconnect_on_poll is not None and self.polls >= self._disconnect_on_poll


def test_run_until_disconnect_returns_result():
    async def work() -> str:
        await asyncio.sleep(0.05)
        return "done"

    request = FakeRequest(disconnect_on_poll=None)
    result = asyncio.run(run_until_disconnect(request, work(), poll_seconds=0.01))  # type: ignore[arg-type]
    assert result == "done"
    assert request.polls >= 1


def test_run_until_disconnect_cancels_work_when_client_leaves():
    cancelled = False

    async def work() -> str:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return "never"

    async def scenario() -> None:
        request = FakeRequest(disconnect_on_poll=2)
        with pytest.raises(ServiceError) as exc_info:
            await run_until_disconnect(request, work(), poll_seconds=0.01)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 499
        assert exc_info.value.code == "CLIENT_DISCONNECTED"

    asyncio.run(scenario())
    assert cancelled


def test_run_until_disconnect_propagates_work_errors():
    async def work() -> str:
        raise RuntimeError("boom")

    request = FakeRequest(disconnect_on_poll=None)
    with pytest.raises(RuntimeError):
        asyncio.run(run_until_disconnect(request, work(), poll_seconds=0.01))  # type: ignore[arg-type]


# ------------------------------------------------------------------ readyz
def _ready_client(platform: FakePlatform, cache_seconds: float) -> TestClient:
    settings = make_settings(readiness_cache_seconds=cache_seconds)
    app = create_app(settings, platform_transport=httpx.MockTransport(platform.handler))
    return TestClient(app)


def test_readyz_ok_when_platform_answers(platform):
    with _ready_client(platform, cache_seconds=0) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"status": "ok", "platform": "ok"}
    sent = platform.last
    assert sent.url.path.endswith("/agent/payments")
    assert dict(sent.url.params) == {"page_size": "1"}


def test_readyz_treats_missing_scope_as_reachable(platform):
    platform.respond(403, {"code": "SCOPE_DENIED", "message": "no", "data": None})
    with _ready_client(platform, cache_seconds=0) as client:
        assert client.get("/readyz").status_code == 200


def test_readyz_fails_on_invalid_key_or_unreachable_platform(platform):
    platform.respond(401, {"code": "OPEN_API_KEY_INVALID", "message": "bad", "data": None})
    with _ready_client(platform, cache_seconds=0) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["code"] == "PLATFORM_UNAVAILABLE"

        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        platform.responder = down
        assert client.get("/readyz").status_code == 503


def test_readyz_caches_result(platform):
    platform.respond(200, OK_BODY)
    with _ready_client(platform, cache_seconds=60) as client:
        assert client.get("/readyz").status_code == 200
        platform.respond(401, {"code": "OPEN_API_KEY_INVALID", "message": "bad", "data": None})
        assert client.get("/readyz").status_code == 200
    assert len(platform.requests) == 1


# -------------------------------------------------------------- JWT claims
def test_read_user_claims_handles_bad_inputs():
    assert read_user_claims("Bearer nope") is None
    assert read_user_claims("Bearer a.b") is None
    assert read_user_claims("Bearer a.!!!.c") is None
    assert read_user_claims("Bearer a.e30.c") is None  # {} 没有 sub
