from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def envelope(code: int | str, message: str, data: Any = None) -> dict[str, Any]:
    """与平台一致的统一响应体：成功时 code 为 200，错误时 code 为字符串错误码。"""
    return {"code": code, "message": message, "data": data}


class ServiceError(Exception):
    """本服务自身产生的错误。

    ``code`` 是字符串错误码，与平台的错误码风格一致（例如平台的 ``OPEN_API_KEY_INVALID``），
    调用方可据此区分错误来源与类型。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data
        self.headers = headers or {}

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=envelope(self.code, self.message, self.data),
            headers=self.headers,
        )


class UpstreamError(ServiceError):
    """调用平台失败：网络不通、超时或响应不是约定的 JSON 结构。"""


_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=envelope("VALIDATION_ERROR", "请求字段校验失败", errors)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(exc.detail)),
            headers=exc.headers or {},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常: %s", exc)
        return JSONResponse(status_code=500, content=envelope("INTERNAL_ERROR", "内部错误"))
