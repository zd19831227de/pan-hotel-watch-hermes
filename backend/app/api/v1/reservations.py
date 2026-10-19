from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security import require_operator_api_key
from app.domain.reservation_orders import service
from app.domain.reservation_orders.service import RESERVATION_PLATFORMS

router = APIRouter(prefix="/reservations", tags=["reservations"])

_PLATFORM_QUERY = Query(pattern="^(ctrip|fliggy|meituan)$")
_PLATFORM_QUERY_OR_NONE = Query(default=None, pattern="^(ctrip|fliggy|meituan)$")


class ReservationFetchRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|fliggy|meituan)$")
    chat_id: str | None = None


def _ensure_valid_calendar_dates(*values: str) -> None:
    """拦下 2026-02-31 这类正则通过但日历上不存在的日期（否则静默查空）。"""
    for value in values:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"日期非法：{value}（需为真实存在的 YYYY-MM-DD 日期）。",
            ) from None


@router.get("")
async def get_reservation_route(
    platform: str | None = _PLATFORM_QUERY_OR_NONE,
    reservation_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    shop_key: str | None = Query(default=None, pattern=r"^shop_[A-Za-z0-9_-]+$"),
) -> dict[str, Any]:
    # 历史范围模式：date_from/date_to（可只传一个，另一端取同日）；按业务日期过滤。
    if date_from or date_to:
        start = date_from or date_to
        end = date_to or date_from
        _ensure_valid_calendar_dates(start, end)
        if start > end:
            raise HTTPException(status_code=422, detail="date_from 不能晚于 date_to。")
        if platform:
            return await service.get_reservation_status_range(
                platform=platform, date_from=start, date_to=end
            )
        results = []
        for p in RESERVATION_PLATFORMS:
            results.append(
                await service.get_reservation_status_range(
                    platform=p, date_from=start, date_to=end
                )
            )
        return {
            "date_range": {"from": start, "to": end},
            "platforms": results,
            "total_count": sum(int(r.get("total_count") or 0) for r in results),
        }
    if platform:
        snapshot = await service.get_today_reservations(
            platform=platform, reservation_date=reservation_date, shop_key=shop_key
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="该平台还没有今日预定抓取记录。")
        return {"snapshot": snapshot}
    # 不指定平台时返回已接入平台最新汇总（供飞书问答「今天订了多少」）。
    snapshots = []
    for p in RESERVATION_PLATFORMS:
        snapshot = await service.get_today_reservations(
            platform=p, reservation_date=reservation_date, shop_key=shop_key
        )
        if snapshot is not None:
            snapshots.append(snapshot)
    return {"snapshots": snapshots}


@router.post("/fetch", dependencies=[Depends(require_operator_api_key)])
async def fetch_reservation_route(request: ReservationFetchRequest) -> dict[str, Any]:
    result = await service.fetch_today_reservations(
        platform=request.platform, source="manual", chat_id=request.chat_id
    )
    return {"result": result}
