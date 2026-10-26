from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from modelrouter.main import create_app

from .conftest import OK_BODY, FakePlatform, make_settings


def test_missing_service_token_is_rejected_before_calling_platform(client, platform):
    client.headers.pop("X-Service-Token")
    resp = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 401
    assert platform.requests == []


def test_wrong_service_token_is_rejected(client, platform):
    client.headers["X-Service-Token"] = "nope"
    resp = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code == 401
    assert platform.requests == []


def test_any_configured_token_is_accepted(platform):
    settings = make_settings(service_api_tokens="one, two")
    app = create_app(settings, platform_transport=httpx.MockTransport(platform.handler))
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "a", "password": "b"},
            headers={"X-Service-Token": "two"},
        )
    assert resp.status_code == 200
    assert resp.json() == OK_BODY


def test_auth_can_be_disabled_for_local_debugging(platform):
    settings = make_settings(service_api_tokens="", service_auth_disabled=True)
    app = create_app(settings, platform_transport=httpx.MockTransport(platform.handler))
    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code == 200


def test_settings_require_token_unless_explicitly_disabled():
    try:
        make_settings(service_api_tokens="")
    except ValueError as exc:
        assert "SERVICE_API_TOKENS" in str(exc)
    else:
        raise AssertionError("缺少令牌时应当拒绝启动")


def test_healthz_is_public(client: TestClient):
    client.headers.pop("X-Service-Token")
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


def test_unknown_path_uses_envelope(client: TestClient):
    resp = client.get("/api/v1/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 404


def test_responses_are_not_cacheable_and_carry_request_id(client: TestClient, platform):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "a", "password": "b"},
        headers={"X-Request-ID": "rid-123"},
    )
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Request-ID"] == "rid-123"
    assert isinstance(platform, FakePlatform)
