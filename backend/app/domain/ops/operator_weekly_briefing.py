from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, select

from app.db.session import ensure_db_ready, session_scope
from app.domain.competitor_prices.models import CtripCompetitorPriceRun, CtripCompetitorWatchGroup
from app.domain.competitor_prices.repository import (
    _poll_status_advances_schedule,
    _run_summary_to_dict as competitor_run_summary,
)
from app.domain.ctrip_ebooking_watch.models import (
    CtripEbookingWatchDatasetResult,
    CtripEbookingWatchGroup,
    CtripEbookingWatchRun,
)
from app.domain.ctrip_ebooking_watch.repository import (
    _result_to_dict,
    _run_summary_to_dict,
)
from app.domain.ops.feishu_chat_context import build_feishu_chat_context
from app.domain.ops.weekly_period import previous_week_bounds, resolve_previous_calendar_week
from app.domain.price_change_alerts.models import PriceChangeAlertRecord
from app.domain.price_change_alerts.repository import _record_to_dict


def _metric_display_value(metric: Any) -> str | None:
    if not isinstance(metric, dict):
        return None
    value = metric.get("value")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_review_summary(dataset_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "comment_monitor":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        monitor = data.get("comment_monitor") if isinstance(data.get("comment_monitor"), dict) else {}
        overview = monitor.get("review_overview") if isinstance(monitor.get("review_overview"), dict) else {}
        return {
            "available": True,
            "positive_reviews_yesterday": _metric_display_value(overview.get("positive_reviews_yesterday")),
            "negative_reviews_yesterday": _metric_display_value(overview.get("negative_reviews_yesterday")),
            "overall_score": _metric_display_value(overview.get("overall_score")),
            "captured_at": item.get("captured_at"),
            "caliber_note": "昨日好评/差评口径，来自该次 EBK 快照，非整周汇总。",
        }
    return {"available": False, "summary": "该次 EBK 读取无点评数据。"}


def _extract_beneficial_metrics(dataset_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    for item in dataset_results:
        if not isinstance(item, dict) or item.get("dataset_id") != "beneficial_data":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        rows: list[dict[str, str]] = []
        for key in ("occupancy_rate", "adr", "revpar", "room_nights", "revenue"):
            metric = metrics.get(key)
            if not isinstance(metric, dict):
                continue
            label = str(metric.get("label") or key)
            value = metric.get("display_value") or metric.get("value")
            if value is not None:
                rows.append({"field": label, "value": str(value)})
        return rows
    return []


def _format_time(value: Any) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    if "T" in text:
        return text.replace("T", " ")[:16]
    return text[:16]


async def _ebk_runs_in_period(
    *,
    watch_group_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await session.scalar(
            select(CtripEbookingWatchGroup).where(CtripEbookingWatchGroup.group_key == watch_group_key)
        )
        if group is None:
            return []
        runs = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchRun)
                    .where(
                        CtripEbookingWatchRun.group_id == group.id,
                        CtripEbookingWatchRun.captured_at >= start_at,
                        CtripEbookingWatchRun.captured_at <= end_at,
                    )
                    .order_by(CtripEbookingWatchRun.captured_at.desc(), CtripEbookingWatchRun.id.desc())
                )
            ).all()
        )
    return [_run_summary_to_dict(run, watch_group_key) for run in runs if run is not None]


async def _latest_ebk_run_payload_in_period(
    *,
    watch_group_key: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any] | None:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await session.scalar(
            select(CtripEbookingWatchGroup).where(CtripEbookingWatchGroup.group_key == watch_group_key)
        )
        if group is None:
            return None
        run = await session.scalar(
            select(CtripEbookingWatchRun)
            .where(
                CtripEbookingWatchRun.group_id == group.id,
                CtripEbookingWatchRun.captured_at >= start_at,
                CtripEbookingWatchRun.captured_at <= end_at,
                CtripEbookingWatchRun.status.in_(("success", "partial")),
            )
            .order_by(CtripEbookingWatchRun.captured_at.desc(), CtripEbookingWatchRun.id.desc())
            .limit(1)
        )
        if run is None:
            return None
        results = list(
            (
                await session.scalars(
                    select(CtripEbookingWatchDatasetResult)
                    .where(CtripEbookingWatchDatasetResult.run_id == run.id)
                    .order_by(CtripEbookingWatchDatasetResult.id.asc())
                )
            ).all()
        )
    dataset_results = [_result_to_dict(result) for result in results]
    return {
        "watch_group_key": watch_group_key,
        "run": _run_summary_to_dict(run, watch_group_key),
        "dataset_results": dataset_results,
    }


async def _competitor_runs_in_period(
    *,
    watch_group_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        group = await session.scalar(
            select(CtripCompetitorWatchGroup).where(CtripCompetitorWatchGroup.group_key == watch_group_key)
        )
        if group is None:
            return []
        runs = list(
            (
                await session.scalars(
                    select(CtripCompetitorPriceRun)
                    .where(
                        CtripCompetitorPriceRun.group_id == group.id,
                        CtripCompetitorPriceRun.captured_at >= start_at,
                        CtripCompetitorPriceRun.captured_at <= end_at,
                    )
                    .order_by(CtripCompetitorPriceRun.captured_at.desc(), CtripCompetitorPriceRun.id.desc())
                )
            ).all()
        )
    from app.domain.price_change_alerts.repository import _record_to_dict

    return [competitor_run_summary(run) for run in runs if run is not None]


async def _price_alerts_in_period(
    *,
    watch_group_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    await ensure_db_ready()
    async with session_scope() as session:
        rows = list(
            (
                await session.scalars(
                    select(PriceChangeAlertRecord)
                    .where(
                        PriceChangeAlertRecord.watch_group_key == watch_group_key,
                        PriceChangeAlertRecord.created_at >= start_at,
                        PriceChangeAlertRecord.created_at <= end_at,
                    )
                    .order_by(desc(PriceChangeAlertRecord.created_at), desc(PriceChangeAlertRecord.id))
                    .limit(20)
                )
            ).all()
        )
    return [_record_to_dict(row) for row in rows]


def _count_trusted_runs(runs: list[dict[str, Any]]) -> int:
    count = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        if _poll_status_advances_schedule(str(run.get("status") or "")):
            count += 1
    return count


def _ebk_section_rows(
    *,
    runs: list[dict[str, Any]],
    latest_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not runs:
        rows.append({"field": "EBK 读取", "value": "上周无 EBK 读取记录"})
        return rows
    success_count = sum(
        1 for run in runs if str(run.get("status") or "") in {"success", "partial"}
    )
    rows.append({"field": "EBK 轮询次数", "value": f"{len(runs)} 次（成功/部分成功 {success_count} 次）"})
    rows.append(
        {
            "field": "最后一次读取",
            "value": f"{runs[0].get('status') or '—'} · {_format_time(runs[0].get('captured_at'))}",
        }
    )
    if latest_payload is None:
        rows.append({"field": "经营指标", "value": "上周无可用 EBK 成功快照"})
        return rows
    dataset_results = latest_payload.get("dataset_results")
    results = dataset_results if isinstance(dataset_results, list) else []
    review = _extract_review_summary(results)
    if review.get("available"):
        rows.append(
            {
                "field": "快照·昨日好评",
                "value": str(review.get("positive_reviews_yesterday") or "—"),
            }
        )
        rows.append(
            {
                "field": "快照·昨日差评",
                "value": str(review.get("negative_reviews_yesterday") or "—"),
            }
        )
    for metric in _extract_beneficial_metrics(results)[:3]:
        rows.append({"field": f"快照·{metric['field']}", "value": metric["value"]})
    run = latest_payload.get("run") if isinstance(latest_payload.get("run"), dict) else {}
    rows.append(
        {
            "field": "指标口径",
            "value": f"来自 {_format_time(run.get('captured_at'))} 最后一次成功快照，非整周汇总或同比。",
        }
    )
    return rows


def _competitor_section_rows(
    *,
    runs: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not runs:
        rows.append({"field": "竞品轮询", "value": "上周无竞品轮询记录"})
    else:
        trusted = _count_trusted_runs(runs)
        rows.append({"field": "竞品轮询次数", "value": f"{len(runs)} 次（可信 {trusted} 次）"})
        rows.append(
            {
                "field": "最后一次轮询",
                "value": f"{runs[0].get('status') or '—'} · {_format_time(runs[0].get('captured_at'))}",
            }
        )
    if alerts:
        rows.append({"field": "价格变化提醒", "value": f"{len(alerts)} 条"})
        latest = alerts[0]
        rows.append(
            {
                "field": "最近一条提醒",
                "value": str(latest.get("summary") or latest.get("status") or "—")[:100],
            }
        )
    else:
        rows.append({"field": "价格变化提醒", "value": "上周无记录"})
    return rows


async def build_operator_weekly_briefing(
    *,
    chat_id: str | None,
    store_hint: str | None = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    context = await build_feishu_chat_context(chat_id, store_hint=store_hint)
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    period = resolve_previous_calendar_week(reference_date=reference_date)
    start_at, end_at = previous_week_bounds(reference_date=reference_date)

    if resolution.get("status") != "resolved":
        summary = str(resolution.get("summary") or "请先说明要查看哪家门店的上周简报。")
        context_summary = (
            context.get("operator_summary")
            if isinstance(context.get("operator_summary"), dict)
            else {}
        )
        operator_summary: dict[str, Any] = {
            "intro": summary,
            "overview_rows": context_summary.get("overview_rows") or [],
            "caliber_note": "一群多店时必须在问题里带上店名，例如「静里 上周经营简报」。",
            "routing_hints": [
                "先 resolve_feishu_chat_context(store_hint=店名)，resolution.status=resolved 后再调 weekly briefing。"
            ],
        }
        store_selection_rows = context_summary.get("store_selection_rows")
        if isinstance(store_selection_rows, list) and store_selection_rows:
            operator_summary["store_selection_rows"] = store_selection_rows
        elif isinstance(resolution.get("store_candidates"), list):
            operator_summary["store_selection_rows"] = [
                {
                    "field": str(item.get("store_label") or "未命名门店"),
                    "value": (
                        f"EBK={item.get('ebk_watch_group_key') or '未配对'}；"
                        f"竞品={item.get('competitor_watch_group_key') or '未配对'}"
                    ),
                }
                for item in resolution.get("store_candidates") or []
                if isinstance(item, dict)
            ]
        return {
            "status": "ambiguous_store",
            "briefing_kind": "weekly",
            "period": period,
            "chat_id": context.get("chat_id"),
            "binding_mode": context.get("binding_mode"),
            "resolution": resolution,
            "operator_summary": operator_summary,
            "usage_note": "未选定门店，不返回任何经营数字。",
        }

    store_label = str(resolution.get("store_label") or "未命名门店")
    ebk_key = str(resolution.get("ebk_watch_group_key") or "").strip()
    competitor_key = str(resolution.get("competitor_watch_group_key") or "").strip()

    ebk_runs: list[dict[str, Any]] = []
    competitor_runs: list[dict[str, Any]] = []
    latest_ebk: dict[str, Any] | None = None
    alerts: list[dict[str, Any]] = []

    if ebk_key:
        ebk_runs = await _ebk_runs_in_period(
            watch_group_key=ebk_key,
            start_at=start_at,
            end_at=end_at,
        )
        latest_ebk = await _latest_ebk_run_payload_in_period(
            watch_group_key=ebk_key,
            start_at=start_at,
            end_at=end_at,
        )
    if competitor_key:
        competitor_runs = await _competitor_runs_in_period(
            watch_group_key=competitor_key,
            start_at=start_at,
            end_at=end_at,
        )
        alerts = await _price_alerts_in_period(
            watch_group_key=competitor_key,
            start_at=start_at,
            end_at=end_at,
        )

    overview_rows = [
        {"field": "门店", "value": store_label},
        {"field": "统计周期", "value": period["label"]},
        *_ebk_section_rows(runs=ebk_runs, latest_payload=latest_ebk),
        *_competitor_section_rows(runs=competitor_runs, alerts=alerts),
    ]

    data_gaps: list[str] = []
    if not ebk_runs:
        data_gaps.append("上周无 EBK 读取记录")
    if not competitor_runs:
        data_gaps.append("上周无竞品轮询记录")

    intro_parts = [
        f"{store_label} 上周经营简报（{period['label']}）。",
        "以下数字全部来自数据库已持久化记录，不含模型推算或同比。",
    ]
    if data_gaps:
        intro_parts.append("数据缺口：" + "；".join(data_gaps) + "。")

    return {
        "status": "success",
        "briefing_kind": "weekly",
        "period": period,
        "chat_id": context.get("chat_id"),
        "store_label": store_label,
        "ebk_watch_group_key": ebk_key or None,
        "competitor_watch_group_key": competitor_key or None,
        "ebk_run_count": len(ebk_runs),
        "competitor_run_count": len(competitor_runs),
        "price_alert_count": len(alerts),
        "data_gaps": data_gaps,
        "has_comparable_yoy": False,
        "operator_summary": {
            "intro": "\n".join(intro_parts),
            "overview_rows": overview_rows,
            "caliber_note": (
                "本简报不提供「上周同期」或同比百分比；EBK 经营指标仅展示周期内最后一次成功快照。"
                "竞品变化提醒按相邻轮询口径单独查询。"
            ),
            "routing_hints": [
                "用户追问明细时，分别用 read_ebk_datasets / get_competitor_latest_prices，"
                "并继续使用已解析的 watch_group_key。"
            ],
        },
        "usage_note": "只读周报；不触发浏览器、不写价、不编造缺失数据。",
    }
