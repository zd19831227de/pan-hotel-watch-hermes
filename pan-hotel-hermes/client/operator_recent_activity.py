from __future__ import annotations

from typing import Any

from client.feishu_cards import attach_feishu_card


def enrich_operator_recent_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return attach_feishu_card(payload, "recent_activity")
