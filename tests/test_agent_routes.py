from __future__ import annotations

from .conftest import OPEN_API_BASE

REQUEST_ID = "4f1c2a4e-6c1b-4c89-9d1e-9d9f3f2d1a10"


def test_request_detail_requires_uuid(client, platform):
    resp = client.get("/api/v1/agent/requests/not-a-uuid")
    assert resp.status_code == 422
    assert platform.requests == []


def test_request_detail_forwards_and_passes_404(client, platform):
    platform.respond(404, {"code": 404, "message": "不存在", "data": None})
    resp = client.get(f"/api/v1/agent/requests/{REQUEST_ID}")
    assert resp.status_code == 404
    assert resp.json()["message"] == "不存在"
    assert str(platform.last.url) == f"{OPEN_API_BASE}/agent/requests/{REQUEST_ID}"
    assert "Authorization" not in platform.last.headers


def test_payments_forwards_only_given_filters(client, platform):
    platform.respond(
        200, {"code": 200, "message": "ok", "data": {"list": [], "total": 0, "page": 2}}
    )
    resp = client.get(
        "/api/v1/agent/payments",
        params={"page": 2, "status": "paid", "start_date": "2026-10-01", "end_date": "2026-10-31"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["page"] == 2
    assert dict(platform.last.url.params) == {
        "page": "2",
        "page_size": "20",
        "status": "paid",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
    }


def test_payments_validates_enums_and_limits(client, platform):
    assert client.get("/api/v1/agent/payments", params={"page_size": 101}).status_code == 422
    assert client.get("/api/v1/agent/payments", params={"status": "nope"}).status_code == 422
    assert platform.requests == []


def test_payments_rejects_reversed_date_range(client, platform):
    resp = client.get(
        "/api/v1/agent/payments", params={"start_date": "2026-10-31", "end_date": "2026-10-01"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_DATE_RANGE"
    assert platform.requests == []
