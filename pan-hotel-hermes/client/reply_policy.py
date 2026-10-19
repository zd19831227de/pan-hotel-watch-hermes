"""Feishu Q&A reply policy: tool response_mode routing.

Q&A text replies are rendered as Feishu interactive cards by the plugin layer
(`_send_text_as_card` in pan-hotel-feishu-session), not by tool response_mode.
This module only decides whether an MCP tool attaches its own structured
`feishu_card` (detail) or stays on the plain text channel (text) for the LLM to
summarize — the LLM summary is then card-ified by the plugin regardless.
Pricing approval / card-callback tools may emit interactive cards directly.
Since 2026-10-08, `get_competitor_price_trend` with `with_chart=true` may also
emit its chart card (price curve PNG) — the only Q&A chart exception, allowed
in `_with_auto_feishu_card`, not via response_mode.
`PAN_HOTEL_REPLY_POLICY` is ignored if present.
"""

from __future__ import annotations

_TEXT_TOOLS = frozenset(
    {
        "resolve_feishu_chat_context",
        "get_operator_watch_configuration",
        "list_watch_groups",
        "get_operator_hotel_overview",
        "get_operator_analysis_context",
        "get_weekly_operator_briefing",
        "get_ebk_latest_result",
        "read_ebk_datasets",
        "get_operator_recent_activity",
        "get_watch_scheduler_status",
        "run_ebk_watch_once",
        "get_calendar_price_status",
        "get_calendar_price_watch_status",
    }
)

_ACTION_CARD_TOOLS = frozenset(
    {
        "create_manual_pricing_approval",
        "dispatch_watch_feishu_event",
    }
)


def is_reply_policy_v2() -> bool:
    """Always true: text-first is the only Feishu Q&A reply policy."""
    return True


def default_reply_mode_for_tool(tool_name: str) -> str:
    """Return text | action_card."""
    name = str(tool_name or "").strip()
    if name in _ACTION_CARD_TOOLS:
        return "action_card"
    if name in _TEXT_TOOLS:
        return "text"
    # All unlisted read tools also stay on the native text channel.
    return "text"


def resolve_tool_response_mode(
    tool_name: str,
    *,
    requested_mode: str | None = None,
    user_text: str | None = None,
) -> str:
    """Hard policy: Hermes Q&A never sends briefing cards.

    Only pricing approval / Feishu card-callback tools may emit interactive cards.
    Model or user requests for ``response_mode=detail`` on read tools are ignored.
    ``user_text`` is accepted for call-site compatibility and intentionally unused.
    """
    del user_text  # Q&A cards are banned regardless of phrasing.
    if default_reply_mode_for_tool(tool_name) == "action_card":
        return "detail"
    # Keep requested_mode only as a no-op signal for callers/tests; always text for Q&A.
    _ = str(requested_mode or "").strip().lower()
    return "text"
