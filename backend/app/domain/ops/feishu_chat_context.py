from __future__ import annotations

from typing import Any

from app.domain.browser.sessions import (
    BrowserSession,
    get_browser_session_store,
    get_pricing_browser_session_for_feishu_chat_id,
)
from app.domain.competitor_prices.repository import (
    list_competitor_hotels,
    list_competitor_watch_group_summaries,
    list_competitor_watch_groups_by_feishu_chat_id,
)
from app.domain.ctrip_ebooking_watch.repository import (
    list_data_center_watch_group_summaries,
    list_data_center_watch_groups_by_feishu_chat_id,
)
from app.domain.ops.feishu_store_resolution import (
    build_store_catalog,
    infer_binding_mode,
    resolve_store_selection,
)


def get_browser_session_display_name(browser_session_id: str | None) -> str | None:
    session_id = str(browser_session_id or "").strip()
    if not session_id:
        return None
    try:
        session = get_browser_session_store().get(session_id)
    except (KeyError, ValueError):
        return None
    display_name = str(session.display_name or "").strip()
    return display_name or session.platform_account_id or None


_OTA_EBK_PLATFORMS = ("ctrip", "meituan", "fliggy")
_STORE_HINT_STOPWORDS = frozenset(
    {
        "酒店",
        "宾馆",
        "饭店",
        "客栈",
        "账号",
        "账户",
        "美团",
        "飞猪",
        "携程",
        "ebk",
        "hotel",
    }
)


def _ebk_brand_tokens(display: str, account: str) -> list[str]:
    tokens: list[str] = []
    if account:
        prefix = account.split("-", 1)[0].strip()
        if len(prefix) >= 2:
            tokens.append(prefix)
    if display:
        head = display
        for sep in ("（", "(", "【", "[", "酒店", "宾馆", "饭店", "客栈", "美团", "飞猪", "账号"):
            if sep in head:
                head = head.split(sep, 1)[0].strip()
        if len(head) >= 2:
            tokens.append(head)
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        normalized = token.casefold()
        if normalized in seen or token in _STORE_HINT_STOPWORDS or normalized in _STORE_HINT_STOPWORDS:
            continue
        seen.add(normalized)
        ordered.append(token)
    return ordered


def _ebk_session_matches_store_hint(
    *,
    display_name: str,
    platform_account_id: str,
    hint: str,
) -> bool:
    display = display_name.casefold().strip()
    account = platform_account_id.casefold().strip()
    haystack = f"{display} {account}".strip()
    if not hint:
        return True
    if hint in haystack or (display and display in hint) or (account and account in hint):
        return True
    return any(token.casefold() in hint for token in _ebk_brand_tokens(display_name, platform_account_id))


def _serialize_ota_ebk_account(session: BrowserSession) -> dict[str, str]:
    display = str(session.display_name or "").strip()
    account_id = str(session.platform_account_id or "").strip()
    return {
        "platform": str(session.platform or "").strip().casefold(),
        "platform_account_id": account_id,
        "display_name": display or account_id,
        "browser_session_id": str(session.browser_session_id or "").strip(),
    }


def resolve_ota_ebk_accounts_for_store(
    *,
    store_hint: str | None,
    bound_ctrip_account_id: str | None = None,
    bound_ctrip_browser_session_id: str | None = None,
    store_shop_key: str | None = None,
) -> dict[str, dict[str, str] | None]:
    """Resolve shop-scoped EBK browser accounts (ctrip/meituan/fliggy).

    Feishu EBK watch groups only bind Ctrip. Meituan/Fliggy live as browser
    sessions and must still be reported so operators are not told they are missing.

    P6：选中门店已绑店铺时按 session.shop_key 精确过滤（事实来源）；
    未绑店铺的旧数据仍走下方品牌词兜底路径，待店铺归属补齐后退役。
    """
    sessions = get_browser_session_store().list()
    normalized_shop_key = str(store_shop_key or "").strip()
    if normalized_shop_key:
        scoped = [
            session
            for session in sessions
            if str(session.intended_use or "").strip().casefold() == "ebk"
            and str(getattr(session, "shop_key", "") or "").strip() == normalized_shop_key
        ]
        bound_account = str(bound_ctrip_account_id or "").strip()
        resolved: dict[str, dict[str, str] | None] = {}
        for platform in _OTA_EBK_PLATFORMS:
            matches = [
                session
                for session in scoped
                if str(session.platform or "").strip().casefold() == platform
                and str(session.platform_account_id or "").strip()
            ]
            if platform == "ctrip" and bound_account:
                for session in matches:
                    if str(session.platform_account_id or "").strip() == bound_account:
                        resolved[platform] = _serialize_ota_ebk_account(session)
                        break
                else:
                    resolved[platform] = (
                        _serialize_ota_ebk_account(matches[0])
                        if len(matches) == 1
                        else None
                    )
                continue
            resolved[platform] = (
                _serialize_ota_ebk_account(matches[0]) if len(matches) == 1 else None
            )
        return resolved

    hint_parts: list[str] = []
    normalized_hint = str(store_hint or "").strip()
    if normalized_hint:
        hint_parts.append(normalized_hint)
    bound_account = str(bound_ctrip_account_id or "").strip()
    bound_browser = str(bound_ctrip_browser_session_id or "").strip()
    for session in sessions:
        if str(session.intended_use or "").strip().casefold() != "ebk":
            continue
        account_id = str(session.platform_account_id or "").strip()
        browser_id = str(session.browser_session_id or "").strip()
        if bound_account and account_id == bound_account:
            hint_parts.extend(
                [
                    str(session.display_name or "").strip(),
                    account_id,
                    *(_ebk_brand_tokens(str(session.display_name or ""), account_id)),
                ]
            )
            break
        if bound_browser and browser_id == bound_browser:
            hint_parts.extend(
                [
                    str(session.display_name or "").strip(),
                    account_id,
                    *(_ebk_brand_tokens(str(session.display_name or ""), account_id)),
                ]
            )
            break
    combined_hint = " ".join(part for part in hint_parts if part).casefold().strip()

    candidates: dict[str, list[BrowserSession]] = {platform: [] for platform in _OTA_EBK_PLATFORMS}
    for session in sessions:
        if str(session.intended_use or "").strip().casefold() != "ebk":
            continue
        platform = str(session.platform or "").strip().casefold()
        account_id = str(session.platform_account_id or "").strip()
        if platform not in candidates or not account_id:
            continue
        if combined_hint and not _ebk_session_matches_store_hint(
            display_name=str(session.display_name or ""),
            platform_account_id=account_id,
            hint=combined_hint,
        ):
            continue
        candidates[platform].append(session)

    resolved: dict[str, dict[str, str] | None] = {}
    for platform in _OTA_EBK_PLATFORMS:
        matches = candidates[platform]
        if platform == "ctrip" and bound_account:
            for session in matches:
                if str(session.platform_account_id or "").strip() == bound_account:
                    resolved[platform] = _serialize_ota_ebk_account(session)
                    break
            else:
                resolved[platform] = (
                    _serialize_ota_ebk_account(matches[0]) if len(matches) == 1 else None
                )
            continue
        resolved[platform] = (
            _serialize_ota_ebk_account(matches[0]) if len(matches) == 1 else None
        )
    return resolved


def _hotel_label(hotel: dict[str, Any]) -> str:
    for key in ("alias", "hotel_name"):
        text = str(hotel.get(key) or "").strip()
        if text:
            return text
    ctrip_id = hotel.get("ctrip_hotel_id")
    if ctrip_id:
        return f"携程酒店ID {ctrip_id}"
    return "未命名酒店"


async def _collect_enabled_hotel_labels(watch_group_key: str) -> tuple[list[str], list[str]]:
    own_hotels: list[str] = []
    competitor_hotels: list[str] = []
    hotels = await list_competitor_hotels(watch_group_key=watch_group_key)
    for hotel in hotels:
        if not isinstance(hotel, dict) or not hotel.get("enabled"):
            continue
        label = _hotel_label(hotel)
        if hotel.get("role") == "own":
            own_hotels.append(label)
        elif hotel.get("role") == "competitor":
            competitor_hotels.append(label)
    return own_hotels, competitor_hotels


def _summarize_ebk_group(group: dict[str, Any]) -> dict[str, Any]:
    latest_run = group.get("latest_run") if isinstance(group.get("latest_run"), dict) else {}
    browser_session_id = str(group.get("browser_session_id") or "").strip()
    return {
        "group_key": group.get("group_key"),
        "name": group.get("name"),
        "platform_account_id": group.get("platform_account_id"),
        "browser_session_id": browser_session_id or None,
        "display_name": get_browser_session_display_name(browser_session_id),
        "auto_poll_enabled": bool(group.get("auto_poll_enabled")),
        "latest_run_status": latest_run.get("status"),
        "latest_captured_at": latest_run.get("captured_at"),
    }


def _connection_mode_label(mode: str | None) -> str:
    labels = {
        "non_direct": "非直连",
        "direct": "直连",
        "unknown": "未确认",
    }
    return labels.get(str(mode or "").strip(), str(mode or "未确认"))


def _pricing_reference_card_outcome(group: dict[str, Any]) -> str:
    configured = bool(group.get("pricing_account_configured"))
    if not configured:
        return (
            "未配置调价 EBK：竞品降价跨阈值时只发价格变化提醒，"
            "不发降价参考卡，也不能走自动改价链。"
        )
    mode_label = _connection_mode_label(group.get("pricing_connection_mode"))
    if group.get("pricing_can_auto_write_price"):
        return (
            f"{mode_label}且已绑调价 EBK：竞品降价跨阈值时发降价参考卡"
            "（自定义调价/暂不处理）；点自定义并确认目标价后可走审批改价。"
        )
    message = str(group.get("pricing_write_policy_message") or "").strip()
    if message:
        return f"{mode_label}：{message}竞品降价跨阈值时不发降价参考卡。"
    return (
        f"{mode_label}：竞品降价跨阈值时只发提醒和规则化建议，"
        "不发降价参考卡，不能自动改价。"
    )


def _summarize_competitor_group(group: dict[str, Any]) -> dict[str, Any]:
    latest_run = group.get("latest_run") if isinstance(group.get("latest_run"), dict) else {}
    summarized = {
        "group_key": group.get("group_key"),
        "name": group.get("name"),
        "platform_account_id": group.get("platform_account_id"),
        "pricing_browser_session_id": group.get("pricing_browser_session_id"),
        "pricing_platform_account_id": group.get("pricing_platform_account_id"),
        "pricing_account_configured": bool(group.get("pricing_account_configured")),
        "pricing_connection_mode": group.get("pricing_connection_mode"),
        "pricing_can_auto_write_price": bool(group.get("pricing_can_auto_write_price")),
        "pricing_write_policy_message": group.get("pricing_write_policy_message"),
        "auto_poll_enabled": bool(group.get("auto_poll_enabled")),
        "latest_run_status": latest_run.get("status"),
        "latest_captured_at": latest_run.get("captured_at"),
    }
    summarized["pricing_reference_card_outcome"] = _pricing_reference_card_outcome(
        {**group, **summarized}
    )
    return summarized


async def _ebk_groups_for_catalog_pairing(
    chat_id: str,
    competitor_groups_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    chat_bound = await list_data_center_watch_groups_by_feishu_chat_id(chat_id)
    bound_accounts = {
        str(group.get("platform_account_id") or "").strip()
        for group in chat_bound
        if str(group.get("platform_account_id") or "").strip()
    }
    needed_accounts = {
        str(group.get("pricing_platform_account_id") or "").strip()
        for group in competitor_groups_raw
        if isinstance(group, dict)
        and str(group.get("pricing_platform_account_id") or "").strip()
        and str(group.get("pricing_platform_account_id") or "").strip() not in bound_accounts
    }
    if not needed_accounts:
        return chat_bound

    merged = list(chat_bound)
    seen_keys = {str(group.get("group_key") or "").strip() for group in merged}
    for group in await list_data_center_watch_group_summaries():
        group_key = str(group.get("group_key") or "").strip()
        account_id = str(group.get("platform_account_id") or "").strip()
        if not group_key or group_key in seen_keys or account_id not in needed_accounts:
            continue
        if not account_id or account_id.startswith("__"):
            continue
        merged.append(group)
        seen_keys.add(group_key)
    return merged


async def _competitor_groups_for_catalog_pairing(
    ebk_groups_raw: list[dict[str, Any]],
    competitor_groups_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [group for group in competitor_groups_raw if isinstance(group, dict)]
    seen_keys = {
        str(group.get("group_key") or "").strip()
        for group in merged
        if str(group.get("group_key") or "").strip()
    }
    ebk_account_ids = {
        str(group.get("platform_account_id") or "").strip()
        for group in ebk_groups_raw
        if isinstance(group, dict)
        and str(group.get("platform_account_id") or "").strip()
        and not str(group.get("platform_account_id") or "").strip().startswith("__")
    }
    ebk_browser_session_ids = {
        str(group.get("browser_session_id") or "").strip()
        for group in ebk_groups_raw
        if isinstance(group, dict) and str(group.get("browser_session_id") or "").strip()
    }
    if not ebk_account_ids and not ebk_browser_session_ids:
        return merged

    needed_account_ids: set[str] = set()
    needed_browser_session_ids: set[str] = set()
    for ebk_group in ebk_groups_raw:
        if not isinstance(ebk_group, dict):
            continue
        account_id = str(ebk_group.get("platform_account_id") or "").strip()
        browser_session_id = str(ebk_group.get("browser_session_id") or "").strip()
        if account_id.startswith("__"):
            account_id = ""
        if not account_id and not browser_session_id:
            continue
        already_paired = any(
            (
                account_id
                and account_id == str(group.get("pricing_platform_account_id") or "").strip()
            )
            or (
                browser_session_id
                and browser_session_id
                == str(group.get("pricing_browser_session_id") or "").strip()
            )
            for group in merged
            if isinstance(group, dict)
        )
        if already_paired:
            continue
        if account_id:
            needed_account_ids.add(account_id)
        if browser_session_id:
            needed_browser_session_ids.add(browser_session_id)
    if not needed_account_ids and not needed_browser_session_ids:
        return merged

    for group in await list_competitor_watch_group_summaries():
        if not isinstance(group, dict):
            continue
        group_key = str(group.get("group_key") or "").strip()
        if not group_key or group_key in seen_keys:
            continue
        pricing_account_id = str(group.get("pricing_platform_account_id") or "").strip()
        pricing_browser_session_id = str(group.get("pricing_browser_session_id") or "").strip()
        if pricing_account_id.startswith("__"):
            pricing_account_id = ""
        if pricing_account_id not in needed_account_ids and (
            not pricing_browser_session_id
            or pricing_browser_session_id not in needed_browser_session_ids
        ):
            continue
        merged.append(group)
        seen_keys.add(group_key)
    return merged


async def build_feishu_chat_context(
    chat_id: str | None,
    *,
    store_hint: str | None = None,
) -> dict[str, Any]:
    normalized_chat_id = str(chat_id or "").strip()
    pricing_session = (
        get_pricing_browser_session_for_feishu_chat_id(normalized_chat_id)
        if normalized_chat_id
        else None
    )

    chat_bound_competitor_groups_raw = await list_competitor_watch_groups_by_feishu_chat_id(
        normalized_chat_id
    )
    ebk_groups_raw = await _ebk_groups_for_catalog_pairing(
        normalized_chat_id,
        chat_bound_competitor_groups_raw,
    )
    competitor_groups_raw = await _competitor_groups_for_catalog_pairing(
        ebk_groups_raw,
        chat_bound_competitor_groups_raw,
    )

    ebk_groups = [_summarize_ebk_group(item) for item in ebk_groups_raw if isinstance(item, dict)]
    competitor_groups: list[dict[str, Any]] = []
    for group in competitor_groups_raw:
        if not isinstance(group, dict):
            continue
        group_key = str(group.get("group_key") or "").strip()
        own_hotels, competitor_hotels = await _collect_enabled_hotel_labels(group_key)
        summarized = _summarize_competitor_group(group)
        summarized["own_hotels"] = own_hotels
        summarized["competitor_hotels"] = competitor_hotels
        competitor_groups.append(summarized)

    store_catalog = build_store_catalog(
        ebk_groups=ebk_groups_raw,
        competitor_groups=competitor_groups,
    )
    binding_mode = infer_binding_mode(
        ebk_groups=ebk_groups,
        competitor_groups=competitor_groups,
        store_catalog=store_catalog,
    )
    resolution = resolve_store_selection(
        store_catalog=store_catalog,
        store_hint=store_hint,
    )

    pricing_ebk_display_name = None
    pricing_browser_session_id = None
    pricing_platform_account_id = None
    if pricing_session is not None:
        pricing_ebk_display_name = (
            str(pricing_session.display_name or "").strip() or pricing_session.platform_account_id
        )
        pricing_browser_session_id = pricing_session.browser_session_id
        pricing_platform_account_id = pricing_session.platform_account_id

    chat_id_configured = binding_mode != "unbound"
    if not competitor_groups:
        competitor_data_source = "none"
    elif chat_bound_competitor_groups_raw:
        competitor_data_source = (
            "chat_bound"
            if len(competitor_groups_raw) == len(chat_bound_competitor_groups_raw)
            else "chat_bound_plus_paired_by_pricing_account"
        )
    else:
        competitor_data_source = "paired_by_pricing_account"
    resolved_ebk_key = resolution.get("ebk_watch_group_key")
    resolved_competitor_key = resolution.get("competitor_watch_group_key")
    resolved_store = resolution.get("store_label")

    own_hotels: list[str] = []
    competitor_hotels: list[str] = []
    competitor_group_name = None
    if resolution.get("status") == "resolved" and resolved_competitor_key:
        for store in store_catalog:
            if store.get("competitor_watch_group_key") == resolved_competitor_key:
                own_hotels = list(store.get("own_hotels") or [])
                competitor_hotels = list(store.get("competitor_hotels") or [])
                competitor_group_name = store.get("competitor_group_name")
                break
        selected_competitor_group = _find_competitor_group(competitor_groups, resolved_competitor_key)
        if selected_competitor_group is not None:
            selected_browser_session_id = str(
                selected_competitor_group.get("pricing_browser_session_id") or ""
            ).strip()
            selected_platform_account_id = str(
                selected_competitor_group.get("pricing_platform_account_id") or ""
            ).strip()
            if selected_browser_session_id or selected_platform_account_id:
                pricing_browser_session_id = selected_browser_session_id or None
                pricing_platform_account_id = selected_platform_account_id or None
                pricing_ebk_display_name = (
                    get_browser_session_display_name(pricing_browser_session_id)
                    or pricing_platform_account_id
                    or None
                )

    intro_parts = [
        "本接口按运营问答上下文群 chat_id 解析绑定的 EBK 组、竞品组，以及本次选中的门店（只读）。",
    ]
    if binding_mode == "unbound":
        intro_parts.append(
            "运营问答上下文群未绑定：当前飞书群还没有出现在任何 EBK 数据中心监控组或竞品监控组里，无法推断门店。"
        )
    elif binding_mode == "single_store":
        intro_parts.append(
            f"当前运营问答上下文群为单店模式，已绑定 {len(ebk_groups)} 组 EBK + {len(competitor_groups)} 组竞品。"
        )
    else:
        intro_parts.append(f"当前运营问答上下文群为多店模式，共 {len(store_catalog)} 家门店配置。")
    if resolution.get("status") == "resolved" and resolved_store:
        intro_parts.append(f"本次已选中：{resolved_store}。")
    elif resolution.get("status") == "ambiguous":
        intro_parts.append(str(resolution.get("summary") or "请在问题里带上店名。"))
    if pricing_ebk_display_name:
        intro_parts.append(f"本群调价 EBK 账号：{pricing_ebk_display_name}。")

    bound_ctrip_account_id = ""
    bound_ctrip_browser_session_id = ""
    if resolution.get("status") == "resolved" and resolved_ebk_key:
        for group in ebk_groups:
            if str(group.get("group_key") or "").strip() != str(resolved_ebk_key).strip():
                continue
            bound_ctrip_account_id = str(group.get("platform_account_id") or "").strip()
            bound_ctrip_browser_session_id = str(group.get("browser_session_id") or "").strip()
            break
    if not bound_ctrip_account_id and len(ebk_groups) == 1:
        bound_ctrip_account_id = str(ebk_groups[0].get("platform_account_id") or "").strip()
        bound_ctrip_browser_session_id = str(ebk_groups[0].get("browser_session_id") or "").strip()

    ota_store_hint = (
        str(resolved_store or store_hint or "").strip()
        or (own_hotels[0] if own_hotels else "")
        or None
    )
    ota_ebk_accounts = resolve_ota_ebk_accounts_for_store(
        store_hint=ota_store_hint,
        bound_ctrip_account_id=bound_ctrip_account_id or None,
        bound_ctrip_browser_session_id=bound_ctrip_browser_session_id or None,
        store_shop_key=resolution.get("shop_key"),
    )
    configured_ota_labels = [
        f"{label}={account['display_name']}（{account['platform_account_id']}）"
        for label, key in (("携程", "ctrip"), ("美团", "meituan"), ("飞猪", "fliggy"))
        if (account := ota_ebk_accounts.get(key))
    ]
    if configured_ota_labels:
        intro_parts.append(
            "本店浏览器已配置的 OTA EBK 账号："
            + "；".join(configured_ota_labels)
            + "。EBK 数据中心监控组通常只挂携程，不等于美团/飞猪未配置。"
        )

    routing_hints: list[str] = []
    if resolution.get("status") == "resolved":
        if resolved_ebk_key and resolved_competitor_key:
            routing_hints.append(
                "后续查询请使用 resolution 中的 ebk_watch_group_key 与 "
                "competitor_watch_group_key，禁止改用 default_competitor_watch 或系统默认 EBK。"
            )
        elif resolved_ebk_key:
            routing_hints.append(
                "后续 EBK/经营查询请使用 resolution 中的 ebk_watch_group_key；"
                "竞品查询需要先补齐对应竞品监控组的运营问答上下文群绑定。"
            )
        elif resolved_competitor_key:
            routing_hints.append(
                "后续竞品查询请使用 resolution 中的 competitor_watch_group_key；"
                "EBK/经营查询需要先补齐对应 EBK 数据中心监控组的运营问答上下文群绑定。"
            )
    elif binding_mode == "multi_store":
        routing_hints.append(
            "多店模式下，用户未指明店名时不得查询经营/竞品数据；"
            "应提示「请说明是静里还是澜江路泉吉」。"
        )
    elif binding_mode == "unbound":
        routing_hints.append(
            "请在控制台对应门店的 EBK 数据中心监控组和竞品监控组里填写当前飞书群 ID，作为运营问答上下文群。"
        )
        routing_hints.append(
            "不要去改 EBK 账号上的手动调价指令群；那只用于手动调价命令解析。"
        )
    if ota_ebk_accounts.get("meituan") or ota_ebk_accounts.get("fliggy"):
        routing_hints.append(
            "ota_ebk_accounts 已列出本店美团/飞猪浏览器 EBK；"
            "查美团/飞猪/三端经营请调用 get_ebk_joint_analysis_context。"
            "禁止因「EBK 监控组只有携程」就说未配置美团或飞猪，也禁止再要登录信息去绑定。"
        )

    operator_summary = {
        "intro": "\n".join(intro_parts),
        "overview_rows": _overview_rows(
            binding_mode=binding_mode,
            store_catalog=store_catalog,
            resolution=resolution,
            pricing_ebk_display_name=pricing_ebk_display_name,
            ota_ebk_accounts=ota_ebk_accounts,
        ),
        "pricing_reference_policy_rows": _pricing_reference_policy_rows(competitor_groups),
        "pricing_reference_policy_note": (
            "竞品涨价跨阈值时只发价格变化提醒，不会建议本店跟涨，也不发参考卡或审批卡。"
            "「降价参考卡」只有「自定义调价」「暂不处理」，没有一键通过改价。"
        ),
        "routing_hints": routing_hints,
        "usage_note": (
            "binding_mode=single_store 表示当前飞书群可作为一个酒店上下文；"
            "EBK、竞品和日历价只读查询可使用 resolution 中的 key。"
            "binding_mode=multi_store 表示一群多店；只有 resolution.status=resolved "
            "时才能省略 watch_group_key。调价 EBK 账号只决定降价参考卡和写价链路是否可继续；"
            "direct/unknown 或未配置时仍允许只读查询，禁止写价。禁止回落到 system_default。"
            "ota_ebk_accounts 是按店名/品牌匹配的浏览器 EBK（含美团/飞猪）；"
            "不要把「只有携程 EBK 监控组」误说成系统未配置美团/飞猪。"
        ),
    }
    if resolution.get("status") == "ambiguous":
        operator_summary["store_selection_rows"] = [
            {
                "field": str(item.get("store_label") or "未命名门店"),
                "value": (
                    f"EBK={item.get('ebk_watch_group_key') or '未配对'}；"
                    f"竞品={item.get('competitor_watch_group_key') or '未配对'}"
                ),
            }
            for item in (resolution.get("store_candidates") or [])
        ]

    return {
        "status": "success" if binding_mode != "unbound" else "config_required",
        "chat_id": normalized_chat_id or None,
        "chat_id_configured": chat_id_configured,
        "binding_mode": binding_mode,
        "ebk_groups": ebk_groups,
        "competitor_groups": competitor_groups,
        "store_catalog": store_catalog,
        "resolution": resolution,
        "resolved_shop_key": resolution.get("shop_key"),
        "competitor_data_source": competitor_data_source,
        "competitor_watch_group_key": resolved_competitor_key,
        "competitor_group_name": competitor_group_name,
        "ebk_watch_group_key": resolved_ebk_key,
        "own_hotels": own_hotels,
        "competitor_hotels": competitor_hotels,
        "pricing_ebk_display_name": pricing_ebk_display_name,
        "pricing_browser_session_id": pricing_browser_session_id,
        "pricing_platform_account_id": pricing_platform_account_id,
        "ota_ebk_accounts": ota_ebk_accounts,
        "operator_summary": operator_summary,
        "usage_note": operator_summary["usage_note"],
    }


def _pricing_reference_policy_rows(
    competitor_groups: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group in competitor_groups:
        if not isinstance(group, dict):
            continue
        label = str(group.get("name") or group.get("group_key") or "竞品组").strip()
        outcome = str(group.get("pricing_reference_card_outcome") or "").strip()
        if not outcome:
            outcome = _pricing_reference_card_outcome(group)
        rows.append({"field": f"{label} 降价参考卡", "value": outcome})
    return rows


def _find_competitor_group(
    competitor_groups: list[dict[str, Any]],
    group_key: Any,
) -> dict[str, Any] | None:
    normalized_key = str(group_key or "").strip()
    if not normalized_key:
        return None
    for group in competitor_groups:
        if not isinstance(group, dict):
            continue
        if str(group.get("group_key") or "").strip() == normalized_key:
            return group
    return None


def _overview_rows(
    *,
    binding_mode: str,
    store_catalog: list[dict[str, Any]],
    resolution: dict[str, Any],
    pricing_ebk_display_name: str | None,
    ota_ebk_accounts: dict[str, dict[str, str] | None] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [
        {
            "field": "绑定模式",
            "value": {
                "unbound": "未绑定",
                "single_store": "单店",
                "multi_store": "多店",
            }.get(binding_mode, binding_mode),
        }
    ]
    if pricing_ebk_display_name:
        rows.append({"field": "调价 EBK 账号", "value": pricing_ebk_display_name})
    accounts = ota_ebk_accounts or {}
    for field, key in (
        ("携程 EBK 账号", "ctrip"),
        ("美团 EBK 账号", "meituan"),
        ("飞猪 EBK 账号", "fliggy"),
    ):
        account = accounts.get(key)
        if not account:
            continue
        rows.append(
            {
                "field": field,
                "value": f"{account.get('display_name') or account.get('platform_account_id')}"
                f"（{account.get('platform_account_id')}）",
            }
        )
    if resolution.get("status") == "resolved":
        rows.append({"field": "本次门店", "value": str(resolution.get("store_label") or "—")})
        rows.append(
            {
                "field": "EBK 监控组",
                "value": str(resolution.get("ebk_watch_group_key") or "未配对"),
            }
        )
        rows.append(
            {
                "field": "竞品监控组",
                "value": str(resolution.get("competitor_watch_group_key") or "未配对"),
            }
        )
    elif store_catalog:
        for index, store in enumerate(store_catalog, start=1):
            rows.append(
                {
                    "field": f"门店{index}",
                    "value": (
                        f"{store.get('store_label') or '未命名'}；"
                        f"EBK={store.get('ebk_watch_group_key') or '未配对'}；"
                        f"竞品={store.get('competitor_watch_group_key') or '未配对'}"
                    ),
                }
            )
    else:
        rows.append({"field": "门店", "value": "本群未绑定"})
    return rows
