"""V5 Step 2 — cross-profile (cross-hotel) data analytics aggregation.

This module adds the one capability the existing per-group endpoints lack:
mapping every competitor/EBK watch group back to the Feishu profile (hotel)
that owns it, so a supervisor can compare hotels side by side and an operator
is strictly confined to their own hotel.

Permission model (two levels, per AGENTS.md):
  - supervisor: may query ``own`` / ``all`` / a selected set of profiles.
  - operator / viewer (any non-supervisor admitted to a group): ``own`` only.
    Any request that reaches beyond the caller's own profile raises
    :class:`HotelAnalyticsPermissionError` (fail-closed, never empty data).

The caller identity is the Feishu ``chat_id`` (the group the question came
from). Role is resolved via ``resolve_feishu_role_for_chat``; the API route is
itself supervisor-key-gated, so only a trusted caller (Hermes, holding the
supervisor key) reaches this code and passes the user's chat_id through. Raw
chat_ids / webhook URLs are used internally to map groups→profiles but are
never returned; only masked/profile-level identifiers leave this service.
"""

from __future__ import annotations

from typing import Any

from app.domain.competitor_prices.repository import (
    get_latest_competitor_price_snapshot,
    list_competitor_watch_group_summaries,
)
from app.domain.config import repository as config_repository
from app.domain.config.service import resolve_feishu_role_for_chat
from app.domain.ctrip_ebooking_watch.repository import (
    list_data_center_watch_group_summaries,
)

MAX_TRUSTED_HOTELS_PER_GROUP = 8


class HotelAnalyticsPermissionError(ValueError):
    """Caller requested data beyond their permitted scope (→ HTTP 403)."""


class HotelAnalyticsScopeError(ValueError):
    """Caller referenced a profile/scope that cannot be resolved (→ HTTP 422)."""


async def get_hotel_analytics_overview(
    *,
    caller_chat_id: str,
    scope: str | None = None,
    requested_profile_keys: list[str] | None = None,
) -> dict[str, Any]:
    normalized_chat_id = str(caller_chat_id or "").strip()
    if not normalized_chat_id:
        raise HotelAnalyticsScopeError("缺少调用方飞书群 chat_id，无法判定数据范围。")

    role = await resolve_feishu_role_for_chat(normalized_chat_id)
    caller_binding = await config_repository.get_feishu_chat_agent_binding_by_chat_id(
        normalized_chat_id
    )
    caller_profile_key = (
        str(caller_binding.profile_key) if caller_binding is not None else None
    )

    resolved_scope, target_profile_keys = _resolve_target_profiles(
        role=role,
        caller_profile_key=caller_profile_key,
        scope=scope,
        requested_profile_keys=requested_profile_keys,
    )

    profiles = await config_repository.list_feishu_agent_profiles()
    profile_display = {
        str(record.profile_key): (str(record.display_name), bool(record.enabled))
        for record in profiles
    }
    if target_profile_keys is not None:
        unknown = [key for key in target_profile_keys if key not in profile_display]
        if unknown:
            raise HotelAnalyticsScopeError(
                "未找到以下店铺配置：" + "、".join(unknown) + "。"
            )

    # chat_id -> profile_key, built from bindings (raw chat_ids never leave here).
    chat_to_profile: dict[str, str] = {}
    for binding in await config_repository.list_feishu_chat_agent_bindings():
        if not binding.enabled:
            continue
        chat_to_profile[str(binding.chat_id)] = str(binding.profile_key)

    competitor_groups = await list_competitor_watch_group_summaries(include_secrets=True)
    ebk_groups = await list_data_center_watch_group_summaries(include_secrets=True)

    candidate_profile_keys = (
        sorted(profile_display)
        if target_profile_keys is None
        else list(target_profile_keys)
    )
    hotels: list[dict[str, Any]] = []
    for profile_key in candidate_profile_keys:
        display_name, profile_enabled = profile_display.get(
            profile_key, (profile_key, False)
        )
        competitor_for_profile = _groups_for_profile(
            competitor_groups, chat_to_profile, profile_key
        )
        ebk_for_profile = _groups_for_profile(
            ebk_groups, chat_to_profile, profile_key
        )
        hotels.append(
            await _build_hotel_entry(
                profile_key=profile_key,
                display_name=display_name,
                profile_enabled=profile_enabled,
                competitor_groups=competitor_for_profile,
                ebk_groups=ebk_for_profile,
            )
        )

    return {
        "caller_role": role,
        "caller_profile_key": caller_profile_key,
        "scope": resolved_scope,
        "requested_profile_keys": list(requested_profile_keys or []),
        "hotels": hotels,
    }


def _resolve_target_profiles(
    *,
    role: str,
    caller_profile_key: str | None,
    scope: str | None,
    requested_profile_keys: list[str] | None,
) -> tuple[str, list[str] | None]:
    """Return (resolved_scope, target_profile_keys or None=all).

    ``None`` target means "every configured profile". Non-supervisor callers
    are pinned to their own profile; anything broader is a permission error.
    """

    normalized_scope = (scope or "own").strip().lower()
    requested = [key.strip().lower() for key in (requested_profile_keys or []) if key.strip()]

    if role != "supervisor":
        # operator / viewer: own-store only. Any broader intent is denied.
        if normalized_scope == "all" or requested:
            raise HotelAnalyticsPermissionError(
                "权限不足：仅 supervisor 可查询跨店数据，当前账号只能查询本店。"
            )
        if caller_profile_key is None:
            raise HotelAnalyticsScopeError(
                "当前飞书群未绑定酒店 Agent，无法确定本店范围。"
            )
        return "own", [caller_profile_key]

    # supervisor
    if normalized_scope == "all":
        return "all", None
    if normalized_scope == "selected":
        if not requested:
            raise HotelAnalyticsScopeError("selected 范围需要至少一个 profile_key。")
        return "selected", requested
    # default: own
    if caller_profile_key is None:
        raise HotelAnalyticsScopeError(
            "当前 supervisor 群未绑定酒店 Agent，无法确定本店范围；请改用 all 或 selected。"
        )
    return "own", [caller_profile_key]


def _groups_for_profile(
    groups: list[dict[str, Any]],
    chat_to_profile: dict[str, str],
    profile_key: str,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for group in groups:
        chat_id = str(group.get("feishu_chat_id") or "")
        if chat_id and chat_to_profile.get(chat_id) == profile_key:
            matched.append(group)
    return matched


async def _build_hotel_entry(
    *,
    profile_key: str,
    display_name: str,
    profile_enabled: bool,
    competitor_groups: list[dict[str, Any]],
    ebk_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    competitor_summary = [
        await _competitor_group_summary(group) for group in competitor_groups
    ]
    ebk_summary = [_ebk_group_summary(group) for group in ebk_groups]
    has_data = bool(competitor_summary or ebk_summary)
    note: str | None
    if not has_data:
        note = "该店铺尚未绑定竞品/EBK 盯盘组，暂无可汇总数据。"
    elif not profile_enabled:
        note = "该店铺 Agent profile 已停用。"
    else:
        note = None
    return {
        "profile_key": profile_key,
        "display_name": display_name,
        "profile_enabled": profile_enabled,
        "competitor_groups": competitor_summary,
        "ebk_groups": ebk_summary,
        "note": note,
    }


async def _competitor_group_summary(group: dict[str, Any]) -> dict[str, Any]:
    group_key = str(group.get("group_key") or "")
    latest_run = _run_excerpt(group.get("latest_run"))
    summary: dict[str, Any] = {
        "group_key": group_key,
        "name": group.get("name"),
        "enabled_own_hotel_count": group.get("enabled_own_hotel_count", 0),
        "enabled_competitor_hotel_count": group.get("enabled_competitor_hotel_count", 0),
        "latest_run": latest_run,
        "latest_trusted": None,
    }
    if group_key:
        snapshot = await get_latest_competitor_price_snapshot(watch_group_key=group_key)
        summary["latest_trusted"] = _compact_trusted_snapshot(snapshot)
    # Strip any raw chat_id / webhook the source dict carried.
    return _strip_secrets(summary)


def _ebk_group_summary(group: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "group_key": group.get("group_key"),
        "name": group.get("name"),
        "latest_run": _run_excerpt(group.get("latest_run")),
    }
    return _strip_secrets(summary)


def _run_excerpt(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    return {
        "status": run.get("status"),
        "summary": run.get("summary"),
        "captured_at": run.get("captured_at"),
    }


def _compact_trusted_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    hotels: list[dict[str, Any]] = []
    for hotel in snapshot.get("hotels", [])[:MAX_TRUSTED_HOTELS_PER_GROUP]:
        config = hotel.get("hotel_config") if isinstance(hotel.get("hotel_config"), dict) else {}
        prices: list[float] = []
        for snapshot_entry in hotel.get("snapshots", {}).values():
            if not isinstance(snapshot_entry, dict):
                continue
            for rate in snapshot_entry.get("rates", []):
                if not isinstance(rate, dict):
                    continue
                if rate.get("is_trusted"):
                    amount = rate.get("price_amount_yuan")
                    if isinstance(amount, (int, float)):
                        prices.append(float(amount))
        hotels.append(
            {
                "name": config.get("display_name") or config.get("hotel_name"),
                "role": config.get("role"),
                "min_trusted_price_yuan": min(prices) if prices else None,
            }
        )
    return {
        "captured_at": run.get("captured_at"),
        "hotels": hotels,
    }


_SECRET_KEYS = (
    "feishu_chat_id",
    "feishu_webhook_url",
    "feishu_mobile_webhook_url",
    "feishu_desktop_webhook_url",
    "platform_account_id",
    "browser_session_id",
    "pricing_platform_account_id",
    "pricing_browser_session_id",
)


def _strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop raw chat_ids / webhook URLs / account ids from an analytics entry.

    These are used internally to map groups to profiles but must never appear
    in operator-facing analytics output.
    """

    return {key: value for key, value in payload.items() if key not in _SECRET_KEYS}
