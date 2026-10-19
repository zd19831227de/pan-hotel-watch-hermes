from __future__ import annotations

from typing import Any

from client.feishu_cards import attach_feishu_card
from client.watch_groups import watch_kind_label


def ambiguous_store_payload(context: dict[str, Any]) -> dict[str, Any]:
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    binding_mode = str(context.get("binding_mode") or "")
    is_unbound = binding_mode == "unbound" or resolution.get("status") == "unbound" or resolution.get("reason") == "unbound"
    if is_unbound:
        summary = str(
            resolution.get("summary")
            or "运营问答上下文群未绑定：当前飞书群还没有绑定到 EBK 数据中心监控组或竞品监控组。"
        )
        routing_hints = [
            "请在控制台对应门店的 EBK 数据中心监控组和竞品监控组里填写当前飞书群 ID，作为运营问答上下文群。",
            "不要改 EBK 账号上的手动调价指令群来解决普通经营问答绑定问题。",
        ]
    else:
        summary = str(resolution.get("summary") or "当前运营问答上下文群绑定了多家门店，请在问题里带上店名。")
        routing_hints = [
            "多店模式下必须先 resolve_feishu_chat_context(store_hint=店名)，"
            "resolution.status=resolved 后再查 EBK/竞品数据。"
        ]
    payload: dict[str, Any] = {
        "status": "config_required" if is_unbound else "ambiguous_store",
        "summary": summary,
        "binding_mode": context.get("binding_mode"),
        "resolution": resolution,
        "operator_summary": {
            "intro": summary,
            "routing_hints": routing_hints,
        },
    }
    return attach_feishu_card(payload, "chat_context")


def watch_group_not_allowed_payload(
    context: dict[str, Any],
    *,
    watch_kind: str,
) -> dict[str, Any]:
    label = watch_kind_label(watch_kind) if watch_kind in {"ebk", "competitor"} else watch_kind
    summary = f"指定的{label}监控组不属于当前运营问答上下文群，已拒绝访问。"
    return attach_feishu_card(
        {
            "status": "watch_group_not_allowed",
            "summary": summary,
            "binding_mode": context.get("binding_mode"),
            "watch_kind": watch_kind,
            "operator_summary": {
                "intro": summary,
                "routing_hints": ["请使用当前群已绑定的监控组，或先在控制台完成运营问答上下文群绑定。"],
            },
        },
        "chat_context",
    )


def missing_watch_group_binding_payload(context: dict[str, Any], *, watch_kind: str) -> dict[str, Any]:
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    store_name = str(resolution.get("store_name") or resolution.get("matched_store_name") or "").strip()
    label = watch_kind_label(watch_kind) if watch_kind in {"ebk", "competitor"} else watch_kind
    target = f"{store_name} " if store_name else ""
    summary = (
        f"已通过运营问答上下文群匹配到{target}门店，"
        f"但缺少该门店的{label}监控组绑定，不能改用默认监控组。"
    )
    payload: dict[str, Any] = {
        "status": "config_required",
        "summary": summary,
        "binding_mode": context.get("binding_mode"),
        "resolution": resolution,
        "watch_kind": watch_kind,
        "operator_summary": {
            "intro": summary,
            "routing_hints": [
                "先在控制台补齐该门店 EBK/竞品监控组与运营问答上下文群或价格变化提醒群的绑定。",
                "不要改 EBK 账号上的手动调价指令群来解决普通经营问答绑定问题。",
            ],
        },
    }
    return attach_feishu_card(payload, "chat_context")


def missing_session_chat_id_payload(*, action: str = "解析本群绑定") -> dict[str, Any]:
    return attach_feishu_card(
        {
            "status": "session_context_required",
            "summary": (
                f"缺少运营问答上下文群 chat_id，无法{action}。"
                "请在飞书群里 @机器人 提问，并确认 pan-hotel-feishu-session 插件已启用。"
            ),
            "chat_id_configured": None,
            "binding_mode": "unknown",
            "operator_summary": {
                "intro": (
                    "这不是控制台未绑定，而是当前 MCP 调用没拿到运营问答上下文群 chat_id。"
                    "不是价格变化提醒群未配置，也不是手动调价指令群未配置。"
                ),
                "routing_hints": [
                    "在飞书会话中重试；工具会自动注入 chat_id。",
                    "若仍失败，检查 Hermes Gateway 是否加载 pan-hotel-feishu-session 插件。",
                ],
            },
        },
        "chat_context",
    )


def _single_group_key(groups: Any) -> str | None:
    if not isinstance(groups, list):
        return None
    keys: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        key = str(group.get("group_key") or "").strip()
        if key:
            keys.add(key)
    if len(keys) == 1:
        return next(iter(keys))
    return None


def _authorized_group_keys(context: dict[str, Any], *, watch_kind: str) -> set[str]:
    groups_field = "ebk_groups" if watch_kind == "ebk" else "competitor_groups"
    resolution_field = (
        "ebk_watch_group_key" if watch_kind == "ebk" else "competitor_watch_group_key"
    )
    keys = {
        str(group.get("group_key") or "").strip()
        for group in context.get(groups_field, [])
        if isinstance(group, dict) and str(group.get("group_key") or "").strip()
    }
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    if resolution.get("status") == "resolved":
        resolved_key = str(resolution.get(resolution_field) or "").strip()
        if resolved_key:
            keys.add(resolved_key)
    return keys


async def resolve_watch_group_keys(
    client: Any,
    *,
    chat_id: str | None,
    store_hint: str | None = None,
    watch_kind: str,
    explicit_key: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return None, missing_session_chat_id_payload(action="确定监控组")

    context = await client.get_feishu_chat_context(normalized_chat_id, store_hint=store_hint)
    requested_key = str(explicit_key or "").strip()
    if requested_key:
        if requested_key in _authorized_group_keys(context, watch_kind=watch_kind):
            return requested_key, None
        return None, watch_group_not_allowed_payload(context, watch_kind=watch_kind)

    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    if resolution.get("status") != "resolved":
        if watch_kind == "ebk":
            single_ebk_key = _single_group_key(context.get("ebk_groups"))
            if single_ebk_key:
                return single_ebk_key, None
        return None, ambiguous_store_payload(context)

    if watch_kind == "ebk":
        key = str(resolution.get("ebk_watch_group_key") or "").strip()
        if not key:
            return None, missing_watch_group_binding_payload(context, watch_kind=watch_kind)
        return key, None
    if watch_kind == "competitor":
        key = str(resolution.get("competitor_watch_group_key") or "").strip()
        if not key:
            return None, missing_watch_group_binding_payload(context, watch_kind=watch_kind)
        return key, None
    return None, None


async def resolve_overview_watch_groups(
    client: Any,
    *,
    chat_id: str | None,
    store_hint: str | None = None,
    ebk_watch_group_key: str | None = None,
    competitor_watch_group_key: str | None = None,
) -> tuple[tuple[str | None, str | None], dict[str, Any] | None]:
    explicit_ebk = str(ebk_watch_group_key or "").strip() or None
    explicit_competitor = str(competitor_watch_group_key or "").strip() or None
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return (None, None), missing_session_chat_id_payload(action="生成今日经营概览")

    context = await client.get_feishu_chat_context(normalized_chat_id, store_hint=store_hint)
    if explicit_ebk and explicit_ebk not in _authorized_group_keys(context, watch_kind="ebk"):
        return (None, None), watch_group_not_allowed_payload(context, watch_kind="ebk")
    if explicit_competitor and explicit_competitor not in _authorized_group_keys(
        context,
        watch_kind="competitor",
    ):
        return (None, None), watch_group_not_allowed_payload(context, watch_kind="competitor")
    if explicit_ebk or explicit_competitor:
        return (explicit_ebk, explicit_competitor), None

    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    if resolution.get("status") != "resolved":
        return (None, None), ambiguous_store_payload(context)

    resolved_ebk = str(resolution.get("ebk_watch_group_key") or "").strip() or None
    resolved_competitor = str(resolution.get("competitor_watch_group_key") or "").strip() or None
    final_ebk = explicit_ebk or resolved_ebk
    final_competitor = explicit_competitor or resolved_competitor
    if final_ebk or final_competitor:
        return (final_ebk, final_competitor), None
    return (None, None), missing_watch_group_binding_payload(context, watch_kind="ebk")
