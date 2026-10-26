"""平台「代理开放接口」的 HTTP 客户端。

只负责两件事：给每个请求加上代理 API Key，以及把平台响应原样解析出来。
不做业务判断，状态码和 {code, message, data} 都交给上层透传。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings
from ..errors import UpstreamError

logger = logging.getLogger(__name__)

AGENT_KEY_HEADER = "X-Agent-API-Key"
IDEMPOTENCY_HEADER = "Idempotency-Key"

# 平台路径（相对 /api/agent/open/v1）。文档里 /user 与 /users 单复数混用，
# 这里按文档原样保留，集中放在一处，日后平台修正时只改这里。
PATH_REGISTER = "/auth/register"
PATH_LOGIN = "/auth/login"
PATH_GROUP_OPTIONS = "/user/api-keys/group-options"
PATH_CREATE_API_KEY = "/user/api-keys"
PATH_CREATE_RECHARGE_ORDER = "/users/recharge-orders"
PATH_WAIT_RECHARGE_ORDER = "/users/recharge-orders/{order_no}/wait"
PATH_REQUEST_DETAIL = "/agent/requests/{request_id}"
PATH_PAYMENTS = "/agent/payments"

# 需要原样带回给调用方的平台响应头
PASSTHROUGH_HEADERS = frozenset({"retry-after"})


@dataclass(slots=True)
class PlatformResponse:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def code(self) -> Any:
        return self.body.get("code")

    @property
    def data(self) -> Any:
        return self.body.get("data")


class PlatformClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._wait_timeout = httpx.Timeout(settings.platform_wait_timeout_seconds, connect=10.0)
        self._client = httpx.AsyncClient(
            base_url=settings.platform_open_api_base,
            headers={
                AGENT_KEY_HEADER: settings.platform_agent_api_key,
                "Accept": "application/json",
                "User-Agent": "modelrouter/0.1",
            },
            timeout=httpx.Timeout(settings.platform_timeout_seconds, connect=10.0),
            verify=settings.platform_verify_ssl,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ 认证
    async def register(self, payload: dict[str, Any]) -> PlatformResponse:
        return await self._request("POST", PATH_REGISTER, json_body=payload)

    async def login(self, payload: dict[str, Any]) -> PlatformResponse:
        return await self._request("POST", PATH_LOGIN, json_body=payload)

    # ------------------------------------------------------------ 用户模型 Key
    async def group_options(self, user_authorization: str) -> PlatformResponse:
        return await self._request(
            "GET", PATH_GROUP_OPTIONS, user_authorization=user_authorization
        )

    async def create_api_key(
        self, user_authorization: str, payload: dict[str, Any]
    ) -> PlatformResponse:
        return await self._request(
            "POST", PATH_CREATE_API_KEY, json_body=payload, user_authorization=user_authorization
        )

    # ------------------------------------------------------------------ 充值
    async def create_recharge_order(
        self, user_authorization: str, idempotency_key: str, payload: dict[str, Any]
    ) -> PlatformResponse:
        return await self._request(
            "POST",
            PATH_CREATE_RECHARGE_ORDER,
            json_body=payload,
            user_authorization=user_authorization,
            idempotency_key=idempotency_key,
        )

    async def wait_recharge_order(
        self, user_authorization: str, order_no: str
    ) -> PlatformResponse:
        return await self._request(
            "POST",
            PATH_WAIT_RECHARGE_ORDER.format(order_no=order_no),
            user_authorization=user_authorization,
            timeout=self._wait_timeout,
        )

    # -------------------------------------------------------------- 代理查询
    async def request_detail(self, request_id: str) -> PlatformResponse:
        return await self._request("GET", PATH_REQUEST_DETAIL.format(request_id=request_id))

    async def payments(self, params: dict[str, str]) -> PlatformResponse:
        return await self._request("GET", PATH_PAYMENTS, params=params)

    # ------------------------------------------------------------------ 内部
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        user_authorization: str | None = None,
        idempotency_key: str | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> PlatformResponse:
        headers: dict[str, str] = {}
        if user_authorization:
            headers["Authorization"] = user_authorization
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key

        started = time.perf_counter()
        try:
            response = await self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            logger.warning("platform %s %s 超时: %s", method, path, exc.__class__.__name__)
            raise UpstreamError(504, "平台响应超时", data={"path": path}) from exc
        except httpx.HTTPError as exc:
            logger.warning("platform %s %s 请求失败: %s", method, path, exc.__class__.__name__)
            raise UpstreamError(
                502, "无法连接平台", data={"path": path, "error": exc.__class__.__name__}
            ) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "platform %s %s -> %s (%.0f ms)", method, path, response.status_code, elapsed_ms
        )
        return self._parse(response, path)

    @staticmethod
    def _parse(response: httpx.Response, path: str) -> PlatformResponse:
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                502,
                "平台返回了非 JSON 响应",
                data={"path": path, "status": response.status_code},
            ) from exc
        if not isinstance(body, dict) or "code" not in body:
            raise UpstreamError(
                502,
                "平台响应格式异常",
                data={"path": path, "status": response.status_code},
            )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() in PASSTHROUGH_HEADERS
        }
        return PlatformResponse(status_code=response.status_code, body=body, headers=headers)
