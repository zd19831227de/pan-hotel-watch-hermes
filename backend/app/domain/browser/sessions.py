from __future__ import annotations

import json
import re
import os
import tempfile
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.settings import get_settings

ConnectionMode = Literal["direct", "non_direct", "unknown"]

# Module-level reentrant lock serializing the read-modify-persist cycle of
# browser session state. BrowserSessionStore instances are normally accessed
# via the module singleton (get_browser_session_store), but worker/request
# threads and tests may construct independent instances pointing at the same
# JSON file. A single shared lock plus a disk re-read before every mutation
# prevents lost updates across instances within one process; the atomic
# os.replace write protects on-disk integrity regardless of caller.
_SESSION_STATE_LOCK = threading.RLock()
_SESSION_STATE_LOCK_DEPTH = threading.local()


@contextmanager
def _session_state_lock() -> Iterator[None]:
    """Serialize one complete reload-mutate-save cycle across threads and processes."""
    with _SESSION_STATE_LOCK:
        depth = int(getattr(_SESSION_STATE_LOCK_DEPTH, "value", 0))
        if depth:
            _SESSION_STATE_LOCK_DEPTH.value = depth + 1
            try:
                yield
            finally:
                _SESSION_STATE_LOCK_DEPTH.value = depth
            return

        state_path = _session_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = state_path.with_suffix(f"{state_path.suffix}.lock")
        with lock_path.open("a+b") as handle:
            _acquire_session_file_lock(handle)
            _SESSION_STATE_LOCK_DEPTH.value = 1
            try:
                yield
            finally:
                _SESSION_STATE_LOCK_DEPTH.value = 0
                _release_session_file_lock(handle)


def _acquire_session_file_lock(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_session_file_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_CHAT_URL_RE = __import__("re").compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _validate_chat_url(*, intended_use: str, chat_url: str | None) -> str | None:
    """chat_url 仅 llm_chat 会话可配置，且必须是 http(s) 链接；空值表示用平台默认。"""
    cleaned = _clean_optional_text(chat_url)
    if cleaned is None:
        return None
    if str(intended_use).strip() != "llm_chat":
        raise ValueError("chat_url 仅允许配置在 llm_chat 浏览器会话上。")
    if not _CHAT_URL_RE.match(cleaned):
        raise ValueError("chat_url 必须是 http(s) 链接。")
    return cleaned


class BrowserSession(BaseModel):
    browser_session_id: str
    platform_account_id: str
    display_name: str = ""
    platform: str = "ctrip"
    # 店铺归属（重构 P2）：EBK 账号必填；competitor_price/llm_chat 账号保持全局（None）。
    # 旧 JSON 无此字段按 None 读（未分组），不回填猜测。
    shop_key: str | None = None
    session_port: int
    intended_use: str = "ebk"
    connection_mode: ConnectionMode = "unknown"
    chat_url: str | None = None
    can_auto_write_price: bool = False
    pricing_feishu_chat_id: str | None = None
    pricing_feishu_chat_id_configured: bool = False
    pricing_feishu_chat_id_masked: str | None = None
    login_state: str = "needs_check"
    human_verification_status: str = "required"
    last_heartbeat_at: str
    takeover_url: str
    can_takeover: bool = True
    can_logout: bool = False
    current_operator: str | None = None
    last_action: str
    human_required_reason: str | None = "login_state_check_required"
    risk_flags: list[str] = Field(default_factory=lambda: ["login_check"])
    manual_takeover_url: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("intended_use", mode="before")
    @classmethod
    def normalize_intended_use(cls, value: Any) -> str:
        return normalize_browser_intended_use(value)

    @field_validator("connection_mode", mode="before")
    @classmethod
    def normalize_connection_mode_field(cls, value: Any) -> str:
        return normalize_connection_mode(value)

    @model_validator(mode="after")
    def apply_capability_flags(self) -> "BrowserSession":
        self.can_auto_write_price = can_auto_write_price(self)
        self.pricing_feishu_chat_id = _clean_optional_text(self.pricing_feishu_chat_id)
        self.pricing_feishu_chat_id_configured = bool(self.pricing_feishu_chat_id)
        self.pricing_feishu_chat_id_masked = _mask_secret(self.pricing_feishu_chat_id)
        return self


class BrowserSessionCreate(BaseModel):
    platform_account_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    display_name: str = Field(default="", max_length=120)
    platform: str = Field(
        default="ctrip",
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    intended_use: str = Field(
        default="ebk",
        min_length=1,
        max_length=32,
        pattern=r"^(ebk|competitor_price|llm_chat)$",
    )
    connection_mode: ConnectionMode = "unknown"
    pricing_feishu_chat_id: str | None = Field(default=None, max_length=128)
    chat_url: str | None = Field(default=None, max_length=512)
    shop_key: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^$|^shop_[A-Za-z0-9_-]+$",
    )
    session_port: int = Field(ge=1024, le=65535)
    browser_session_id: str = Field(
        default="",
        max_length=120,
        pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )

    @field_validator("connection_mode", mode="before")
    @classmethod
    def normalize_connection_mode_field(cls, value: Any) -> str:
        return normalize_connection_mode(value)


class BrowserSessionPatch(BaseModel):
    platform_account_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    display_name: str | None = Field(default=None, max_length=120)
    platform: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    session_port: int | None = Field(default=None, ge=1024, le=65535)
    intended_use: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^(ebk|competitor_price|llm_chat)$",
    )
    connection_mode: ConnectionMode | None = None
    chat_url: str | None = Field(default=None, max_length=512)
    pricing_feishu_chat_id: str | None = Field(default=None, max_length=128)
    shop_key: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^$|^shop_[A-Za-z0-9_-]+$",
    )

    @field_validator("connection_mode", mode="before")
    @classmethod
    def normalize_connection_mode_field(cls, value: Any) -> str | None:
        if value is None:
            return None
        return normalize_connection_mode(value)


class BrowserSessionStore:
    def __init__(self) -> None:
        self._sessions = _load_sessions()

    def _reload_from_disk(self) -> None:
        """Refresh the in-memory snapshot from disk so mutations applied on top
        of it include concurrent writes from other store instances/processes."""
        self._sessions = _load_sessions()

    def reset(self) -> None:
        with _session_state_lock():
            _clear_session_state()
            self._sessions = _load_sessions()

    def list(self) -> list[BrowserSession]:
        with _session_state_lock():
            self._reload_from_disk()
            return list(self._sessions.values())

    def get(self, browser_session_id: str) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            try:
                return self._sessions[browser_session_id]
            except KeyError as exc:
                raise KeyError(f"Unknown browser session: {browser_session_id}") from exc

    def restore(self, session: BrowserSession) -> None:
        with _session_state_lock():
            self._reload_from_disk()
            self._sessions[session.browser_session_id] = _normalize_loaded_session_state(
                session.model_copy(deep=True)
            )
            _save_session_state(self._sessions)

    def create(self, request: BrowserSessionCreate) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            return self._create_locked(request)

    def _create_locked(self, request: BrowserSessionCreate) -> BrowserSession:
        browser_session_id = request.browser_session_id.strip() or _default_browser_session_id(
            request.platform_account_id
        )
        if browser_session_id in self._sessions:
            raise ValueError(f"Browser session already exists: {browser_session_id}")
        if self._platform_account_exists(request.platform_account_id):
            raise ValueError(f"Platform account already exists: {request.platform_account_id}")
        if self._session_port_exists(request.session_port):
            raise ValueError(f"Session port already exists: {request.session_port}")
        pricing_feishu_chat_id = _clean_optional_text(request.pricing_feishu_chat_id)
        _validate_pricing_feishu_chat_binding(
            platform=request.platform,
            intended_use=request.intended_use,
            pricing_feishu_chat_id=pricing_feishu_chat_id,
        )
        if pricing_feishu_chat_id and self._pricing_feishu_chat_exists(pricing_feishu_chat_id):
            raise ValueError("Pricing approval Feishu group is already bound to another EBK browser account.")
        chat_url = _validate_chat_url(
            intended_use=request.intended_use, chat_url=request.chat_url
        )
        shop_key = _clean_optional_text(request.shop_key)
        if request.intended_use == "ebk" and shop_key is None:
            raise ValueError("EBK 浏览器账号必须归属一个店铺（shop_key），请先选择或创建店铺。")
        if request.intended_use != "ebk" and shop_key is not None:
            raise ValueError("shop_key 仅允许配置在 EBK 浏览器会话上。")
        session = BrowserSession(
            browser_session_id=browser_session_id,
            platform_account_id=request.platform_account_id.strip(),
            display_name=request.display_name.strip() or request.platform_account_id.strip(),
            platform=request.platform.strip(),
            intended_use=request.intended_use.strip(),
            connection_mode=request.connection_mode,
            chat_url=chat_url,
            pricing_feishu_chat_id=pricing_feishu_chat_id,
            shop_key=shop_key,
            session_port=request.session_port,
            last_heartbeat_at=_now(),
            takeover_url=f"/browser-control?session={browser_session_id}",
            manual_takeover_url=f"/browser-control?session={browser_session_id}&mode=manual",
            can_logout=False,
            last_action="Operator added this browser account; login is required before reads.",
            human_required_reason="login_required",
            risk_flags=["logged_out"],
            login_state="logged_out",
            evidence={"state_source": "operator_configured", "profile_path_returned": False},
        )
        self._sessions[browser_session_id] = session
        _save_session_state(self._sessions)
        return session

    def update(self, browser_session_id: str, patch: BrowserSessionPatch) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            return self._update_locked(browser_session_id, patch)

    def _update_locked(self, browser_session_id: str, patch: BrowserSessionPatch) -> BrowserSession:
        session = self.get(browser_session_id)
        update = patch.model_dump(exclude_unset=True)
        next_platform = str(update.get("platform", session.platform)).strip()
        next_intended_use = str(update.get("intended_use", session.intended_use)).strip()
        next_pricing_feishu_chat_id = _clean_optional_text(
            update.get("pricing_feishu_chat_id", session.pricing_feishu_chat_id)
        )
        next_chat_url = _validate_chat_url(
            intended_use=next_intended_use,
            chat_url=update.get("chat_url", session.chat_url),
        )
        # 店铺归属语义（先校验后改动）：EBK 必须归属店铺；非 EBK 保持全局；
        # 用途切走 EBK 时归属自动清空；切换到 EBK 必须同时给店铺。
        next_shop_key = _clean_optional_text(update.get("shop_key", session.shop_key))
        if "intended_use" in update and next_intended_use != "ebk":
            next_shop_key = None
        if next_intended_use != "ebk" and "shop_key" in update and next_shop_key is not None:
            raise ValueError("shop_key 仅允许配置在 EBK 浏览器会话上。")
        if next_intended_use == "ebk" and next_shop_key is None and (
            "shop_key" in update or "intended_use" in update
        ):
            raise ValueError("EBK 浏览器账号必须归属一个店铺（shop_key）。")
        _validate_pricing_feishu_chat_binding(
            platform=next_platform,
            intended_use=next_intended_use,
            pricing_feishu_chat_id=next_pricing_feishu_chat_id,
        )
        if "platform_account_id" in update:
            next_account_id = str(update["platform_account_id"]).strip()
            if (
                next_account_id != session.platform_account_id
                and self._platform_account_exists(next_account_id)
            ):
                raise ValueError(f"Platform account already exists: {next_account_id}")
            session.platform_account_id = next_account_id
        if "display_name" in update:
            session.display_name = str(update["display_name"] or "").strip()
        if "platform" in update:
            session.platform = str(update["platform"]).strip()
        if "session_port" in update:
            next_port = int(update["session_port"])
            if next_port != session.session_port and self._session_port_exists(next_port):
                raise ValueError(f"Session port already exists: {next_port}")
            session.session_port = next_port
        if "intended_use" in update:
            session.intended_use = str(update["intended_use"]).strip()
            if session.intended_use != "ebk":
                session.pricing_feishu_chat_id = None
        if "connection_mode" in update:
            session.connection_mode = normalize_connection_mode(update["connection_mode"])
            session.can_auto_write_price = can_auto_write_price(session)
        if "chat_url" in update or next_intended_use != "llm_chat":
            session.chat_url = next_chat_url
        if "pricing_feishu_chat_id" in update:
            pricing_feishu_chat_id = _clean_optional_text(update["pricing_feishu_chat_id"])
            if pricing_feishu_chat_id and self._pricing_feishu_chat_exists(
                pricing_feishu_chat_id,
                exclude_browser_session_id=session.browser_session_id,
            ):
                raise ValueError(
                    "Pricing approval Feishu group is already bound to another EBK browser account."
                )
            session.pricing_feishu_chat_id = pricing_feishu_chat_id
        session.last_heartbeat_at = _now()
        session.last_action = "Operator updated browser account configuration."
        session.shop_key = next_shop_key
        session = _normalize_loaded_session_state(session)
        self._sessions[browser_session_id] = session
        _save_session_state(self._sessions)
        return session

    def delete(self, browser_session_id: str) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            removed = self.get(browser_session_id)
            self._sessions.pop(browser_session_id)
            _save_session_state(self._sessions)
            return removed

    def request_verification(
        self,
        browser_session_id: str,
        actor_id: str,
        reason: str = "manual_verification_requested",
        risk_flags: list[str] | None = None,
    ) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            _mark_requires_human_action(
                session,
                actor_id=actor_id,
                reason=reason,
                risk_flags=risk_flags or ["manual_verification"],
                action="Human verification requested.",
            )
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def report_abnormal(
        self,
        browser_session_id: str,
        actor_id: str,
        abnormal_type: str,
        detail: str | None = None,
    ) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            reason_map = {
                "captcha": "captcha_required",
                "sms": "sms_verification_required",
                "qr": "qr_verification_required",
                "risk_control": "risk_control_required",
                "login_abnormal": "login_abnormal",
                "page_changed": "page_structure_changed",
            }
            reason = reason_map.get(abnormal_type, "manual_takeover_required")
            session = self.request_verification(
                browser_session_id,
                actor_id,
                reason=reason,
                risk_flags=[abnormal_type],
            )
            session.last_action = detail or f"Abnormal browser state: {abnormal_type}"
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def mark_requires_human_action(
        self,
        browser_session_id: str,
        actor_id: str,
        *,
        reason: str,
        risk_flags: list[str] | None = None,
        action: str | None = None,
    ) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            _mark_requires_human_action(
                session,
                actor_id=actor_id,
                reason=reason,
                risk_flags=risk_flags or [reason],
                action=action or "Real browser read requires manual login or verification.",
            )
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def mark_login_window_opened(
        self,
        browser_session_id: str,
        actor_id: str,
        *,
        action: str | None = None,
    ) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            session.login_state = "needs_check"
            session.human_verification_status = "required"
            session.human_required_reason = "login_state_check_required"
            session.risk_flags = ["login_check"]
            session.can_takeover = True
            session.last_action = action or f"Login window opened by {actor_id}."
            session.last_heartbeat_at = _now()
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def mark_authenticated(
        self,
        browser_session_id: str,
        actor_id: str,
        *,
        action: str | None = None,
    ) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            session.login_state = "logged_in"
            session.human_verification_status = "none"
            session.human_required_reason = None
            session.risk_flags = []
            session.can_takeover = True
            session.can_logout = True
            session.last_action = action or f"Real browser read confirmed login state by {actor_id}."
            session.last_heartbeat_at = _now()
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def takeover(self, browser_session_id: str, actor_id: str) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            session.current_operator = actor_id
            session.can_takeover = False
            session.last_action = f"Manual takeover granted to {actor_id}."
            session.last_heartbeat_at = _now()
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def logout(self, browser_session_id: str, actor_id: str) -> BrowserSession:
        with _session_state_lock():
            self._reload_from_disk()
            session = self.get(browser_session_id)
            session.login_state = "logged_out"
            session.human_verification_status = "none"
            session.current_operator = None
            session.can_takeover = True
            session.can_logout = False
            session.human_required_reason = "login_required"
            session.risk_flags = ["logged_out"]
            session.last_action = f"Session logged out by {actor_id}; manual login is required."
            session.last_heartbeat_at = _now()
            self._sessions[browser_session_id] = session
            _save_session_state(self._sessions)
            return session

    def _platform_account_exists(self, platform_account_id: str) -> bool:
        normalized = platform_account_id.strip()
        return any(session.platform_account_id == normalized for session in self._sessions.values())

    def _session_port_exists(self, session_port: int) -> bool:
        return any(session.session_port == session_port for session in self._sessions.values())

    def _pricing_feishu_chat_exists(
        self,
        pricing_feishu_chat_id: str,
        *,
        exclude_browser_session_id: str | None = None,
    ) -> bool:
        normalized = pricing_feishu_chat_id.strip()
        return any(
            session.browser_session_id != exclude_browser_session_id
            and session.platform == "ctrip"
            and session.intended_use == "ebk"
            and _clean_optional_text(session.pricing_feishu_chat_id) == normalized
            for session in self._sessions.values()
        )


def get_browser_session_store() -> BrowserSessionStore:
    return _BROWSER_SESSION_STORE


def _validate_pricing_feishu_chat_binding(
    *,
    platform: str,
    intended_use: str,
    pricing_feishu_chat_id: str | None,
) -> None:
    if not pricing_feishu_chat_id:
        return
    # V9 Step 4 — 绑定放开到三平台 EBK 账号；写价放行仍由平台白名单拦截。
    if str(platform or "").strip() not in (
        "ctrip",
        "meituan",
        "fliggy",
    ) or str(intended_use or "").strip() != "ebk":
        raise ValueError("调价飞书群只能绑定携程/美团/飞猪 EBK 浏览器账号。")


def reset_browser_session_store() -> None:
    _BROWSER_SESSION_STORE.reset()


def get_browser_session_id_for_platform_account(platform_account_id: str | None) -> str | None:
    if not platform_account_id:
        return None
    for session in _BROWSER_SESSION_STORE.list():
        if session.platform_account_id == platform_account_id:
            return session.browser_session_id
    return None


def get_browser_session_for_platform_account(platform_account_id: str | None) -> BrowserSession | None:
    if not platform_account_id:
        return None
    for session in _BROWSER_SESSION_STORE.list():
        if session.platform_account_id == platform_account_id:
            return session
    return None


def get_pricing_browser_session_for_feishu_chat_id(
    chat_id: str | None,
) -> BrowserSession | None:
    chat_id = _clean_optional_text(chat_id)
    if not chat_id:
        return None
    # V9 Step 4 — 手动调价账号解析放开到三平台 EBK 绑定；
    # 写价是否放行仍由 can_auto_write_price 平台白名单逐阶段拦截。
    matches = [
        session
        for session in _BROWSER_SESSION_STORE.list()
        if session.platform in ("ctrip", "meituan", "fliggy")
        and session.intended_use == "ebk"
        and _clean_optional_text(session.pricing_feishu_chat_id) == chat_id
    ]
    if len(matches) > 1:
        raise ValueError("Pricing approval Feishu group is bound to multiple EBK browser accounts.")
    return matches[0] if matches else None


# V9 Step 0 — 平台写价能力白名单：某平台的写价链经真实账号联机验证上线后才加入。
# B8（2026-08-24）开放美团/飞猪：前置 B1-B6 全部落地，B7-1 联机 dry-run 与
# B7-2 产品选定核验通过并留档（docs/pan-hotel-watch-b7-live-dry-run-verification-
# 2026-08-24.md）。B7-3 监督执行仍为上线后首次真实执行的前置门禁。
WRITE_CAPABLE_PLATFORMS: tuple[str, ...] = ("ctrip", "meituan", "fliggy")


def can_auto_write_price(account: Any) -> bool:
    platform = str(getattr(account, "platform", "ctrip") or "ctrip").strip()
    return platform in WRITE_CAPABLE_PLATFORMS and normalize_connection_mode(
        getattr(account, "connection_mode", "unknown")
    ) == "non_direct"


def write_policy_message_for_account(account: Any | None) -> str:
    mode = normalize_connection_mode(getattr(account, "connection_mode", "unknown"))
    if mode == "non_direct":
        return "非直连账号可在 Feishu 审批通过后继续调价流程。"
    if mode == "direct":
        return "直连账号仅生成调价建议，不会自动改价。"
    return "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。"


def account_write_policy(account: Any | None) -> dict[str, Any]:
    mode = normalize_connection_mode(getattr(account, "connection_mode", "unknown"))
    return {
        "connection_mode": mode,
        "can_auto_write_price": bool(account is not None and can_auto_write_price(account)),
        "write_policy_message": write_policy_message_for_account(account),
    }


def public_browser_session_payload(session: BrowserSession) -> dict[str, Any]:
    payload = session.model_dump(mode="json")
    payload.pop("pricing_feishu_chat_id", None)
    payload["pricing_feishu_chat_id_configured"] = bool(session.pricing_feishu_chat_id)
    payload["pricing_feishu_chat_id_masked"] = _mask_secret(session.pricing_feishu_chat_id)
    return payload


def _load_sessions() -> dict[str, BrowserSession]:
    sessions: dict[str, BrowserSession] = {}
    default_session = _configured_default_session()
    if default_session:
        sessions[default_session.browser_session_id] = default_session

    state_path = _session_state_path()
    if not state_path.exists():
        return sessions
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return sessions
    raw_sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(raw_sessions, dict):
        return sessions
    for session_id, raw_session in raw_sessions.items():
        if not isinstance(session_id, str) or not isinstance(raw_session, dict):
            continue
        try:
            session = BrowserSession.model_validate(
                _coerce_loaded_session_payload(session_id, raw_session)
            )
        except ValueError:
            continue
        sessions[session_id] = _normalize_loaded_session_state(session)
    return sessions


def _configured_default_session() -> BrowserSession | None:
    settings = get_settings()
    account_id = settings.ctrip_ebooking_default_platform_account_id.strip()
    session_id = settings.ctrip_ebooking_default_browser_session_id.strip()
    if not account_id or not session_id:
        return None
    return BrowserSession(
        browser_session_id=session_id,
        platform_account_id=account_id,
        display_name=settings.ctrip_ebooking_default_display_name.strip() or account_id,
        platform="ctrip",
        session_port=settings.ctrip_ebooking_default_session_port,
            intended_use="ebk",
        last_heartbeat_at=_now(),
        takeover_url=f"/browser-control?session={session_id}",
        manual_takeover_url=f"/browser-control?session={session_id}&mode=manual",
        can_logout=True,
        last_action="Configured Ctrip EBK browser session; run a real read to confirm login.",
        evidence={"state_source": "environment", "profile_path_returned": False},
    )


def _coerce_loaded_session_payload(session_id: str, raw_session: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_session)
    platform_account_id = str(payload.get("platform_account_id") or session_id)
    payload.setdefault("browser_session_id", session_id)
    payload.setdefault("platform_account_id", platform_account_id)
    payload.setdefault("display_name", platform_account_id)
    payload.setdefault("platform", "ctrip")
    payload.setdefault("intended_use", "ebk")
    payload.setdefault("connection_mode", "unknown")
    payload.setdefault("pricing_feishu_chat_id", None)
    payload.setdefault("shop_key", None)
    payload["can_auto_write_price"] = can_auto_write_price_from_mode(payload.get("connection_mode"))
    payload.setdefault("last_heartbeat_at", _now())
    payload.setdefault("takeover_url", f"/browser-control?session={session_id}")
    payload.setdefault("manual_takeover_url", f"/browser-control?session={session_id}&mode=manual")
    payload.setdefault("session_port", get_settings().ctrip_ebooking_default_session_port)
    payload.setdefault("last_action", "Loaded browser session state.")
    payload.setdefault("risk_flags", [])
    payload.setdefault("evidence", {})
    return payload


def _normalize_loaded_session_state(session: BrowserSession) -> BrowserSession:
    session.intended_use = normalize_browser_intended_use(session.intended_use)
    session.connection_mode = normalize_connection_mode(session.connection_mode)
    session.can_auto_write_price = can_auto_write_price(session)
    session.pricing_feishu_chat_id = _clean_optional_text(session.pricing_feishu_chat_id)
    if session.intended_use != "ebk":
        session.pricing_feishu_chat_id = None
    session.pricing_feishu_chat_id_configured = bool(session.pricing_feishu_chat_id)
    session.pricing_feishu_chat_id_masked = _mask_secret(session.pricing_feishu_chat_id)
    if not session.manual_takeover_url:
        session.manual_takeover_url = f"/browser-control?session={session.browser_session_id}&mode=manual"
    if not session.takeover_url:
        session.takeover_url = f"/browser-control?session={session.browser_session_id}"
    session.evidence.pop("persistent_profile_dir", None)
    session.evidence["profile_path_returned"] = False
    return session


def normalize_browser_intended_use(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"ebk", "ebk_data_center"}:
        return "ebk"
    if text in {"competitor_price", "competitor", "public_competitor"}:
        return "competitor_price"
    if text in {"llm_chat", "llm-chat", "llmchat", "chat"}:
        return "llm_chat"
    return text


def normalize_connection_mode(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"direct", "non_direct", "unknown"}:
        return text
    if text in {"non-direct", "nondirect"}:
        return "non_direct"
    if not text:
        return "unknown"
    return text


def can_auto_write_price_from_mode(value: Any) -> bool:
    return normalize_connection_mode(value) == "non_direct"


def _mark_requires_human_action(
    session: BrowserSession,
    *,
    actor_id: str,
    reason: str,
    risk_flags: list[str],
    action: str,
) -> None:
    session.login_state = "human_required"
    session.human_verification_status = "required"
    session.human_required_reason = reason
    session.risk_flags = list(dict.fromkeys(risk_flags))
    session.can_takeover = True
    session.last_action = f"{action} ({actor_id})"
    session.last_heartbeat_at = _now()


def _save_session_state(sessions: dict[str, BrowserSession]) -> None:
    state_path = _session_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessions": {
            session_id: session.model_dump(mode="json") for session_id, session in sessions.items()
        }
    }
    _atomic_write_json(state_path, payload)


def _atomic_write_json(state_path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically: serialize to a temp file in the same directory
    (via Path.write_text so failures surface the same way as the legacy write),
    then os.replace onto the target so a crash or write failure never leaves a
    half-written or corrupt browser_sessions.json behind."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=state_path.parent,
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, state_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _clear_session_state() -> None:
    state_path = _session_state_path()
    if state_path.exists():
        state_path.unlink()


def _session_state_path() -> Path:
    return Path(get_settings().browser_state_dir) / "browser_sessions.json"


def _default_browser_session_id(platform_account_id: str) -> str:
    return f"browser-{platform_account_id.strip()}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


_BROWSER_SESSION_STORE = BrowserSessionStore()
