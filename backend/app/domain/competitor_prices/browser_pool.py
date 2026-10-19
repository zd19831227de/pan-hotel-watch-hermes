from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.settings import get_settings
from app.domain.browser.sessions import _load_sessions, get_browser_session_store
from app.db.session import ensure_db_ready, session_scope
from app.domain.competitor_prices.models import (
    CtripCompetitorBrowserLease,
    CtripCompetitorWatchGroup,
    CtripCompetitorWatchGroupBrowserPoolMember,
    local_now,
)
from sqlalchemy import delete, select


@dataclass(frozen=True)
class PublicCompetitorCdpDecision:
    enabled: bool
    cdp_url: str
    fallback_enabled: bool
    browser_mode: str
    reason: str
    pool_enabled: bool
    pool_size: int

    def evidence(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "browser_mode": self.browser_mode,
            "cdp_enabled": self.enabled,
            "cdp_fallback_enabled": self.fallback_enabled,
            "browser_pool_enabled": self.pool_enabled,
            "browser_pool_size": self.pool_size,
        }
        if self.enabled:
            payload["cdp_url"] = self.cdp_url
        if self.reason:
            payload["browser_mode_reason"] = self.reason
        return payload


@dataclass(frozen=True)
class CompetitorBrowserPoolSelection:
    status: str
    summary: str
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    member_id: int | None = None
    watch_group_key: str | None = None
    lease_owner: str | None = None
    pool_size: int = 0
    usable_pool_size: int = 0
    selected_index: int = -1
    rotation_mode: str = "pool_round_robin"
    next_action: str | None = None
    evidence_payload: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success" and bool(self.browser_session_id)

    def evidence(self) -> dict[str, Any]:
        payload = {
            "watch_group_key": self.watch_group_key,
            "browser_session_id": self.browser_session_id,
            "platform_account_id": self.platform_account_id,
            "browser_pool_member_id": self.member_id,
            "lease_owner": self.lease_owner,
            "pool_enabled": True,
            "rotation_mode": self.rotation_mode,
            "pool_size": self.pool_size,
            "usable_pool_size": self.usable_pool_size,
            "selected_index": self.selected_index,
            "source": "watch_group_pool",
        }
        if self.evidence_payload:
            payload.update(self.evidence_payload)
        return payload


async def select_competitor_browser_from_pool(
    *,
    watch_group_key: str,
    lease_owner: str,
    lease_seconds: int = 900,
) -> CompetitorBrowserPoolSelection:
    await ensure_db_ready()
    now = local_now()
    async with session_scope() as session:
        await session.execute(
            delete(CtripCompetitorBrowserLease).where(
                CtripCompetitorBrowserLease.lease_expires_at <= now
            )
        )
        group = await session.scalar(
            select(CtripCompetitorWatchGroup).where(
                CtripCompetitorWatchGroup.group_key == watch_group_key
            )
        )
        if group is None:
            return CompetitorBrowserPoolSelection(
                status="config_required",
                summary="Competitor watch group is not configured.",
                watch_group_key=watch_group_key,
                lease_owner=lease_owner,
                next_action="Configure the competitor watch group first.",
            )
        members = list(
            (
                await session.scalars(
                    select(CtripCompetitorWatchGroupBrowserPoolMember)
                    .where(
                        CtripCompetitorWatchGroupBrowserPoolMember.group_id == group.id,
                        CtripCompetitorWatchGroupBrowserPoolMember.enabled.is_(True),
                    )
                    .order_by(
                        CtripCompetitorWatchGroupBrowserPoolMember.sort_order.asc(),
                        CtripCompetitorWatchGroupBrowserPoolMember.id.asc(),
                    )
                )
            ).all()
        )
        if not members:
            return CompetitorBrowserPoolSelection(
                status="config_required",
                summary="Pool mode requires at least one enabled competitor browser account.",
                watch_group_key=watch_group_key,
                lease_owner=lease_owner,
                next_action="Add a logged-in competitor_price browser account to the pool.",
                pool_size=0,
                usable_pool_size=0,
            )
        leased_session_ids = set(
            (
                await session.scalars(
                    select(CtripCompetitorBrowserLease.browser_session_id)
                )
            ).all()
        )
        candidates: list[tuple[int, CtripCompetitorWatchGroupBrowserPoolMember, Any]] = []
        skipped: list[dict[str, Any]] = []
        browser_session_store = _fresh_browser_session_store()
        for index, member in enumerate(members):
            session_state = _browser_session(
                member.browser_session_id,
                store=browser_session_store,
            )
            skip_reason = _pool_member_skip_reason(
                member,
                session_state=session_state,
                leased_session_ids=leased_session_ids,
                now=now,
            )
            if skip_reason:
                skipped.append(
                    {
                        "browser_session_id": member.browser_session_id,
                        "reason": skip_reason,
                    }
                )
                continue
            candidates.append((index, member, session_state))
        if not candidates:
            return CompetitorBrowserPoolSelection(
                status="human_required",
                summary="No usable competitor browser account is available in the pool.",
                watch_group_key=watch_group_key,
                lease_owner=lease_owner,
                next_action="Log in or repair at least one competitor_price browser account.",
                pool_size=len(members),
                usable_pool_size=0,
                evidence_payload={"skipped_members": skipped},
            )
        selected_index, member, session_state = min(
            candidates,
            key=lambda item: (
                item[1].last_selected_at or datetime.min,
                item[1].sort_order,
                item[1].id or 0,
            ),
        )
        lease_expires_at = now + timedelta(seconds=max(30, int(lease_seconds)))
        session.add(
            CtripCompetitorBrowserLease(
                browser_session_id=member.browser_session_id,
                watch_group_key=watch_group_key,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )
        )
        member.last_selected_at = now
        await session.flush()
        return CompetitorBrowserPoolSelection(
            status="success",
            summary="Selected a competitor browser account from the pool.",
            platform_account_id=session_state.platform_account_id,
            browser_session_id=session_state.browser_session_id,
            member_id=member.id,
            watch_group_key=watch_group_key,
            lease_owner=lease_owner,
            pool_size=len(members),
            usable_pool_size=len(candidates),
            selected_index=selected_index,
            evidence_payload={
                "lease_expires_at": lease_expires_at.isoformat(),
                "skipped_members": skipped,
            },
        )


async def record_competitor_browser_pool_run_result(
    *,
    selection: Any,
    status: str,
    trusted_rate_count: int | None = None,
) -> None:
    evidence = selection.evidence() if hasattr(selection, "evidence") else {}
    if not evidence.get("pool_enabled"):
        return
    member_id = evidence.get("browser_pool_member_id")
    if member_id is None:
        return
    await ensure_db_ready()
    now = local_now()
    async with session_scope() as session:
        watch_group_key = str(evidence.get("watch_group_key") or "").strip()
        cooldown_minutes = 60
        if watch_group_key:
            group = await session.scalar(
                select(CtripCompetitorWatchGroup).where(
                    CtripCompetitorWatchGroup.group_key == watch_group_key
                )
            )
            if group is not None:
                try:
                    cooldown_minutes = max(1, min(int(group.pool_cooldown_minutes), 1440))
                except (TypeError, ValueError):
                    cooldown_minutes = 60
        member = await session.scalar(
            select(CtripCompetitorWatchGroupBrowserPoolMember).where(
                CtripCompetitorWatchGroupBrowserPoolMember.id == int(member_id)
            )
        )
        if member is None:
            return
        member.last_status = str(status or "")
        if status == "success":
            member.last_success_at = now
            member.consecutive_failures = 0
            member.cooldown_until = None
        elif status in {"failed", "human_required"}:
            member.last_failure_at = now
            member.consecutive_failures += 1
            member.cooldown_until = now + timedelta(minutes=cooldown_minutes)
        if trusted_rate_count is not None and trusted_rate_count > 0 and status != "success":
            member.cooldown_until = None


async def release_competitor_browser_pool_lease(
    *,
    selection: Any | None = None,
    browser_session_id: str | None = None,
    lease_owner: str | None = None,
) -> None:
    if selection is not None:
        browser_session_id = getattr(selection, "browser_session_id", browser_session_id)
        lease_owner = getattr(selection, "lease_owner", lease_owner)
    if not browser_session_id:
        return
    await ensure_db_ready()
    async with session_scope() as session:
        statement = delete(CtripCompetitorBrowserLease).where(
            CtripCompetitorBrowserLease.browser_session_id == browser_session_id
        )
        if lease_owner:
            statement = statement.where(CtripCompetitorBrowserLease.lease_owner == lease_owner)
        await session.execute(statement)


def _fresh_browser_session_store():
    store = get_browser_session_store()
    store._sessions = _load_sessions()
    return store


def _browser_session(browser_session_id: str, *, store=None):
    try:
        return (store or _fresh_browser_session_store()).get(browser_session_id)
    except KeyError:
        return None


def _pool_member_skip_reason(
    member: CtripCompetitorWatchGroupBrowserPoolMember,
    *,
    session_state: Any,
    leased_session_ids: set[str],
    now: datetime,
) -> str | None:
    if session_state is None:
        return "browser_session_missing"
    if getattr(session_state, "platform", None) != "ctrip":
        return "not_ctrip"
    if getattr(session_state, "intended_use", None) != "competitor_price":
        return "not_competitor_price"
    if member.cooldown_until and member.cooldown_until > now:
        return "cooling_down"
    if member.browser_session_id in leased_session_ids:
        return "leased"
    return None


def public_competitor_cdp_decision(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> PublicCompetitorCdpDecision:
    settings = get_settings()
    fallback_enabled = settings.ctrip_public_competitor_cdp_fallback_enabled
    session_port = _browser_session_port(browser_session_id)
    if settings.ctrip_public_competitor_enable_cdp:
        return PublicCompetitorCdpDecision(
            enabled=True,
            cdp_url=settings.ctrip_public_competitor_cdp_url,
            fallback_enabled=fallback_enabled,
            browser_mode="global_cdp",
            reason="configured_global_cdp",
            pool_enabled=False,
            pool_size=1,
        )
    if session_port is not None:
        return PublicCompetitorCdpDecision(
            enabled=True,
            cdp_url=f"http://127.0.0.1:{session_port}",
            fallback_enabled=fallback_enabled,
            browser_mode="session_port_cdp",
            reason="browser_session_remote_debug_port",
            pool_enabled=False,
            pool_size=1,
        )
    return PublicCompetitorCdpDecision(
        enabled=False,
        cdp_url="",
        fallback_enabled=fallback_enabled,
        browser_mode="persistent_session",
        reason="cdp_disabled",
        pool_enabled=False,
        pool_size=1,
    )


def _browser_session_port(browser_session_id: str) -> int | None:
    from contextlib import suppress

    with suppress(KeyError, ValueError):
        session = get_browser_session_store().get(browser_session_id)
        port = int(session.session_port)
        if 1024 <= port <= 65535:
            return port
    return None


def get_competitor_browser_pool_status() -> dict[str, object]:
    return {
        "pool_enabled": False,
        "pool_size": 0,
        "usable_pool_size": 0,
        "entries": [],
        "summary": "Browser pool is not enabled in Pan Hotel Watch V1; bind one browser session per watch group.",
    }
