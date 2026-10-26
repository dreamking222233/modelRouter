from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, Path
from fastapi.responses import JSONResponse

from ..errors import ServiceError
from .deps import PlatformDep, UserAuthDep, passthrough
from .schemas import CreateApiKeyRequest, CreateRechargeOrderRequest

router = APIRouter(prefix="/user", tags=["user"])

# 平台要求：8–128 位可见 ASCII
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7E]{8,128}$")
ORDER_NO_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"


@router.get("/api-keys/group-options", summary="读取模型分组选项")
async def group_options(user_auth: UserAuthDep, platform: PlatformDep) -> JSONResponse:
    """返回 series、model_series、groups、groups_by_series，作为创建 Key 表单的数据来源。"""
    return passthrough(await platform.group_options(user_auth))


@router.post("/api-keys", summary="创建用户模型 API Key")
async def create_api_key(
    payload: CreateApiKeyRequest, user_auth: UserAuthDep, platform: PlatformDep
) -> JSONResponse:
    """创建成功返回模型 API Key 明文，仅展示一次。"""
    return passthrough(await platform.create_api_key(user_auth, payload.to_upstream()))


@router.post("/recharge-orders", summary="创建充值订单")
async def create_recharge_order(
    payload: CreateRechargeOrderRequest,
    user_auth: UserAuthDep,
    platform: PlatformDep,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="8–128 位可见 ASCII，重试时必须复用"),
    ] = None,
) -> JSONResponse:
    """同一业务请求重试时必须复用同一个 Idempotency-Key；参数不一致平台返回 409。"""
    if not idempotency_key:
        raise ServiceError(400, "缺少 Idempotency-Key 请求头")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise ServiceError(400, "Idempotency-Key 必须为 8–128 位可见 ASCII 字符")
    return passthrough(
        await platform.create_recharge_order(user_auth, idempotency_key, payload.to_upstream())
    )


@router.post("/recharge-orders/{order_no}/wait", summary="同步等待支付结果（最长 120 秒）")
async def wait_recharge_order(
    order_no: Annotated[str, Path(pattern=ORDER_NO_PATTERN)],
    user_auth: UserAuthDep,
    platform: PlatformDep,
) -> JSONResponse:
    """平台最多同步等待 120 秒。调用方自身的请求超时需大于本服务的
    PLATFORM_WAIT_TIMEOUT_SECONDS。wait_result=wait_timeout 且 order_status=pending 时，
    可用同一订单号再次等待。"""
    return passthrough(await platform.wait_recharge_order(user_auth, order_no))
