"""蒸馏 API（V6 Step 2）：数据源管理 + 手动/一次性蒸馏 + 运行记录。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.api.security import require_supervisor_api_key
from app.domain.distill import repository, service
from app.domain.distill.service import SUPPORTED_PLATFORMS, SUPPORTED_SOURCE_TYPES
from app.domain.exports.csv_builder import build_csv_bytes
from app.domain.operator_payload import sanitize_operator_payload

router = APIRouter(prefix="/distill", tags=["distill"])

DISTILL_MIN_SCHEDULE_INTERVAL_MINUTES = 30
DISTILL_MAX_SCHEDULE_INTERVAL_MINUTES = 10080


def _validate_schedule_interval_minutes(value: int) -> int:
    """F4-F：0=仅手动；非 0 值必须 ≥30 分钟（防过频触发平台风控）。"""
    if value == 0:
        return value
    if value < DISTILL_MIN_SCHEDULE_INTERVAL_MINUTES:
        raise ValueError(
            "schedule_interval_minutes 必须 ≥ 30 分钟（0 表示仅手动）",
        )
    return value


class DistillSourceCreateRequest(BaseModel):
    source_type: str = Field(pattern=r"^(text|image_url|video_url)$")
    content: str = Field(min_length=1, max_length=20000)
    platform: str = Field(pattern=r"^(doubao|qwen)$")
    title: str = Field(default="", max_length=256)
    distill_prompt: str = Field(default="", max_length=4000)
    schedule_interval_minutes: int = Field(
        default=120,
        ge=0,
        le=DISTILL_MAX_SCHEDULE_INTERVAL_MINUTES,
    )
    enabled: bool = True

    _validate_interval = field_validator("schedule_interval_minutes")(
        _validate_schedule_interval_minutes
    )


class DistillRunRequest(BaseModel):
    source_type: str = Field(pattern=r"^(text|image_url|video_url)$")
    content: str = Field(min_length=1, max_length=20000)
    platform: str = Field(pattern=r"^(doubao|qwen)$")
    distill_prompt: str = Field(default="", max_length=4000)
    title: str = Field(default="", max_length=256)
    actor_id: str = "operator"


class DistillSourceRunRequest(BaseModel):
    actor_id: str = "operator"


class DistillSourcePatchRequest(BaseModel):
    enabled: bool | None = None
    schedule_interval_minutes: int | None = Field(
        default=None,
        ge=0,
        le=DISTILL_MAX_SCHEDULE_INTERVAL_MINUTES,
    )

    _validate_interval = field_validator("schedule_interval_minutes")(
        _validate_schedule_interval_minutes
    )


@router.post("/sources", status_code=201)
async def create_distill_source(request: DistillSourceCreateRequest) -> dict[str, Any]:
    source = await repository.create_distill_source(
        source_type=request.source_type,
        content=request.content,
        platform=request.platform,
        title=request.title,
        distill_prompt=request.distill_prompt,
        schedule_interval_minutes=request.schedule_interval_minutes,
        enabled=request.enabled,
    )
    return sanitize_operator_payload(source)


@router.get("/sources")
async def list_distill_sources(enabled_only: bool = False) -> dict[str, Any]:
    sources = await repository.list_distill_sources(enabled_only=enabled_only)
    return {"sources": sanitize_operator_payload(sources), "count": len(sources)}


@router.patch("/sources/{source_id}")
async def patch_distill_source(
    source_id: int,
    request: DistillSourcePatchRequest,
) -> dict[str, Any]:
    if request.enabled is None and request.schedule_interval_minutes is None:
        raise HTTPException(status_code=422, detail="至少提供 enabled 或 schedule_interval_minutes 之一")
    try:
        updated = await repository.update_distill_source_fields(
            source_id,
            enabled=request.enabled,
            schedule_interval_minutes=request.schedule_interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"distill source {source_id} 不存在")
    return sanitize_operator_payload(updated)


@router.delete("/sources/{source_id}")
async def delete_distill_source(source_id: int) -> dict[str, Any]:
    deleted = await repository.delete_distill_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"distill source {source_id} 不存在")
    return {"status": "success", "summary": f"distill source {source_id} 已删除"}


@router.post("/runs")
async def run_distill_once(request: DistillRunRequest) -> dict[str, Any]:
    """一次性蒸馏（MCP 工具 / 手动触发共用入口）。"""
    result = await service.run_distill(
        source_type=request.source_type,
        content=request.content,
        platform=request.platform,
        distill_prompt=request.distill_prompt,
        title=request.title,
        actor_id=request.actor_id,
    )
    return sanitize_operator_payload(result)


@router.post("/sources/{source_id}/run")
async def run_distill_source(
    source_id: int,
    request: DistillSourceRunRequest,
) -> dict[str, Any]:
    result = await service.run_distill_source(source_id)
    return sanitize_operator_payload(result)


@router.get("/runs")
async def list_distill_runs(limit: int = 20) -> dict[str, Any]:
    runs = await repository.list_distill_runs(limit=limit)
    return {"runs": sanitize_operator_payload(runs), "count": len(runs)}


@router.get("/runs/{run_id}/export", dependencies=[Depends(require_supervisor_api_key)])
async def export_distill_run_csv(run_id: int) -> Response:
    """单次蒸馏运行导出（F2-C）：该次运行提取的知识条目与拒绝原因（CSV，utf-8-sig）。"""
    run = await repository.get_distill_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"distill run {run_id} 不存在")
    headers = [
        "id",
        "created_at",
        "platform",
        "source_type",
        "status",
        "knowledge_title",
        "rag_document_id",
        "summary",
        "extracted_text",
        "reject_reason",
    ]
    payload = build_csv_bytes(headers=headers, rows=[run])
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="distill-run-{run_id}.csv"'},
    )


@router.get("/meta")
async def distill_meta() -> dict[str, Any]:
    return {
        "source_types": list(SUPPORTED_SOURCE_TYPES),
        "platforms": list(SUPPORTED_PLATFORMS),
        "admission_rule": "蒸馏入库只允许知识类内容；实时数据（价格/房态/经营数字）一律拒绝。",
    }
