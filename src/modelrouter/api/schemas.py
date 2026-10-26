"""对 Agent 框架暴露的请求模型。字段与平台文档保持一致，在本地先做一次校验，
让调用方在请求到达平台之前就拿到明确的 422 错误。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

RechargeRateCode = Literal["a", "b", "c", "d", "e", "f", "g"]
PaymentChannel = Literal["alipay", "wechat"]
OrderStatus = Literal["pending", "paid", "closed", "failed"]
RechargeType = Literal["balance", "image_credit", "subscription"]


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    invite_code: str | None = Field(default=None, max_length=64)

    def to_upstream(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    def to_upstream(self) -> dict[str, Any]:
        return self.model_dump()


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    group_mode: Literal["unified", "special"]
    group_model_series: str | None = Field(default=None, min_length=1, max_length=64)
    group_id: int | None = Field(default=None, ge=1)
    daily_limit_usd: Decimal | None = Field(default=None, ge=0, decimal_places=6)

    @model_validator(mode="after")
    def _check_group(self) -> CreateApiKeyRequest:
        if self.group_mode == "special":
            if self.group_model_series is None or self.group_id is None:
                raise ValueError("group_mode=special 时必须同时提供 group_model_series 和 group_id")
        elif self.group_model_series is not None or self.group_id is not None:
            raise ValueError("group_mode=unified 时不能提供 group_model_series 或 group_id")
        return self

    def to_upstream(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "group_mode": self.group_mode}
        if self.group_mode == "special":
            payload["group_model_series"] = self.group_model_series
            payload["group_id"] = self.group_id
        if self.daily_limit_usd is not None:
            payload["daily_limit_usd"] = float(self.daily_limit_usd)
        return payload


class CreateRechargeOrderRequest(BaseModel):
    amount_cny: Decimal = Field(ge=Decimal("1"), le=Decimal("10000"), decimal_places=2)
    payment_channel: PaymentChannel
    recharge_rate_code: RechargeRateCode | None = None

    def to_upstream(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "amount_cny": float(self.amount_cny),
            "payment_channel": self.payment_channel,
        }
        if self.recharge_rate_code is not None:
            payload["recharge_rate_code"] = self.recharge_rate_code
        return payload


class PaymentsQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    user_id: int | None = Field(default=None, ge=1)
    status: OrderStatus | None = None
    payment_channel: PaymentChannel | None = None
    recharge_type: RechargeType | None = None
    start_date: date | None = None
    end_date: date | None = None

    @property
    def date_range_valid(self) -> bool:
        if self.start_date is None or self.end_date is None:
            return True
        return self.start_date <= self.end_date

    def to_params(self) -> dict[str, str]:
        data = self.model_dump(mode="json", exclude_none=True)
        return {key: str(value) for key, value in data.items()}
