"""Shared types and pure helpers for the review management domain."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

ReviewPlatform = Literal["ctrip", "meituan", "fliggy"]
ReviewCategory = Literal["good", "medium", "bad"]
AutoReplyMode = Literal["off", "semi_auto", "full_auto"]

REVIEW_PLATFORMS: tuple[str, ...] = ("ctrip", "meituan", "fliggy")
REVIEW_CATEGORIES: tuple[str, ...] = ("good", "medium", "bad")
AUTO_REPLY_MODES: tuple[str, ...] = ("off", "semi_auto", "full_auto")
REVIEW_REPLY_STATUSES: tuple[str, ...] = ("pending", "replied", "ignored")

# 半自动/全自动每日定时触发时间（HH:MM），默认 23:00（2026-09-16 需求）。
DEFAULT_AUTO_PROCESS_TIME = "23:00"
AUTO_PROCESS_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
_AUTO_PROCESS_TIME_RE = re.compile(AUTO_PROCESS_TIME_PATTERN)


def normalize_auto_process_time(value: Any) -> str:
    """空值回落默认 23:00；非法值抛 ValueError（调度读取侧兜底为默认）。"""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_AUTO_PROCESS_TIME
    if not _AUTO_PROCESS_TIME_RE.fullmatch(text):
        raise ValueError(f"定时处理时间必须是 HH:MM，例如 {DEFAULT_AUTO_PROCESS_TIME}。")
    return text

# 1-5 分制分类阈值：>=4 好评，>=3 且 <4 中评，<3 差评。
GOOD_SCORE_MIN = 4.0
MEDIUM_SCORE_MIN = 3.0


def classify_review_score(score: float | int | None) -> str:
    """Map a platform 1-5 score onto good/medium/bad categories."""
    if score is None:
        return "medium"
    if score >= GOOD_SCORE_MIN:
        return "good"
    if score >= MEDIUM_SCORE_MIN:
        return "medium"
    return "bad"


class ReviewItemInput(BaseModel):
    """A single review row coming from a platform adapter."""

    review_id: str = Field(min_length=1, max_length=128)
    user_nickname: str | None = Field(default=None, max_length=255)
    score: float | None = Field(default=None, ge=0, le=5)
    content: str | None = None
    review_time: str | None = Field(default=None, max_length=64)
    reply_status: Literal["pending", "replied"] | None = None
    reply_content: str | None = None


class ReplyTemplateInput(BaseModel):
    """Create/update payload for a reply template."""

    id: int | None = None
    platform: ReviewPlatform
    browser_session_id: str | None = Field(default=None, max_length=128)
    category: ReviewCategory
    name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=2000)
    score_range: dict[str, Any] | None = None
    priority: int = Field(default=100, ge=1, le=1000)
    enabled: bool = True
    source: Literal["manual", "imported", "auto"] = "manual"


class ReviewAutoConfigPatch(BaseModel):
    """Partial update for a platform auto-reply config."""

    auto_reply_mode: AutoReplyMode | None = None
    auto_fetch_enabled: bool | None = None
    fetch_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    auto_process_time: str | None = Field(
        default=None, pattern=AUTO_PROCESS_TIME_PATTERN
    )
    approval_chat_id: str | None = Field(default=None, max_length=128)
