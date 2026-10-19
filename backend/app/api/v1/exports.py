"""CSV/Excel 导出端点（UI-Fix-F2AC + 修正4）：只读，复用现有 repository/service 查询。

安全红线：导出字段白名单（仅业务数据），绝不导出密码/cookie/浏览器
profile 路径/webhook URL/API key/截图路径/browser_session_id。

留存口径（修正4）：业务数据导出默认窗口 180 天（半年），可用 days 参数自定义；
本模块只控制导出/查询窗口，不自动删除任何持久化数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import Response

from app.domain.browser.sessions import get_browser_session_store
from app.domain.competitor_prices import repository as competitor_prices_repository
from app.domain.ctrip_ebooking_watch import repository as ebk_watch_repository
from app.domain.distill import repository as distill_repository
from app.core.settings import get_settings
from app.domain.ebk_snapshots import repository as ebk_snapshots_repository
from app.domain.exports import ebk_business_metrics, ebk_sources
from app.domain.exports.csv_builder import build_csv_bytes
from app.domain.exports.xlsx_builder import build_xlsx_bytes, build_xlsx_workbook_bytes
from app.domain.price_change_alerts import service as price_change_alerts_service

router = APIRouter(prefix="/export", tags=["export"])

ExportFormat = Literal["csv", "xlsx"]

DEFAULT_RETENTION_DAYS = 180

_EBK_EXPORT_RANGES = frozenset({"today", "week", "month", "custom"})

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _export_response(
    *,
    headers: list[str],
    rows: list[dict[str, Any]] | list[list[Any]],
    export_format: ExportFormat,
    filename_stem: str,
) -> Response:
    if export_format == "xlsx":
        payload = build_xlsx_bytes(headers=headers, rows=rows)
        media_type = _XLSX_MEDIA_TYPE
        extension = "xlsx"
    else:
        payload = build_csv_bytes(headers=headers, rows=rows)
        media_type = "text/csv; charset=utf-8"
        extension = "csv"
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename_stem}.{extension}"'
        },
    )


def _timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve_export_window(
    range_value: str | None,
    start_date: str | None,
    end_date: str | None,
    days: int,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime | None]:
    """返回当前 EBK 业务数据的服务端留存天数配置。

    ``end_at`` is exclusive.  For a custom end date this means next-day
    midnight, which covers every timestamp on the selected day including
    values with microseconds.
    """

    local_now = now or datetime.now()
    normalized_range = str(range_value or "").strip().lower()
    if not normalized_range:
        return local_now - timedelta(days=max(1, min(int(days), 3650))), None
    if normalized_range not in _EBK_EXPORT_RANGES:
        raise HTTPException(status_code=422, detail="不支持的经营数据导出范围")
    if normalized_range == "today":
        return local_now.replace(hour=0, minute=0, second=0, microsecond=0), None
    if normalized_range == "week":
        start_at = local_now - timedelta(days=local_now.weekday())
        return start_at.replace(hour=0, minute=0, second=0, microsecond=0), None
    if normalized_range == "month":
        return local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), None
    if not start_date or not end_date:
        raise HTTPException(
            status_code=422,
            detail="自定义范围必须同时填写开始日期和结束日期",
        )
    try:
        start_at = datetime.strptime(start_date, "%Y-%m-%d")
        inclusive_end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="日期格式必须为 YYYY-MM-DD") from exc
    if start_at > inclusive_end:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    return start_at, inclusive_end + timedelta(days=1)


@router.get("/competitor-price-trend.csv")
async def export_competitor_price_trend_csv(
    key: str = Query(default="default_competitor_watch", min_length=1, max_length=128),
    days: int = Query(default=7, ge=1, le=3650),
    granularity: Literal["run", "hour", "day"] = Query(default="run"),
    date_label: str | None = Query(default=None),
    breakfast_kind: str | None = Query(default=None),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    try:
        trend = await competitor_prices_repository.get_competitor_price_trend(
            watch_group_key=key,
            days=days,
            granularity=granularity,
            date_label=date_label,
            breakfast_kind=breakfast_kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if trend is None:
        raise HTTPException(status_code=404, detail="未找到竞品监控组。")
    headers = [
        "bucket_at",
        "hotel_name",
        "hotel_role",
        "date_label",
        "checkin_date",
        "breakfast_kind",
        "room_type",
        "min_price_amount_yuan",
        "max_price_amount_yuan",
        "trusted_rate_count",
    ]
    points = trend.get("points") if isinstance(trend.get("points"), list) else []
    return _export_response(
        headers=headers,
        rows=points,
        export_format=format,
        filename_stem=f"competitor-price-trend-{key}-{_timestamp_tag()}",
    )


@router.get("/price-change-alerts.csv")
async def export_price_change_alerts_csv(
    watch_group_key: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=100),
    days: int | None = Query(default=None, ge=1, le=3650),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    alerts = await price_change_alerts_service.list_price_change_alerts(
        watch_group_key=watch_group_key,
        limit=limit,
        days=days,
    )
    headers = [
        "created_at",
        "watch_group_key",
        "status",
        "summary",
    ]
    rows = [
        {
            "created_at": alert.get("created_at"),
            "watch_group_key": alert.get("watch_group_key"),
            "status": alert.get("status"),
            "summary": alert.get("summary"),
        }
        for alert in alerts
    ]
    return _export_response(
        headers=headers,
        rows=rows,
        export_format=format,
        filename_stem=f"price-change-alerts-{_timestamp_tag()}",
    )


@router.get("/ctrip-ebk-runs.csv")
async def export_ctrip_ebk_runs_csv(
    key: str = Query(default="default_data_center_watch", min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=100),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    runs_payload: dict[str, Any] | None = await ebk_watch_repository.list_data_center_watch_runs(
        watch_group_key=key,
        limit=limit,
    )
    if runs_payload is None:
        raise HTTPException(status_code=404, detail="Data-center watch group not found.")
    headers = [
        "captured_at",
        "status",
        "source",
        "actor_id",
        "dataset_count",
        "success_count",
        "failed_count",
        "human_required_count",
        "summary",
    ]
    rows = [
        {
            "captured_at": run.get("captured_at"),
            "status": run.get("status"),
            "source": run.get("source"),
            "actor_id": run.get("actor_id"),
            "dataset_count": run.get("dataset_count"),
            "success_count": run.get("success_count"),
            "failed_count": run.get("failed_count"),
            "human_required_count": run.get("human_required_count"),
            "summary": run.get("summary"),
        }
        for run in runs_payload.get("runs", [])
    ]
    return _export_response(
        headers=headers,
        rows=rows,
        export_format=format,
        filename_stem=f"ctrip-ebk-runs-{key}-{_timestamp_tag()}",
    )


@router.get("/distill-runs.csv")
async def export_distill_runs_csv(
    limit: int = Query(default=100, ge=1, le=100),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    runs = await distill_repository.list_distill_runs(limit=limit)
    headers = [
        "created_at",
        "platform",
        "source_type",
        "status",
        "knowledge_title",
        "rag_document_id",
        "reject_reason",
    ]
    rows = [
        {
            "created_at": run.get("created_at"),
            "platform": run.get("platform"),
            "source_type": run.get("source_type"),
            "status": run.get("status"),
            "knowledge_title": run.get("knowledge_title"),
            "rag_document_id": run.get("rag_document_id"),
            "reject_reason": run.get("reject_reason"),
        }
        for run in runs
    ]
    return _export_response(
        headers=headers,
        rows=rows,
        export_format=format,
        filename_stem=f"distill-runs-{_timestamp_tag()}",
    )


@router.get("/ebk-business-data")
async def export_ebk_business_data(
    days: int = Query(default=DEFAULT_RETENTION_DAYS, ge=1, le=3650),
    range_value: str | None = Query(default=None, alias="range", max_length=16),
    start_date: str | None = Query(default=None, max_length=10),
    end_date: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=1000, ge=1, le=5000),
    platforms: str | None = Query(default=None, max_length=128),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    """整体业务数据（规范化）：三平台（携程/美团/飞猪）经营指标中文明细长表；
    xlsx 额外附跨平台核心指标对比 Sheet。CSV 仅导出明细长表。"""
    platform_filter = _parse_platforms_query(platforms)
    start_at, end_at = _resolve_export_window(
        range_value,
        start_date,
        end_date,
        days,
    )
    snapshot_candidates = await ebk_snapshots_repository.list_ebk_snapshots_with_payload_for_export(
        days=days,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        platforms=platform_filter,
    )
    watch_runs: list[dict[str, Any]] = []
    ctrip_global_run_links: dict[str, str] = {}
    if platform_filter is None or "ctrip" in platform_filter:
        watch_runs = await ebk_watch_repository.list_data_center_watch_runs_for_export(
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
        # 携程 run 无 global_run_id 载体；用全局运行表关联实现成组（设计 §8.2.1）
        from app.domain.ebk_global_push.repository import (
            list_ctrip_watch_run_links_for_export,
        )

        ctrip_global_run_links = await list_ctrip_watch_run_links_for_export(
            start_at=start_at, end_at=end_at
        )
    snapshots = ebk_sources.merge_export_snapshots(
        snapshot_candidates,
        watch_runs,
        limit=limit,
        ctrip_global_run_links=ctrip_global_run_links,
    )
    _attach_account_display_names(snapshots)
    detail_rows = ebk_business_metrics.extract_business_metric_rows(snapshots)
    filename_stem = f"ebk-business-data-{_timestamp_tag()}"
    if format == "xlsx":
        core_rows = ebk_business_metrics.build_core_metric_rows(snapshots)
        brief_rows = ebk_business_metrics.build_operating_brief_rows(snapshots)
        app_conversion_rows = ebk_business_metrics.build_app_conversion_rows(snapshots)
        competition_rows = ebk_business_metrics.build_competition_rank_rows(snapshots)
        payload = build_xlsx_workbook_bytes(
            sheets=[
                {
                    "name": "经营简报",
                    "title": "EBK 经营数据简报",
                    "subtitle": "概要页昨日 APP 漏斗与竞争圈排名分区展示；空白表示本次 EBK 未提供该数据",
                    "headers": ebk_business_metrics.OPERATING_BRIEF_HEADERS,
                    "rows": brief_rows,
                    "freeze_panes": "A5",
                    "column_groups": [
                        {
                            "title": "基础信息",
                            "start": "快照时间",
                            "end": "酒店",
                            "fill": "455A64",
                        },
                        {
                            "title": "数据简报",
                            "start": "城市排名",
                            "end": "PSI",
                            "fill": "5B50D6",
                        },
                        {
                            "title": "本月经营概览",
                            "start": "本月间夜量",
                            "end": "本月销售额同比",
                            "fill": "138A72",
                        },
                        {
                            "title": "竞争圈排名",
                            "start": "离店销售额排名",
                            "end": "点评分排名",
                            "fill": "C56A12",
                        },
                        {
                            "title": "APP流量转化",
                            "start": "列表页曝光量-我的酒店",
                            "end": "成交转化率-竞争圈平均",
                            "fill": "2563EB",
                        },
                        {
                            "title": "口径",
                            "start": "口径说明",
                            "end": "口径说明",
                            "fill": "667085",
                        },
                    ],
                    "header_fills": {
                        header: (
                            "3B6FD8"
                            if header.endswith("-我的酒店")
                            else "15967D"
                            if header.endswith("-竞争圈平均")
                            else "C56A12"
                            if header.endswith("排名")
                            else "245B9E"
                        )
                        for header in ebk_business_metrics.OPERATING_BRIEF_HEADERS
                    },
                    "column_widths": {
                        "快照时间": 20,
                        "账号": 16,
                        "酒店": 24,
                        "口径说明": 28,
                    },
                },
                {
                    "name": "APP流量转化",
                    "title": "APP 流量转化",
                    "subtitle": "数量取概要页昨日漏斗；我的酒店与竞争圈平均相邻展示，不从数量反算转换率",
                    "headers": ebk_business_metrics.APP_CONVERSION_HEADERS,
                    "rows": app_conversion_rows,
                    "freeze_panes": "A5",
                    "column_groups": [
                        {
                            "title": "基础信息",
                            "start": "快照时间",
                            "end": "酒店",
                            "fill": "455A64",
                        },
                        {
                            "title": "APP流量转化",
                            "start": "列表页曝光量-我的酒店",
                            "end": "成交转化率-竞争圈平均",
                            "fill": "2563EB",
                        },
                        {
                            "title": "口径",
                            "start": "数据口径",
                            "end": "数据口径",
                            "fill": "667085",
                        },
                    ],
                    "header_fills": {
                        header: (
                            "3B6FD8"
                            if header.endswith("-我的酒店")
                            else "15967D"
                            if header.endswith("-竞争圈平均")
                            else "245B9E"
                        )
                        for header in ebk_business_metrics.APP_CONVERSION_HEADERS
                    },
                },
                {
                    "name": "竞争圈排名",
                    "title": "竞争圈排名",
                    "subtitle": "仅保留 EBK 页面明确返回的竞争圈均值、排名及排名变化",
                    "headers": ebk_business_metrics.COMPETITION_HEADERS,
                    "rows": competition_rows,
                    "freeze_panes": "A4",
                },
                {
                    "name": "核心指标对比",
                    "title": "核心指标对比",
                    "subtitle": "跨平台核心指标；平台自身口径不作隐式合并",
                    "headers": ebk_business_metrics.CORE_HEADERS,
                    "rows": core_rows,
                },
                {
                    "name": "经营明细",
                    "title": "经营明细",
                    "subtitle": "每个成功读取指标一行，含原始数据口径与比较字段",
                    "headers": ebk_business_metrics.DETAIL_HEADERS,
                    "rows": detail_rows,
                },
                {
                    "name": "指标说明",
                    "title": "指标说明",
                    "subtitle": "经营简报与 APP 流量转化的字段口径",
                    "headers": ["指标", "口径"],
                    "rows": ebk_business_metrics.METRIC_CALIBER_ROWS,
                },
            ]
        )
        return Response(
            content=payload,
            media_type=_XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.xlsx"'
            },
        )
    payload = build_csv_bytes(
        headers=ebk_business_metrics.DETAIL_HEADERS,
        rows=detail_rows,
    )
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
        },
    )


def _attach_account_display_names(snapshots: list[dict[str, Any]]) -> None:
    """Add configured operator-facing account names without exposing session data."""

    account_names = {
        (str(session.platform or ""), str(session.platform_account_id or "")): str(
            session.display_name or ""
        ).strip()
        for session in get_browser_session_store().list()
        if str(session.display_name or "").strip()
    }
    for snapshot in snapshots:
        key = (
            str(snapshot.get("platform") or ""),
            str(snapshot.get("platform_account_id") or ""),
        )
        display_name = account_names.get(key)
        if display_name:
            snapshot["account_display_name"] = display_name


def _parse_platforms_query(raw: str | None) -> list[str] | None:
    if not raw or not raw.strip():
        return None
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    supported = set(ebk_business_metrics.PLATFORM_LABELS)
    invalid = [item for item in selected if item not in supported]
    if invalid:
        raise HTTPException(status_code=422, detail=f"不支持的平台：{'、'.join(invalid)}")
    return selected or None


@router.get("/pricing-strategy-history")
async def export_pricing_strategy_history(
    days: int = Query(default=DEFAULT_RETENTION_DAYS, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=100),
    format: ExportFormat = Query(default="csv"),
) -> Response:
    """历史策略（修正4）：价格变化告警附带的规则化调价建议，逐条展开。"""
    alerts = await price_change_alerts_service.list_price_change_alerts(
        limit=limit,
        days=days,
    )
    headers = [
        "created_at",
        "watch_group_key",
        "hotel_name",
        "room_type",
        "checkin_date",
        "competitor_current_price_yuan",
        "current_own_price_yuan",
        "recommended_price_yuan",
        "recommended_adjustment_yuan",
        "trigger_reason",
        "needs_human_attention",
        "connection_mode",
        "can_auto_write_price",
    ]
    rows: list[dict[str, Any]] = []
    for alert in alerts:
        suggestions = alert.get("suggestions")
        if not isinstance(suggestions, list):
            continue
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            rows.append(
                {
                    "created_at": alert.get("created_at"),
                    "watch_group_key": alert.get("watch_group_key"),
                    "hotel_name": suggestion.get("hotel_name"),
                    "room_type": suggestion.get("room_type"),
                    "checkin_date": suggestion.get("checkin_date"),
                    "competitor_current_price_yuan": suggestion.get(
                        "competitor_current_price_yuan"
                    ),
                    "current_own_price_yuan": suggestion.get("current_own_price_yuan"),
                    "recommended_price_yuan": suggestion.get("recommended_price_yuan"),
                    "recommended_adjustment_yuan": suggestion.get(
                        "recommended_adjustment_yuan"
                    ),
                    "trigger_reason": suggestion.get("trigger_reason"),
                    "needs_human_attention": suggestion.get("needs_human_attention"),
                    "connection_mode": suggestion.get("connection_mode"),
                    "can_auto_write_price": suggestion.get("can_auto_write_price"),
                }
            )
    return _export_response(
        headers=headers,
        rows=rows,
        export_format=format,
        filename_stem=f"pricing-strategy-history-{_timestamp_tag()}",
    )

@router.get("/ebk-business-data/retention-config")
async def get_ebk_business_retention_config() -> dict[str, object]:
    """返回当前 EBK 业务数据的服务端留存天数配置。
    由服务端配置 EBK_BUSINESS_RETENTION_DAYS 决定，默认 180 天。
    """
    return {
        "retention_days": get_settings().ebk_business_retention_days,
        "source": "server_setting",
    }


class EbkCleanupRequest(BaseModel):
    retention_days: int | None = Field(default=None, ge=0, le=3650)


@router.post("/ebk-business-data/cleanup")
async def cleanup_ebk_business_data(payload: EbkCleanupRequest) -> dict[str, object]:
    """按留存天数清理 EBK 业务历史数据（快照 + 运行记录）。
    手动清理接口；retention_days 为 0 时清空全部历史。
    retention_days 缺省时回退到 EBK_BUSINESS_RETENTION_DAYS（默认 180 天）。
    """
    days = (
        get_settings().ebk_business_retention_days
        if payload.retention_days is None
        else payload.retention_days
    )
    result = await ebk_snapshots_repository.cleanup_expired_ebk_business_data(
        retention_days=days,
    )
    return {"status": "success", "retention_days": days, **result}

