from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..errors import ServiceError
from .deps import PlatformDep, passthrough
from .schemas import PaymentsQuery

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/requests/{request_id}", summary="查询代理范围内的单条请求详情")
async def request_detail(request_id: uuid.UUID, platform: PlatformDep) -> JSONResponse:
    """不存在或不属于当前代理时平台返回 404。"""
    return passthrough(await platform.request_detail(str(request_id)))


@router.get("/payments", summary="查询代理充值订单记录")
async def payments(
    query: Annotated[PaymentsQuery, Query()], platform: PlatformDep
) -> JSONResponse:
    """返回 data.list、total、page、page_size。"""
    if not query.date_range_valid:
        raise ServiceError(400, "start_date 不能晚于 end_date", code="INVALID_DATE_RANGE")
    return passthrough(await platform.payments(query.to_params()))
