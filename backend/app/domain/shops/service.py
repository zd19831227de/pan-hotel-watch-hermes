from __future__ import annotations

from typing import Any

from . import repository

SHOP_PLATFORMS = ("ctrip", "meituan", "fliggy")


class ShopNotFound(Exception):
    """shop_key 不存在（API 层映射 404）。"""


class ShopReferenceBlocked(Exception):
    """店铺仍被引用，禁止删除（API 层映射 409）。"""

    def __init__(self, references: list[str]) -> None:
        super().__init__("店铺仍有引用绑定，禁止删除。")
        self.references = references


async def collect_shop_references(shop_key: str) -> list[str]:
    """删除前核查的引用来源清单（计划 §3.2.1：组/账号/推送/评价/持久化归属）。

    v2 §2：三平台监控组均按自身 shop_key 直连统计；EBK 账号按 session 归属。
    每项返回「来源: 标识」的可读引用。
    """
    references: list[str] = []
    from sqlalchemy import select as _select

    from app.db.session import ensure_db_ready as _ensure_db_ready
    from app.db.session import session_scope as _session_scope
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
    from app.domain.fliggy_ebooking_watch.models import FliggyEbookingWatchGroup
    from app.domain.meituan_ebooking_watch.models import MeituanEbookingWatchGroup

    await _ensure_db_ready()
    async with _session_scope() as session:
        for label, model in (
            ("携程监控组", CtripEbookingWatchGroup),
            ("美团监控组", MeituanEbookingWatchGroup),
            ("飞猪监控组", FliggyEbookingWatchGroup),
        ):
            groups = (
                (
                    await session.execute(
                        _select(model).where(model.shop_key == shop_key)
                    )
                )
                .scalars()
                .all()
            )
            references.extend(f"{label}:{group.group_key}" for group in groups)
    try:
        from app.domain.browser.sessions import get_browser_session_store

        for session in get_browser_session_store().list():
            if (
                session.intended_use == "ebk"
                and str(session.shop_key or "").strip() == shop_key
            ):
                references.append(f"EBK 账号:{session.platform_account_id}")
    except Exception:  # noqa: BLE001 - 浏览器状态不可用时仅少报一类引用
        pass
    return references


def _shop_to_dict(shop: Any) -> dict[str, Any]:
    return {
        "shop_key": shop.shop_key,
        "name": shop.name,
        "short_name": shop.short_name,
        "sort_order": shop.sort_order,
        "created_at": shop.created_at.isoformat(),
        "updated_at": shop.updated_at.isoformat(),
    }


async def list_shops() -> list[dict[str, Any]]:
    return [_shop_to_dict(shop) for shop in await repository.list_shops()]


async def create_shop(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("店铺名称不能为空。")
    short_name = payload.get("short_name")
    shop = await repository.create_shop(
        name=name,
        short_name=str(short_name).strip() or None if short_name is not None else None,
        sort_order=int(payload.get("sort_order") or 0),
    )
    return _shop_to_dict(shop)


async def get_shop(shop_key: str) -> dict[str, Any]:
    shop = await repository.get_shop_by_key(shop_key)
    if shop is None:
        raise ShopNotFound(shop_key)
    return _shop_to_dict(shop)


async def update_shop(shop_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if payload.get("name") is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise ValueError("店铺名称不能为空。")
        updates["name"] = name
    if payload.get("short_name") is not None:
        updates["short_name"] = str(payload["short_name"]).strip() or None
    if payload.get("sort_order") is not None:
        updates["sort_order"] = int(payload["sort_order"])
    shop = await repository.update_shop_by_key(shop_key, **updates)
    if shop is None:
        raise ShopNotFound(shop_key)
    return _shop_to_dict(shop)


async def delete_shop(shop_key: str) -> dict[str, Any]:
    existing = await repository.get_shop_by_key(shop_key)
    if existing is None:
        raise ShopNotFound(shop_key)
    references = await collect_shop_references(shop_key)
    if references:
        raise ShopReferenceBlocked(references)
    await repository.delete_shop_by_key(shop_key)
    return {"deleted": True, "shop_key": shop_key}


async def _anchor_groups_by_shop() -> dict[str, Any]:
    """shop_key → 主携程锚点组（每店至多一个，唯一索引兜底）。"""
    from sqlalchemy import select as _select

    from app.db.session import ensure_db_ready as _ensure_db_ready
    from app.db.session import session_scope as _session_scope
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup

    await _ensure_db_ready()
    async with _session_scope() as session:
        groups = (
            (
                await session.execute(
                    _select(CtripEbookingWatchGroup).where(
                        CtripEbookingWatchGroup.shop_key.isnot(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    return {str(group.shop_key): group for group in groups}


async def _platform_groups_by_shop(platform: str) -> dict[str, Any]:
    """shop_key → 该平台按自身 shop_key 直连的监控组（v2 §2，每店至多一个）。"""
    from sqlalchemy import select as _select

    from app.db.session import ensure_db_ready as _ensure_db_ready
    from app.db.session import session_scope as _session_scope

    if platform == "meituan":
        from app.domain.meituan_ebooking_watch.models import (
            MeituanEbookingWatchGroup as Model,
        )
    elif platform == "fliggy":
        from app.domain.fliggy_ebooking_watch.models import (
            FliggyEbookingWatchGroup as Model,
        )
    else:
        return {}
    await _ensure_db_ready()
    async with _session_scope() as session:
        groups = (
            (
                await session.execute(
                    _select(Model).where(Model.shop_key.isnot(None))
                )
            )
            .scalars()
            .all()
        )
    return {str(group.shop_key): group for group in groups}


async def _unassigned_watch_group_keys() -> list[dict[str, str]]:
    """无店铺归属的监控组清单（v2 §2 未分组区：三平台如实上报，key+平台+名称）。"""
    from sqlalchemy import select as _select

    from app.db.session import ensure_db_ready as _ensure_db_ready
    from app.db.session import session_scope as _session_scope
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
    from app.domain.fliggy_ebooking_watch.models import FliggyEbookingWatchGroup
    from app.domain.meituan_ebooking_watch.models import MeituanEbookingWatchGroup

    await _ensure_db_ready()
    unassigned: list[dict[str, str]] = []
    async with _session_scope() as session:
        for platform, model in (
            ("ctrip", CtripEbookingWatchGroup),
            ("meituan", MeituanEbookingWatchGroup),
            ("fliggy", FliggyEbookingWatchGroup),
        ):
            groups = (
                (
                    await session.execute(
                        _select(model).where(model.shop_key.is_(None))
                    )
                )
                .scalars()
                .all()
            )
            unassigned.extend(
                {
                    "platform": platform,
                    "group_key": group.group_key,
                    "name": group.name,
                }
                for group in groups
            )
    return unassigned


def _tree_node(
    shop: Any,
    ebk_sessions: list[Any],
    anchor: Any | None,
    direct_groups: dict[str, dict[str, Any]],
    competitor_group_keys: list[str] | None = None,
) -> dict[str, Any]:
    """树节点绑定状态（v2 §2：三平台监控组均按自身 shop_key 直连）。

    - ctrip：锚点组已绑账号 → resolved；有组无账号 → binding_required；
      无组时若有归属账号 → resolved（仅账号级，组待建）。
    - meituan/fliggy：直连组（shop_key 相同）已绑账号 → resolved；
      有组无账号 → binding_required；无组时若有归属账号 → resolved（仅账号级）。
    不冒充可用；legacy 挂载键不参与展示（迁移回填后自然消失）。
    """

    def account_binding(platform: str) -> dict[str, Any]:
        matched = any(
            session.platform == platform
            and str(getattr(session, "shop_key", "") or "") == shop.shop_key
            for session in ebk_sessions
        )
        if matched:
            return {"status": "resolved", "summary": "已绑定该平台 EBK 账号。"}
        return {"status": "binding_required", "summary": "尚未绑定该平台的 EBK 账号。"}

    def group_binding(platform: str, group: Any | None, label: str) -> dict[str, Any]:
        if group is None:
            return account_binding(platform)
        group_account = str(getattr(group, "platform_account_id", "") or "").strip()
        if group_account and not group_account.startswith("__"):
            return {
                "status": "resolved",
                "group_key": group.group_key,
                "platform_account_id": group_account,
                "browser_session_id": getattr(group, "browser_session_id", None),
                "summary": f"已绑定该店铺的{label}监控组。",
            }
        return {
            "status": "binding_required",
            "group_key": group.group_key,
            "summary": f"{label}监控组尚未绑定 EBK 账号。",
        }

    bindings: dict[str, Any] = {
        "ctrip": group_binding("ctrip", anchor, "携程"),
        "meituan": group_binding(
            "meituan", direct_groups.get("meituan", {}).get(shop.shop_key), "美团"
        ),
        "fliggy": group_binding(
            "fliggy", direct_groups.get("fliggy", {}).get(shop.shop_key), "飞猪"
        ),
    }
    return {
        "shop_key": shop.shop_key,
        "name": shop.name,
        "short_name": shop.short_name,
        "sort_order": shop.sort_order,
        "bindings": bindings,
        "competitor_group_count": len(competitor_group_keys or []),
        "competitor_group_keys": list(competitor_group_keys or []),
    }


async def list_shop_tree() -> dict[str, Any]:
    shops = await repository.list_shops()
    try:
        from app.domain.browser.sessions import get_browser_session_store

        sessions = get_browser_session_store().list()
    except Exception:  # noqa: BLE001 - 浏览器状态不可用时树仍要可读
        sessions = []
    ebk_sessions = [
        session for session in sessions if session.intended_use == "ebk"
    ]
    anchors = await _anchor_groups_by_shop()
    direct_groups = {
        platform: await _platform_groups_by_shop(platform)
        for platform in ("meituan", "fliggy")
    }
    # 竞品组归属摘要（P5）：只列 key，详情仍在全局竞品区查看。
    # 迁移滞后/查询异常时降级为空摘要，不让整棵树 500。
    competitor_keys_by_shop: dict[str, list[str]] = {}
    try:
        from sqlalchemy import select as _select2

        from app.db.session import ensure_db_ready as _ensure2
        from app.db.session import session_scope as _scope2
        from app.domain.competitor_prices.models import CtripCompetitorWatchGroup

        await _ensure2()
        async with _scope2() as session:
            competitor_groups = (
                (
                    await session.execute(
                        _select2(CtripCompetitorWatchGroup).where(
                            CtripCompetitorWatchGroup.shop_key.isnot(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for group in competitor_groups:
            competitor_keys_by_shop.setdefault(str(group.shop_key), []).append(
                group.group_key
            )
    except Exception:  # noqa: BLE001 - 摘要缺失不阻断树
        competitor_keys_by_shop = {}
    unassigned_count = sum(
        1 for session in ebk_sessions if not str(session.shop_key or "").strip()
    )
    unassigned_groups = await _unassigned_watch_group_keys()
    return {
        "shops": [
            _tree_node(
                shop,
                ebk_sessions,
                anchors.get(shop.shop_key),
                direct_groups,
                competitor_keys_by_shop.get(shop.shop_key),
            )
            for shop in shops
        ],
        "unassigned": {
            "summary": "未分组 = 无店铺归属的旧账号/旧监控组；补绑店铺后自动归位。",
            "ebk_account_count": unassigned_count,
            "watch_groups": unassigned_groups,
            "watch_group_keys": [item["group_key"] for item in unassigned_groups],
        },
    }
