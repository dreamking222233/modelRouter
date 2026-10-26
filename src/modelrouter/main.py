from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from . import __version__
from .api import agent, auth, user
from .concurrency import WaitSlots
from .config import Settings
from .errors import envelope, install_exception_handlers
from .platform import PlatformClient
from .readiness import ReadinessProbe
from .security import require_service_token

logger = logging.getLogger("modelrouter")

# 调用方传入的 X-Request-ID 只接受这个形态，否则替换为服务端生成的值
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

DESCRIPTION = """
本服务持有平台「代理开放接口」的 API Key，为 Agent 框架提供统一接口。

* 所有 `/api/v1` 接口需携带 `X-Service-Token`。
* `/api/v1/user/**` 接口还需携带平台签发的用户 JWT：`Authorization: Bearer <JWT>`。
* 响应体与平台一致：`{"code": ..., "message": ..., "data": ...}`，HTTP 状态码原样透传。
  成功时 `code` 为 200，错误时 `code` 为字符串错误码。
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def create_app(
    settings: Settings | None = None,
    *,
    platform_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """应用工厂。uvicorn 用 ``modelrouter.main:create_app --factory`` 启动。

    ``platform_transport`` 仅供测试注入 mock 平台。
    """
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.platform = PlatformClient(settings, transport=platform_transport)
        app.state.wait_slots = WaitSlots(
            settings.wait_max_concurrency, settings.wait_retry_after_seconds
        )
        app.state.readiness = ReadinessProbe(settings.readiness_cache_seconds)
        logger.info("平台开放接口地址: %s", settings.platform_open_api_base)
        try:
            yield
        finally:
            await app.state.platform.aclose()

    app = FastAPI(
        title="modelRouter",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.settings = settings
    install_exception_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        # 登录结果、支付凭据、模型 Key 明文都经过本服务，统一禁止缓存
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        logger.info(
            "%s %s -> %s (%.0f ms) rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_service_token)])
    api.include_router(auth.router)
    api.include_router(user.router)
    api.include_router(agent.router)
    app.include_router(api)

    @app.get("/healthz", tags=["ops"], summary="存活检查")
    async def healthz() -> dict:
        return envelope(200, "ok", {"status": "ok", "version": __version__})

    @app.get("/readyz", tags=["ops"], summary="就绪检查：平台可达且代理 Key 有效")
    async def readyz(request: Request) -> JSONResponse:
        ok = await request.app.state.readiness.check(request.app.state.platform)
        if ok:
            return JSONResponse(
                status_code=200, content=envelope(200, "ok", {"status": "ok", "platform": "ok"})
            )
        return JSONResponse(
            status_code=503,
            content=envelope(
                "PLATFORM_UNAVAILABLE", "平台不可用", {"status": "unavailable", "platform": "error"}
            ),
        )

    return app
