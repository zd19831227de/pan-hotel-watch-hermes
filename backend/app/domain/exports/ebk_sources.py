from __future__ import annotations

from typing import Any


def watch_runs_to_snapshots(
    runs: list[dict[str, Any]],
    *,
    global_run_links: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Adapt persisted Ctrip watch runs to the unified export snapshot shape.

    This allow-list intentionally excludes evidence, browser-session and push
    fields even when a caller supplies them on the run dictionaries.

    ``global_run_links`` maps a Ctrip watch run id to its EBK global push run id
    (loaded from the global push run table, design §8.2.1). It only applies to
    ``source="global_push"`` runs so grouped trimming can keep one global run's
    Ctrip/Meituan/Fliggy results together.
    """

    links = global_run_links or {}
    snapshots: list[dict[str, Any]] = []
    for run in runs:
        datasets: dict[str, Any] = {}
        results = run.get("results") if isinstance(run.get("results"), list) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            dataset_id = str(result.get("dataset_id") or "").strip()
            if not dataset_id:
                continue
            datasets[dataset_id] = {
                "status": str(result.get("status") or "error"),
                "data": result.get("data_json")
                if isinstance(result.get("data_json"), dict)
                else {},
            }
        captured_at = run.get("captured_at")
        if hasattr(captured_at, "isoformat"):
            captured_at = captured_at.isoformat()
        payload_json: dict[str, Any] = {"datasets": datasets}
        global_run_id = (
            links.get(str(run.get("run_id") or ""))
            if str(run.get("source") or "").strip().lower() == "global_push"
            else None
        )
        if global_run_id:
            payload_json["read_trigger"] = {
                "via": "global_push",
                "global_run_id": global_run_id,
            }
        snapshots.append(
            {
                "platform": "ctrip",
                "platform_account_id": str(run.get("platform_account_id") or ""),
                "created_at": str(captured_at or ""),
                "payload_json": payload_json,
                "export_source": _watch_run_source_label(run),
            }
        )
    return snapshots


def merge_export_snapshots(
    snapshots: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    *,
    limit: int,
    ctrip_global_run_links: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Merge both persisted sources, keep the newest global limit, output ascending."""

    normalized_snapshots = []
    for snapshot in snapshots:
        normalized = dict(snapshot)
        normalized["export_source"] = _snapshot_source_label(normalized)
        normalized_snapshots.append(normalized)
    candidates = normalized_snapshots + watch_runs_to_snapshots(
        runs, global_run_links=ctrip_global_run_links
    )
    return _trim_by_groups(candidates, _safe_limit(limit))


def _trim_by_groups(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按运行成组裁剪（设计 §8.2.1/C6）：同一 global_run_id 的多平台快照要么
    整组保留、要么整组裁掉，避免按时间全局截断拆散同一次全局运行；
    组时间取组内最新 created_at；无 global_run_id 的记录各自成组（旧行为）。
    携程 watch runs 经运行表关联映射补挂 global_run_id（T2.2）。
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in candidates:
        payload = item.get("payload_json")
        trigger = payload.get("read_trigger") if isinstance(payload, dict) else None
        group_key = (
            str(trigger.get("global_run_id") or "").strip()
            if isinstance(trigger, dict)
            else ""
        ) or f"__single__{_created_at_text(item)}"
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append(item)
    group_times = {
        key: max(_created_at_text(item) for item in groups[key]) for key in order
    }
    kept_keys = set(sorted(order, key=lambda k: group_times[k], reverse=True)[:limit])
    kept = [item for key in order if key in kept_keys for item in groups[key]]
    return sorted(kept, key=_created_at_text)


def _watch_run_source_label(run: dict[str, Any]) -> str:
    source = str(run.get("source") or "").strip().lower()
    actor_id = str(run.get("actor_id") or "").strip().lower()
    if source == "scheduler" and actor_id == "daily_brief_scheduler":
        return "每日简报"
    if source == "scheduler":
        return "定时轮询"
    if source == "api" and actor_id == "hermes":
        return "机器人触发"
    if source == "api":
        return "手动读取"
    if source == "feishu":
        return "机器人触发"
    if source == "global_push":
        return "全局定时推送"
    return "未标记"


def _snapshot_source_label(snapshot: dict[str, Any]) -> str:
    existing = str(snapshot.get("export_source") or "").strip()
    if existing:
        return existing
    payload = snapshot.get("payload_json")
    trigger = payload.get("read_trigger") if isinstance(payload, dict) else None
    if not isinstance(trigger, dict):
        return "未标记"
    via = str(trigger.get("via") or "").strip().lower()
    actor_id = str(trigger.get("actor_id") or "").strip().lower()
    if via in {"bot_analysis", "hermes", "feishu"} or actor_id == "hermes":
        return "机器人分析"
    if via == "console" or actor_id == "operator":
        return "手动读取"
    if via == "global_push" or actor_id == "ebk_global_push_scheduler":
        return "全局定时推送"
    return "未标记"


def _created_at_text(item: dict[str, Any]) -> str:
    value = item.get("created_at")
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value or "")


def _safe_limit(value: int) -> int:
    return max(1, min(int(value), 5000))
