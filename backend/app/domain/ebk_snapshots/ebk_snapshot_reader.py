from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service
from app.domain.browser.fliggy_ebooking import get_fliggy_ebooking_service
from app.domain.browser.meituan_ebooking import get_meituan_ebooking_service
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.ebk_snapshots.repository import save_ebk_snapshot
from app.domain.ebk_snapshots.types import (
    EbkSnapshotStatus,
    UnifiedEbkSnapshot,
    serialize_unified_ebk_snapshot,
)
from app.domain.operator_payload import sanitize_operator_payload

MEITUAN_READ_ALL_DATASETS: tuple[str, ...] = (
    "workbench_home",
    "data_center_home",
    "data_center_trade",
    "data_center_traffic",
    "data_center_service",
    "data_center_peer_ranking",
    "promotion_coupon",
    "comment_manage_home",
    # 2026-09-11：推广通广告总览（广告营销卡数据源；未开通店铺读为
    # activated=False 的 success，不改变快照整体状态）。
    "ad_cpc",
)

FLIGGY_READ_ALL_DATASETS: tuple[str, ...] = (
    "home_v1",
    "diagnose_report",
    "operating_data",
    "promotion_agent",
    # 2026-09-11：付费推广-直通车（未开通商业化权限同样是 success）。
    "ad_p4p",
)


async def read_all_ebk_snapshots(
    *,
    ctrip_platform_account_id: str | None = None,
    meituan_platform_account_id: str | None = None,
    fliggy_platform_account_id: str | None = None,
    platforms: list[str] | None = None,
    actor_id: str = "operator",
    headless: bool = False,
    push_to_feishu: bool = False,
    feishu_watch_group_key: str | None = None,
    trigger_via: str | None = None,
) -> dict[str, Any]:
    resolved_trigger_via = _resolve_trigger_via(actor_id=actor_id, trigger_via=trigger_via)
    selected_platforms = _resolve_requested_platforms(
        platforms=platforms,
        ctrip_platform_account_id=ctrip_platform_account_id,
        meituan_platform_account_id=meituan_platform_account_id,
        fliggy_platform_account_id=fliggy_platform_account_id,
    )
    results: dict[str, Any] = {}

    if "ctrip" in selected_platforms:
        if not ctrip_platform_account_id:
            raise ValueError("ctrip_platform_account_id is required when reading Ctrip EBK.")
        ctrip_browser_session_id = _require_ebk_session(
            platform="ctrip",
            platform_account_id=ctrip_platform_account_id,
        )
        results["ctrip"] = await _read_ctrip_platform(
            platform_account_id=ctrip_platform_account_id,
            browser_session_id=ctrip_browser_session_id,
            actor_id=actor_id,
            trigger_via=resolved_trigger_via,
        )

    if "meituan" in selected_platforms:
        if not meituan_platform_account_id:
            raise ValueError("meituan_platform_account_id is required when reading Meituan EBK.")
        meituan_browser_session_id = _require_ebk_session(
            platform="meituan",
            platform_account_id=meituan_platform_account_id,
        )
        results["meituan"] = await _read_meituan_platform(
            platform_account_id=meituan_platform_account_id,
            browser_session_id=meituan_browser_session_id,
            actor_id=actor_id,
            headless=headless,
            trigger_via=resolved_trigger_via,
        )

    if "fliggy" in selected_platforms:
        if not fliggy_platform_account_id:
            raise ValueError("fliggy_platform_account_id is required when reading Fliggy EBK.")
        fliggy_browser_session_id = _require_ebk_session(
            platform="fliggy",
            platform_account_id=fliggy_platform_account_id,
        )
        results["fliggy"] = await _read_fliggy_platform(
            platform_account_id=fliggy_platform_account_id,
            browser_session_id=fliggy_browser_session_id,
            actor_id=actor_id,
            headless=headless,
            trigger_via=resolved_trigger_via,
        )

    feishu_pushes: dict[str, Any] = {}
    if push_to_feishu:
        from app.domain.ebk_snapshots.feishu_push import push_platform_snapshot_to_ctrip_webhook

        for platform_key in ("meituan", "fliggy"):
            platform_result = results.get(platform_key)
            if not isinstance(platform_result, dict):
                continue
            feishu_pushes[platform_key] = await push_platform_snapshot_to_ctrip_webhook(
                platform=platform_key,
                platform_result=platform_result,
                feishu_watch_group_key=feishu_watch_group_key,
            )

    overall_status = _combine_result_statuses(
        [str(result.get("status") or "error") for result in results.values()]
    )
    payload: dict[str, Any] = {
        "status": overall_status,
        "summary": _overall_summary_from_results(overall_status, results),
        "results": results,
    }
    if push_to_feishu:
        payload["feishu_push"] = feishu_pushes
        payload["summary"] = _append_feishu_push_summary(payload["summary"], feishu_pushes)
    return sanitize_operator_payload(payload)


def _append_feishu_push_summary(summary: str, feishu_pushes: dict[str, Any]) -> str:
    if not feishu_pushes:
        return summary
    parts: list[str] = []
    for platform, push in feishu_pushes.items():
        if not isinstance(push, dict):
            continue
        label = _platform_label(platform)
        status = str(push.get("status") or "unknown")
        push_summary = str(push.get("summary") or "")
        if status == "success":
            parts.append(f"{label}已推飞书")
        elif status == "config_required":
            parts.append(f"{label}飞书需配置")
        elif status == "skipped":
            parts.append(f"{label}飞书已跳过")
        else:
            parts.append(f"{label}飞书推送{status}" + (f"：{push_summary}" if push_summary else ""))
    if not parts:
        return summary
    return f"{summary}；{'；'.join(parts)}"


def _resolve_requested_platforms(
    *,
    platforms: list[str] | None,
    ctrip_platform_account_id: str | None,
    meituan_platform_account_id: str | None,
    fliggy_platform_account_id: str | None,
) -> list[str]:
    if platforms:
        selected: list[str] = []
        for item in platforms:
            value = str(item or "").strip().lower()
            if value in {"both", "all", "joint"}:
                selected.extend(["ctrip", "meituan", "fliggy"])
            elif value in {"ctrip", "meituan", "fliggy"}:
                selected.append(value)
            else:
                raise ValueError(f"Unsupported EBK platform: {item}")
        selected = list(dict.fromkeys(selected))
    else:
        selected = []
        if ctrip_platform_account_id:
            selected.append("ctrip")
        if meituan_platform_account_id:
            selected.append("meituan")
        if fliggy_platform_account_id:
            selected.append("fliggy")
    if not selected:
        raise ValueError("At least one EBK platform/account must be requested.")
    return selected


def _combine_result_statuses(statuses: list[str]) -> EbkSnapshotStatus:
    if not statuses:
        return "error"
    normalized = [_normalize_snapshot_status(status) for status in statuses]
    if all(status == "success" for status in normalized):
        return "success"
    if all(status == "error" for status in normalized):
        return "error"
    return "partial"


def _overall_summary_from_results(overall_status: str, results: dict[str, Any]) -> str:
    if overall_status == "success":
        keys = set(results)
        if keys == {"ctrip", "meituan", "fliggy"}:
            return "携程、美团与飞猪 EBK 数据均已读取并保存。"
        if keys == {"ctrip", "meituan"}:
            return "携程与美团 EBK 数据均已读取并保存。"
        if keys == {"ctrip", "fliggy"}:
            return "携程与飞猪 EBK 数据均已读取并保存。"
        if keys == {"meituan", "fliggy"}:
            return "美团与飞猪 EBK 数据均已读取并保存。"
        if keys == {"ctrip"}:
            return "携程 EBK 数据已读取并保存。"
        if keys == {"meituan"}:
            return "美团 EBK 数据已读取并保存。"
        if keys == {"fliggy"}:
            return "飞猪 EBK 数据已读取并保存。"
    status_parts = "，".join(
        f"{_platform_label(platform)}={result.get('status', 'unknown')}"
        for platform, result in results.items()
    )
    return f"统一 EBK 读取完成（{overall_status}）：{status_parts}。"


def _platform_label(platform: str) -> str:
    return {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}.get(platform, platform)


async def _read_ctrip_platform(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    trigger_via: str,
    global_run_id: str | None = None,
) -> dict[str, Any]:
    monitor = await get_ctrip_ebooking_service().read_monitor(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        filters={},
        actor_id=actor_id,
        push_to_feishu=False,
    )
    payload = monitor.model_dump(mode="json")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    raw_datasets = data.get("datasets") if isinstance(data.get("datasets"), dict) else {}
    dataset_statuses = (
        evidence.get("dataset_statuses")
        if isinstance(evidence.get("dataset_statuses"), dict)
        else {}
    )
    datasets = {
        str(dataset_id): {
            "status": str(dataset_statuses.get(dataset_id) or payload.get("status") or "error"),
            "data": dataset_data if isinstance(dataset_data, dict) else {},
        }
        for dataset_id, dataset_data in raw_datasets.items()
    }
    status = _normalize_snapshot_status(str(payload.get("status") or "error"))
    snapshot = UnifiedEbkSnapshot(
        platform="ctrip",
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        read_at=str(data.get("captured_at") or datetime.now(timezone.utc).isoformat()),
        status=status,
        summary=str(payload.get("summary") or "Ctrip EBK monitor read finished."),
        datasets=datasets,
        evidence=evidence,
        platform_data={
            "query_type": data.get("query_type"),
            "monitor": data.get("monitor"),
            "raw_status": payload.get("status"),
        },
    )
    saved = await save_ebk_snapshot(
        snapshot,
        read_trigger=_read_trigger_payload(
            actor_id=actor_id, via=trigger_via, global_run_id=global_run_id
        ),
    )
    return {
        "platform": "ctrip",
        "status": status,
        "summary": snapshot.summary,
        "snapshot_id": saved["id"],
        "dataset_count": saved["dataset_count"],
        "payload": serialize_unified_ebk_snapshot(snapshot),
    }


async def persist_ctrip_run_snapshot(
    *,
    run_payload: dict[str, Any],
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    trigger_via: str,
    global_run_id: str | None = None,
    source_watch_run_id: int | None = None,
    shop_key: str | None = None,
) -> dict[str, Any]:
    """携程 watch-group 运行结果补存为统一快照（ebk_read_snapshots）。

    全局推送的携程读取走 watch-group 链路（只落 ctrip_ebooking_watch_runs），
    若不补存，get_latest_success_ebk_snapshot("ctrip") 永远取不到全局推送
    数据，环比基线会错拿其它来源（如定时看板）的旧快照。
    source_watch_run_id 使补存按 run 幂等（重试不新增行，重构 P4 §5.1）；
    未显式传入时从 run_payload.persistence.run_id 提取。
    """
    if not isinstance(source_watch_run_id, int) or source_watch_run_id <= 0:
        persistence = run_payload.get("persistence")
        if isinstance(persistence, dict):
            candidate = persistence.get("run_id")
            if isinstance(candidate, int):
                source_watch_run_id = candidate

    datasets_raw = run_payload.get("dataset_results")
    datasets: dict[str, Any] = {}
    if isinstance(datasets_raw, list):
        for item in datasets_raw:
            if not isinstance(item, dict):
                continue
            dataset_id = str(item.get("dataset_id") or "").strip()
            if not dataset_id:
                continue
            entry: dict[str, Any] = {
                "status": str(item.get("status") or "error"),
                "data": item.get("data") if isinstance(item.get("data"), dict) else {},
            }
            if item.get("captured_at"):
                entry["captured_at"] = item.get("captured_at")
            datasets[dataset_id] = entry
    raw_status = str(run_payload.get("status") or "").strip()
    if raw_status:
        status = _normalize_snapshot_status(raw_status)
    else:
        status = _combine_dataset_statuses(
            [str(entry.get("status") or "error") for entry in datasets.values()]
        )
    snapshot = UnifiedEbkSnapshot(
        platform="ctrip",
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        read_at=str(
            run_payload.get("captured_at")
            or datetime.now(timezone.utc).isoformat()
        ),
        status=status,
        summary=str(run_payload.get("summary") or "Ctrip global push read snapshot."),
        datasets=datasets,
        evidence={},
        platform_data={"source": "global_push"},
    )
    return await save_ebk_snapshot(
        snapshot,
        read_trigger=_read_trigger_payload(
            actor_id=actor_id, via=trigger_via, global_run_id=global_run_id
        ),
        source_watch_run_id=source_watch_run_id,
        shop_key=shop_key,
    )


async def _read_meituan_platform(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    headless: bool,
    trigger_via: str,
    global_run_id: str | None = None,
) -> dict[str, Any]:
    service = get_meituan_ebooking_service()
    datasets: dict[str, Any] = {}
    evidence_parts: list[dict[str, Any]] = []
    platform_data: dict[str, Any] = {"datasets": {}}
    statuses: list[str] = []

    for dataset in MEITUAN_READ_ALL_DATASETS:
        result = await service.read_dataset(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            dataset=dataset,
            headless=headless,
        )
        status = _normalize_snapshot_status(str(result.get("status") or "error"))
        statuses.append(status)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        datasets[dataset] = {
            "status": status,
            "label": dataset,
            "data": data,
        }
        evidence_parts.append(evidence)
        platform_data["datasets"][dataset] = {
            "status": result.get("status"),
            "summary": result.get("summary"),
            "data": data,
        }

    status = _combine_dataset_statuses(statuses)
    snapshot = UnifiedEbkSnapshot(
        platform="meituan",
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        read_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=_meituan_summary(status, statuses),
        datasets=datasets,
        evidence=_merge_evidence(evidence_parts),
        platform_data=platform_data,
    )
    saved = await save_ebk_snapshot(
        snapshot,
        read_trigger=_read_trigger_payload(
            actor_id=actor_id, via=trigger_via, global_run_id=global_run_id
        ),
    )
    return {
        "platform": "meituan",
        "status": status,
        "summary": snapshot.summary,
        "snapshot_id": saved["id"],
        "dataset_count": saved["dataset_count"],
        "payload": serialize_unified_ebk_snapshot(snapshot),
    }


async def _read_fliggy_platform(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    headless: bool,
    trigger_via: str,
    global_run_id: str | None = None,
) -> dict[str, Any]:
    service = get_fliggy_ebooking_service()
    datasets: dict[str, Any] = {}
    evidence_parts: list[dict[str, Any]] = []
    platform_data: dict[str, Any] = {"datasets": {}}
    statuses: list[str] = []

    for dataset in FLIGGY_READ_ALL_DATASETS:
        result = await service.read_dataset(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            dataset=dataset,
            headless=headless,
        )
        status = _normalize_snapshot_status(str(result.get("status") or "error"))
        statuses.append(status)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        datasets[dataset] = {
            "status": status,
            "label": dataset,
            "data": data,
        }
        evidence_parts.append(evidence)
        platform_data["datasets"][dataset] = {
            "status": result.get("status"),
            "summary": result.get("summary"),
            "data": data,
        }

    status = _combine_dataset_statuses(statuses)
    snapshot = UnifiedEbkSnapshot(
        platform="fliggy",
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        read_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        summary=_fliggy_summary(status, statuses),
        datasets=datasets,
        evidence=_merge_evidence(evidence_parts),
        platform_data=platform_data,
    )
    saved = await save_ebk_snapshot(
        snapshot,
        read_trigger=_read_trigger_payload(
            actor_id=actor_id, via=trigger_via, global_run_id=global_run_id
        ),
    )
    return {
        "platform": "fliggy",
        "status": status,
        "summary": snapshot.summary,
        "snapshot_id": saved["id"],
        "dataset_count": saved["dataset_count"],
        "payload": serialize_unified_ebk_snapshot(snapshot),
    }


async def read_meituan_platform_snapshot(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    headless: bool = False,
    trigger_via: str = "console",
    global_run_id: str | None = None,
) -> dict[str, Any]:
    """Read all Meituan EBK datasets and persist one unified snapshot."""
    return await _read_meituan_platform(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        headless=headless,
        trigger_via=trigger_via,
        global_run_id=global_run_id,
    )


async def read_fliggy_platform_snapshot(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    headless: bool = False,
    trigger_via: str = "console",
    global_run_id: str | None = None,
) -> dict[str, Any]:
    """Read all Fliggy EBK datasets and persist one unified snapshot."""
    return await _read_fliggy_platform(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        headless=headless,
        trigger_via=trigger_via,
        global_run_id=global_run_id,
    )


def _read_trigger_payload(
    *, actor_id: str, via: str, global_run_id: str | None = None
) -> dict[str, str]:
    payload = {"actor_id": actor_id, "via": via}
    if str(global_run_id or "").strip():
        payload["global_run_id"] = str(global_run_id).strip()
    return payload


def _resolve_trigger_via(*, actor_id: str, trigger_via: str | None) -> str:
    normalized = str(trigger_via or "").strip().lower()
    if normalized in {"console", "bot_analysis"}:
        return normalized
    return "bot_analysis" if str(actor_id or "").strip().lower() == "hermes" else "console"


def _require_ebk_session(*, platform: str, platform_account_id: str) -> str:
    browser_session_id = get_browser_session_id_for_platform_account(platform_account_id)
    if not browser_session_id:
        raise KeyError(f"No browser session is configured for {platform_account_id}.")
    session = get_browser_session_store().get(browser_session_id)
    if session.platform != platform or session.intended_use != "ebk":
        raise ValueError(
            f"{platform} EBK read-all requires a {platform} EBK browser session."
        )
    if session.platform_account_id != platform_account_id:
        raise ValueError("browser_session_id does not match the requested platform_account_id.")
    return browser_session_id


def _normalize_snapshot_status(status: str) -> EbkSnapshotStatus:
    normalized = str(status or "").strip().lower()
    if normalized in {"success", "partial", "human_required", "needs_review", "error"}:
        return normalized  # type: ignore[return-value]
    if normalized in {"page_structure_changed", "failed"}:
        return "partial" if normalized == "page_structure_changed" else "error"
    return "error"


def _combine_dataset_statuses(statuses: list[str]) -> EbkSnapshotStatus:
    if not statuses:
        return "error"
    if all(status == "success" for status in statuses):
        return "success"
    if all(status == "human_required" for status in statuses):
        return "human_required"
    if "human_required" in statuses or "needs_review" in statuses or "partial" in statuses:
        return "partial"
    if "error" in statuses:
        return "error"
    return "partial"


def _meituan_summary(status: str, dataset_statuses: list[str]) -> str:
    success_count = sum(1 for item in dataset_statuses if item == "success")
    total = len(dataset_statuses)
    if status == "success":
        return f"美团 EBK 已读取 {total} 个数据集并保存。"
    if status == "human_required":
        return "美团 EBK 需要人工登录或验证后才能继续读取。"
    return f"美团 EBK 部分可读（成功 {success_count}/{total}）。"


def _fliggy_summary(status: str, dataset_statuses: list[str]) -> str:
    success_count = sum(1 for item in dataset_statuses if item == "success")
    total = len(dataset_statuses)
    if status == "success":
        return f"飞猪 EBK 已读取 {total} 个数据集并保存。"
    if status == "human_required":
        return "飞猪 EBK 需要人工登录或验证后才能继续读取。"
    return f"飞猪 EBK 部分可读（成功 {success_count}/{total}）。"


def _merge_evidence(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "dataset_count": len(parts),
        "parsers": [],
    }
    for part in parts:
        parser = part.get("parser")
        if parser and parser not in merged["parsers"]:
            merged["parsers"].append(parser)
        for key, value in part.items():
            if key == "parser":
                continue
            if key not in merged:
                merged[key] = value
    if merged["parsers"]:
        merged["parser"] = merged["parsers"][0]
    return merged
