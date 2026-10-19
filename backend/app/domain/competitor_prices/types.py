from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_WATCH_GROUP_KEY = "default_competitor_watch"
DEFAULT_WATCH_GROUP_NAME = "携程竞品价格盯盘"
WATCH_GROUP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
SCREENSHOT_KEYS = {
    "screenshot_path",
    "screenshot_paths",
    "before_screenshot",
    "after_screenshot",
}

TIME_OF_DAY_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
SCHEDULE_MODE_PATTERN = r"^(fixed_slots|last_run_interval)$"
FEISHU_CARD_LAYOUT_PATTERN = r"^(mobile_blocks|desktop_table)$"
PUSH_DATE_SCOPE_PATTERN = r"^(today_only|today_tomorrow)$"
BROWSER_ROTATION_MODE_PATTERN = r"^(fixed|pool_round_robin)$"
DEFAULT_SCHEDULE_MODE = "fixed_slots"
DEFAULT_SCHEDULE_START_TIME = "06:00"
DEFAULT_SCHEDULE_END_TIME = "01:00"
DEFAULT_SCHEDULE_INTERVAL_MINUTES = 120
DEFAULT_FEISHU_CARD_LAYOUT = "mobile_blocks"
DEFAULT_PUSH_DATE_SCOPE = "today_tomorrow"
DEFAULT_BROWSER_ROTATION_MODE = "fixed"
DEFAULT_POOL_COOLDOWN_MINUTES = 60


class CompetitorWatchGroupCreate(BaseModel):
    group_key: str = Field(pattern=WATCH_GROUP_KEY_PATTERN)
    name: str
    brief_title: str | None = Field(default=None, max_length=128)
    price_change_alert_title: str | None = Field(default=None, max_length=128)
    # 可选店铺归属（重构 P5）：竞品组仍是全局对象，shop_key 只作归属/筛选维度。
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    browser_rotation_mode: str | None = Field(default=None, pattern=BROWSER_ROTATION_MODE_PATTERN)
    pool_failover_enabled: bool | None = None
    pool_cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)
    pricing_platform_account_id: str | None = None
    pricing_browser_session_id: str | None = None
    feishu_webhook_url: str | None = None
    feishu_mobile_webhook_url: str | None = None
    feishu_desktop_webhook_url: str | None = None
    feishu_chat_id: str | None = None
    feishu_card_layout: str | None = Field(default=None, pattern=FEISHU_CARD_LAYOUT_PATTERN)
    push_date_scope: str | None = Field(default=None, pattern=PUSH_DATE_SCOPE_PATTERN)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    query_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    schedule_mode: str | None = Field(default=None, pattern=SCHEDULE_MODE_PATTERN)
    schedule_start_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_end_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None


class CompetitorWatchGroupPatch(BaseModel):
    name: str | None = None
    brief_title: str | None = Field(default=None, max_length=128)
    price_change_alert_title: str | None = Field(default=None, max_length=128)
    shop_key: str | None = Field(default=None, pattern=r"^$|^shop_[A-Za-z0-9_-]+$")
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    browser_rotation_mode: str | None = Field(default=None, pattern=BROWSER_ROTATION_MODE_PATTERN)
    pool_failover_enabled: bool | None = None
    pool_cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)
    pricing_platform_account_id: str | None = None
    pricing_browser_session_id: str | None = None
    clear_pricing_binding: bool | None = None
    clear_binding: bool | None = None
    feishu_webhook_url: str | None = None
    feishu_mobile_webhook_url: str | None = None
    feishu_desktop_webhook_url: str | None = None
    feishu_chat_id: str | None = None
    feishu_card_layout: str | None = Field(default=None, pattern=FEISHU_CARD_LAYOUT_PATTERN)
    push_date_scope: str | None = Field(default=None, pattern=PUSH_DATE_SCOPE_PATTERN)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    query_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    schedule_mode: str | None = Field(default=None, pattern=SCHEDULE_MODE_PATTERN)
    schedule_start_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_end_time: str | None = Field(default=None, pattern=TIME_OF_DAY_PATTERN)
    schedule_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    auto_poll_enabled: bool | None = None
    enabled: bool | None = None


class CompetitorHotelConfigIn(BaseModel):
    role: Literal["own", "competitor"]
    alias: str = Field(min_length=1, max_length=128)
    short_alias: str | None = Field(default=None, max_length=32)
    detail_url: str = Field(min_length=1)
    sort_order: int = Field(default=0, ge=0, le=9999)
    enabled: bool = True


class CompetitorHotelConfigPatch(BaseModel):
    role: Literal["own", "competitor"] | None = None
    alias: str | None = Field(default=None, min_length=1, max_length=128)
    short_alias: str | None = Field(default=None, max_length=32)
    hotel_name: str | None = Field(default=None, max_length=255)
    detail_url: str | None = Field(default=None, min_length=1)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    enabled: bool | None = None


class CompetitorBrowserPoolMemberIn(BaseModel):
    browser_session_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=9999)
    weight: int = Field(default=1, ge=1, le=100)


class CompetitorBrowserPoolUpdate(BaseModel):
    members: list[CompetitorBrowserPoolMemberIn] = Field(default_factory=list)


class CompetitorReadRequest(BaseModel):
    actor_id: str = "operator"
    own_hotel_url: str | None = None
    competitor_urls: list[str] = Field(default_factory=list)
    check_dates: list[str] | None = None
    browser_session_id: str | None = None
    push_to_feishu: bool = False


class CompetitorPersistenceResult(BaseModel):
    status: str
    summary: str
    watch_group_key: str
    run_id: int | None = None
    snapshot_count: int = 0
    skipped_snapshot_count: int = 0
    rate_count: int = 0
    trusted_rate_count: int = 0


@dataclass(frozen=True)
class ConfiguredCompetitorTargets:
    own_hotel_url: str
    competitor_urls: list[str]
    own_hotel_alias: str | None = None
    competitor_aliases: list[str] = field(default_factory=list)
