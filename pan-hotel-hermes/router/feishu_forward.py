"""飞书事件转发决策（2026-10-14 全量 LLM 先行）。

文本消息不再按词表直转后端：词表子串匹配会劫持测算/图表追问等问法
（2026-10-09 图表追问、2026-10-14「房型价格提高20元的进单测算」两起生产
实证），且问法无限、排除词补不完。现在仅两类事件直转 pan-hotel-watch 后端：

1. url_verification（飞书 webhook 注册握手）；
2. 卡片回调（card.action.trigger/_v1 或 action 载荷）——审批按钮等，
   审批链路依赖后端接收卡片动作，必须保留直转。

一切 im.message.receive_v1 文本消息都交 Hermes LLM（generic_agent）判断：
LLM 认定是价目表问法或固定格式指令（手动调价/促销调整/金字塔出价）时调
dispatch_watch_feishu_event 转发**运营原话**，后端分类器（backend
pricing_query_command.py / manual_price_command.py 等）仍是确定性终审闸。
"""

from __future__ import annotations

from typing import Any


def event_type(payload: dict[str, Any]) -> str | None:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    value = header.get("event_type")
    return str(value).strip() if value else None


def is_url_verification(payload: dict[str, Any]) -> bool:
    if payload.get("type") == "url_verification":
        return True
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    return header.get("event_type") == "url_verification"


def is_card_action(payload: dict[str, Any]) -> bool:
    event = event_type(payload) or ""
    if event == "card.action.trigger":
        return True
    if isinstance(payload.get("action"), dict):
        return True
    return False


def should_forward_to_watch(payload: dict[str, Any]) -> bool:
    """Return True when the event must be handled by pan-hotel-watch feishu_bot."""
    if is_url_verification(payload):
        return True
    return is_card_action(payload)


def route_target(payload: dict[str, Any]) -> str:
    if should_forward_to_watch(payload):
        return "watch"
    return "hermes"
