"""删除一条页面健康告警记录，确认已处理后可移除。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query

from app.domain.page_change_alerts import repository

router = APIRouter(prefix="/page-health", tags=["page-health"])


@router.get("/alerts")
async def list_page_health_alerts_route(
    platform: str | None = Query(default=None, min_length=1, max_length=32),
    level: Literal["hard_fail", "partial_fail", "layout_changed"] | None = Query(
        default=None
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    alerts = await repository.list_page_health_alerts(
        platform=platform,
        level=level,
        limit=limit,
    )
    return {"alerts": alerts, "count": len(alerts)}

@router.delete("/alerts/{alert_id}")
async def delete_page_health_alert_route(alert_id: int) -> dict[str, Any]:
    """删除一条页面健康告警记录，确认已处理后可移除。"""
    removed = await repository.delete_page_health_alert(alert_id)
    if not removed:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="告警不存在或已被删除")
    return {"status": "success", "deleted_id": alert_id}

