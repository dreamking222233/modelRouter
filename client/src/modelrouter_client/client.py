"""同步与异步客户端。两者共用同一套请求构造（_Calls）、响应解析与重试策略。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import httpx

from .errors import (
    ApiError,
    ConflictError,
    RateLimitedError,
    ServiceUnavailableError,
    TransportError,
    error_for,
)
from .models import (
    ApiKey,
    AuthResult,
    GroupOptions,
    OrderRecord,
    PaymentsPage,
    RechargeOrder,
    WaitResult,
)

DEFAULT_TIMEOUT = 30.0
# 平台最长同步等待 120 秒，modelRouter 默认 150 秒，这里再留余量
DEFAULT_WAIT_TIMEOUT = 170.0
MAX_RETRY_DELAY = 30.0
USER_AGENT = "modelrouter-client/0.1"

PaymentChannel = Literal["alipay", "wechat"]
RateCode = Literal["a", "b", "c", "d", "e", "f", "g"]
GroupMode = Literal["unified", "special"]
PaymentsView = Literal["user", "agent"]
Amount = Decimal | float | int | str


def new_idempotency_key() -> str:
    """生成满足平台要求（8–128 位可见 ASCII）的幂等键。"""
    return f"mrc-{uuid.uuid4().hex}"


def _bearer(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return "Bearer " + token[7:].strip()
    return f"Bearer {token}"


def _number(value: Amount) -> float:
    return float(Decimal(str(value)))


def _iso(value: date | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None


@dataclass(frozen=True)
class _Call:
    method: str
    path: str
    json: dict[str, Any] | None = None
    params: dict[str, str] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None
    # 409 且带 Retry-After 表示同一幂等请求仍在处理中，可用原幂等键重试
    retry_in_progress_conflict: bool = False


class _Calls:
    @staticmethod
    def register(username: str, email: str, password: str, invite_code: str | None) -> _Call:
        body: dict[str, Any] = {"username": username, "email": email, "password": password}
        if invite_code:
            body["invite_code"] = invite_code
        return _Call("POST", "/api/v1/auth/register", json=body)

    @staticmethod
    def login(username: str, password: str) -> _Call:
        return _Call(
            "POST", "/api/v1/auth/login", json={"username": username, "password": password}
        )

    @staticmethod
    def group_options(authorization: str) -> _Call:
        return _Call(
            "GET", "/api/v1/user/api-keys/group-options", headers={"Authorization": authorization}
        )

    @staticmethod
    def create_api_key(
        authorization: str,
        name: str,
        group_mode: GroupMode,
        group_model_series: str | None,
        group_id: int | None,
        daily_limit_usd: Amount | None,
    ) -> _Call:
        body: dict[str, Any] = {"name": name, "group_mode": group_mode}
        if group_model_series is not None:
            body["group_model_series"] = group_model_series
        if group_id is not None:
            body["group_id"] = group_id
        if daily_limit_usd is not None:
            body["daily_limit_usd"] = _number(daily_limit_usd)
        return _Call(
            "POST", "/api/v1/user/api-keys", json=body, headers={"Authorization": authorization}
        )

    @staticmethod
    def create_recharge_order(
        authorization: str,
        amount_cny: Amount,
        payment_channel: PaymentChannel,
        rate_code: RateCode | None,
        idempotency_key: str,
    ) -> _Call:
        body: dict[str, Any] = {
            "amount_cny": _number(amount_cny),
            "payment_channel": payment_channel,
        }
        if rate_code is not None:
            body["recharge_rate_code"] = rate_code
        return _Call(
            "POST",
            "/api/v1/user/recharge-orders",
            json=body,
            headers={"Authorization": authorization, "Idempotency-Key": idempotency_key},
            retry_in_progress_conflict=True,
        )

    @staticmethod
    def get_recharge_order(authorization: str, order_no: str) -> _Call:
        return _Call(
            "GET",
            f"/api/v1/user/recharge-orders/{order_no}",
            headers={"Authorization": authorization},
        )

    @staticmethod
    def wait_for_payment(authorization: str, order_no: str, timeout: float) -> _Call:
        return _Call(
            "POST",
            f"/api/v1/user/recharge-orders/{order_no}/wait",
            headers={"Authorization": authorization},
            timeout=timeout,
        )

    @staticmethod
    def request_detail(request_id: str) -> _Call:
        return _Call("GET", f"/api/v1/agent/requests/{request_id}")

    @staticmethod
    def payments(
        page: int,
        page_size: int,
        user_id: int | None,
        status: str | None,
        payment_channel: str | None,
        recharge_type: str | None,
        start_date: date | str | None,
        end_date: date | str | None,
        view: PaymentsView,
    ) -> _Call:
        raw: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "user_id": user_id,
            "status": status,
            "payment_channel": payment_channel,
            "recharge_type": recharge_type,
            "start_date": _iso(start_date),
            "end_date": _iso(end_date),
            "view": view,
        }
        params = {key: str(value) for key, value in raw.items() if value is not None}
        return _Call("GET", "/api/v1/agent/payments", params=params)

    @staticmethod
    def ready() -> _Call:
        return _Call("GET", "/readyz")

    @staticmethod
    def health() -> _Call:
        return _Call("GET", "/healthz")


class _Base:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        max_retries: int = 2,
    ) -> None:
        if not base_url:
            raise ValueError("base_url 不能为空")
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._wait_timeout = wait_timeout
        self._max_retries = max(0, max_retries)

    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._service_token:
            headers["X-Service-Token"] = self._service_token
        return headers

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        retry_after = _retry_after(response)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or "code" not in body:
            status = response.status_code if response.status_code >= 400 else 502
            raise error_for(
                status,
                "BAD_RESPONSE",
                f"modelRouter 返回了非预期响应（HTTP {response.status_code}）",
                retry_after=retry_after,
            )
        if response.is_success:
            return body
        raise error_for(
            response.status_code,
            str(body.get("code")),
            str(body.get("message") or ""),
            data=body.get("data"),
            retry_after=retry_after,
        )

    def _retry_delay(self, exc: ApiError, call: _Call, attempt: int) -> float | None:
        """返回重试前应等待的秒数；None 表示不重试。"""
        if attempt >= self._max_retries:
            return None
        if isinstance(exc, RateLimitedError):
            return min(exc.retry_after if exc.retry_after is not None else 1.0, MAX_RETRY_DELAY)
        if (
            isinstance(exc, ConflictError)
            and call.retry_in_progress_conflict
            and exc.retry_after is not None
        ):
            return min(exc.retry_after, MAX_RETRY_DELAY)
        return None

    @staticmethod
    def _transport_error(exc: httpx.HTTPError, call: _Call) -> TransportError:
        if isinstance(exc, httpx.TimeoutException):
            return TransportError(f"请求 modelRouter 超时：{call.method} {call.path}")
        return TransportError(f"无法连接 modelRouter：{exc.__class__.__name__}")


# ====================================================================== 同步
class ModelRouterClient(_Base):
    """同步客户端。

    ``service_token`` 是 modelRouter 的 ``SERVICE_API_TOKENS`` 之一。
    用户级接口通过 :meth:`as_user` 绑定用户 JWT 后调用。
    """

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            service_token,
            timeout=timeout,
            wait_timeout=wait_timeout,
            max_retries=max_retries,
        )
        self._http = httpx.Client(
            base_url=self._base_url,
            headers=self._default_headers(),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ModelRouterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, call: _Call) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = self._http.request(
                    call.method,
                    call.path,
                    json=call.json,
                    params=call.params,
                    headers=call.headers,
                    timeout=call.timeout if call.timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as exc:
                raise self._transport_error(exc, call) from exc
            try:
                return self._parse(response)
            except ApiError as exc:
                delay = self._retry_delay(exc, call, attempt)
                if delay is None:
                    raise
                attempt += 1
                time.sleep(delay)

    # ---- 认证
    def register(
        self, username: str, email: str, password: str, *, invite_code: str | None = None
    ) -> AuthResult:
        data = self._request(_Calls.register(username, email, password, invite_code))["data"]
        return AuthResult.from_dict(data)

    def login(self, username: str, password: str) -> AuthResult:
        return AuthResult.from_dict(self._request(_Calls.login(username, password))["data"])

    def as_user(self, token: str) -> UserClient:
        """绑定用户 JWT（登录或注册返回的 ``token``）。"""
        return UserClient(self, _bearer(token))

    # ---- 代理级
    def request_detail(self, request_id: str) -> dict[str, Any]:
        return self._request(_Calls.request_detail(request_id))["data"]

    def payments(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        user_id: int | None = None,
        status: str | None = None,
        payment_channel: str | None = None,
        recharge_type: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        view: PaymentsView = "user",
    ) -> PaymentsPage:
        call = _Calls.payments(
            page,
            page_size,
            user_id,
            status,
            payment_channel,
            recharge_type,
            start_date,
            end_date,
            view,
        )
        return PaymentsPage.from_dict(self._request(call)["data"])

    # ---- 运维
    def ready(self) -> bool:
        """modelRouter 可用且其与平台的连接正常。"""
        try:
            self._request(_Calls.ready())
        except (ServiceUnavailableError, TransportError):
            return False
        return True

    def health(self) -> dict[str, Any]:
        return self._request(_Calls.health())["data"]


class UserClient:
    """绑定了某个终端用户 JWT 的客户端。"""

    def __init__(self, client: ModelRouterClient, authorization: str) -> None:
        self._client = client
        self._authorization = authorization

    @property
    def token(self) -> str:
        return self._authorization[len("Bearer ") :]

    def group_options(self) -> GroupOptions:
        return GroupOptions.from_dict(
            self._client._request(_Calls.group_options(self._authorization))["data"]
        )

    def create_api_key(
        self,
        name: str,
        *,
        group_mode: GroupMode = "unified",
        group_model_series: str | None = None,
        group_id: int | None = None,
        daily_limit_usd: Amount | None = None,
    ) -> ApiKey:
        """创建模型 API Key。返回的 ``key`` 明文只有这一次，请妥善保存。"""
        call = _Calls.create_api_key(
            self._authorization, name, group_mode, group_model_series, group_id, daily_limit_usd
        )
        return ApiKey.from_dict(self._client._request(call)["data"])

    def create_recharge_order(
        self,
        amount_cny: Amount,
        payment_channel: PaymentChannel,
        *,
        rate_code: RateCode | None = None,
        idempotency_key: str | None = None,
    ) -> RechargeOrder:
        """创建充值订单。不传 ``idempotency_key`` 时自动生成；
        若需对同一笔业务重试，请复用返回值里的 ``idempotency_key``。"""
        key = idempotency_key or new_idempotency_key()
        call = _Calls.create_recharge_order(
            self._authorization, amount_cny, payment_channel, rate_code, key
        )
        return RechargeOrder.from_dict(self._client._request(call)["data"], key)

    def get_recharge_order(self, order_no: str) -> OrderRecord:
        """非阻塞查单。"""
        call = _Calls.get_recharge_order(self._authorization, order_no)
        return OrderRecord.from_dict(self._client._request(call)["data"])

    def wait_for_payment(self, order_no: str) -> WaitResult:
        """同步等待支付结果，最长阻塞约 120 秒。未支付时平台约 87 秒即关单并返回 closed。"""
        call = _Calls.wait_for_payment(self._authorization, order_no, self._client._wait_timeout)
        return WaitResult.from_dict(self._client._request(call)["data"])


# ====================================================================== 异步
class AsyncModelRouterClient(_Base):
    """异步客户端，方法与 :class:`ModelRouterClient` 一一对应。"""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            service_token,
            timeout=timeout,
            wait_timeout=wait_timeout,
            max_retries=max_retries,
        )
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._default_headers(),
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncModelRouterClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, call: _Call) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    call.method,
                    call.path,
                    json=call.json,
                    params=call.params,
                    headers=call.headers,
                    timeout=call.timeout if call.timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as exc:
                raise self._transport_error(exc, call) from exc
            try:
                return self._parse(response)
            except ApiError as exc:
                delay = self._retry_delay(exc, call, attempt)
                if delay is None:
                    raise
                attempt += 1
                await asyncio.sleep(delay)

    async def register(
        self, username: str, email: str, password: str, *, invite_code: str | None = None
    ) -> AuthResult:
        data = (await self._request(_Calls.register(username, email, password, invite_code)))[
            "data"
        ]
        return AuthResult.from_dict(data)

    async def login(self, username: str, password: str) -> AuthResult:
        return AuthResult.from_dict((await self._request(_Calls.login(username, password)))["data"])

    def as_user(self, token: str) -> AsyncUserClient:
        return AsyncUserClient(self, _bearer(token))

    async def request_detail(self, request_id: str) -> dict[str, Any]:
        return (await self._request(_Calls.request_detail(request_id)))["data"]

    async def payments(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        user_id: int | None = None,
        status: str | None = None,
        payment_channel: str | None = None,
        recharge_type: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        view: PaymentsView = "user",
    ) -> PaymentsPage:
        call = _Calls.payments(
            page,
            page_size,
            user_id,
            status,
            payment_channel,
            recharge_type,
            start_date,
            end_date,
            view,
        )
        return PaymentsPage.from_dict((await self._request(call))["data"])

    async def ready(self) -> bool:
        try:
            await self._request(_Calls.ready())
        except (ServiceUnavailableError, TransportError):
            return False
        return True

    async def health(self) -> dict[str, Any]:
        return (await self._request(_Calls.health()))["data"]


class AsyncUserClient:
    def __init__(self, client: AsyncModelRouterClient, authorization: str) -> None:
        self._client = client
        self._authorization = authorization

    @property
    def token(self) -> str:
        return self._authorization[len("Bearer ") :]

    async def group_options(self) -> GroupOptions:
        data = (await self._client._request(_Calls.group_options(self._authorization)))["data"]
        return GroupOptions.from_dict(data)

    async def create_api_key(
        self,
        name: str,
        *,
        group_mode: GroupMode = "unified",
        group_model_series: str | None = None,
        group_id: int | None = None,
        daily_limit_usd: Amount | None = None,
    ) -> ApiKey:
        call = _Calls.create_api_key(
            self._authorization, name, group_mode, group_model_series, group_id, daily_limit_usd
        )
        return ApiKey.from_dict((await self._client._request(call))["data"])

    async def create_recharge_order(
        self,
        amount_cny: Amount,
        payment_channel: PaymentChannel,
        *,
        rate_code: RateCode | None = None,
        idempotency_key: str | None = None,
    ) -> RechargeOrder:
        key = idempotency_key or new_idempotency_key()
        call = _Calls.create_recharge_order(
            self._authorization, amount_cny, payment_channel, rate_code, key
        )
        return RechargeOrder.from_dict((await self._client._request(call))["data"], key)

    async def get_recharge_order(self, order_no: str) -> OrderRecord:
        call = _Calls.get_recharge_order(self._authorization, order_no)
        return OrderRecord.from_dict((await self._client._request(call))["data"])

    async def wait_for_payment(self, order_no: str) -> WaitResult:
        call = _Calls.wait_for_payment(self._authorization, order_no, self._client._wait_timeout)
        return WaitResult.from_dict((await self._client._request(call))["data"])
