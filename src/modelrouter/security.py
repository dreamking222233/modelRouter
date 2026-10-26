from __future__ import annotations

import base64
import binascii
import json
import secrets
from dataclasses import dataclass
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
        raise ServiceError(401, "SERVICE_TOKEN_MISSING", f"缺少 {SERVICE_TOKEN_HEADER} 请求头")
    if not any(secrets.compare_digest(x_service_token, t) for t in settings.service_token_list):
        raise ServiceError(401, "SERVICE_TOKEN_INVALID", f"{SERVICE_TOKEN_HEADER} 无效")


async def require_user_bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    """用户级接口必须携带平台签发的用户 JWT，原样转发给平台。

    返回规范化后的 ``Bearer <JWT>`` 字符串。
    """
    if not authorization:
        raise ServiceError(
            401, "USER_TOKEN_MISSING", "缺少用户登录凭据，请在 Authorization 头携带 Bearer <JWT>"
        )
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ServiceError(401, "USER_TOKEN_MALFORMED", "Authorization 头格式应为 Bearer <JWT>")
    return f"Bearer {token}"


@dataclass(frozen=True, slots=True)
class UserClaims:
    sub: str
    exp: int | None


def read_user_claims(authorization: str) -> UserClaims | None:
    """不验签地读取平台用户 JWT 的 ``sub``（用户 ID）与 ``exp``。

    签名只有平台能验证；这里的结果仅用于在代理级接口里按用户筛选，
    不构成任何授权依据。解析失败返回 None。
    """
    token = authorization.partition(" ")[2].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        payload = json.loads(raw)
    except (ValueError, binascii.Error):
        return None
    if not isinstance(payload, dict) or payload.get("sub") in (None, ""):
        return None
    exp = payload.get("exp")
    exp_value = int(exp) if isinstance(exp, int | float) else None
    return UserClaims(sub=str(payload["sub"]), exp=exp_value)
