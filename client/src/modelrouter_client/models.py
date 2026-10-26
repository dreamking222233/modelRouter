"""响应模型。每个模型都保留 ``raw`` 原始字典，平台新增字段时不会丢失。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


@dataclass(frozen=True)
class User:
    id: int | None
    username: str | None
    email: str | None
    role: str | None
    agent_id: int | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> User:
        d = _as_dict(data)
        return cls(
            id=d.get("id"),
            username=d.get("username"),
            email=d.get("email"),
            role=d.get("role"),
            agent_id=d.get("agent_id"),
            raw=d,
        )


@dataclass(frozen=True)
class AuthResult:
    """注册或登录结果。``balance`` 只有登录接口返回。"""

    token: str
    user: User
    is_first_login: bool | None
    balance: float | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> AuthResult:
        d = _as_dict(data)
        return cls(
            token=str(d.get("token") or ""),
            user=User.from_dict(d.get("user")),
            is_first_login=d.get("is_first_login"),
            balance=d.get("balance"),
            raw=d,
        )


@dataclass(frozen=True)
class Group:
    id: int
    name: str | None
    code: str | None
    model_series: str | None
    multiplier: float | None
    enabled: bool
    is_default: bool
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Group:
        d = _as_dict(data)
        return cls(
            id=int(d.get("id", 0)),
            name=d.get("name"),
            code=d.get("code"),
            model_series=d.get("model_series"),
            multiplier=d.get("multiplier"),
            enabled=bool(d.get("enabled", False)),
            is_default=bool(d.get("is_default", False)),
            raw=d,
        )


@dataclass(frozen=True)
class Series:
    value: str
    label: str | None
    default_group_id: int | None
    groups: list[Group]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Series:
        d = _as_dict(data)
        return cls(
            value=str(d.get("value") or ""),
            label=d.get("label"),
            default_group_id=d.get("default_group_id"),
            groups=[Group.from_dict(g) for g in _as_list(d.get("groups"))],
            raw=d,
        )


@dataclass(frozen=True)
class GroupOptions:
    """创建模型 Key 的表单数据来源。``series`` 按系列组织，``groups`` 是全部启用分组。"""

    series: list[Series]
    groups: list[Group]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> GroupOptions:
        d = _as_dict(data)
        return cls(
            series=[Series.from_dict(s) for s in _as_list(d.get("series"))],
            groups=[Group.from_dict(g) for g in _as_list(d.get("groups"))],
            raw=d,
        )

    def find_series(self, value: str) -> Series | None:
        return next((s for s in self.series if s.value == value), None)


@dataclass(frozen=True)
class ApiKey:
    """新建的模型 API Key。``key`` 明文只在创建时返回一次。"""

    id: int | None
    name: str | None
    key: str
    key_prefix: str | None
    daily_limit_usd: float | None
    group_mode: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> ApiKey:
        d = _as_dict(data)
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            key=str(d.get("key") or ""),
            key_prefix=d.get("key_prefix"),
            daily_limit_usd=d.get("daily_limit_usd"),
            group_mode=d.get("group_mode"),
            raw=d,
        )


@dataclass(frozen=True)
class RechargeOrder:
    """新建的充值订单。``idempotency_key`` 是本次使用的幂等键，重试创建时必须复用。"""

    order_no: str
    status: str | None
    amount_cny: float | None
    credited_usd: float | None
    user_recharge_rate: float | None
    user_recharge_rate_code: str | None
    expired_at: str | None
    payment_channel: str | None
    pay_url: str | None
    wechat_code_url: str | None
    idempotency_key: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any, idempotency_key: str) -> RechargeOrder:
        d = _as_dict(data)
        return cls(
            order_no=str(d.get("order_no") or ""),
            status=d.get("status"),
            amount_cny=d.get("amount_cny"),
            credited_usd=d.get("credited_usd"),
            user_recharge_rate=d.get("user_recharge_rate"),
            user_recharge_rate_code=d.get("user_recharge_rate_code"),
            expired_at=d.get("expired_at"),
            payment_channel=d.get("payment_channel"),
            pay_url=d.get("pay_url"),
            wechat_code_url=d.get("wechat_code_url"),
            idempotency_key=idempotency_key,
            raw=d,
        )

    @property
    def payment_target(self) -> str | None:
        """支付宝返回收银台链接，微信返回需要绘制成二维码的 URL。"""
        return self.pay_url or self.wechat_code_url


@dataclass(frozen=True)
class WaitResult:
    wait_result: str | None
    close_result: str | None
    order_status: str | None
    order_no: str | None
    paid_at: str | None
    balance: float | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> WaitResult:
        d = _as_dict(data)
        return cls(
            wait_result=d.get("wait_result"),
            close_result=d.get("close_result"),
            order_status=d.get("order_status"),
            order_no=d.get("order_no"),
            paid_at=d.get("paid_at"),
            balance=d.get("balance"),
            raw=d,
        )

    @property
    def paid(self) -> bool:
        return self.wait_result == "paid"

    @property
    def closed(self) -> bool:
        """订单已被平台关闭，需要用新的幂等键重新下单。"""
        return self.wait_result == "closed" or self.order_status == "closed"

    @property
    def can_wait_again(self) -> bool:
        """超时但订单仍为 pending，可用同一订单号再次等待。"""
        return self.wait_result == "wait_timeout" and self.order_status == "pending"


@dataclass(frozen=True)
class OrderRecord:
    """充值记录（查单接口与充值记录列表共用）。"""

    id: int | None
    order_no: str
    user_id: int | None
    username: str | None
    status: str | None
    trade_status: str | None
    payment_channel: str | None
    recharge_type: str | None
    amount_cny: float | None
    credited_usd: float | None
    user_recharge_rate: float | None
    paid_at: str | None
    created_at: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> OrderRecord:
        d = _as_dict(data)
        return cls(
            id=d.get("id"),
            order_no=str(d.get("order_no") or ""),
            user_id=d.get("user_id"),
            username=d.get("username"),
            status=d.get("status"),
            trade_status=d.get("trade_status"),
            payment_channel=d.get("payment_channel"),
            recharge_type=d.get("recharge_type"),
            amount_cny=d.get("amount_cny"),
            credited_usd=d.get("credited_usd"),
            user_recharge_rate=d.get("user_recharge_rate"),
            paid_at=d.get("paid_at"),
            created_at=d.get("created_at"),
            raw=d,
        )

    @property
    def paid(self) -> bool:
        return self.status == "paid"


@dataclass(frozen=True)
class PaymentsPage:
    items: list[OrderRecord]
    total: int
    page: int
    page_size: int
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> PaymentsPage:
        d = _as_dict(data)
        return cls(
            items=[OrderRecord.from_dict(r) for r in _as_list(d.get("list"))],
            total=int(d.get("total") or 0),
            page=int(d.get("page") or 1),
            page_size=int(d.get("page_size") or len(_as_list(d.get("list")))),
            raw=d,
        )
