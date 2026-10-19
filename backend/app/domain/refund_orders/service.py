"""退单抓取服务：挂在全局推送读取链路（推送时顺带抓）+ 供飞书问答实时触发。

与飞书推送无关：抓取结果只落库，供问答交互（Hermes 工具）读取。
"""

from __future__ import annotations

import logging
from typing import Any

from . import repository
from .adapters import (
    RefundAdapterError,
    RefundAdapterNotReadyError,
    get_refund_adapter,
)

logger = logging.getLogger(__name__)

REFUND_PLATFORMS = ("ctrip", "meituan", "fliggy")


async def fetch_refund_orders(
    *,
    platform: str,
    adapter: Any | None = None,
    source: str = "push_read",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """抓取当日退单并落库；平台未接入时返回 not_ready（无假数据）。"""
    adapter = adapter or get_refund_adapter(platform)
    try:
        if chat_id:
            result = await adapter.fetch_refund_orders(chat_id=chat_id)
        else:
            result = await adapter.fetch_refund_orders()
    except RefundAdapterNotReadyError as exc:
        return {"status": "not_ready", "platform": platform, "error": str(exc)}
    except RefundAdapterError as exc:
        return {"status": "error", "platform": platform, "error": str(exc)}
    except Exception as exc:
        # adapter 内 Playwright 连接失败/页面超时等非领域异常兜底，避免 API 500。
        return {
            "status": "error",
            "platform": platform,
            "error": f"退单抓取异常：{type(exc).__name__}: {str(exc)[:160]}",
        }
    # P3 §4.9.1：落库前解析店铺归属；解析失败不阻断抓取（按无归属落库）。
    try:
        from app.domain.order_browser import resolve_order_binding_context

        attribution = await resolve_order_binding_context(platform, chat_id=chat_id)
    except Exception:
        logger.exception("resolve order binding context failed for %s", platform)
        attribution = None
    snapshot = await repository.append_refund_snapshot(
        platform=platform,
        refund_date=str(result.get("refund_date") or ""),
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


async def fetch_refund_orders_silent(platform: str) -> None:
    """推送链路用的安全包装：任何失败只记日志，绝不影响主推送。"""
    try:
        result = await fetch_refund_orders(platform=platform, source="push_read")
        if result.get("status") not in {"ok", "not_ready"}:
            logger.warning("refund fetch failed for %s: %s", platform, result)
    except Exception:
        logger.exception("refund fetch crashed for %s", platform)


async def get_refund_status(
    *,
    platform: str,
    refund_date: str | None = None,
    shop_key: str | None = None,
) -> dict[str, Any] | None:
    return await repository.get_latest_refund_snapshot(
        platform=platform, refund_date=refund_date, shop_key=shop_key
    )


async def get_refund_status_range(
    *, platform: str, date_from: str, date_to: str
) -> dict[str, Any]:
    """历史范围查询：按 refund_date 范围取每日最新快照并汇总（只读持久化数据）。"""
    snapshots = await repository.list_refund_snapshots_by_date_range(
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
