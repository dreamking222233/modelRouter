from __future__ import annotations

import re
import time
from typing import Annotated

from fastapi import APIRouter, Header, Path, Request
from fastapi.responses import JSONResponse

from ..concurrency import WaitSlots, run_until_disconnect
from ..errors import ServiceError, envelope
from ..security import read_user_claims
from .deps import PlatformDep, UserAuthDep, passthrough
from .projections import project_group_options, project_payment
from .schemas import CreateApiKeyRequest, CreateRechargeOrderRequest

router = APIRouter(prefix="/user", tags=["user"])

# 平台要求：8–128 位可见 ASCII
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7E]{8,128}$")
ORDER_NO_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
OrderNo = Annotated[str, Path(pattern=ORDER_NO_PATTERN)]
LOOKUP_PAGE_SIZE = 100


@router.get("/api-keys/group-options", summary="读取模型分组选项")
async def group_options(user_auth: UserAuthDep, platform: PlatformDep) -> JSONResponse:
    """返回 series、model_series、groups、groups_by_series，作为创建 Key 表单的数据来源。
    每个分组只保留 id、name、code、model_series、multiplier、enabled、is_default 等表单字段。"""
    return passthrough(await platform.group_options(user_auth), project=project_group_options)


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
        raise ServiceError(400, "IDEMPOTENCY_KEY_MISSING", "缺少 Idempotency-Key 请求头")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise ServiceError(
            400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 必须为 8–128 位可见 ASCII 字符"
        )
    return passthrough(
        await platform.create_recharge_order(user_auth, idempotency_key, payload.to_upstream())
    )


@router.get("/recharge-orders/{order_no}", summary="查询单笔充值订单（非阻塞）")
async def get_recharge_order(
    order_no: OrderNo, user_auth: UserAuthDep, platform: PlatformDep, request: Request
) -> JSONResponse:
    """平台没有查单接口。这里从用户 JWT 读取用户 ID，用代理充值记录按用户筛选后匹配订单号。
    需要平台 Key 具备 agent.recharge_records；返回的是用户可见字段。"""
    claims = read_user_claims(user_auth)
    if claims is None:
        raise ServiceError(401, "USER_TOKEN_UNREADABLE", "无法从用户 JWT 中读取用户 ID")
    if claims.exp is not None and claims.exp <= time.time():
        raise ServiceError(401, "USER_TOKEN_EXPIRED", "用户 JWT 已过期，请重新登录")

    max_pages = request.app.state.settings.order_lookup_max_pages
    for page in range(1, max_pages + 1):
        resp = await platform.payments(
            {"user_id": claims.sub, "page": str(page), "page_size": str(LOOKUP_PAGE_SIZE)}
        )
        if resp.status_code != 200:
            return passthrough(resp)
        data = resp.data if isinstance(resp.data, dict) else {}
        rows = data.get("list") if isinstance(data.get("list"), list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("order_no") == order_no:
                return JSONResponse(
                    status_code=200, content=envelope(200, "success", project_payment(row))
                )
        total = data.get("total")
        exhausted = isinstance(total, int) and page * LOOKUP_PAGE_SIZE >= total
        if len(rows) < LOOKUP_PAGE_SIZE or exhausted:
            break
    raise ServiceError(404, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户")


@router.post("/recharge-orders/{order_no}/wait", summary="同步等待支付结果（最长 120 秒）")
async def wait_recharge_order(
    order_no: OrderNo, user_auth: UserAuthDep, platform: PlatformDep, request: Request
) -> JSONResponse:
    """平台最多同步等待 120 秒，未支付时实测约 87 秒即主动关单并返回 closed。
    调用方自身的请求超时需大于本服务的 PLATFORM_WAIT_TIMEOUT_SECONDS。
    本进程并发超过 WAIT_MAX_CONCURRENCY 时直接返回 429；调用方中途断开则取消对平台的等待。"""
    slots: WaitSlots = request.app.state.wait_slots
    with slots.acquire():
        resp = await run_until_disconnect(
            request, platform.wait_recharge_order(user_auth, order_no)
        )
    return passthrough(resp)
