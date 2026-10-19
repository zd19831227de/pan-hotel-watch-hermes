from __future__ import annotations

from typing import Any

from client.feishu_cards import attach_feishu_card
from client.watch_groups import WATCH_KIND_COMPETITOR, WATCH_KIND_EBK, watch_kind_label


def _session_index(sessions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in sessions:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("browser_session_id") or "").strip()
        if session_id:
            indexed[session_id] = item
    return indexed


def _hotel_label(hotel: dict[str, Any]) -> str:
    for key in ("alias", "hotel_name"):
        text = str(hotel.get(key) or "").strip()
        if text:
            return text
    ctrip_id = hotel.get("ctrip_hotel_id")
    if ctrip_id:
        return f"携程酒店ID {ctrip_id}"
    return "未命名酒店"


def _poll_status_text(*, enabled: Any, auto_poll_enabled: Any) -> str:
    enabled_flag = bool(enabled)
    auto_flag = bool(auto_poll_enabled)
    if enabled_flag and auto_flag:
        return "已启用，自动轮询开启"
    if enabled_flag:
        return "已启用，自动轮询关闭（仅手动读取/推送）"
    return "已停用"


def _format_ebk_section(
    group: dict[str, Any],
    *,
    sessions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    browser_session_id = str(group.get("browser_session_id") or "").strip()
    session = sessions_by_id.get(browser_session_id) or {}
    display_name = str(session.get("display_name") or "").strip()
    datasets = group.get("datasets") if isinstance(group.get("datasets"), list) else []
    enabled_datasets = [
        str(item.get("dataset_label") or item.get("dataset_id"))
        for item in datasets
        if isinstance(item, dict) and item.get("enabled")
    ]
    return {
        "watch_kind": WATCH_KIND_EBK,
        "watch_group_key": group.get("group_key"),
        "group_name": group.get("name"),
        "ebk_account_display_name": display_name or None,
        "platform_account_id": group.get("platform_account_id"),
        "browser_session_id": browser_session_id or None,
        "browser_intended_use": session.get("intended_use"),
        "poll_status": _poll_status_text(
            enabled=group.get("enabled"),
            auto_poll_enabled=group.get("auto_poll_enabled"),
        ),
        "enabled": bool(group.get("enabled")),
        "auto_poll_enabled": bool(group.get("auto_poll_enabled")),
        "last_polled_at": group.get("last_polled_at"),
        "enabled_datasets": enabled_datasets,
        "latest_run": group.get("latest_run") if isinstance(group.get("latest_run"), dict) else None,
    }


def _format_competitor_section(
    group: dict[str, Any],
    hotels: list[dict[str, Any]],
    *,
    sessions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    browser_session_id = str(group.get("browser_session_id") or "").strip()
    session = sessions_by_id.get(browser_session_id) or {}
    enabled_hotels = [item for item in hotels if isinstance(item, dict) and item.get("enabled")]
    own_hotels = [_hotel_label(item) for item in enabled_hotels if item.get("role") == "own"]
    competitor_hotels = [_hotel_label(item) for item in enabled_hotels if item.get("role") == "competitor"]
    browser_rotation_mode = str(group.get("browser_rotation_mode") or "fixed").strip() or "fixed"
    return {
        "watch_kind": WATCH_KIND_COMPETITOR,
        "watch_group_key": group.get("group_key"),
        "group_name": group.get("name"),
        "browser_display_name": str(session.get("display_name") or "").strip() or None,
        "platform_account_id": group.get("platform_account_id"),
        "browser_session_id": browser_session_id or None,
        "browser_rotation_mode": browser_rotation_mode,
        "browser_pool": _safe_browser_pool_summary(group.get("browser_pool")),
        "pricing_browser_session_id": group.get("pricing_browser_session_id"),
        "pricing_account_configured": bool(group.get("pricing_account_configured")),
        "poll_status": _poll_status_text(
            enabled=group.get("enabled"),
            auto_poll_enabled=group.get("auto_poll_enabled"),
        ),
        "enabled": bool(group.get("enabled")),
        "auto_poll_enabled": bool(group.get("auto_poll_enabled")),
        "last_polled_at": group.get("last_polled_at") or group.get("schedule_state", {}).get("last_polled_at")
        if isinstance(group.get("schedule_state"), dict)
        else None,
        "own_hotels": own_hotels,
        "competitor_hotels": competitor_hotels,
        "enabled_own_hotel_count": len(own_hotels),
        "enabled_competitor_hotel_count": len(competitor_hotels),
        "latest_run": group.get("latest_run") if isinstance(group.get("latest_run"), dict) else None,
        "runtime_config_status": group.get("runtime_config_status"),
    }


def _safe_browser_pool_summary(pool: Any) -> dict[str, Any]:
    source = pool if isinstance(pool, dict) else {}
    return {
        "pool_enabled": bool(source.get("pool_enabled")),
        "pool_size": _safe_int(source.get("pool_size")),
        "usable_pool_size": _safe_int(source.get("usable_pool_size")),
        "failover_enabled": bool(source.get("failover_enabled")),
        "cooldown_minutes": _safe_int(source.get("cooldown_minutes")),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _overview_rows(
    ebk_sections: list[dict[str, Any]],
    competitor_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in ebk_sections:
        key = str(section.get("watch_group_key") or "")
        account = section.get("ebk_account_display_name") or section.get("platform_account_id") or "未绑定"
        rows.append({"field": f"EBK 后台 · {key}", "value": str(account)})
        rows.append({"field": f"EBK 轮询 · {key}", "value": str(section.get("poll_status") or "—")})
        datasets = section.get("enabled_datasets") if isinstance(section.get("enabled_datasets"), list) else []
        if datasets:
            rows.append({"field": f"EBK 数据集 · {key}", "value": "、".join(str(item) for item in datasets[:6])})
    for section in competitor_sections:
        key = str(section.get("watch_group_key") or "")
        name = str(section.get("group_name") or key)
        own = section.get("own_hotels") if isinstance(section.get("own_hotels"), list) else []
        comps = section.get("competitor_hotels") if isinstance(section.get("competitor_hotels"), list) else []
        rows.append({"field": f"竞品组 · {name}", "value": f"{key} / {section.get('poll_status') or '—'}"})
        rows.append(
            {
                "field": f"本店（公开页）· {name}",
                "value": "、".join(str(item) for item in own) if own else "未配置",
            }
        )
        rows.append(
            {
                "field": f"竞对酒店 · {name}",
                "value": "、".join(str(item) for item in comps) if comps else "未配置",
            }
        )
    return rows


def build_operator_watch_configuration(
    *,
    ebk_groups: list[dict[str, Any]],
    competitor_groups: list[dict[str, Any]],
    competitor_hotels_by_key: dict[str, list[dict[str, Any]]],
    browser_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    sessions_by_id = _session_index(browser_sessions)
    ebk_sections = [_format_ebk_section(group, sessions_by_id=sessions_by_id) for group in ebk_groups]
    competitor_sections = [
        _format_competitor_section(
            group,
            competitor_hotels_by_key.get(str(group.get("group_key") or ""), []),
            sessions_by_id=sessions_by_id,
        )
        for group in competitor_groups
    ]
    rows = _overview_rows(ebk_sections, competitor_sections)
    primary_ebk = ebk_sections[0] if ebk_sections else {}
    primary_comp = next((item for item in competitor_sections if item.get("enabled")), competitor_sections[0] if competitor_sections else {})
    intro_parts = [
        "以下为当前数据库里的真实盯盘配置（只读），含 EBK 绑定账号与竞品酒店清单。",
    ]
    if primary_ebk.get("ebk_account_display_name"):
        intro_parts.append(
            f"EBK 数据中心对应账号：{primary_ebk['ebk_account_display_name']}（组 {primary_ebk.get('watch_group_key')}）。"
        )
    if primary_comp.get("competitor_hotels"):
        intro_parts.append(
            f"默认竞品组竞对：{'、'.join(primary_comp['competitor_hotels'][:5])}。"
        )
    return {
        "status": "success",
        "summary": "已返回当前 EBK 与竞品盯盘配置。",
        "operator_summary": {
            "overall_status": "success",
            "intro": "\n".join(intro_parts),
            "overview_rows": rows,
            "ebk_sections": ebk_sections,
            "competitor_sections": competitor_sections,
            "defaults_note": (
                f"EBK 组看 {watch_kind_label(WATCH_KIND_EBK)}；竞品组看 {watch_kind_label(WATCH_KIND_COMPETITOR)}。"
                "问「本群盯着哪家酒店 / 竞对是谁」时使用 resolve_feishu_chat_context；"
                "本工具只用于全局配置总览。"
            ),
            "usage_note": (
                "ebk_account_display_name 来自浏览器账号 display_name，即携程 EBK 后台绑定账号。"
                "竞品 own_hotels 是本店公开页，competitor_hotels 是竞对公开页。"
            ),
        },
    }


def build_scoped_operator_watch_configuration(
    *,
    ebk_groups: list[dict[str, Any]],
    competitor_groups: list[dict[str, Any]],
    competitor_hotels_by_key: dict[str, list[dict[str, Any]]],
    browser_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = build_operator_watch_configuration(
        ebk_groups=ebk_groups,
        competitor_groups=competitor_groups,
        competitor_hotels_by_key=competitor_hotels_by_key,
        browser_sessions=browser_sessions,
    )
    operator = dict(payload.get("operator_summary") or {})
    intro = str(operator.get("intro") or "")
    intro = intro.replace(
        "以下为当前数据库里的真实盯盘配置（只读）",
        "以下仅列出当前飞书群已授权的真实盯盘配置（只读）",
        1,
    ).replace("默认竞品组竞对：", "本群竞品组竞对：")
    operator["intro"] = intro
    operator["defaults_note"] = (
        "仅可使用当前群授权的 EBK 与竞品监控组；显式 group key 也必须属于当前群。"
    )
    return {
        **payload,
        "summary": "已返回当前飞书群授权的 EBK 与竞品盯盘配置。",
        "operator_summary": operator,
    }


def enrich_operator_watch_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    return attach_feishu_card(payload, "watch_configuration")


def build_chat_binding_summary(chat_context: dict[str, Any] | None) -> dict[str, Any]:
    if chat_context is None:
        return {
            "chat_binding_status": "session_context_required",
            "chat_binding_intro": (
                "未能获取本群 chat_id，无法判断本群绑定状态。"
                "请在飞书会话中 @机器人 重试，不要根据全局配置猜测本群是否绑定。"
            ),
            "chat_binding_rows": [],
        }

    if str(chat_context.get("status") or "") == "session_context_required":
        return {
            "chat_binding_status": "session_context_required",
            "chat_binding_intro": str(
                chat_context.get("summary")
                or "缺少飞书群 chat_id，无法解析本群绑定。"
            ),
            "chat_binding_rows": [],
        }

    operator = (
        chat_context.get("operator_summary")
        if isinstance(chat_context.get("operator_summary"), dict)
        else {}
    )
    rows = operator.get("overview_rows") if isinstance(operator.get("overview_rows"), list) else []
    binding_mode = str(chat_context.get("binding_mode") or "unknown")
    resolution = chat_context.get("resolution") if isinstance(chat_context.get("resolution"), dict) else {}
    configured = bool(chat_context.get("chat_id_configured"))
    status = str(resolution.get("status") or "")

    if not configured or binding_mode == "unbound" or status == "unbound":
        return {
            "chat_binding_status": "unbound",
            "chat_binding_intro": str(
                resolution.get("summary")
                or "本群尚未绑定运营问答上下文群，请在控制台对应监控组填写本群 chat_id。"
            ),
            "chat_binding_rows": rows,
        }

    if status == "ambiguous":
        return {
            "chat_binding_status": "ambiguous",
            "chat_binding_intro": str(
                resolution.get("summary")
                or "本群绑定了多家门店或配对未完成，请在问题里带上店名。"
            ),
            "chat_binding_rows": rows,
            "store_candidates": resolution.get("store_candidates") or [],
        }

    if status == "resolved":
        store_label = str(resolution.get("store_label") or "已绑定门店")
        ebk_key = str(resolution.get("ebk_watch_group_key") or "—")
        competitor_key = str(resolution.get("competitor_watch_group_key") or "—")
        return {
            "chat_binding_status": "resolved",
            "chat_binding_intro": (
                f"本群已绑定：{store_label}。"
                f"EBK 组 {ebk_key}，竞品组 {competitor_key}。"
            ),
            "chat_binding_rows": rows,
            "store_label": store_label,
            "ebk_watch_group_key": ebk_key,
            "competitor_watch_group_key": competitor_key,
        }

    return {
        "chat_binding_status": status or "unknown",
        "chat_binding_intro": str(resolution.get("summary") or "本群绑定状态待确认。"),
        "chat_binding_rows": rows,
    }


def attach_chat_binding_to_payload(
    payload: dict[str, Any],
    chat_binding: dict[str, Any],
) -> dict[str, Any]:
    operator = dict(payload.get("operator_summary") or {})
    binding_intro = str(chat_binding.get("chat_binding_intro") or "").strip()
    global_intro = str(operator.get("intro") or "").strip()
    operator["chat_binding_status"] = chat_binding.get("chat_binding_status")
    operator["chat_binding_intro"] = binding_intro
    operator["chat_binding_rows"] = chat_binding.get("chat_binding_rows") or []
    if binding_intro and global_intro:
        operator["intro"] = f"{binding_intro}\n\n{global_intro}"
    elif binding_intro:
        operator["intro"] = binding_intro
    routing_hints = list(operator.get("routing_hints") or [])
    binding_status = str(chat_binding.get("chat_binding_status") or "")
    if binding_status == "resolved":
        routing_hints.insert(
            0,
            "本群绑定已解析；禁止再说本群未绑定。运行状态见 runtime_module_rows。",
        )
    elif binding_status in {"unbound", "session_context_required"}:
        routing_hints.insert(
            0,
            "只有 chat_binding_status=unbound 且 chat_id_configured=false 时，才可说本群未绑定。",
        )
    elif binding_status == "ambiguous":
        routing_hints.insert(0, "本群存在歧义或未配对完成，请让用户补充店名，不要猜默认组。")
    operator["routing_hints"] = routing_hints
    return {
        **payload,
        "chat_binding": chat_binding,
        "operator_summary": operator,
    }


def attach_runtime_health_to_payload(
    payload: dict[str, Any],
    health: dict[str, Any],
) -> dict[str, Any]:
    operator = dict(payload.get("operator_summary") or {})
    health_operator = health.get("operator_summary") if isinstance(health.get("operator_summary"), dict) else {}
    module_rows = health_operator.get("module_rows") if isinstance(health_operator.get("module_rows"), list) else []
    overall_status = str(health_operator.get("overall_status") or health.get("summary", {}).get("status") or "unknown")
    runtime_intro = f"系统运行状态：{overall_status}。"
    if health_operator.get("attention_modules"):
        runtime_intro += f" 需关注：{'、'.join(str(item) for item in health_operator['attention_modules'])}。"
    operator["runtime_status"] = overall_status
    operator["runtime_module_rows"] = module_rows
    operator["runtime_intro"] = runtime_intro
    binding_intro = str(operator.get("chat_binding_intro") or "").strip()
    global_intro = str(operator.get("intro") or "").strip()
    if binding_intro and global_intro.startswith(binding_intro):
        tail = global_intro[len(binding_intro) :].strip(" \n")
        operator["intro"] = f"{binding_intro}\n\n{runtime_intro}\n\n{tail}".strip()
    else:
        operator["intro"] = f"{global_intro}\n\n{runtime_intro}".strip() if global_intro else runtime_intro
    return {**payload, "runtime_health": health, "operator_summary": operator}
