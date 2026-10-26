from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..errors import ServiceError
from .deps import PlatformDep, passthrough
from .projections import project_payments
from .schemas import PaymentsQuery

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/requests/{request_id}", summary="查询代理范围内的单条请求详情")
async def request_detail(request_id: uuid.UUID, platform: PlatformDep) -> JSONResponse:
    """不存在或不属于当前代理时平台返回 404。"""
    return passthrough(await platform.request_detail(str(request_id)))


@router.get("/payments", summary="查询代理充值订单记录")
async def payments(
    query: Annotated[PaymentsQuery, Query()],
    platform: PlatformDep,
    view: Annotated[
        Literal["user", "agent"],
        Query(description="user：只含用户可见字段（默认）；agent：含代理名称、结算比例、分润等经营字段"),
    ] = "user",
) -> JSONResponse:
    """返回 data.list、total、page、page_size。默认 view=user 会去掉代理经营字段，
    只有本服务内部管理用途才使用 view=agent，且不要把结果原样下发给终端用户。"""
    if not query.date_range_valid:
        raise ServiceError(400, "INVALID_DATE_RANGE", "start_date 不能晚于 end_date")
    resp = await platform.payments(query.to_params())
    return passthrough(resp, project=project_payments if view == "user" else None)
