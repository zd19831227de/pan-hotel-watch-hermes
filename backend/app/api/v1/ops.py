from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.domain.ops.feishu_chat_context import build_feishu_chat_context
from app.domain.ops.operator_pending_action import (
    build_pending_operator_summary,
    clear_pending_action,
    get_pending_action,
    upsert_immediate_run_offer,
)
from app.domain.ops.operator_recent_activity import build_operator_recent_activity
from app.domain.ops.operator_weekly_briefing import build_operator_weekly_briefing
from app.domain.ops.runtime_health import get_runtime_health
from app.domain.ops.smoke_checks import RealEnvSmokeCheckOptions, run_real_env_smoke_check

router = APIRouter(prefix="/ops", tags=["ops"])


class OperatorPendingActionUpsertRequest(BaseModel):
    chat_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    watch_kind: str = Field(min_length=1)
    watch_group_key: str = Field(min_length=1)
    source: str = "stale_latest_offer"


@router.get("/runtime-health")
async def runtime_health() -> dict[str, Any]:
    return await get_runtime_health()


@router.get("/feishu-chat-context")
async def feishu_chat_context(
    chat_id: str | None = Query(default=None),
    store_hint: str | None = Query(default=None),
) -> dict[str, Any]:
    return await build_feishu_chat_context(chat_id, store_hint=store_hint)


@router.get("/operator-recent-activity")
async def operator_recent_activity(
    ebk_watch_group_key: str | None = Query(default=None),
    competitor_watch_group_key: str | None = Query(default=None),
    allow_default_groups: bool = Query(default=True),
) -> dict[str, Any]:
    return await build_operator_recent_activity(
        ebk_watch_group_key=ebk_watch_group_key,
        competitor_watch_group_key=competitor_watch_group_key,
        allow_default_groups=allow_default_groups,
    )


@router.get("/weekly-briefing")
async def weekly_briefing(
    chat_id: str | None = Query(default=None),
    store_hint: str | None = Query(default=None),
    reference_date: date | None = Query(default=None),
) -> dict[str, Any]:
    return await build_operator_weekly_briefing(
        chat_id=chat_id,
        store_hint=store_hint,
        reference_date=reference_date,
    )


@router.put("/operator-pending-action")
async def put_operator_pending_action(request: OperatorPendingActionUpsertRequest) -> dict[str, Any]:
    record = await upsert_immediate_run_offer(
        chat_id=request.chat_id,
        operator_id=request.operator_id,
        watch_kind=request.watch_kind,
        watch_group_key=request.watch_group_key,
        source=request.source,
    )
    if record.get("status") == "invalid_arguments":
        return record
    return {
        "status": "success",
        "pending_action": record,
        "operator_summary": {
            "pending_operator_action": build_pending_operator_summary(record),
        },
    }


@router.get("/operator-pending-action")
async def read_operator_pending_action(
    chat_id: str = Query(min_length=1),
    operator_id: str = Query(min_length=1),
) -> dict[str, Any]:
    pending = await get_pending_action(chat_id, operator_id)
    return {
        "status": "success",
        "pending_action": pending,
        "operator_summary": {
            "pending_operator_action": build_pending_operator_summary(pending),
        },
    }


@router.delete("/operator-pending-action")
async def delete_operator_pending_action(
    chat_id: str = Query(min_length=1),
    operator_id: str = Query(min_length=1),
) -> dict[str, Any]:
    cleared = await clear_pending_action(chat_id, operator_id)
    return {"status": "success", "cleared": cleared}


@router.post("/real-env-smoke-check")
async def real_env_smoke_check(
    request: RealEnvSmokeCheckOptions | None = None,
) -> dict[str, Any]:
    return await run_real_env_smoke_check(request or RealEnvSmokeCheckOptions())
