from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.api.security import require_operator_api_key
from app.core.settings import get_settings
from app.domain.ebk_global_push.repository import (
    CTRIP_WATCH_GROUP_KEY_PATTERN,
    EbkGlobalPushConfigCreate,
    EbkGlobalPushConfigPatch,
    claim_global_push_run,
    create_global_push_config,
    fail_stale_running_runs,
    finish_global_push_run,
    get_global_push_config,
    list_global_push_configs,
    list_global_push_runs,
    mark_config_run_finished,
    soft_disable_global_push_config,
    update_global_push_config,
)
from app.domain.ebk_global_push.scheduling import compute_next_run_at
from app.domain.ebk_global_push.service import run_global_push, default_deps
from app.domain.meituan_ebooking_watch.models import local_now
from app.domain.ops.worker_heartbeat import get_worker_heartbeat_status

router = APIRouter(prefix="/ebk-global-push", tags=["ebk-global-push"])
logger = logging.getLogger(__name__)

ANCHOR_KEY_PATH = Path(pattern=CTRIP_WATCH_GROUP_KEY_PATTERN)


async def _legacy_still_enabled_reasons(anchor_key: str) -> list[str]:
    """§4.2 API 侧启用前校验：旧 auto_poll_enabled 未关闭时拒绝启用。"""

    from app.domain.ctrip_ebooking_watch.repository import (
        get_data_center_watch_group_runtime,
    )

    reasons: list[str] = []
    anchor = await get_data_center_watch_group_runtime(watch_group_key=anchor_key)
    if anchor is not None and bool(getattr(anchor, "auto_poll_enabled", False)):
        reasons.append("携程")
    for platform in ("meituan", "fliggy"):
        if platform == "meituan":
            from app.domain.meituan_ebooking_watch.repository import (
                list_meituan_watch_groups,
            )

            groups = await list_meituan_watch_groups()
        else:
            from app.domain.fliggy_ebooking_watch.repository import (
                list_fliggy_watch_groups,
            )

            groups = await list_fliggy_watch_groups()
        for group in groups:
            if (
                str(getattr(group, "ctrip_watch_group_key", "") or "") == anchor_key
                and bool(getattr(group, "auto_poll_enabled", False))
            ):
                reasons.append("美团" if platform == "meituan" else "飞猪")
    return reasons


async def _assert_legacy_disabled(anchor_key: str) -> None:
    reasons = await _legacy_still_enabled_reasons(anchor_key)
    if reasons:
        raise HTTPException(
            status_code=422,
            detail=(
                f"legacy_schedule_still_enabled：{'、'.join(reasons)}旧自动排程仍开启，"
                "请先在对应平台控制台关闭 auto_poll_enabled 后再启用全局推送。"
            ),
        )


class RunNowResponse(BaseModel):
    run_id: int
    status: str
    platform_results: dict[str, Any] = Field(default_factory=dict)
    card_results: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


@router.get("/configs")
async def list_configs() -> dict[str, Any]:
    return {"configs": await list_global_push_configs()}


@router.post("/configs", dependencies=[Depends(require_operator_api_key)])
async def create_config(request: EbkGlobalPushConfigCreate) -> dict[str, Any]:
    if request.enabled:
        await _assert_legacy_disabled(request.ctrip_watch_group_key)
    try:
        config = await create_global_push_config(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=409, detail="该携程监控组已存在全局推送配置（一锚点一条记录，软停用后复用）。")
    return {"config": config}


@router.get("/configs/{config_id}")
async def get_config(config_id: int) -> dict[str, Any]:
    config = await get_global_push_config(config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="全局推送配置不存在。")
    return {
        "config": config,
        "platform_targets": await _resolved_platform_targets(config),
    }


async def _resolved_platform_targets(config: dict[str, Any]) -> list[dict[str, Any]]:
    """设计 §9/§10.1：解析后的三平台账号状态；只返回展示名/平台/状态/摘要。"""

    from app.domain.browser.sessions import get_browser_session_store
    from app.domain.ebk_global_push.service import (
        default_deps,
        resolve_platform_targets,
    )

    enabled = [
        platform
        for platform in ("ctrip", "meituan", "fliggy")
        if platform in set(config.get("enabled_platforms") or [])
    ]
    anchor = None
    try:
        anchor = await default_deps().get_ctrip_group(config["ctrip_watch_group_key"])
    except Exception:  # noqa: BLE001 - 解析失败按缺配置展示，不影响配置本体
        anchor = None
    if anchor is None:
        return [
            {
                "platform": platform,
                "status": "config_required",
                "account_name": None,
                "summary": "锚点携程监控组不存在或不可用。",
            }
            for platform in enabled
        ]
    targets = await resolve_platform_targets(
        config=config, anchor=anchor, deps=default_deps()
    )
    display_names = {
        (str(session.platform or ""), str(session.platform_account_id or "")): str(
            session.display_name or ""
        ).strip()
        for session in get_browser_session_store().list()
    }
    return [
        {
            "platform": platform,
            "status": targets[platform].status,
            "account_name": (
                display_names.get((platform, targets[platform].account_id or ""))
                or targets[platform].account_id
                if targets[platform].account_id
                else None
            ),
            "summary": targets[platform].summary,
        }
        for platform in enabled
        if platform in targets
    ]


@router.patch("/configs/{config_id}", dependencies=[Depends(require_operator_api_key)])
async def patch_config(
    patch: EbkGlobalPushConfigPatch, config_id: int
) -> dict[str, Any]:
    existing = await get_global_push_config(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="全局推送配置不存在。")
    if patch.enabled:
        await _assert_legacy_disabled(existing["ctrip_watch_group_key"])
    try:
        config = await update_global_push_config(config_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": config}


@router.delete("/configs/{config_id}", dependencies=[Depends(require_operator_api_key)])
async def delete_config(config_id: int) -> dict[str, Any]:
    """软停用并保留运行历史（设计 §9：二次确认后才允许物理删除，本版本不做物理删除）。"""

    existing = await get_global_push_config(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="全局推送配置不存在。")
    await soft_disable_global_push_config(config_id)
    return {"status": "soft_disabled", "config_id": config_id}


@router.post(
    "/configs/{config_id}/run-now",
    dependencies=[Depends(require_operator_api_key)],
)
async def run_now(config_id: int) -> RunNowResponse:
    """手动三卡测试入口：manual 独立键不占固定槽位，但受同配置互斥约束（§9）。"""

    config = await get_global_push_config(config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="全局推送配置不存在。")
    # 入口先回收超时 running（§8.3），避免 scheduler 关闭时孤儿记录永久互斥
    # （实测：run_global_push 抛异常若未收尾会留下 running，导致后续 run-now 全 409）。
    await fail_stale_running_runs()
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")[:-3]
    run = await claim_global_push_run(
        config_id=config_id,
        schedule_key=f"manual:{config_id}:{timestamp}",
        trigger="operator",
    )
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="已有运行中的全局推送任务（互斥），请稍后重试。",
        )
    try:
        result = await run_global_push(config=config, run_id=run["id"], deps=default_deps())
    except Exception as exc:  # noqa: BLE001 - 任何异常都必须收尾运行记录，不留 running 孤儿
        logger.exception("global push run-now failed: run_id=%s", run["id"])
        await finish_global_push_run(
            run["id"],
            status="failed",
            error_summary=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        if config.get("schedule_mode") == "last_run_interval":
            await mark_config_run_finished(
                config_id,
                succeeded=False,
                next_run_at=compute_next_run_at(
                    {
                        "interval_minutes": config.get("interval_minutes") or 60,
                    },
                    finished_at=local_now(),
                    succeeded=False,
                    consecutive_failures=int(config.get("consecutive_failure_count") or 0),
                ),
            )
        raise HTTPException(
            status_code=500,
            detail="全局推送运行失败，已记录失败原因，请查看「最近运行」。",
        ) from exc
    await finish_global_push_run(
        run["id"],
        status=result["status"],
        platform_results=result["platform_results"],
        card_results=result["card_results"],
        error_summary=result["error_summary"],
    )
    if config.get("schedule_mode") == "last_run_interval":
        await mark_config_run_finished(
            config_id,
            succeeded=bool(result["succeeded"]),
            next_run_at=compute_next_run_at(
                {
                    "interval_minutes": config.get("interval_minutes") or 60,
                },
                finished_at=local_now(),
                succeeded=bool(result["succeeded"]),
                consecutive_failures=int(config.get("consecutive_failure_count") or 0),
            ),
        )
    return RunNowResponse(
        run_id=run["id"],
        status=result["status"],
        platform_results=result["platform_results"],
        card_results=result["card_results"],
        summary=str(result["error_summary"] or "已执行一次全局三卡测试。"),
    )


@router.get("/configs/{config_id}/runs")
async def get_runs(config_id: int, limit: int = 20) -> dict[str, Any]:
    config = await get_global_push_config(config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="全局推送配置不存在。")
    return {"runs": await list_global_push_runs(config_id, limit=limit)}


@router.get("/scheduler-status")
async def scheduler_status() -> dict[str, Any]:
    heartbeat = get_worker_heartbeat_status("ebk_global_push_scheduler")
    # 后端与 read-only worker 是独立进程、各自缓存 settings：worker 是否
    # 真的在 tick 只能由心跳判定，本地开关副本不得覆盖新鲜心跳证据。
    return {
        "name": "ebk_global_push_scheduler",
        "enabled": heartbeat["status"] == "healthy"
        or bool(get_settings().ebk_global_push_scheduler_enabled),
        "status": heartbeat["status"],
        "last_heartbeat_at": heartbeat.get("last_heartbeat_at"),
        "heartbeat_age_seconds": heartbeat.get("heartbeat_age_seconds"),
    }
