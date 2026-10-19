"""金字塔出价执行链：预检 -> 页面核验空跑 -> 真实调整出价。

与 pricing_execution.service 同构的三段式链路；每段都重新校验
账号策略（non_direct + 白名单）与飞书卡审批来源，执行出价只取
审批预览里锁定的目标出价，绝不二次生成。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.sessions import (
    account_write_policy,
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.pricing_safety import (
    account_policy_allows_write,
    normalized_account_policy,
    strip_sensitive,
)
from app.domain.pyramid_bidding.approvals import PYRAMID_WRITE_NOT_ALLOWED_SUMMARY
from app.domain.pyramid_bidding.models import (
    PyramidBidApprovalRecord,
    PyramidBidExecutionRecord,
    PyramidBidPreviewRecord,
)

_BID_EPSILON = 0.005


class PyramidBidExecutionResult(BaseModel):
    status: str
    summary: str
    execution_record_id: int | None = None
    approval_record_id: int | None = None
    preview_id: int | None = None
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    execute_request: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


def _pyramid_browser_service() -> Any:
    from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

    return get_ctrip_ebooking_service()


async def prepare_pyramid_bid_execution_preflight(
    *,
    approval_record_id: int,
    actor_id: str = "operator",
) -> PyramidBidExecutionResult:
    await ensure_db_ready()
    async with session_scope() as session:
        approval = await session.get(PyramidBidApprovalRecord, approval_record_id)
        if approval is None:
            return PyramidBidExecutionResult(
                status="not_found",
                summary="金字塔调价审批记录不存在。",
                approval_record_id=approval_record_id,
                evidence={"execute_triggered": False},
            )
        preview = await session.get(PyramidBidPreviewRecord, approval.preview_id)
        if preview is None:
            return PyramidBidExecutionResult(
                status="not_found",
                summary="金字塔调价预览记录不存在。",
                approval_record_id=approval_record_id,
                preview_id=approval.preview_id,
                evidence={"execute_triggered": False},
            )
        policy = _policy_from_preview(preview)
        if not account_policy_allows_write(policy):
            result = _write_not_allowed_result(
                event_type="preflight",
                approval_record_id=approval_record_id,
                preview=preview,
                policy=policy,
            )
            return await _save_execution_result(
                event_type="preflight",
                result=result,
                actor_id=actor_id,
            )
        if approval.status != "approved" or approval.source != "feishu_card":
            result = PyramidBidExecutionResult(
                status="not_approved",
                summary="只有飞书卡片审批通过后才能进入金字塔调价执行预检。",
                approval_record_id=approval_record_id,
                preview_id=preview.id,
                evidence={
                    "approval_status": approval.status,
                    "approval_source": approval.source,
                    "execute_triggered": False,
                },
                next_action="请在飞书金字塔调价审批卡片点击确认调整。",
            )
            return await _save_execution_result(
                event_type="preflight",
                result=result,
                actor_id=actor_id,
            )
        actions = _preview_actions(preview)
        if not actions:
            result = PyramidBidExecutionResult(
                status="data_required",
                summary="金字塔调价预览中没有可执行动作。",
                approval_record_id=approval_record_id,
                preview_id=preview.id,
                evidence={"execute_triggered": False},
                next_action="重新发送金字塔调价指令。",
            )
            return await _save_execution_result(
                event_type="preflight",
                result=result,
                actor_id=actor_id,
            )
        execute_request = _execute_request_from_actions(
            approval_record_id=approval_record_id,
            preview=preview,
            actions=actions,
        )
        result = PyramidBidExecutionResult(
            status="preflight_ready",
            summary="金字塔调价执行预检通过，已锁定审批预览中的目标出价。",
            approval_record_id=approval_record_id,
            preview_id=preview.id,
            platform_account_id=_platform_account_from_preview(preview),
            browser_session_id=_browser_session_from_preview(preview),
            execute_request=execute_request,
            data={"action_count": len(actions), "connection_mode": preview.connection_mode},
            evidence={
                "approved_preview_bid_locked": True,
                "connection_mode_checked": True,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
            },
            next_action="继续页面核验空跑。",
        )
        return await _save_execution_result(
            event_type="preflight",
            result=result,
            actor_id=actor_id,
        )


async def prepare_pyramid_bid_page_write_dry_run(
    *,
    execution_record_id: int,
    actor_id: str = "operator",
) -> PyramidBidExecutionResult:
    return await _run_browser_stage(
        execution_record_id=execution_record_id,
        actor_id=actor_id,
        event_type="page_write_dry_run",
        require_source_status="preflight_ready",
        browser_method="prepare_pyramid_bid_page_write_dry_run",
        default_status="page_write_preview_ready",
        not_ready_summary="需要先完成金字塔调价执行预检。",
    )


async def execute_pyramid_bid_write(
    *,
    execution_record_id: int,
    actor_id: str = "operator",
) -> PyramidBidExecutionResult:
    return await _run_browser_stage(
        execution_record_id=execution_record_id,
        actor_id=actor_id,
        event_type="execute",
        require_source_status="page_write_preview_ready",
        browser_method="execute_pyramid_bid_write",
        default_status="executed",
        not_ready_summary="需要先完成金字塔页面核验空跑，且空跑状态必须为已就绪。",
        require_source_event_type="page_write_dry_run",
    )


async def get_pyramid_bid_execution(execution_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidExecutionRecord, execution_id)
    return _execution_record_to_dict(record) if record is not None else None


async def _run_browser_stage(
    *,
    execution_record_id: int,
    actor_id: str,
    event_type: str,
    require_source_status: str,
    browser_method: str,
    default_status: str,
    not_ready_summary: str,
    require_source_event_type: str | None = None,
) -> PyramidBidExecutionResult:
    loaded = await _load_execution_with_policy(execution_record_id)
    if loaded is None:
        return PyramidBidExecutionResult(
            status="not_found",
            summary="金字塔调价执行记录不存在。",
            execution_record_id=execution_record_id,
            evidence={"execute_triggered": False},
        )
    source, policy = loaded
    if not account_policy_allows_write(policy):
        return await _save_execution_result(
            event_type=event_type,
            result=_write_not_allowed_from_source(source, policy),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    source_event_ok = (
        (require_source_event_type is None or source.event_type == require_source_event_type)
        and source.status == require_source_status
    )
    if not source_event_ok:
        return await _save_execution_result(
            event_type=event_type,
            result=PyramidBidExecutionResult(
                status="not_preflight_ready",
                summary=not_ready_summary,
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                execute_request=source.execute_request_json or {},
                evidence={"execute_triggered": False},
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    execute_request = source.execute_request_json or {}
    if event_type == "execute":
        locked = await _locked_target_bid(source, execute_request)
        if locked is None:
            return await _save_execution_result(
                event_type=event_type,
                result=PyramidBidExecutionResult(
                    status="data_required",
                    summary="执行出价与审批预览不一致，已停止自动调整。",
                    execution_record_id=execution_record_id,
                    approval_record_id=source.approval_record_id,
                    preview_id=source.preview_id,
                    execute_request=execute_request,
                    evidence={
                        "approved_price_lock": False,
                        "execute_triggered": False,
                    },
                    next_action="请重新发起金字塔调价审批。",
                ),
                actor_id=actor_id,
                source_execution_record_id=execution_record_id,
            )
        execute_request = {**execute_request, "target_bid_cny": locked, "actions": _locked_actions(execute_request, locked)}
    platform_account_id = _platform_account_from_source(source) or str(
        (execute_request.get("pricing_account") or {}).get("platform_account_id") or ""
    )
    browser_session_id = (
        _browser_session_from_source(source)
        or str((execute_request.get("pricing_account") or {}).get("browser_session_id") or "")
        or get_browser_session_id_for_platform_account(platform_account_id)
    )
    if not browser_session_id:
        return await _save_execution_result(
            event_type=event_type,
            result=PyramidBidExecutionResult(
                status="human_required",
                summary="没有可用的携程 EBK 浏览器账号，无法执行金字塔页面步骤。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                execute_request=execute_request,
                evidence={"blocked_reason": "browser_session_missing", "execute_triggered": False},
                next_action="请先绑定并登录携程 EBK 浏览器账号。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    try:
        browser_result = await getattr(_pyramid_browser_service(), browser_method)(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
        )
    except Exception as exc:  # noqa: BLE001
        return await _save_execution_result(
            event_type=event_type,
            result=PyramidBidExecutionResult(
                status="failed",
                summary="携程 EBK 金字塔页面步骤失败。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                execute_request=execute_request,
                data={"error_type": type(exc).__name__},
                evidence={"browser_write_invoked": event_type == "execute", "ota_submit_invoked": False},
                next_action="检查浏览器登录态和金字塔页面后重试。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    return await _save_execution_result(
        event_type=event_type,
        result=_result_from_browser(
            browser_result,
            source=source,
            default_status=default_status,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            dry_run=event_type != "execute",
        ),
        actor_id=actor_id,
        source_execution_record_id=execution_record_id,
    )


async def _locked_target_bid(
    source: PyramidBidExecutionRecord,
    execute_request: dict[str, Any],
) -> float | None:
    """执行出价必须等于审批预览锁定的目标出价。"""
    if source.preview_id is None:
        return None
    await ensure_db_ready()
    async with session_scope() as session:
        preview = await session.get(PyramidBidPreviewRecord, source.preview_id)
    if preview is None:
        return None
    actions = _preview_actions(preview)
    if not actions:
        return None
    approved_bid = _safe_float(actions[0].get("target_bid_cny"))
    target = _safe_float(execute_request.get("target_bid_cny"))
    if approved_bid is None or target is None or abs(target - approved_bid) > _BID_EPSILON:
        return None
    return target


def _locked_actions(execute_request: dict[str, Any], locked: float) -> list[dict[str, Any]]:
    actions = [
        {**action, "target_bid_cny": locked, "proposed_bid_cny": locked}
        for action in (execute_request.get("actions") or [])
        if isinstance(action, dict)
    ]
    return actions


def _policy_from_preview(preview: PyramidBidPreviewRecord) -> dict[str, Any]:
    pricing_account = _pricing_account_from_preview(preview)
    browser_session_id = str(pricing_account.get("browser_session_id") or "")
    if not browser_session_id:
        return normalized_account_policy({})
    try:
        account = get_browser_session_store().get(browser_session_id)
    except Exception:  # noqa: BLE001 - account lookup must fail closed
        return normalized_account_policy({})
    account_matches_preview = (
        str(getattr(account, "platform", "") or "").strip().lower() == "ctrip"
        and str(getattr(account, "intended_use", "") or "").strip() == "ebk"
        and str(getattr(account, "platform_account_id", "") or "")
        == str(pricing_account.get("platform_account_id") or "")
        and str(getattr(account, "pricing_feishu_chat_id", "") or "")
        == str(preview.chat_id or "")
    )
    if not account_matches_preview:
        return normalized_account_policy({})
    return normalized_account_policy(account_write_policy(account))


def _write_not_allowed_result(
    *,
    event_type: str,
    approval_record_id: int | None = None,
    preview: PyramidBidPreviewRecord,
    policy: dict[str, Any],
) -> PyramidBidExecutionResult:
    return PyramidBidExecutionResult(
        status="write_not_allowed",
        summary=PYRAMID_WRITE_NOT_ALLOWED_SUMMARY,
        approval_record_id=approval_record_id,
        preview_id=int(preview.id),
        data={"account_policy": policy, "event_type": event_type},
        evidence={
            "connection_mode": policy["connection_mode"],
            "connection_mode_checked": True,
            "execute_triggered": False,
        },
        next_action="仅发送金字塔调价建议，由人工在 EBK 后台处理。",
    )


def _write_not_allowed_from_source(
    source: PyramidBidExecutionRecord,
    policy: dict[str, Any],
) -> PyramidBidExecutionResult:
    return PyramidBidExecutionResult(
        status="write_not_allowed",
        summary=PYRAMID_WRITE_NOT_ALLOWED_SUMMARY,
        approval_record_id=source.approval_record_id,
        preview_id=source.preview_id,
        execute_request=source.execute_request_json or {},
        data={"account_policy": policy},
        evidence={
            "connection_mode": policy["connection_mode"],
            "connection_mode_checked": True,
            "execute_triggered": False,
        },
        next_action="仅发送金字塔调价建议，由人工在 EBK 后台处理。",
    )


def _execute_request_from_actions(
    *,
    approval_record_id: int,
    preview: PyramidBidPreviewRecord,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    pricing_account = _pricing_account_from_preview(preview)
    return {
        "request_id": f"pyramid-bid-execute-{uuid4().hex[:12]}",
        "approval_record_id": approval_record_id,
        "preview_id": int(preview.id),
        "chat_id": preview.chat_id,
        "pricing_account": pricing_account,
        "platform_account_id": pricing_account.get("platform_account_id"),
        "browser_session_id": pricing_account.get("browser_session_id"),
        "target_bid_cny": actions[0].get("target_bid_cny"),
        "actions": [
            {
                "operation": "set_pyramid_click_bid",
                "platform": "ctrip",
                "campaign_name": action.get("campaign_name"),
                "current_bid_cny": action.get("current_bid_cny"),
                "proposed_bid_cny": action.get("proposed_bid_cny"),
                "target_bid_cny": action.get("target_bid_cny"),
                "trigger_reason": action.get("trigger_reason"),
            }
            for action in actions
        ],
    }


def _preview_actions(preview: PyramidBidPreviewRecord) -> list[dict[str, Any]]:
    candidate = preview.preview_candidate_json if isinstance(preview.preview_candidate_json, dict) else {}
    actions = candidate.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _pricing_account_from_preview(preview: PyramidBidPreviewRecord) -> dict[str, Any]:
    candidate = preview.preview_candidate_json if isinstance(preview.preview_candidate_json, dict) else {}
    pricing_account = candidate.get("pricing_account")
    if not isinstance(pricing_account, dict):
        return {}
    return {
        "platform": pricing_account.get("platform"),
        "platform_account_id": pricing_account.get("platform_account_id"),
        "browser_session_id": pricing_account.get("browser_session_id"),
        "connection_mode": pricing_account.get("connection_mode"),
        "can_auto_write_price": bool(pricing_account.get("can_auto_write_price")),
    }


def _platform_account_from_preview(preview: PyramidBidPreviewRecord) -> str | None:
    value = _pricing_account_from_preview(preview).get("platform_account_id")
    return str(value) if value else None


def _browser_session_from_preview(preview: PyramidBidPreviewRecord) -> str | None:
    value = _pricing_account_from_preview(preview).get("browser_session_id")
    return str(value) if value else None


def _platform_account_from_source(source: PyramidBidExecutionRecord) -> str:
    result = source.result_json if isinstance(source.result_json, dict) else {}
    execute_request = (
        source.execute_request_json if isinstance(source.execute_request_json, dict) else {}
    )
    pricing_account = (
        execute_request.get("pricing_account")
        if isinstance(execute_request.get("pricing_account"), dict)
        else {}
    )
    value = (
        source.platform_account_id
        or execute_request.get("platform_account_id")
        or pricing_account.get("platform_account_id")
        or result.get("platform_account_id")
    )
    return str(value or "")


def _browser_session_from_source(source: PyramidBidExecutionRecord) -> str:
    execute_request = (
        source.execute_request_json if isinstance(source.execute_request_json, dict) else {}
    )
    pricing_account = (
        execute_request.get("pricing_account")
        if isinstance(execute_request.get("pricing_account"), dict)
        else {}
    )
    value = (
        source.browser_session_id
        or execute_request.get("browser_session_id")
        or pricing_account.get("browser_session_id")
    )
    return str(value or "")


async def _load_execution_with_policy(
    execution_record_id: int,
) -> tuple[PyramidBidExecutionRecord, dict[str, Any]] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PyramidBidExecutionRecord, execution_record_id)
        if record is None:
            return None
        preview = (
            await session.get(PyramidBidPreviewRecord, record.preview_id)
            if record.preview_id
            else None
        )
    if preview is None:
        return record, normalized_account_policy({})
    return record, _policy_from_preview(preview)


async def _save_execution_result(
    *,
    event_type: str,
    result: PyramidBidExecutionResult,
    actor_id: str,
    source_execution_record_id: int | None = None,
) -> PyramidBidExecutionResult:
    await ensure_db_ready()
    async with session_scope() as session:
        record = PyramidBidExecutionRecord(
            approval_record_id=result.approval_record_id,
            preview_id=result.preview_id,
            source_execution_record_id=source_execution_record_id,
            event_type=event_type,
            status=result.status,
            summary=result.summary,
            actor_id=actor_id.strip() or "operator",
            platform_account_id=result.platform_account_id,
            browser_session_id=result.browser_session_id,
            execute_request_json=strip_sensitive(result.execute_request),
            result_json=strip_sensitive(
                result.model_dump(mode="json", exclude={"execution_record_id"})
            ),
            evidence_json=strip_sensitive(result.evidence),
        )
        session.add(record)
        await session.flush()
        result.execution_record_id = int(record.id)
    return result


def _result_from_browser(
    browser_result: Any,
    *,
    source: PyramidBidExecutionRecord,
    default_status: str,
    platform_account_id: str,
    browser_session_id: str,
    dry_run: bool,
) -> PyramidBidExecutionResult:
    payload = (
        browser_result.model_dump(mode="json")
        if hasattr(browser_result, "model_dump")
        else dict(browser_result)
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    status = _normalize_browser_status(str(payload.get("status") or default_status), dry_run=dry_run)
    return PyramidBidExecutionResult(
        status=status,
        summary=str(payload.get("summary") or "携程金字塔页面步骤完成。"),
        approval_record_id=source.approval_record_id,
        preview_id=source.preview_id,
        platform_account_id=str(payload.get("platform_account_id") or platform_account_id),
        browser_session_id=str(payload.get("browser_session_id") or browser_session_id),
        execute_request=source.execute_request_json or {},
        data={
            "query_type": "pyramid_bid_page_write_dry_run" if dry_run else "pyramid_bid_write_execute",
            "requested_url": data.get("requested_url"),
            "current_url": data.get("current_url"),
            "captured_at": data.get("captured_at"),
            "campaign_read": data.get("campaign_read"),
            "bid_fill_result": data.get("bid_fill_result"),
            "submit_result": data.get("submit_result"),
            "readback_result": data.get("readback_result"),
        },
        evidence=strip_sensitive(
            {
                **evidence,
                "connection_mode_checked": True,
                "dry_run": dry_run,
            }
        ),
        next_action=payload.get("next_action"),
    )


def _normalize_browser_status(status: str, *, dry_run: bool) -> str:
    if status in {"success", "page_write_preview_ready"} and dry_run:
        return "page_write_preview_ready"
    if status in {"success", "executed", "execute_submitted"}:
        return "executed"
    if status == "page_structure_changed":
        return "needs_review"
    return status


def _execution_record_to_dict(record: PyramidBidExecutionRecord) -> dict[str, Any]:
    return {
        "execution_record_id": record.id,
        "id": record.id,
        "approval_record_id": record.approval_record_id,
        "preview_id": record.preview_id,
        "source_execution_record_id": record.source_execution_record_id,
        "event_type": record.event_type,
        "status": record.status,
        "summary": record.summary,
        "platform_account_id": record.platform_account_id,
        "browser_session_id": record.browser_session_id,
        "execute_request": record.execute_request_json,
        "result": record.result_json,
        "evidence": record.evidence_json,
        "created_at": record.created_at.isoformat(),
    }


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed
