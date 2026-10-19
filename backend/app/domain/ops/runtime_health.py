from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.ops.worker_heartbeat import get_worker_heartbeat_status

_HEALTHY_FRESHNESS_STATES = {"healthy", "fresh", "managed_external"}


def _effective_worker_freshness(heartbeat: dict[str, Any]) -> str:
    base = str(heartbeat.get("status") or "missing")
    if base != "healthy":
        return base
    tick = heartbeat.get("last_tick_summary") if isinstance(heartbeat.get("last_tick_summary"), dict) else {}
    failed = _int_or_zero(tick.get("last_failed_count"))
    success = _int_or_zero(tick.get("last_success_count"))
    if failed > 0 and success == 0:
        return "tick_failed"
    if failed > 0:
        return "degraded"
    return base


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _tick_health_note(heartbeat: dict[str, Any], *, freshness_status: str) -> str | None:
    if freshness_status not in {"tick_failed", "degraded"}:
        return None
    tick = heartbeat.get("last_tick_summary") if isinstance(heartbeat.get("last_tick_summary"), dict) else {}
    failed = _int_or_zero(tick.get("last_failed_count"))
    success = _int_or_zero(tick.get("last_success_count"))
    if freshness_status == "tick_failed":
        return f"调度进程正常，但最近一次 tick 全部失败（{failed} 次），推送/读价可能已中断。"
    return f"调度进程正常，但最近一次 tick 有 {failed} 次失败、{success} 次成功。"


def _feishu_agent_mode() -> str:
    """飞书入口模式。默认 hermes：飞书入口由 Hermes Gateway 接管，后端不跑内置 listener。"""
    from app.core.settings import get_settings

    return (get_settings().feishu_agent_mode or "hermes").strip().lower()


async def get_runtime_health() -> dict[str, Any]:
    data_center = _worker_module("ctrip_ebooking_data_center_scheduler", "Ctrip EBK data-center scheduler")
    competitor = _worker_module("competitor_price_scheduler", "Ctrip competitor price scheduler")
    meituan_ebk = _worker_module("meituan_ebooking_scheduler", "Meituan EBK watch scheduler")
    fliggy_ebk = _worker_module("fliggy_ebooking_scheduler", "Fliggy EBK watch scheduler")
    if _feishu_agent_mode() == "hermes":
        # Hermes 接管飞书入口，内置 feishu_listener 不再运行，
        # 其缺失心跳不应把整体盯盘状态拉成「需关注」。
        feishu = _hermes_managed_feishu_module()
    else:
        feishu = _worker_module("feishu_listener", "Feishu bot listener")
    attention_modules = [
        module["module_key"]
        for module in (data_center, competitor, meituan_ebk, fliggy_ebk, feishu)
        if module["freshness_status"] not in _HEALTHY_FRESHNESS_STATES
    ]
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "modules": {
            "backend_api": {
                "module_key": "backend_api",
                "display_name": "Backend API",
                "freshness_status": "fresh",
                "process_confirmed": True,
            },
            "ctrip_ebooking_data_center_scheduler": data_center,
            "competitor_price_scheduler": competitor,
            "meituan_ebooking_scheduler": meituan_ebk,
            "fliggy_ebooking_scheduler": fliggy_ebk,
            "feishu_listener": feishu,
        },
        "summary": {
            "status": "fresh" if not attention_modules else "attention_required",
            "attention_modules": attention_modules,
            "stale_or_missing_modules": attention_modules,
        },
        "evidence": {
            "polling_workers_read_only": True,
            "feishu_approval_execution_supported": True,
            "browser_read_invoked": False,
            "webhook_called": False,
            "ota_write_invoked": False,
            "secrets_included": False,
        },
    }


def _hermes_managed_feishu_module() -> dict[str, Any]:
    return {
        "module_key": "feishu_listener",
        "display_name": "飞书入口（Hermes 接管）",
        "freshness_status": "managed_external",
        "process_confirmed": True,
        "managed_by": "hermes_gateway",
        "last_heartbeat_at": None,
        "heartbeat_age_seconds": None,
        "last_error": None,
        "last_tick_summary": {},
    }


def _worker_module(worker_name: str, display_name: str) -> dict[str, Any]:
    heartbeat = get_worker_heartbeat_status(worker_name)
    freshness_status = _effective_worker_freshness(heartbeat)
    return {
        "module_key": worker_name,
        "display_name": display_name,
        "freshness_status": freshness_status,
        "heartbeat_status": heartbeat.get("status") or "missing",
        "process_confirmed": bool(heartbeat.get("process_confirmed")),
        "last_heartbeat_at": heartbeat.get("last_heartbeat_at"),
        "heartbeat_age_seconds": heartbeat.get("heartbeat_age_seconds"),
        "last_error": heartbeat.get("last_error"),
        "last_tick_summary": heartbeat.get("last_tick_summary") or {},
        "tick_health_note": _tick_health_note(heartbeat, freshness_status=freshness_status),
    }
