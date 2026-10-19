from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.pricing_execution.models import (
    PricingApprovalRecord,
    PricingExecutionRecord,
    PricingPreviewRecord,
)
from app.domain.pricing_safety import (
    TRUSTED_PRICE_SOURCE,
    pricing_platform_for_browser_session,
    write_not_allowed_reason,
    account_policy_allows_write,
    normalized_account_policy,
    strip_sensitive,
)


class PricingExecutionResult(BaseModel):
    status: str
    summary: str
    execution_record_id: int | None = None
    approval_record_id: int | None = None
    preview_id: int | None = None
    watch_group_key: str = ""
    platform_account_id: str | None = None
    browser_session_id: str | None = None
    execute_request: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


# V9 Step 3 — 执行链平台分发：按浏览器会话的 platform 选择写价服务。
_PRICING_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}


def _pricing_platform_label(platform: str | None) -> str:
    return _PRICING_PLATFORM_LABELS.get(str(platform or "").strip().lower(), "携程")


def _pricing_platform_for_browser_session(browser_session_id: str | None) -> str:
    return pricing_platform_for_browser_session(browser_session_id)


def _pricing_browser_service_for(platform: str | None) -> Any:
    """白名单未放开的平台在 can_auto_write_price 闸门处已被拒，不会走到这里。"""
    normalized = str(platform or "ctrip").strip().lower()
    if normalized == "meituan":
        from app.domain.browser.meituan_ebooking import get_meituan_ebooking_service

        return get_meituan_ebooking_service()
    if normalized == "fliggy":
        from app.domain.browser.fliggy_ebooking import get_fliggy_ebooking_service

        return get_fliggy_ebooking_service()
    from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

    return get_ctrip_ebooking_service()


async def prepare_pricing_execution_preflight(
    *,
    approval_record_id: int,
    actor_id: str = "operator",
) -> PricingExecutionResult:
    await ensure_db_ready()
    async with session_scope() as session:
        approval = await session.get(PricingApprovalRecord, approval_record_id)
        if approval is None:
            return PricingExecutionResult(
                status="not_found",
                summary="调价审批记录不存在。",
                approval_record_id=approval_record_id,
                evidence={"execute_triggered": False},
            )
        preview = await session.get(PricingPreviewRecord, approval.preview_id)
        if preview is None:
            return PricingExecutionResult(
                status="not_found",
                summary="调价预览记录不存在。",
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
            result = PricingExecutionResult(
                status="not_approved",
                summary="只有 Feishu 卡片审批通过后才能进入执行预检。",
                approval_record_id=approval_record_id,
                preview_id=preview.id,
                watch_group_key=preview.watch_group_key,
                evidence={
                    "approval_status": approval.status,
                    "approval_source": approval.source,
                    "execute_triggered": False,
                },
                next_action="请在 Feishu 审批卡片点击通过。",
            )
            return await _save_execution_result(
                event_type="preflight",
                result=result,
                actor_id=actor_id,
            )
        actions = _preview_actions(preview)
        if not actions:
            result = PricingExecutionResult(
                status="data_required",
                summary="调价预览中没有可执行动作。",
                approval_record_id=approval_record_id,
                preview_id=preview.id,
                watch_group_key=preview.watch_group_key,
                evidence={"trusted_price_source": TRUSTED_PRICE_SOURCE, "execute_triggered": False},
                next_action="重新生成调价预览。",
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
        result = PricingExecutionResult(
            status="preflight_ready",
            summary="执行预检通过，已锁定审批预览中的建议后价格。",
            approval_record_id=approval_record_id,
            preview_id=preview.id,
            watch_group_key=preview.watch_group_key,
            platform_account_id=_pricing_platform_account_from_preview(preview),
            browser_session_id=_pricing_browser_session_from_preview(preview),
            execute_request=execute_request,
            data={"action_count": len(actions), "connection_mode": preview.connection_mode},
            evidence={
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
                "approved_preview_price_locked": True,
                "connection_mode_checked": True,
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
            },
            next_action="继续页面写入空跑。",
        )
        return await _save_execution_result(
            event_type="preflight",
            result=result,
            actor_id=actor_id,
        )


async def prepare_pricing_page_write_dry_run(
    *,
    execution_record_id: int,
    actor_id: str = "operator",
    account_policy: dict[str, Any] | None = None,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
    target_url: str | None = None,
) -> PricingExecutionResult:
    policy = normalized_account_policy(account_policy)
    if account_policy is not None and not account_policy_allows_write(policy):
        return PricingExecutionResult(
            status="write_not_allowed",
            summary=write_not_allowed_reason(
                _pricing_platform_for_browser_session(browser_session_id)
            ),
            execution_record_id=execution_record_id,
            evidence={"connection_mode_checked": True, "execute_triggered": False},
        )
    loaded = await _load_execution_with_policy(execution_record_id, fallback_policy=policy)
    if loaded is None:
        return PricingExecutionResult(
            status="not_found",
            summary="执行预检记录不存在。",
            execution_record_id=execution_record_id,
            evidence={"execute_triggered": False},
        )
    source, policy = loaded
    if not account_policy_allows_write(policy):
        return await _save_execution_result(
            event_type="page_write_dry_run",
            result=_write_not_allowed_from_source(source, policy),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    if source.status != "preflight_ready":
        return await _save_execution_result(
            event_type="page_write_dry_run",
            result=PricingExecutionResult(
                status="not_preflight_ready",
                summary="需要先完成执行预检。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                execute_request=source.execute_request_json or {},
                evidence={"execute_triggered": False},
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    platform_account_id = platform_account_id or _platform_account_from_source(source)
    browser_session_id = (
        browser_session_id
        or _browser_session_from_source(source)
        or get_browser_session_id_for_platform_account(platform_account_id)
    )
    if not browser_session_id:
        return await _save_execution_result(
            event_type="page_write_dry_run",
            result=PricingExecutionResult(
                status="human_required",
                summary="没有可用的携程 EBK 浏览器账号，无法进行页面空跑。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                execute_request=source.execute_request_json or {},
                evidence={"blocked_reason": "browser_session_missing", "execute_triggered": False},
                next_action="请先绑定并登录携程 EBK 浏览器账号。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    platform = _pricing_platform_for_browser_session(browser_session_id)
    try:
        browser_result = await _pricing_browser_service_for(
            platform
        ).prepare_pricing_page_write_dry_run(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=source.execute_request_json or {},
            target_url=target_url,
        )
    except Exception as exc:
        return await _save_execution_result(
            event_type="page_write_dry_run",
            result=PricingExecutionResult(
                status="failed",
                summary=f"{_pricing_platform_label(platform)} EBK 页面写入空跑失败。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                execute_request=source.execute_request_json or {},
                data={"error_type": type(exc).__name__, "error_message": str(exc)[:240]},
                evidence={"browser_write_invoked": False, "ota_submit_invoked": False},
                next_action="检查浏览器登录态和 EBK 房价页后重试。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    return await _save_execution_result(
        event_type="page_write_dry_run",
        result=_result_from_browser(
            browser_result,
            source=source,
            default_status="page_write_preview_ready",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            dry_run=True,
        ),
        actor_id=actor_id,
        source_execution_record_id=execution_record_id,
    )


async def execute_pricing_page_write(
    *,
    execution_record_id: int,
    actor_id: str = "operator",
    account_policy: dict[str, Any] | None = None,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
    target_url: str | None = None,
) -> PricingExecutionResult:
    policy = normalized_account_policy(account_policy)
    if account_policy is not None and not account_policy_allows_write(policy):
        return PricingExecutionResult(
            status="write_not_allowed",
            summary=write_not_allowed_reason(
                _pricing_platform_for_browser_session(browser_session_id)
            ),
            execution_record_id=execution_record_id,
            evidence={"connection_mode_checked": True, "execute_triggered": False},
        )
    loaded = await _load_execution_with_policy(execution_record_id, fallback_policy=policy)
    if loaded is None:
        return PricingExecutionResult(
            status="not_found",
            summary="页面空跑记录不存在。",
            execution_record_id=execution_record_id,
            evidence={"execute_triggered": False},
        )
    source, policy = loaded
    if not account_policy_allows_write(policy):
        return await _save_execution_result(
            event_type="execute",
            result=_write_not_allowed_from_source(source, policy),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    if source.event_type != "page_write_dry_run" or source.status != "page_write_preview_ready":
        return await _save_execution_result(
            event_type="execute",
            result=PricingExecutionResult(
                status="not_preflight_ready",
                summary="需要先完成页面写入空跑，且空跑状态必须为已就绪。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                execute_request=source.execute_request_json or {},
                evidence={"execute_triggered": False},
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    platform_account_id = platform_account_id or _platform_account_from_source(source)
    browser_session_id = (
        browser_session_id
        or _browser_session_from_source(source)
        or get_browser_session_id_for_platform_account(platform_account_id)
    )
    if not browser_session_id:
        return await _save_execution_result(
            event_type="execute",
            result=PricingExecutionResult(
                status="human_required",
                summary="没有可用的携程 EBK 浏览器账号，无法执行写价。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                execute_request=source.execute_request_json or {},
                evidence={"blocked_reason": "browser_session_missing", "execute_triggered": False},
                next_action="请先绑定并登录携程 EBK 浏览器账号。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    platform = _pricing_platform_for_browser_session(browser_session_id)
    try:
        browser_result = await _pricing_browser_service_for(
            platform
        ).execute_pricing_page_write(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=source.execute_request_json or {},
            target_url=target_url,
        )
    except Exception as exc:
        return await _save_execution_result(
            event_type="execute",
            result=PricingExecutionResult(
                status="failed",
                summary=f"{_pricing_platform_label(platform)} EBK 自动写价执行失败。",
                execution_record_id=execution_record_id,
                approval_record_id=source.approval_record_id,
                preview_id=source.preview_id,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                execute_request=source.execute_request_json or {},
                data={"error_type": type(exc).__name__, "error_message": str(exc)[:240]},
                evidence={"browser_write_invoked": True, "ota_submit_invoked": False},
                next_action="人工检查浏览器页面和执行审计记录。",
            ),
            actor_id=actor_id,
            source_execution_record_id=execution_record_id,
        )
    return await _save_execution_result(
        event_type="execute",
        result=_result_from_browser(
            browser_result,
            source=source,
            default_status="executed",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            dry_run=False,
        ),
        actor_id=actor_id,
        source_execution_record_id=execution_record_id,
    )


async def get_pricing_execution(execution_id: int) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingExecutionRecord, execution_id)
    return _execution_record_to_dict(record) if record is not None else None


async def _save_execution_result(
    *,
    event_type: str,
    result: PricingExecutionResult,
    actor_id: str,
    source_execution_record_id: int | None = None,
) -> PricingExecutionResult:
    await ensure_db_ready()
    async with session_scope() as session:
        record = PricingExecutionRecord(
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
            result_json=strip_sensitive(result.model_dump(mode="json", exclude={"execution_record_id"})),
            evidence_json=strip_sensitive(result.evidence),
        )
        session.add(record)
        await session.flush()
        result.execution_record_id = int(record.id)
    return result


async def _load_execution_with_policy(
    execution_record_id: int,
    *,
    fallback_policy: dict[str, Any],
) -> tuple[PricingExecutionRecord, dict[str, Any]] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(PricingExecutionRecord, execution_record_id)
        if record is None:
            return None
        preview = await session.get(PricingPreviewRecord, record.preview_id) if record.preview_id else None
    if preview is None:
        return record, fallback_policy
    return record, _policy_from_preview(preview)


def _policy_from_preview(preview: PricingPreviewRecord) -> dict[str, Any]:
    return normalized_account_policy(
        {
            "connection_mode": preview.connection_mode,
            "can_auto_write_price": preview.can_auto_write_price,
            "write_policy_message": preview.write_policy_message,
        }
    )


def _write_not_allowed_result(
    *,
    event_type: str,
    approval_record_id: int | None = None,
    preview: PricingPreviewRecord,
    policy: dict[str, Any],
) -> PricingExecutionResult:
    return PricingExecutionResult(
        status="write_not_allowed",
        summary=write_not_allowed_reason(
            _pricing_platform_for_browser_session(
                _pricing_browser_session_from_preview(preview)
            )
        ),
        approval_record_id=approval_record_id,
        preview_id=int(preview.id),
        watch_group_key=preview.watch_group_key,
        data={"account_policy": policy, "event_type": event_type},
        evidence={
            "connection_mode": policy["connection_mode"],
            "connection_mode_checked": True,
            "execute_triggered": False,
        },
        next_action="仅发送调价建议，由人工在 EBK 后台处理。",
    )


def _write_not_allowed_from_source(
    source: PricingExecutionRecord,
    policy: dict[str, Any],
) -> PricingExecutionResult:
    return PricingExecutionResult(
        status="write_not_allowed",
        summary=write_not_allowed_reason(
            _pricing_platform_for_browser_session(_browser_session_from_source(source))
        ),
        approval_record_id=source.approval_record_id,
        preview_id=source.preview_id,
        execute_request=source.execute_request_json or {},
        data={"account_policy": policy},
        evidence={
            "connection_mode": policy["connection_mode"],
            "connection_mode_checked": True,
            "execute_triggered": False,
        },
        next_action="仅发送调价建议，由人工在 EBK 后台处理。",
    )


def _execute_request_from_actions(
    *,
    approval_record_id: int,
    preview: PricingPreviewRecord,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    pricing_account = _pricing_account_from_preview(preview)
    return {
        "request_id": f"pricing-execute-{uuid4().hex[:12]}",
        "approval_record_id": approval_record_id,
        "preview_id": int(preview.id),
        "watch_group_key": preview.watch_group_key,
        "price_source": TRUSTED_PRICE_SOURCE,
        "pricing_account": pricing_account,
        "platform_account_id": pricing_account.get("platform_account_id"),
        "browser_session_id": pricing_account.get("browser_session_id"),
        "actions": [
            {
                "operation": "set_room_price",
                "platform": str(action.get("platform") or "ctrip"),
                "room_type": action.get("room_type"),
                "normalized_room_type": action.get("normalized_room_type"),
                "checkin_date": action.get("checkin_date"),
                "checkout_date": action.get("checkout_date"),
                "breakfast_kind": action.get("breakfast_kind"),
                "current_price_yuan": action.get("current_price_yuan"),
                "proposed_price_yuan": action.get("proposed_price_yuan"),
                "target_price_yuan": action.get("proposed_price_yuan"),
                "proposed_base_price_yuan": action.get("proposed_base_price_yuan"),
                "commission_rate": action.get("commission_rate"),
                "trigger_reason": action.get("trigger_reason"),
                "source": TRUSTED_PRICE_SOURCE,
            }
            for action in actions
        ],
    }


def _preview_actions(preview: PricingPreviewRecord) -> list[dict[str, Any]]:
    candidate = preview.preview_candidate_json if isinstance(preview.preview_candidate_json, dict) else {}
    actions = candidate.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _platform_account_from_source(source: PricingExecutionRecord) -> str:
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
        or result.get("data", {}).get("platform_account_id")
    )
    return str(value or "")


def _browser_session_from_source(source: PricingExecutionRecord) -> str:
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


def _pricing_account_from_preview(preview: PricingPreviewRecord) -> dict[str, Any]:
    candidate = preview.preview_candidate_json if isinstance(preview.preview_candidate_json, dict) else {}
    pricing_account = candidate.get("pricing_account")
    if not isinstance(pricing_account, dict):
        return {}
    return {
        "platform_account_id": pricing_account.get("platform_account_id"),
        "browser_session_id": pricing_account.get("browser_session_id"),
        "connection_mode": pricing_account.get("connection_mode"),
        "can_auto_write_price": bool(pricing_account.get("can_auto_write_price")),
    }


def _pricing_platform_account_from_preview(preview: PricingPreviewRecord) -> str | None:
    value = _pricing_account_from_preview(preview).get("platform_account_id")
    return str(value) if value else None


def _pricing_browser_session_from_preview(preview: PricingPreviewRecord) -> str | None:
    value = _pricing_account_from_preview(preview).get("browser_session_id")
    return str(value) if value else None


def _result_from_browser(
    browser_result: Any,
    *,
    source: PricingExecutionRecord,
    default_status: str,
    platform_account_id: str,
    browser_session_id: str,
    dry_run: bool,
) -> PricingExecutionResult:
    payload = browser_result.model_dump(mode="json") if hasattr(browser_result, "model_dump") else dict(browser_result)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    status = _normalize_browser_status(str(payload.get("status") or default_status), dry_run=dry_run)
    return PricingExecutionResult(
        status=status,
        summary=str(payload.get("summary") or "携程 EBK 页面步骤完成。"),
        approval_record_id=source.approval_record_id,
        preview_id=source.preview_id,
        platform_account_id=str(payload.get("platform_account_id") or platform_account_id),
        browser_session_id=str(payload.get("browser_session_id") or browser_session_id),
        execute_request=source.execute_request_json or {},
        data={
            "query_type": "pricing_page_write_dry_run" if dry_run else "pricing_page_write_execute",
            "requested_url": data.get("requested_url"),
            "current_url": data.get("current_url"),
            "captured_at": data.get("captured_at"),
            "action_results": data.get("action_results"),
            "submit_result": data.get("submit_result"),
            "page_read": data.get("page_read"),
            "page_write_preview": data.get("page_write_preview"),
        },
        evidence=strip_sensitive(
            {
                **evidence,
                "trusted_price_source": TRUSTED_PRICE_SOURCE,
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


def _execution_record_to_dict(record: PricingExecutionRecord) -> dict[str, Any]:
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
