"""订单/退单抓取的浏览器会话解析与获取。

按平台 + 飞书 chat_id 解析当前店铺的 EBK 监控组账号绑定，确保浏览器 session
已注册，并复用对应平台的 EBK 浏览器服务（含 profile 单所有权锁、CDP 复用、
持久化浏览器自动启动）获取已登录页面。不再让 adapter 裸连固定 CDP 端口——
浏览器未开时自动按已登录 profile 拉起，无需人工先打开浏览器。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.settings import get_settings
from app.domain.browser.sessions import (
    BrowserSessionCreate,
    get_browser_session_store,
)

logger = logging.getLogger(__name__)

ORDER_PLATFORMS = ("ctrip", "fliggy", "meituan")


def _default_port_for_platform(platform: str) -> int:
    settings = get_settings()
    if platform == "ctrip":
        return settings.ctrip_ebooking_default_session_port
    if platform == "meituan":
        return settings.meituan_ebooking_default_session_port
    if platform == "fliggy":
        return settings.fliggy_ebooking_default_session_port
    raise ValueError(f"unsupported order platform: {platform}")


async def _list_platform_groups(platform: str, chat_id: str | None) -> list[dict[str, Any]]:
    """返回该平台的监控组列表；有 chat_id 时携程优先按群过滤，其余平台取全部。"""
    if platform == "ctrip":
        from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

        if chat_id:
            bound = await ctrip_repo.list_data_center_watch_groups_by_feishu_chat_id(chat_id)
            if bound:
                return bound
        return await ctrip_repo.list_data_center_watch_group_summaries()
    if platform == "meituan":
        from app.domain.meituan_ebooking_watch import repository as meituan_repo

        groups = await meituan_repo.list_meituan_watch_groups()
    else:  # fliggy
        from app.domain.fliggy_ebooking_watch import repository as fliggy_repo

        groups = await fliggy_repo.list_fliggy_watch_groups()
    if chat_id:
        matched = [
            g for g in groups if str(g.get("feishu_chat_id") or "").strip() == chat_id
        ]
        if matched:
            return matched
    return groups


async def _chat_bound_ctrip_anchor_keys(chat_id: str) -> list[str]:
    """chat 直绑的携程 EBK 监控组 group_key 列表（店铺锚点）。"""
    from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

    keys: list[str] = []
    for group in await ctrip_repo.list_data_center_watch_groups_by_feishu_chat_id(chat_id):
        key = str(group.get("group_key") or "").strip()
        if key:
            keys.append(key)
    return keys


async def resolve_order_account_bindings_for_chat(
    platform: str, chat_id: str | None
) -> list[dict[str, Any]]:
    """按 chat 解析该平台全部有效账号绑定（含组名 label，按监控组序去重）。

    解析顺序（推送链路同款的店铺定位）：
    1. 平台监控组自身 feishu_chat_id 直绑；
    2. chat 直绑的携程 EBK 锚点组 → 挂载在该锚点上的平台监控组
       （携程平台即锚点组自身）。
    chat 给定但整条链解析不到 → 返回 []（not_ready），绝不回落
    「全部组的第一个」——否则会跨店读错数据（2026-09-16 立瑞群事故）。
    chat 为空 → 全部监控组（日终快照遍历所有店铺的入口）。
    """
    platform = str(platform or "").strip().lower()
    if platform not in ORDER_PLATFORMS:
        return []
    chat = str(chat_id or "").strip()
    if not chat:
        groups = await _list_platform_groups(platform, None)
    else:
        groups = [
            g
            for g in await _list_platform_groups(platform, chat)
            if str(g.get("feishu_chat_id") or "").strip() == chat
        ]
        if not groups:
            anchor_keys = await _chat_bound_ctrip_anchor_keys(chat)
            if anchor_keys:
                if platform == "ctrip":
                    groups = await _list_platform_groups(platform, chat)
                    groups = [
                        g
                        for g in groups
                        if str(g.get("group_key") or "").strip() in anchor_keys
                    ]
                else:
                    platform_groups = await _list_platform_groups(platform, None)
                    groups = [
                        g
                        for g in platform_groups
                        if str(g.get("ctrip_watch_group_key") or "").strip() in anchor_keys
                    ]
    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        binding = _binding_from_group(group)
        if binding is None or binding["platform_account_id"] in seen:
            continue
        seen.add(binding["platform_account_id"])
        bindings.append(
            {
                **binding,
                "label": str(group.get("name") or "").strip()
                or binding["platform_account_id"],
            }
        )
    return bindings


async def list_order_account_bindings(platform: str) -> list[dict[str, Any]]:
    """该平台全部店铺的账号绑定（日终快照遍历所有店铺用）。

    监控组全无有效绑定时回落 session store 的历史注册（单店铺时代语义）。
    P3 起 store 回落改为确定性枚举该平台全部 EBK 会话（每店一条），不再
    只取第一个。
    """
    bindings = await resolve_order_account_bindings_for_chat(platform, None)
    if bindings:
        return bindings
    legacy = _session_store_ebk_bindings(platform)
    return [{**binding, "label": binding["platform_account_id"]} for binding in legacy]


def _binding_from_group(group: dict[str, Any]) -> dict[str, str] | None:
    account_id = str(group.get("platform_account_id") or "").strip()
    session_id = str(group.get("browser_session_id") or "").strip()
    if not account_id or account_id.startswith("__"):
        return None
    if not session_id:
        session_id = f"browser-{account_id}"
    return {"platform_account_id": account_id, "browser_session_id": session_id}


async def resolve_order_account_binding(
    platform: str, *, chat_id: str | None = None
) -> dict[str, str] | None:
    """解析该平台当前店铺（按 chat_id 定位监控组）的账号绑定。

    返回 {platform_account_id, browser_session_id}；无有效绑定返回 None。
    chat 给定时按「平台组直绑 → 携程锚点链」解析，解析不到即 None（不跨店
    回落）；chat 为空取第一个有效绑定（历史单店铺语义）。监控组
    platform_account_id 为空且 chat 为空时，回退到 session store 里该平台
    已注册的 EBK session。
    """
    platform = str(platform or "").strip().lower()
    if platform not in ORDER_PLATFORMS:
        return None
    bindings = await resolve_order_account_bindings_for_chat(platform, chat_id)
    if bindings:
        # 注意：监控组的 enabled 控制的是定时轮询，不代表账号绑定失效。订单/退单
        # 为按需抓取，只要有有效账号绑定即可用，故不按 enabled 过滤。
        first = bindings[0]
        return {
            "platform_account_id": first["platform_account_id"],
            "browser_session_id": first["browser_session_id"],
        }
    if str(chat_id or "").strip():
        return None
    # P3：chat 为空不再取「store 第一个 EBK 账号」（多店铺下是跨店猜测）。
    # 唯一候选时确定可用；多候选返回 None，等待显式店铺/监控组指定。
    candidates = _session_store_ebk_bindings(platform)
    if len(candidates) == 1:
        return candidates[0]
    return None


async def resolve_order_binding_context(
    platform: str, *, chat_id: str | None = None
) -> dict[str, str | None] | None:
    """解析账号绑定并带上店铺归属（订单/退单快照落库用，P3 §4.9.1）。

    返回 {platform_account_id, browser_session_id, shop_key}；解析不到返回
    None（不猜账号），调用方按无归属落库并如实展示。
    """
    binding = await resolve_order_account_binding(platform, chat_id=chat_id)
    if binding is None:
        return None
    shop_key = None
    try:
        session = get_browser_session_store().get(binding["browser_session_id"])
        shop_key = session.shop_key
    except KeyError:
        pass
    return {**binding, "shop_key": shop_key}


def _session_store_ebk_bindings(platform: str) -> list[dict[str, str]]:
    """store 内该平台全部 EBK 会话绑定（确定性枚举，供快照遍历/兜底）。"""
    store = get_browser_session_store()
    bindings: list[dict[str, str]] = []
    for session in store.list():
        if session.platform != platform:
            continue
        if session.intended_use != "ebk":
            continue
        account_id = str(session.platform_account_id or "").strip()
        if not account_id or account_id.startswith("__"):
            continue
        bindings.append(
            {
                "platform_account_id": account_id,
                "browser_session_id": session.browser_session_id,
            }
        )
    return bindings


def ensure_order_browser_session(
    platform: str, *, platform_account_id: str, browser_session_id: str
) -> None:
    """确保该绑定对应的浏览器 session 已注册（含默认端口），供服务自动启动用。"""
    store = get_browser_session_store()
    try:
        store.get(browser_session_id)
        return
    except KeyError:
        pass
    port = _default_port_for_platform(platform)
    try:
        store.create(
            BrowserSessionCreate(
                platform_account_id=platform_account_id,
                display_name=f"{platform} 订单抓取账号",
                platform=platform,
                intended_use="ebk",
                connection_mode="unknown",
                session_port=port,
                browser_session_id=browser_session_id,
            )
        )
    except ValueError:
        # 端口/账号已被其他 session 占用时，沿用已存在的 session，不重复注册。
        logger.info(
            "order browser session already registered or port busy: %s/%s",
            platform,
            browser_session_id,
        )


async def acquire_order_page(platform: str, *, chat_id: str | None = None) -> Any:
    """按店铺绑定获取该平台已登录的浏览器页面（自动启动浏览器）。

    返回 EBK 浏览器服务管理的 Page；无有效绑定时返回 None（由调用方决定
    回退为 not_ready 或 human_required）。
    """
    binding = await resolve_order_account_binding(platform, chat_id=chat_id)
    if binding is None:
        return None
    return await acquire_order_page_for_binding(platform, binding)


async def acquire_order_page_for_binding(platform: str, binding: dict[str, str]) -> Any:
    """按已解析的账号绑定获取已登录页面（自动启动浏览器）。"""
    ensure_order_browser_session(
        platform,
        platform_account_id=binding["platform_account_id"],
        browser_session_id=binding["browser_session_id"],
    )
    if platform == "ctrip":
        from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

        service = get_ctrip_ebooking_service()
    elif platform == "meituan":
        from app.domain.browser.meituan_ebooking import get_meituan_ebooking_service

        service = get_meituan_ebooking_service()
    else:
        from app.domain.browser.fliggy_ebooking import get_fliggy_ebooking_service

        service = get_fliggy_ebooking_service()
    return await service._get_or_create_page(
        platform_account_id=binding["platform_account_id"],
        browser_session_id=binding["browser_session_id"],
        headless=False,
    )
