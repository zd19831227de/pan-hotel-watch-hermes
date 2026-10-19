from __future__ import annotations

from typing import Any

from client.feishu_cards import attach_feishu_card


def enrich_weekly_briefing(payload: dict[str, Any]) -> dict[str, Any]:
    return attach_feishu_card(payload, "weekly_briefing")
