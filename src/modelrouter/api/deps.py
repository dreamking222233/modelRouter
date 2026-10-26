from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from ..platform import PlatformClient, PlatformResponse
from ..security import require_user_bearer


def get_platform(request: Request) -> PlatformClient:
    return request.app.state.platform


PlatformDep = Annotated[PlatformClient, Depends(get_platform)]
UserAuthDep = Annotated[str, Depends(require_user_bearer)]


def passthrough(
    resp: PlatformResponse, *, project: Callable[[Any], Any] | None = None
) -> JSONResponse:
    """把平台的状态码、{code, message, data} 响应体和 Retry-After 头原样返回。

    ``project`` 只在成功响应上对 ``data`` 做字段裁剪，错误响应原样返回。
    """
    body = resp.body
    if project is not None and 200 <= resp.status_code < 300:
        body = {**body, "data": project(body.get("data"))}
    return JSONResponse(status_code=resp.status_code, content=body, headers=resp.headers)
