from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.browser.sessions import BrowserSession, _load_sessions, get_browser_session_store
from app.domain.competitor_prices.types import DEFAULT_WATCH_GROUP_KEY


@dataclass(frozen=True)
class CompetitorBrowserSelection:
    platform_account_id: str
    browser_session_id: str
    requested_platform_account_id: str
    requested_browser_session_id: str | None
    source: str
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY
    pool_enabled: bool = False
    pool_size: int = 1
    usable_pool_size: int = 1
    selected_index: int = 0
    rotation_mode: str = "fixed"
    pool_member_id: int | None = None
    lease_owner: str | None = None
    extra_evidence: dict[str, Any] = field(default_factory=dict)

    def evidence(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "watch_group_key": self.watch_group_key,
            "platform_account_id": self.platform_account_id,
            "browser_session_id": self.browser_session_id,
            "requested_platform_account_id": self.requested_platform_account_id,
            "requested_browser_session_id": self.requested_browser_session_id,
            "source": self.source,
            "pool_enabled": self.pool_enabled,
            "pool_size": self.pool_size,
            "usable_pool_size": self.usable_pool_size,
            "selected_index": self.selected_index,
            "rotation_mode": self.rotation_mode,
            "rotation_enabled": self.pool_enabled,
        }
        if self.pool_member_id is not None:
            payload["browser_pool_member_id"] = self.pool_member_id
        if self.lease_owner:
            payload["lease_owner"] = self.lease_owner
        payload.update(self.extra_evidence)
        return payload


@dataclass(frozen=True)
class CompetitorBrowserBindingResolution:
    status: str
    summary: str
    selection: CompetitorBrowserSelection | None = None
    next_action: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.selection is not None


def validate_competitor_browser_session_config(browser_session_id: str) -> BrowserSession:
    normalized = str(browser_session_id or "").strip()
    if not normalized:
        raise ValueError("请选择一个携程浏览器账号。")
    try:
        session = _fresh_browser_session_store().get(normalized)
    except KeyError as exc:
        raise ValueError(f"浏览器账号不存在：{normalized}") from exc
    if session.platform != "ctrip":
        raise ValueError("竞品监控组只能绑定携程浏览器账号。")
    if session.intended_use != "competitor_price":
        raise ValueError("竞品监控组只能绑定用途为竞品价格的浏览器账号。")
    return session


def _fresh_browser_session_store():
    store = get_browser_session_store()
    # Scheduler workers are long-lived; browser accounts can be edited by the API process.
    store._sessions = _load_sessions()
    return store


def resolve_competitor_browser_binding(
    *,
    browser_session_id: str | None,
    requested_platform_account_id: str | None = None,
    requested_browser_session_id: str | None = None,
    watch_group_key: str = DEFAULT_WATCH_GROUP_KEY,
    source: str = "watch_group_binding",
) -> CompetitorBrowserBindingResolution:
    normalized_session_id = str(browser_session_id or "").strip()
    requested_account = str(requested_platform_account_id or "").strip()
    if not normalized_session_id:
        return CompetitorBrowserBindingResolution(
            status="config_required",
            summary="请先为竞品监控组绑定一个携程竞品账号。",
            next_action="在账号与浏览器区域新增或选择用途为竞品价格的账号，然后保存到当前竞品监控组。",
            evidence={
                "watch_group_key": watch_group_key,
                "browser_session_id_configured": False,
            },
        )
    try:
        session = validate_competitor_browser_session_config(normalized_session_id)
    except ValueError as exc:
        return CompetitorBrowserBindingResolution(
            status="config_required",
            summary=str(exc),
            next_action="重新选择一个已配置的竞品价格浏览器账号。",
            evidence={"watch_group_key": watch_group_key, "browser_session_id": normalized_session_id},
        )
    stale_login_state = session.login_state in {"human_required", "logged_out"}
    selection = CompetitorBrowserSelection(
        platform_account_id=session.platform_account_id,
        browser_session_id=session.browser_session_id,
        requested_platform_account_id=requested_account or session.platform_account_id,
        requested_browser_session_id=requested_browser_session_id,
        source=source,
        watch_group_key=watch_group_key,
    )
    summary = "已绑定竞品浏览器账号，可以开始只读读取。"
    next_action = None
    evidence = {
        **_session_evidence(session, watch_group_key=watch_group_key),
        "browser_selection": selection.evidence(),
    }
    if stale_login_state:
        summary = (
            "绑定的竞品浏览器账号上次读取未确认登录状态，系统将自动重试只读读取。"
        )
        next_action = (
            "若自动重试仍失败，请打开登录窗口处理登录、验证码、短信、二维码或风控。"
        )
        evidence["login_state_retry_deferred"] = True
    return CompetitorBrowserBindingResolution(
        status="success",
        summary=summary,
        selection=selection,
        next_action=next_action,
        evidence=evidence,
    )


async def resolve_competitor_browser_binding_for_group(
    *,
    group: dict[str, Any] | None,
    actor_id: str,
) -> CompetitorBrowserBindingResolution:
    if not isinstance(group, dict):
        return CompetitorBrowserBindingResolution(
            status="config_required",
            summary="Competitor watch group is not configured.",
            next_action="Configure the competitor watch group first.",
            evidence={"watch_group_configured": False},
        )
    watch_group_key = str(group.get("group_key") or DEFAULT_WATCH_GROUP_KEY)
    rotation_mode = str(group.get("browser_rotation_mode") or "fixed").strip()
    if rotation_mode != "pool_round_robin":
        return resolve_competitor_browser_binding(
            browser_session_id=group.get("browser_session_id"),
            requested_platform_account_id=group.get("platform_account_id"),
            requested_browser_session_id=group.get("browser_session_id"),
            watch_group_key=watch_group_key,
            source="watch_group_binding",
        )

    from app.domain.competitor_prices.browser_pool import select_competitor_browser_from_pool

    pool_result = await select_competitor_browser_from_pool(
        watch_group_key=watch_group_key,
        lease_owner=actor_id,
    )
    if not pool_result.ok:
        return CompetitorBrowserBindingResolution(
            status=pool_result.status,
            summary=pool_result.summary,
            next_action=pool_result.next_action,
            evidence=pool_result.evidence(),
        )
    selection = CompetitorBrowserSelection(
        platform_account_id=str(pool_result.platform_account_id or ""),
        browser_session_id=str(pool_result.browser_session_id or ""),
        requested_platform_account_id=str(group.get("platform_account_id") or ""),
        requested_browser_session_id=None,
        source="watch_group_pool",
        watch_group_key=watch_group_key,
        pool_enabled=True,
        pool_size=pool_result.pool_size,
        usable_pool_size=pool_result.usable_pool_size,
        selected_index=pool_result.selected_index,
        rotation_mode="pool_round_robin",
        pool_member_id=pool_result.member_id,
        lease_owner=actor_id,
        extra_evidence={
            key: value
            for key, value in pool_result.evidence().items()
            if key
            not in {
                "watch_group_key",
                "platform_account_id",
                "browser_session_id",
                "source",
                "pool_enabled",
                "pool_size",
                "usable_pool_size",
                "selected_index",
                "rotation_mode",
                "lease_owner",
                "browser_pool_member_id",
            }
        },
    )
    return CompetitorBrowserBindingResolution(
        status="success",
        summary=pool_result.summary,
        selection=selection,
        evidence={"browser_selection": selection.evidence(), **pool_result.evidence()},
    )


def _session_evidence(session: BrowserSession, *, watch_group_key: str) -> dict[str, Any]:
    return {
        "watch_group_key": watch_group_key,
        "browser_session_id": session.browser_session_id,
        "platform_account_id": session.platform_account_id,
        "platform": session.platform,
        "intended_use": session.intended_use,
        "login_state": session.login_state,
        "human_verification_status": session.human_verification_status,
    }
