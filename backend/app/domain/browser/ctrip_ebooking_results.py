from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.domain.notifications.feishu import FeishuPushResult


class CtripEbookingLoginResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    login_url: str
    account_phone_masked: str | None = None
    account_username_masked: str | None = None
    persistent_profile_configured: bool = True
    manual_login_required: bool
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


class CtripEbookingSnapshotResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: FeishuPushResult | None = None
    next_action: str | None = None


class CtripEbookingDatasetResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    dataset_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: FeishuPushResult | None = None
    next_action: str | None = None


class CtripEbookingMonitorResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: FeishuPushResult | None = None
    next_action: str | None = None


class CtripEbookingCommentMonitorResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: FeishuPushResult | None = None
    next_action: str | None = None


class CtripPublicCompetitorPriceResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    feishu_push: FeishuPushResult | None = None
    next_action: str | None = None


class CtripPricingPageWritePreviewResult(BaseModel):
    status: str
    summary: str
    platform: str = "ctrip"
    platform_account_id: str
    browser_session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


@dataclass(frozen=True)
class _LoginCredentials:
    username: str | None
    password: str | None
    auto_submit: bool
