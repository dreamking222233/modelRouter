"""modelRouter 服务的 Python 客户端 SDK。"""

from .client import AsyncModelRouterClient, AsyncUserClient, ModelRouterClient, UserClient
from .errors import (
    ApiError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    ModelRouterError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    TransportError,
    ValidationError,
)
from .models import (
    ApiKey,
    AuthResult,
    Group,
    GroupOptions,
    OrderRecord,
    PaymentsPage,
    RechargeOrder,
    Series,
    User,
    WaitResult,
)

__version__ = "0.1.0"

__all__ = [
    "ApiError",
    "ApiKey",
    "AsyncModelRouterClient",
    "AsyncUserClient",
    "AuthResult",
    "AuthenticationError",
    "ConflictError",
    "ForbiddenError",
    "Group",
    "GroupOptions",
    "ModelRouterClient",
    "ModelRouterError",
    "NotFoundError",
    "OrderRecord",
    "PaymentsPage",
    "RateLimitedError",
    "RechargeOrder",
    "Series",
    "ServiceUnavailableError",
    "TransportError",
    "User",
    "UserClient",
    "ValidationError",
    "WaitResult",
]
