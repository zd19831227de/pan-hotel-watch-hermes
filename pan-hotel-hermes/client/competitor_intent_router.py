"""Competitor / calendar price-query classifier (trend / alert / latest / calendar).

2026-10-09 起 self-contained：主路由已翻转为通用 agent 模式
（client/domain_intent_router.py，仅剩查房价/手动调价两条安全通道），本
模块不再依赖它，改用自带 token 分类。所有 gate 均为 advisory
（`apply_competitor_card_gate` 是 no-op；两个 is_*_tool_allowed 只在
生产链路之外使用）。生产工具选择由 LLM 通用模式 + pre-LLM 能力简报决定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_MCP_PREFIX = "mcp_pan_hotel_watch_"

COMPETITOR_READ_TOOLS = frozenset(
    {
        "get_competitor_latest_prices",
        "get_competitor_price_trend",
        "analyze_competitor_price_change",
    }
)

CALENDAR_READ_TOOLS = frozenset(
    {
        "get_calendar_price_latest",
        "get_calendar_price_watch_status",
        "run_calendar_price_watch_once",
    }
)

_TREND_TOKENS = (
    "涨幅",
    "跌幅",
    "涨跌",
    "趋势",
    "曲线",
    "波动图",
    "趋势图",
    "历史价格",
    "价格走势",
)
_WINDOW_RE = re.compile(
    r"(?:最近|近)\s*([一二两三四五六七八九十\d]+)\s*天|昨天|今日以来|今天以来"
)
_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_ALERT_TOKENS = (
    "有没有明显变化",
    "要不要关注",
    "是否触发",
    "触发提醒",
    "触发告警",
    "价格变化提醒",
    "有没有变",
    "变了吗",
)
_LATEST_TOKENS = (
    "现在多少钱",
    "现在多少",
    "最新竞品价",
    "最新价格",
    "当前价",
    "现价",
    "最新价",
)
# 竞品对象兜底：提到竞品但无趋势/变化/日历词时按最新落库现价处理
# （对齐旧主路由的语义默认）。
_COMPETITOR_OBJECT_TOKENS = ("竞品", "竞对", "竞店", "对手", "竞争酒店")
_CALENDAR_TOKENS = (
    "日历价",
    "日历价格",
    "未来15天",
    "未来十五天",
    "未来房价",
    "未来价格",
    "calendar price",
)
_OWN_ROOM_SPOT_TOKENS = (
    "房型价格",
    "房型价",
    "本店房价",
    "本店价格",
    "本店多少钱",
)


@dataclass(frozen=True)
class CompetitorIntentDecision:
    intent: str
    allowed_competitor_tools: frozenset[str]
    days: int | None
    reason: str


def plain_mcp_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith(_MCP_PREFIX):
        return name[len(_MCP_PREFIX) :]
    return name


def looks_like_calendar_price_query(text: str) -> bool:
    body = str(text or "")
    return any(token in body for token in _CALENDAR_TOKENS)


def looks_like_own_room_spot_query(text: str) -> bool:
    """Today/own-hotel room-type spot price from persisted competitor latest."""
    body = str(text or "").strip()
    if not body or looks_like_calendar_price_query(body):
        return False
    if any(token in body for token in _OWN_ROOM_SPOT_TOKENS):
        return True
    if "本店" in body and any(token in body for token in ("房价", "价格", "多少钱", "现价")):
        return True
    if "上一轮" in body and any(token in body for token in ("价格", "房价", "房型")):
        return True
    return False


def parse_trend_days(text: str) -> int | None:
    body = str(text or "")
    if "昨天" in body or "最近一天" in body or "近1天" in body or "近一天" in body:
        return 1
    match = _WINDOW_RE.search(body)
    if not match:
        return None
    if match.lastindex is None or match.group(1) is None:
        return 1
    raw = match.group(1)
    if raw.isdigit():
        return max(1, min(7, int(raw)))
    return max(1, min(7, _CN_NUM.get(raw, 1)))


def classify_competitor_query(text: str) -> CompetitorIntentDecision:
    body = str(text or "").strip()
    if not body:
        return CompetitorIntentDecision("unclassified", frozenset(), None, "empty")
    days = parse_trend_days(body)
    if looks_like_calendar_price_query(body):
        return CompetitorIntentDecision("calendar_price", frozenset(), None, "calendar_token")
    # 涨幅/趋势口径优先于变化提醒（「最近一天价格变化涨幅」按趋势处理）。
    if any(token in body for token in _TREND_TOKENS):
        return CompetitorIntentDecision(
            "competitor_trend",
            frozenset({"get_competitor_price_trend"}),
            days if days is not None else 1,
            "trend_token",
        )
    if any(token in body for token in _ALERT_TOKENS):
        return CompetitorIntentDecision(
            "competitor_change_alert",
            frozenset({"analyze_competitor_price_change"}),
            None,
            "alert_token",
        )
    if looks_like_own_room_spot_query(body):
        return CompetitorIntentDecision(
            "competitor_latest",
            frozenset({"get_competitor_latest_prices"}),
            None,
            "own_room_spot",
        )
    if any(token in body for token in _LATEST_TOKENS):
        return CompetitorIntentDecision(
            "competitor_latest",
            frozenset({"get_competitor_latest_prices"}),
            None,
            "latest_token",
        )
    if any(token in body for token in _COMPETITOR_OBJECT_TOKENS):
        return CompetitorIntentDecision(
            "competitor_latest",
            frozenset({"get_competitor_latest_prices"}),
            None,
            "competitor_object_default",
        )
    return CompetitorIntentDecision("unclassified", frozenset(), None, "no_competitor_token")


def is_competitor_tool_allowed(decision: CompetitorIntentDecision, tool_name: str) -> bool:
    plain = plain_mcp_tool_name(tool_name)
    if plain not in COMPETITOR_READ_TOOLS:
        return True
    if decision.intent == "unclassified":
        return True
    if decision.intent == "calendar_price":
        return False
    return plain in decision.allowed_competitor_tools


def is_calendar_tool_allowed(decision: CompetitorIntentDecision, tool_name: str) -> bool:
    plain = plain_mcp_tool_name(tool_name)
    if plain not in CALENDAR_READ_TOOLS:
        return True
    if decision.intent == "unclassified":
        return True
    if decision.intent == "calendar_price":
        return True
    # Spot / trend / alert price questions must not use calendar matrix tools.
    return False


def build_competitor_misroute_message(decision: CompetitorIntentDecision, tool_name: str) -> str:
    plain = plain_mcp_tool_name(tool_name)
    allowed = ", ".join(sorted(decision.allowed_competitor_tools)) or "(none)"
    days_part = f"，days={decision.days}" if decision.days is not None else ""
    return (
        f"竞品意图硬路由：intent={decision.intent}{days_part}。"
        f"当前工具 {plain} 不允许；请改调 {allowed}。"
        "禁止用 get_competitor_latest_prices 回答涨幅/趋势。"
    )


def build_calendar_misroute_message(decision: CompetitorIntentDecision, tool_name: str) -> str:
    plain = plain_mcp_tool_name(tool_name)
    return (
        f"价格意图硬路由：intent={decision.intent}，reason={decision.reason}。"
        f"当前工具 {plain} 不允许。"
        "「今天本店房型价格 / 上一轮房价」请用 get_competitor_latest_prices 读持久化现价；"
        "仅当用户明确说「日历价 / 未来15天」时才可用 get_calendar_price_latest。"
    )


def apply_competitor_card_gate(
    payload: dict[str, Any],
    *,
    tool_name: str,
    user_text: str,
) -> dict[str, Any]:
    """Deprecated compatibility shim; read-only routing is advisory."""
    return payload
