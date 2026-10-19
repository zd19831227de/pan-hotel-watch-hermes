"""Shared TypedDict definitions for competitor price structures.

These type the high-frequency dict shapes that flow across the
ctrip public competitor ? reader ? messages ? persistence boundary.
They are NOT runtime-validation models ? they document the expected
shape without changing any serialization behavior.
"""

from __future__ import annotations

from typing import Any, TypedDict


class CompetitorPriceCandidate(TypedDict, total=False):
    amount: int
    currency: str
    display: str
    source_line: str
    candidate_count: int
    confidence: str
    reasons: list[str]


class CompetitorRoomRate(TypedDict, total=False):
    room_type: str
    breakfast: str
    price_amount_yuan: int
    display_price: str
    discount_amount_yuan: int
    discount_display: str
    availability: str
    availability_line: str
    source: str
    source_text: str
    source_room_line: str
    source_breakfast_line: str
    source_discount_line: str
    is_trusted: bool


class CompetitorPriceSnapshot(TypedDict, total=False):
    role: str
    label: str
    hotel_name: str
    status: str
    price: CompetitorPriceCandidate | None
    price_candidate: CompetitorPriceCandidate | None
    room_rates: list[CompetitorRoomRate]
    room_type: str | None
    breakfast: str | None
    availability: str | None
    date_context: dict[str, Any]
    requested_url: str
    current_url: str
    page_title: str
    page_state: str
    screenshot_path: str
    captured_at: str
    parser_confidence: str
    date_verification: dict[str, Any]
    review_reason: str | None
    raw_snippets: list[str]
    clue_lines: dict[str, str | None]


class CompetitorPriceComparison(TypedDict, total=False):
    own_price: int | None
    lowest_price: dict[str, Any] | None
    highest_price: dict[str, Any] | None
    spread: int | None
    own_rank: int | None
    priced_count: int
    breakfast_comparison: dict[str, Any]
    summary_lines: list[str]


class CtripEbookingCommonEvidence(TypedDict, total=False):
    """Shared evidence keys across all Ctrip EBK result types."""
    platform_account_id: str
    browser_session_id: str
    password_stored: bool
    password_returned: bool
    automation_bypass: bool
    read_only: bool
    no_booking_attempted: bool
