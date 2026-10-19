from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

TRUSTED_PRICE_SOURCE = "room_rate_dom_display_price"
TRUSTED_SOURCE_STATEMENT = "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议"

PriceChangeAlertStatus = Literal[
    "suggestion_ready",
    "change_detected",
    "no_trigger",
    "data_required",
    "needs_review",
    "config_required",
    "failed",
]


class PriceChangeAlertResult(BaseModel):
    status: PriceChangeAlertStatus
    summary: str
    watch_group_key: str
    connection_mode: str = "unknown"
    can_auto_write_price: bool = False
    write_policy_message: str = "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。"
    current_run_id: int | None = None
    previous_run_id: int | None = None
    changed_count: int = 0
    suggestion_count: int = 0
    compared_key_count: int = 0
    changed_items: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: dict[str, Any] | None = None
    alert_record_id: int | None = None
    next_action: str | None = None


@dataclass(frozen=True)
class ComparableRate:
    snapshot_id: int
    hotel_config_id: int
    hotel_role: str
    hotel_alias: str
    ctrip_hotel_id: str
    hotel_name: str | None
    date_label: str
    checkin_date: str | None
    checkout_date: str | None
    room_type: str | None
    room_type_key: str
    breakfast_kind: str
    breakfast_text: str | None
    price_amount_yuan: int
    availability_status: str
    availability_text: str | None
    is_primary: bool = False

    @property
    def key(self) -> tuple[int, str, str, str]:
        return (
            self.hotel_config_id,
            self.checkin_date or self.date_label,
            self.breakfast_kind,
            self.room_type_key,
        )

    @property
    def own_match_key(self) -> tuple[str, str, str]:
        return (
            self.checkin_date or self.date_label,
            self.breakfast_kind,
            self.room_type_key,
        )

    @property
    def primary_room_scope_key(self) -> tuple[int, str]:
        return (
            self.hotel_config_id,
            self.checkin_date or self.date_label,
        )
