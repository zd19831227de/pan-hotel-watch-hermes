from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.domain.ebk_snapshots.ebk_snapshot_reader import read_all_ebk_snapshots
from app.domain.ebk_snapshots.repository import get_latest_ebk_snapshot
from app.domain.operator_payload import sanitize_operator_payload

router = APIRouter(prefix="/ebk-snapshots", tags=["ebk-snapshots"])


class EbkSnapshotReadAllRequest(BaseModel):
    actor_id: str = "operator"
    ctrip_platform_account_id: str | None = Field(default=None, min_length=1)
    meituan_platform_account_id: str | None = Field(default=None, min_length=1)
    fliggy_platform_account_id: str | None = Field(default=None, min_length=1)
    platforms: list[str] | None = None
    headless: bool = False
    push_to_feishu: bool = False
    feishu_watch_group_key: str | None = Field(default=None, min_length=1)
    trigger_via: str | None = Field(default=None, min_length=1, max_length=32)


@router.post("/read-all")
async def read_all_ebk_snapshots_route(
    request: EbkSnapshotReadAllRequest,
) -> dict[str, Any]:
    try:
        return await read_all_ebk_snapshots(
            ctrip_platform_account_id=request.ctrip_platform_account_id,
            meituan_platform_account_id=request.meituan_platform_account_id,
            fliggy_platform_account_id=request.fliggy_platform_account_id,
            platforms=request.platforms,
            actor_id=request.actor_id,
            headless=request.headless,
            push_to_feishu=request.push_to_feishu,
            feishu_watch_group_key=request.feishu_watch_group_key,
            trigger_via=request.trigger_via,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/latest")
async def get_latest_ebk_snapshot_route(
    platform: str = Query(min_length=1),
    platform_account_id: str = Query(min_length=1),
) -> dict[str, Any]:
    normalized_platform = platform.strip().lower()
    if normalized_platform not in {"ctrip", "meituan", "fliggy"}:
        raise HTTPException(
            status_code=422,
            detail="platform must be ctrip, meituan, or fliggy.",
        )
    latest = await get_latest_ebk_snapshot(
        platform=normalized_platform,
        platform_account_id=platform_account_id.strip(),
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="No EBK snapshot found.")
    return sanitize_operator_payload(latest)
