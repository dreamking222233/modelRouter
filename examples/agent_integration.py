"""Agent 框架接入示例。

把 modelRouter 的能力封装成 Agent 框架里的一个服务对象：登录或注册、确保有模型 Key、
发起充值并按推荐流程确认支付。可以直接复制进你的框架，按需改名。

运行示例（需要一个在跑的 modelRouter）：
    MR_BASE=http://127.0.0.1:8000 MR_SERVICE_TOKEN=... python examples/agent_integration.py
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from modelrouter_client import (
    ApiError,
    AsyncModelRouterClient,
    AuthenticationError,
    AuthResult,
    RechargeOrder,
    WaitResult,
)


@dataclass
class TopUpOutcome:
    """充值确认结果，供 Agent 对用户作出下一步提示。"""

    paid: bool
    closed: bool
    can_wait_again: bool
    balance: float | None
    message: str


class PlatformAccounts:
    """Agent 框架内的账户与充值服务。

    一个进程只需要一个实例；用户 JWT（有效期 24 小时）由框架自己按用户保存，
    每次操作时传入。
    """

    def __init__(self, base_url: str, service_token: str) -> None:
        self._client = AsyncModelRouterClient(base_url, service_token)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- 账户
    async def sign_in(self, username: str, password: str) -> AuthResult:
        """登录。返回值里的 token 请按用户保存，后续用户级操作都要用。"""
        return await self._client.login(username, password)

    async def sign_in_or_register(
        self, username: str, email: str, password: str, *, invite_code: str | None = None
    ) -> AuthResult:
        """先登录，账号不存在或密码错误时尝试注册。

        平台登录失败返回 401，注册时用户名已存在会返回平台自己的错误码，
        这里原样抛出让上层决定如何提示。
        """
        try:
            return await self._client.login(username, password)
        except AuthenticationError:
            return await self._client.register(username, email, password, invite_code=invite_code)

    # ------------------------------------------------------------ 模型 Key
    async def create_model_key(
        self, token: str, name: str, *, daily_limit_usd: float | None = None
    ) -> str:
        """为用户创建一个统一分组的模型 API Key，返回明文（只此一次）。

        需要指定系列和分组时，先用 group_options() 读取选项，再传 group_mode="special"。
        """
        user = self._client.as_user(token)
        key = await user.create_api_key(name, group_mode="unified", daily_limit_usd=daily_limit_usd)
        return key.key

    # --------------------------------------------------------------- 充值
    async def start_top_up(
        self, token: str, amount_cny: float, channel: str = "wechat"
    ) -> RechargeOrder:
        """创建充值订单。

        返回的 order.payment_target：微信是需要绘制成二维码的 URL，支付宝是收银台链接。
        请把 order.idempotency_key 和 order_no 一起保存，网络异常重试创建时复用。
        """
        user = self._client.as_user(token)
        return await user.create_recharge_order(amount_cny, channel)  # type: ignore[arg-type]

    async def confirm_top_up(self, token: str, order_no: str) -> TopUpOutcome:
        """在用户表示已扫码或已打开收银台之后调用。

        会阻塞最长约 120 秒。平台在未支付时约 87 秒就会主动关单，
        所以不要在刚生成二维码时就调用它。
        """
        user = self._client.as_user(token)
        result: WaitResult = await user.wait_for_payment(order_no)
        if result.paid:
            return TopUpOutcome(True, False, False, result.balance, "支付成功，余额已到账")
        if result.closed:
            return TopUpOutcome(False, True, False, None, "订单已关闭，请重新发起充值")
        if result.can_wait_again:
            return TopUpOutcome(False, False, True, None, "尚未收到支付结果，可继续等待")
        return TopUpOutcome(False, False, False, None, f"支付失败：{result.wait_result}")

    async def top_up_status(self, token: str, order_no: str) -> str:
        """不阻塞地查看订单状态：pending / paid / closed / failed。"""
        user = self._client.as_user(token)
        record = await user.get_recharge_order(order_no)
        return record.status or "unknown"

    async def balance(self, username: str, password: str) -> float | None:
        """平台只有登录接口返回余额；需要刷新余额时重新登录一次。"""
        return (await self._client.login(username, password)).balance


async def _demo() -> None:
    accounts = PlatformAccounts(
        os.environ.get("MR_BASE", "http://127.0.0.1:8000"),
        os.environ["MR_SERVICE_TOKEN"],
    )
    try:
        auth = await accounts.sign_in(os.environ["DEMO_USER"], os.environ["DEMO_PASSWORD"])
        print("登录成功，余额:", auth.balance)
        order = await accounts.start_top_up(auth.token, 1, "wechat")
        print("订单:", order.order_no, "支付目标:", order.payment_target)
        print("订单状态:", await accounts.top_up_status(auth.token, order.order_no))
    except ApiError as exc:
        print("接口错误:", exc.status_code, exc.code, exc.message)
    finally:
        await accounts.aclose()


if __name__ == "__main__":
    asyncio.run(_demo())
