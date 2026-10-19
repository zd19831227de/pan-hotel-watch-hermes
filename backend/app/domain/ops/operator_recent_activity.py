from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.competitor_prices.repository import (
    DEFAULT_WATCH_GROUP_KEY as DEFAULT_COMPETITOR_WATCH_GROUP_KEY,
    get_latest_competitor_price_snapshot,
)
from app.domain.ctrip_ebooking_watch.repository import (
    DEFAULT_DATA_CENTER_WATCH_GROUP_KEY,
    get_latest_data_center_watch_run,
)
from app.domain.price_change_alerts.repository import get_latest_price_change_alert_record
from app.domain.pricing_execution.models import PricingApprovalRecord


def _run_brief(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    run = payload.get("run") if isinstance(payload.get("run"), dict) else payload
    if not isinstance(run, dict):
        return None
    summary = run.get("summary")
    if summary is None and isinstance(payload.get("summary"), str):
        summary = payload.get("summary")
    return {
        "status": run.get("status"),
        "captured_at": run.get("captured_at"),
        "summary": summary,
    }


async def list_recent_pricing_approval_records(*, limit: int = 5) -> list[dict[str, Any]]:
    await ensure_db_ready()
    limit = max(1, min(int(limit or 5), 20))
    async with session_scope() as session:
        rows = list(
            (
                await session.scalars(
                    select(PricingApprovalRecord)
                    .order_by(desc(PricingApprovalRecord.created_at), desc(PricingApprovalRecord.id))
                    .limit(limit)
                )
            ).all()
        )
    return [
        {
            "approval_record_id": row.id,
            "status": row.status,
            "decision": row.decision,
            "summary": row.summary,
            "source": row.source,
            "execute_triggered": bool(row.execute_triggered),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _activity_rows(
    *,
    ebk_run: dict[str, Any] | None,
    competitor_run: dict[str, Any] | None,
    price_alert: dict[str, Any] | None,
    approvals: list[dict[str, Any]],
    ebk_unbound: bool = False,
    competitor_unbound: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if ebk_unbound:
        rows.append({"field": "EBK 最近读取", "value": "EBK 数据中心监控组未绑定"})
    elif ebk_run:
        rows.append(
            {
                "field": "EBK 最近读取",
                "value": f"{ebk_run.get('status') or '—'} · {_format_time(ebk_run.get('captured_at'))}",
            }
        )
    else:
        rows.append({"field": "EBK 最近读取", "value": "暂无记录"})
    if competitor_unbound:
        rows.append({"field": "竞品最近读取", "value": "竞品监控组未绑定"})
    elif competitor_run:
        rows.append(
            {
                "field": "竞品最近读取",
                "value": f"{competitor_run.get('status') or '—'} · {_format_time(competitor_run.get('captured_at'))}",
            }
        )
    else:
        rows.append({"field": "竞品最近读取", "value": "暂无记录"})
    if competitor_unbound:
        rows.append({"field": "最近价格变化", "value": "竞品监控组未绑定"})
    elif price_alert:
        rows.append(
            {
                "field": "最近价格变化",
                "value": str(price_alert.get("summary") or price_alert.get("status") or "—")[:120],
            }
        )
    else:
        rows.append({"field": "最近价格变化", "value": "暂无记录"})
    if approvals:
        latest = approvals[0]
        rows.append(
            {
                "field": "最近调价审批",
                "value": f"{latest.get('status') or '—'} · {str(latest.get('summary') or '')[:80]}",
            }
        )
    else:
        rows.append({"field": "最近调价审批", "value": "暂无记录"})
    return rows


def _format_time(value: Any) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    try:
        from datetime import datetime, timedelta, timezone

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        shanghai = timezone(timedelta(hours=8))
        return parsed.astimezone(shanghai).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


async def build_operator_recent_activity(
    *,
    ebk_watch_group_key: str | None = None,
    competitor_watch_group_key: str | None = None,
    allow_default_groups: bool = True,
) -> dict[str, Any]:
    ebk_key = (ebk_watch_group_key or (DEFAULT_DATA_CENTER_WATCH_GROUP_KEY if allow_default_groups else "")).strip()
    competitor_key = (
        competitor_watch_group_key or (DEFAULT_COMPETITOR_WATCH_GROUP_KEY if allow_default_groups else "")
    ).strip()
    ebk_payload = await get_latest_data_center_watch_run(watch_group_key=ebk_key) if ebk_key else None
    competitor_payload = (
        await get_latest_competitor_price_snapshot(watch_group_key=competitor_key)
        if competitor_key
        else None
    )
    price_alert = (
        await get_latest_price_change_alert_record(watch_group_key=competitor_key)
        if competitor_key
        else None
    )
    approvals = await list_recent_pricing_approval_records(limit=5)
    ebk_run = _run_brief(ebk_payload)
    competitor_run = _run_brief(competitor_payload)
    rows = _activity_rows(
        ebk_run=ebk_run,
        competitor_run=competitor_run,
        price_alert=price_alert,
        approvals=approvals,
        ebk_unbound=not bool(ebk_key),
        competitor_unbound=not bool(competitor_key),
    )
    intro_parts = [
        "以下为最近盯盘读取、价格变化与调价审批摘要（只读，来自数据库）。",
        f"EBK 组：{ebk_key or '未绑定'}；竞品组：{competitor_key or '未绑定'}。",
    ]
    return {
        "status": "success",
        "ebk_watch_group_key": ebk_key or None,
        "competitor_watch_group_key": competitor_key or None,
        "ebk_latest_run": ebk_run,
        "competitor_latest_run": competitor_run,
        "latest_price_change_alert": (
            {
                "status": price_alert.get("status"),
                "summary": price_alert.get("summary"),
                "changed_count": price_alert.get("changed_count"),
                "created_at": price_alert.get("created_at"),
            }
            if isinstance(price_alert, dict)
            else None
        ),
        "recent_pricing_approvals": approvals,
        "operator_summary": {
            "intro": "\n".join(intro_parts),
            "overview_rows": rows,
            "usage_note": (
                "本摘要不含 webhook/cookie/secrets。"
                "详细数字仍应使用 get_ebk_latest_result / get_competitor_latest_prices。"
            ),
        },
        "usage_note": "只读活动摘要；不触发浏览器或写价。",
    }
