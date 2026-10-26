from __future__ import annotations

from typing import Any


class ModelRouterError(Exception):
    """SDK 所有异常的基类。"""


class TransportError(ModelRouterError):
    """网络层错误：连不上 modelRouter，或请求超时。"""


class ApiError(ModelRouterError):
    """modelRouter 或平台返回的业务错误（非 2xx）。

    ``code`` 是字符串错误码，可能来自 modelRouter（如 ``SERVICE_TOKEN_INVALID``）
    或平台（如 ``IDEMPOTENCY_CONFLICT``）；``retry_after`` 为响应头建议的重试秒数。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        data: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{status_code} {code}] {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data
        self.retry_after = retry_after


class ValidationError(ApiError):
    """400 / 422：请求头或字段不合法，重试同样的请求不会成功。"""


class AuthenticationError(ApiError):
    """401：服务令牌或用户 JWT 缺失、无效或过期。"""


class ForbiddenError(ApiError):
    """403：平台 Key 缺少 scope，或代理功能开关未开启。"""


class NotFoundError(ApiError):
    """404：用户、订单或请求记录不存在。"""


class ConflictError(ApiError):
    """409：幂等键冲突。若带 retry_after，表示同一请求仍在处理中，可用原幂等键稍后重试。"""


class RateLimitedError(ApiError):
    """429：频率或并发限制，按 retry_after 重试。"""


class ServiceUnavailableError(ApiError):
    """5xx：modelRouter 内部错误、平台不可达、平台超时或支付渠道不可用。"""


_BY_STATUS: dict[int, type[ApiError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitedError,
}


def error_for(
    status_code: int,
    code: str,
    message: str,
    *,
    data: Any = None,
    retry_after: float | None = None,
) -> ApiError:
    cls = _BY_STATUS.get(status_code)
    if cls is None:
        cls = ServiceUnavailableError if status_code >= 500 else ApiError
    return cls(status_code, code, message, data=data, retry_after=retry_after)
