from __future__ import annotations

from .conftest import AGENT_KEY, OPEN_API_BASE


def test_register_forwards_payload_with_agent_key(client, platform):
    platform.respond(200, {"code": 200, "message": "ok", "data": {"token": "jwt", "user": {}}})
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "pw"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["token"] == "jwt"

    sent = platform.last
    assert sent.method == "POST"
    assert str(sent.url) == f"{OPEN_API_BASE}/auth/register"
    assert sent.headers["X-Agent-API-Key"] == AGENT_KEY
    assert "Authorization" not in sent.headers
    assert platform.last_json() == {
        "username": "alice",
        "email": "alice@example.com",
        "password": "pw",
    }


def test_register_includes_invite_code_when_given(client, platform):
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "pw",
            "invite_code": "INV1",
        },
    )
    assert platform.last_json()["invite_code"] == "INV1"


def test_register_rejects_invalid_email_locally(client, platform):
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "email": "not-an-email", "password": "pw"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["data"][0]["loc"] == ["body", "email"]
    assert platform.requests == []


def test_login_passes_through_platform_error(client, platform):
    platform.respond(401, {"code": 401, "message": "密码错误", "data": None})
    resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "bad"})
    assert resp.status_code == 401
    assert resp.json() == {"code": 401, "message": "密码错误", "data": None}
    assert str(platform.last.url) == f"{OPEN_API_BASE}/auth/login"
    assert platform.last_json() == {"username": "alice", "password": "bad"}
