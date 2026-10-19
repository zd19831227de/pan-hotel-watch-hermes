from __future__ import annotations

from typing import Any

from . import repository as shops_repository
from .service import SHOP_PLATFORMS


async def resolve_platform_binding(shop_key: str, platform: str) -> dict[str, Any]:
    """统一账号解析（重构 §3.4）：店铺×平台 → EBK 账号绑定的唯一出口。

    P3 切片基于会话 shop_key 归属解析；监控组挂载与「当前账号」显式指定在
    P4 接入组级 shop_key 后叠加。不返回任何猜测结果：
    - resolved：恰好一个该平台的 EBK 账号归属该店；
    - binding_conflict：多个账号归属，需显式指定或解绑；
    - binding_required：无绑定或店铺不存在（summary 给出下一步）。
    """
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform not in SHOP_PLATFORMS:
        raise ValueError(f"不支持的平台：{platform}")
    key = str(shop_key or "").strip()
    base: dict[str, Any] = {
        "shop_key": key,
        "platform": normalized_platform,
        "group_key": None,
        "platform_account_id": None,
        "browser_session_id": None,
        "mounted": None,
    }
    if not key:
        return {
            **base,
            "status": "binding_required",
            "summary": "缺少 shop_key，无法解析平台绑定。",
        }
    shop = await shops_repository.get_shop_by_key(key)
    if shop is None:
        return {**base, "status": "binding_required", "summary": "店铺不存在或已删除。"}

    from app.domain.browser.sessions import get_browser_session_store

    sessions = [
        session
        for session in get_browser_session_store().list()
        if session.intended_use == "ebk"
        and session.platform == normalized_platform
        and str(session.shop_key or "").strip() == key
    ]
    if not sessions:
        return {
            **base,
            "status": "binding_required",
            "summary": f"店铺尚未绑定 {normalized_platform} 平台的 EBK 账号。",
        }
    if len(sessions) > 1:
        return {
            **base,
            "status": "binding_conflict",
            "summary": (
                f"店铺绑定了 {len(sessions)} 个 {normalized_platform} EBK 账号，"
                "需在监控组显式指定当前账号或解绑多余账号。"
            ),
        }
    session = sessions[0]
    return {
        **base,
        "status": "resolved",
        "platform_account_id": session.platform_account_id,
        "browser_session_id": session.browser_session_id,
        "summary": "已解析到店铺的该平台 EBK 账号。",
    }
