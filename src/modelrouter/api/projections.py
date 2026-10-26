"""平台响应的字段白名单。

平台会把内部统计（分组的 Key 数量、映射数量）和代理经营数据（结算比例、分润）一并返回。
这些对 Agent 框架的终端用户没有用处，默认裁掉；采用白名单而非黑名单，
平台日后新增字段也不会意外透出。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SERIES_FIELDS: tuple[str, ...] = ("value", "label", "default_group_id")
GROUP_FIELDS: tuple[str, ...] = (
    "id",
    "model_series",
    "model_series_label",
    "code",
    "name",
    "multiplier",
    "enabled",
    "is_default",
    "sort_order",
    "description",
)
PAYMENT_USER_FIELDS: tuple[str, ...] = (
    "id",
    "order_no",
    "user_id",
    "username",
    "payment_channel",
    "payment_channel_text",
    "recharge_type",
    "recharge_type_text",
    "amount_cny",
    "credited_usd",
    "credited_image_credits",
    "user_recharge_rate",
    "status",
    "trade_status",
    "subject",
    "channel_trade_no",
    "plan_name_snapshot",
    "duration_days_snapshot",
    "subscription_sale_price_cny",
    "paid_at",
    "created_at",
)


def _pick(obj: Any, fields: Iterable[str]) -> Any:
    if not isinstance(obj, dict):
        return obj
    return {key: obj[key] for key in fields if key in obj}


def project_group(group: Any) -> Any:
    return _pick(group, GROUP_FIELDS)


def _project_groups(groups: Any) -> Any:
    if not isinstance(groups, list):
        return groups
    return [project_group(group) for group in groups]


def project_series(series: Any) -> Any:
    if not isinstance(series, dict):
        return series
    out = _pick(series, SERIES_FIELDS)
    if "groups" in series:
        out["groups"] = _project_groups(series["groups"])
    return out


def project_group_options(data: Any) -> Any:
    """分组选项：保留 series、model_series、groups、groups_by_series 四个键，
    并裁剪每个分组对象的字段。"""
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for key in ("series", "model_series"):
        if key in data:
            value = data[key]
            out[key] = [project_series(s) for s in value] if isinstance(value, list) else value
    if "groups" in data:
        out["groups"] = _project_groups(data["groups"])
    if "groups_by_series" in data:
        by_series = data["groups_by_series"]
        out["groups_by_series"] = (
            {key: _project_groups(value) for key, value in by_series.items()}
            if isinstance(by_series, dict)
            else by_series
        )
    return out


def project_payment(record: Any) -> Any:
    """充值记录的用户可见字段，去掉代理名称、来源站点、结算比例与分润。"""
    return _pick(record, PAYMENT_USER_FIELDS)


def project_payments(data: Any) -> Any:
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        return data
    return {**data, "list": [project_payment(record) for record in data["list"]]}
