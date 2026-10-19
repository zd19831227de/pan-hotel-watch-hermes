from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path

from app.domain.pricing_approvals.service import get_pricing_approval
from app.domain.pricing_execution.service import get_pricing_execution
from app.domain.pricing_suggestions.preview import get_pricing_preview

router = APIRouter(tags=["pricing-audit"])


@router.get("/pricing-previews/{preview_id}")
async def get_pricing_preview_route(preview_id: int = Path(ge=1)) -> dict[str, Any]:
    preview = await get_pricing_preview(preview_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="调价预览不存在。")
    return {"preview": preview}


@router.get("/pricing-approvals/{approval_id}")
async def get_pricing_approval_route(approval_id: int = Path(ge=1)) -> dict[str, Any]:
    approval = await get_pricing_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="调价审批记录不存在。")
    return {"approval": approval}


@router.get("/pricing-executions/{execution_id}")
async def get_pricing_execution_route(execution_id: int = Path(ge=1)) -> dict[str, Any]:
    execution = await get_pricing_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="调价执行记录不存在。")
    return {"execution": execution}
