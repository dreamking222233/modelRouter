from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .deps import PlatformDep, passthrough
from .schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", summary="注册用户（平台 auth.register）")
async def register(payload: RegisterRequest, platform: PlatformDep) -> JSONResponse:
    """注册平台终端用户。平台不校验邮箱所有权，返回 data.token 为用户 JWT。"""
    return passthrough(await platform.register(payload.to_upstream()))


@router.post("/login", summary="用户登录（平台 auth.login）")
async def login(payload: LoginRequest, platform: PlatformDep) -> JSONResponse:
    """返回 data.token、data.user、data.is_first_login、data.balance（美元余额）。"""
    return passthrough(await platform.login(payload.to_upstream()))
