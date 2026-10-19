from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.price_change_alerts.models import PriceChangeAlertRecord
from app.domain.competitor_prices.models import local_now
from app.domain.price_change_alerts.types import PriceChangeAlertResult
from app.domain.operator_payload import sanitize_persisted_payload


async def save_price_change_alert_record(
    *,
    result: PriceChangeAlertResult,
    actor_id: str,
) -> PriceChangeAlertResult:
    await ensure_db_ready()
    async with session_scope() as session:
        record = PriceChangeAlertRecord(
            watch_group_key=result.watch_group_key,
            current_run_id=result.current_run_id,
            previous_run_id=result.previous_run_id,
            status=result.status,
            summary=result.summary,
            changed_count=result.changed_count,
            suggestion_count=result.suggestion_count,
            compared_key_count=result.compared_key_count,
            changed_items_json=_strip_sensitive(result.changed_items),
            suggestions_json=_strip_sensitive(result.suggestions),
            evidence_json=_strip_sensitive(result.evidence),
            feishu_push_json=_strip_sensitive(result.feishu_push),
            actor_id=actor_id.strip() or "operator",
        )
        session.add(record)
        await session.flush()
        result.alert_record_id = int(record.id)
    return result


async def update_price_change_alert_push_result(
    *,
    alert_record_id: int,
    feishu_push: dict[str, Any],
) -> None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PriceChangeAlertRecord, alert_record_id)
        if record is not None:
            record.feishu_push_json = _strip_sensitive(feishu_push)


async def get_latest_price_change_alert_record(
    *,
    watch_group_key: str | None = None,
) -> dict[str, Any] | None:
    records = await list_price_change_alert_records(watch_group_key=watch_group_key, limit=1)
    return records[0] if records else None


async def list_price_change_alert_records(
    *,
    watch_group_key: str | None = None,
    limit: int = 20,
    days: int | None = None,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    limit = max(1, min(int(limit or 20), 100))
    async with session_scope() as session:
        statement = select(PriceChangeAlertRecord)
        if watch_group_key:
            statement = statement.where(PriceChangeAlertRecord.watch_group_key == watch_group_key)
        if days is not None:
            cutoff = local_now() - timedelta(days=max(1, min(int(days), 3650)))
            statement = statement.where(PriceChangeAlertRecord.created_at >= cutoff)
        statement = statement.order_by(
            desc(PriceChangeAlertRecord.created_at),
            desc(PriceChangeAlertRecord.id),
        ).limit(limit)
        rows = list((await session.scalars(statement)).all())
    return [_record_to_dict(row) for row in rows]


def _record_to_dict(record: PriceChangeAlertRecord) -> dict[str, Any]:
    evidence = record.evidence_json or {}
    account_policy = evidence.get("account_write_policy") if isinstance(evidence, dict) else None
    if not isinstance(account_policy, dict):
        account_policy = {
            "connection_mode": "unknown",
            "can_auto_write_price": False,
            "write_policy_message": "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。",
        }
    return {
        "id": record.id,
        "watch_group_key": record.watch_group_key,
        "connection_mode": str(account_policy.get("connection_mode") or "unknown"),
        "can_auto_write_price": bool(account_policy.get("can_auto_write_price")),
        "write_policy_message": str(
            account_policy.get("write_policy_message")
            or "账号类型未确认，默认按直连处理，仅生成调价建议，不会自动改价。"
        ),
        "current_run_id": record.current_run_id,
        "previous_run_id": record.previous_run_id,
        "status": record.status,
        "summary": record.summary,
        "changed_count": record.changed_count,
        "suggestion_count": record.suggestion_count,
        "compared_key_count": record.compared_key_count,
        "changed_items": record.changed_items_json or [],
        "suggestions": record.suggestions_json or [],
        "evidence": evidence,
        "feishu_push": record.feishu_push_json,
        "created_at": record.created_at.isoformat(),
    }


def _strip_sensitive(value: Any) -> Any:
    return sanitize_persisted_payload(value)
