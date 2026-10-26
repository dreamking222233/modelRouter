from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from modelrouter.config import Settings
from modelrouter.main import create_app

SERVICE_TOKEN = "test-service-token"
AGENT_KEY = "agk_test_key"
USER_JWT = "user.jwt.token"
OPEN_API_BASE = "https://platform.test/api/agent/open/v1"

OK_BODY = {"code": 200, "message": "ok", "data": None}


class FakePlatform:
    """记录发往平台的请求，并按 responder 返回响应。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder: Callable[[httpx.Request], httpx.Response] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responder is None:
            return httpx.Response(200, json=OK_BODY)
        return self.responder(request)

    def respond(self, status: int, body: dict | None = None, **headers: str) -> None:
        self.responder = lambda _: httpx.Response(status, json=body or OK_BODY, headers=headers)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "没有发往平台的请求"
        return self.requests[-1]

    def last_json(self) -> dict:
        return json.loads(self.last.content)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "platform_api_base": "https://platform.test",
        "platform_agent_api_key": AGENT_KEY,
        "service_api_tokens": SERVICE_TOKEN,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings, platform: FakePlatform) -> Iterator[TestClient]:
    app = create_app(settings, platform_transport=httpx.MockTransport(platform.handler))
    with TestClient(app) as test_client:
        test_client.headers["X-Service-Token"] = SERVICE_TOKEN
        yield test_client


@pytest.fixture
def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {USER_JWT}"}
