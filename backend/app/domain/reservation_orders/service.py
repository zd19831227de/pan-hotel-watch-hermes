"""今日预定抓取服务：只供飞书 @机器人触发抓取（只读），不挂定时轮询。

抓取结果只落库，供飞书问答交互（Hermes 工具）读取。
"""

from __future__ import annotations

import logging
from typing import Any

from . import repository
from .adapters import (
    ReservationAdapterError,
    ReservationAdapterNotReadyError,
    get_reservation_adapter,
)

logger = logging.getLogger(__name__)

# 携程 + 飞猪 + 美团（@机器人触发，只读）。
RESERVATION_PLATFORMS = ("ctrip", "fliggy", "meituan")


async def fetch_today_reservations(
    *,
    platform: str,
    adapter: Any | None = None,
    source: str = "manual",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """抓取当日今日预定并落库；平台未接入时返回 not_ready（无假数据）。"""
    adapter = adapter or get_reservation_adapter(platform)
    try:
        if chat_id:
            result = await adapter.fetch_today_reservations(chat_id=chat_id)
        else:
            result = await adapter.fetch_today_reservations()
    except ReservationAdapterNotReadyError as exc:
        return {"status": "not_ready", "platform": platform, "error": str(exc)}
    except ReservationAdapterError as exc:
        return {"status": "error", "platform": platform, "error": str(exc)}
    except Exception as exc:
        # adapter 内 Playwright 连接失败/页面超时等非领域异常兜底，避免 API 500。
        return {
            "status": "error",
            "platform": platform,
            "error": f"今日预定抓取异常：{type(exc).__name__}: {str(exc)[:160]}",
        }
    # P3 §4.9.1：落库前解析店铺归属；解析失败不阻断抓取（按无归属落库）。
    try:
        from app.domain.order_browser import resolve_order_binding_context

        attribution = await resolve_order_binding_context(platform, chat_id=chat_id)
    except Exception:
        logger.exception("resolve order binding context failed for %s", platform)
        attribution = None
    snapshot = await repository.append_reservation_snapshot(
        platform=platform,
        reservation_date=str(result.get("reservation_date") or ""),
        total_count=int(result.get("total_count") or 0),
        by_room_type=result.get("by_room_type") or {},
        by_hour=result.get("by_hour") or None,
        time_caliber=result.get("time_caliber"),
        source=source,
        shop_key=(attribution or {}).get("shop_key"),
        platform_account_id=(attribution or {}).get("platform_account_id"),
        browser_session_id=(attribution or {}).get("browser_session_id"),
    )
    return {"status": "ok", "platform": platform, "snapshot": snapshot}


async def get_today_reservations(
    *,
    platform: str,
    reservation_date: str | None = None,
    shop_key: str | None = None,
) -> dict[str, Any] | None:
    return await repository.get_latest_reservation_snapshot(
        platform=platform, reservation_date=reservation_date, shop_key=shop_key
    )


async def get_reservation_status_range(
    *, platform: str, date_from: str, date_to: str
) -> dict[str, Any]:
    """历史范围查询：按 reservation_date 范围取每日最新快照并汇总（只读持久化数据）。"""
    snapshots = await repository.list_reservation_snapshots_by_date_range(
        platform=platform, date_from=date_from, date_to=date_to
    )
    by_room: dict[str, int] = {}
    for snapshot in snapshots:
        for room, count in (snapshot.get("by_room_type") or {}).items():
            by_room[room] = by_room.get(room, 0) + int(count)
    return {
        "platform": platform,
        "date_range": {"from": date_from, "to": date_to},
        "snapshots": snapshots,
        "total_count": sum(int(s.get("total_count") or 0) for s in snapshots),
        "by_room_type": by_room,
    }
