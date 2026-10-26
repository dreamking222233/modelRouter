from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from ..platform import PlatformClient, PlatformResponse
from ..security import require_user_bearer


def get_platform(request: Request) -> PlatformClient:
    return request.app.state.platform


PlatformDep = Annotated[PlatformClient, Depends(get_platform)]
UserAuthDep = Annotated[str, Depends(require_user_bearer)]


def passthrough(resp: PlatformResponse) -> JSONResponse:
    """把平台的状态码、{code, message, data} 响应体和 Retry-After 头原样返回。"""
    return JSONResponse(status_code=resp.status_code, content=resp.body, headers=resp.headers)
