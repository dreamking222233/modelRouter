from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, Request

from .errors import ServiceError

SERVICE_TOKEN_HEADER = "X-Service-Token"


async def require_service_token(
    request: Request,
    x_service_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验 Agent 框架调用本服务时携带的内部令牌。"""
    settings = request.app.state.settings
    if settings.service_auth_disabled:
        return
    if not x_service_token:
        raise ServiceError(401, f"缺少 {SERVICE_TOKEN_HEADER} 请求头")
    if not any(secrets.compare_digest(x_service_token, t) for t in settings.service_token_list):
        raise ServiceError(401, f"{SERVICE_TOKEN_HEADER} 无效")


async def require_user_bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    """用户级接口必须携带平台签发的用户 JWT，原样转发给平台。

    返回规范化后的 ``Bearer <JWT>`` 字符串。
    """
    if not authorization:
        raise ServiceError(401, "缺少用户登录凭据，请在 Authorization 头携带 Bearer <JWT>")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ServiceError(401, "Authorization 头格式应为 Bearer <JWT>")
    return f"Bearer {token}"
