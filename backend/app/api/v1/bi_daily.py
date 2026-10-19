"""经营 BI 日报 API：只读报表/覆盖率/映射/导出 + operator 导入与手动采集。

安全口径：本路由不提供任何 dry-run / execute / OTA 写入口；导入只写
预算/预测事实表。GET 只读不鉴权，POST 按仓库惯例挂 operator key。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.security import require_operator_api_key
from app.db.session import session_scope
from app.domain.bi_daily import ingest, repository, service
from app.domain.bi_daily.models import local_now
from app.domain.exports.csv_builder import build_csv_bytes
from app.domain.exports.xlsx_builder import build_xlsx_bytes

router = APIRouter(prefix="/bi-daily", tags=["bi-daily"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_IMPORT_BYTES = 5 * 1024 * 1024
_MAX_MAPPING_SPAN_DAYS = 400

_WEEKDAY_LABELS = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


def _parse_optional_date(value: str | None, field: str) -> date | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} 必须为 YYYY-MM-DD 日期。") from exc


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail="as_of 必须为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。")


async def _require_shop(shop_key: str):
    from app.domain.shops import repository as shops_repository

    shop = await shops_repository.get_shop_by_key(shop_key)
    if shop is None:
        raise HTTPException(status_code=404, detail="店铺不存在，无法生成经营 BI 数据。")
    return shop


@router.get("/daily-report")
async def get_daily_report_route(
    shop_key: str = Query(pattern=r"^shop_[A-Za-z0-9_-]+$"),
    as_of: str | None = Query(default=None),
    horizon_days: int = Query(default=14, ge=1, le=60),
    lookback_days: int = Query(default=30, ge=1, le=120),
) -> dict[str, Any]:
    await _require_shop(shop_key)
    async with session_scope() as session:
        return await service.build_daily_report(
            session=session,
            shop_key=shop_key,
            as_of=_parse_as_of(as_of),
            horizon_days=horizon_days,
            lookback_days=lookback_days,
        )


@router.get("/coverage")
async def get_coverage_route(
    shop_key: str | None = Query(default=None, pattern=r"^shop_[A-Za-z0-9_-]+$"),
) -> dict[str, Any]:
    async with session_scope() as session:
        run = await repository.get_latest_collection_run(session)
        if run is None:
            return {
                "status": "never_collected",
                "summary": "BI 采集任务尚未运行；可在经营 BI 页手动触发一次采集。",
            }
        coverage = run.coverage_json or {}
        return {
            "status": "ok",
            "last_run": {
                "run_date": run.run_date.isoformat(),
                "source": run.source,
                "status": run.status,
                "fact_rows_written": run.fact_rows_written,
            },
            "coverage": coverage.get(shop_key) if shop_key else coverage,
        }


@router.post("/collect", dependencies=[Depends(require_operator_api_key)])
async def trigger_collect_route() -> dict[str, Any]:
    async with session_scope() as session:
        return await service.run_bi_daily_collection_round(session=session, source="manual")


class BackfillActualsRequest(BaseModel):
    """历史缺数浏览器回填：逐日现读平台页面 → 按日快照 → 事实表。"""

    shop_key: str = Field(pattern=r"^shop_[A-Za-z0-9_-]+$")
    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/backfill-actuals", dependencies=[Depends(require_operator_api_key)])
async def start_backfill_route(request: BackfillActualsRequest) -> dict[str, Any]:
    """启动历史缺数回填（后台逐日现读，浏览器单主一次一个任务）。"""
    from app.domain.bi_daily import backfill

    await _require_shop(request.shop_key)
    start = _parse_optional_date(request.date_from, "date_from")
    end = _parse_optional_date(request.date_to, "date_to")
    assert start is not None and end is not None  # pattern 已保证可解析
    try:
        return backfill.start_backfill(shop_key=request.shop_key, date_from=start, date_to=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/backfill-actuals/status")
async def backfill_status_route() -> dict[str, Any]:
    """当前/最近一次回填任务进度（进程内状态，重启后回到 idle）。"""
    from app.domain.bi_daily import backfill

    return backfill.backfill_status()


@router.get("/weekday-mapping")
async def get_weekday_mapping_route(
    date_from: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    start = _parse_optional_date(date_from, "date_from")
    end = _parse_optional_date(date_to, "date_to")
    assert start is not None and end is not None  # pattern 已保证可解析
    if end < start:
        raise HTTPException(status_code=422, detail="date_from 不能晚于 date_to。")
    if (end - start).days > _MAX_MAPPING_SPAN_DAYS:
        raise HTTPException(status_code=422, detail="映射查询区间不能超过 400 天。")
    async with session_scope() as session:
        try:
            await ingest.refresh_weekday_mappings(session, start=start, end=end)
        except IntegrityError:
            # GET 自愈式生成映射的并发撞唯一约束：回滚写入，读取既有行即可。
            await session.rollback()
        mappings = await repository.weekday_mappings_for_stay_dates(
            session, stay_dates=[start + timedelta(days=i) for i in range((end - start).days + 1)]
        )
        rows = [
            {
                "current_stay_date": mapping.current_stay_date.isoformat(),
                "current_weekday": mapping.current_weekday,
                "prior_year_same_weekday_stay_date": (
                    mapping.prior_year_same_weekday_stay_date.isoformat()
                    if mapping.prior_year_same_weekday_stay_date
                    else None
                ),
                "matching_rule": mapping.matching_rule,
                "holiday_flag": mapping.holiday_flag,
                "fallback_reason": mapping.fallback_reason,
            }
            for _, mapping in sorted(mappings.items())
        ]
        return {"rows": rows}


@router.get("/budget-forecast/versions")
async def get_budget_forecast_versions_route(
    shop_key: str = Query(pattern=r"^shop_[A-Za-z0-9_-]+$"),
) -> dict[str, Any]:
    await _require_shop(shop_key)
    async with session_scope() as session:
        versions = await service.list_budget_forecast_versions(session, shop_key)
        return {"versions": versions}


@router.post("/budget-forecast/import", dependencies=[Depends(require_operator_api_key)])
async def import_budget_forecast_route(
    shop_key: str = Form(pattern=r"^shop_[A-Za-z0-9_-]+$"),
    forecast_version: str = Form(min_length=1, max_length=64),
    valid_from: str | None = Form(default=None),
    valid_to: str | None = Form(default=None),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    await _require_shop(shop_key)
    payload = await file.read()
    if len(payload) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=422, detail="文件过大（上限 5MB）。")
    filename = file.filename or "budget.csv"
    start = _parse_optional_date(valid_from, "valid_from")
    end = _parse_optional_date(valid_to, "valid_to")
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="valid_from 不能晚于 valid_to。")
    async with session_scope() as session:
        try:
            result = await service.import_budget_forecast(
                session,
                shop_key=shop_key,
                payload=payload,
                filename=filename,
                forecast_version=forecast_version,
                valid_from=start,
                valid_to=end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result


def _fmt_rate(completion: dict[str, Any]) -> str:
    value = completion.get("value")
    if value is None:
        return ""
    return f"{float(value):.0%}"


def _fmt_ratio(value: Any, *, digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _fmt_pct_value(value: Any, *, digits: int = 1) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _flatten_report_rows(report: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "板块",
        "入住日期",
        "星期",
        "本店间夜",
        "本店间夜状态",
        "本店在手/总房量",
        "本店出租率",
        "预埋价(无早最低)",
        "竞争圈判定",
        "竞争圈出租率",
        "市场热度(城市)",
        "市场热度(去年同期)",
        "热点事件",
        "竞品最低价",
        "竞品最高价",
        "去年同期同星期",
        "同比完成率",
        "销售额同比",
        "出租率同比(pp)",
        "均价同比",
        "预算完成率",
        "预测完成率",
    ]
    rows: list[list[Any]] = []
    for row in report.get("forward", []):
        competitor = row.get("competitor") or {}
        prices = competitor.get("prices") or {}
        own_full = row.get("own") or {}
        own = own_full.get("booked_room_nights") or {}
        inventory = own_full.get("available_inventory") or {}
        occupancy = own_full.get("occupancy_rate") or {}
        pre_set = own_full.get("pre_set_price") or {}
        circle = row.get("circle") or {}
        market = row.get("market") or {}
        prior_ref = row.get("prior_year") or {}
        prior_actual = prior_ref.get("actual") or {}
        prior_label = prior_ref.get("mapped_date")
        if prior_label and prior_actual:
            if (
                prior_actual.get("occupancy_rate") is not None
                or prior_actual.get("adr") is not None
            ):
                prior_label = (
                    f"{prior_label}（出租率{_fmt_pct_value(prior_actual.get('occupancy_rate'), digits=0)}"
                    f"·均价¥{prior_actual.get('adr')}）"
                )
            else:
                prior_label = f"{prior_label}（间夜{prior_actual.get('booked_room_nights')}）"
        booked = own.get("value")
        rows.append(
            [
                "未来14天",
                row["stay_date"],
                _WEEKDAY_LABELS.get(row["weekday"], str(row["weekday"])),
                booked,
                own.get("status"),
                (
                    f"{booked}/{inventory.get('value')}"
                    if booked is not None and inventory.get("value") is not None
                    else None
                ),
                _fmt_pct_value(occupancy.get("value")),
                pre_set.get("value"),
                circle.get("verdict_label") or circle.get("status"),
                _fmt_pct_value(circle.get("competitor_occupancy_rate")),
                (market.get("city_heat") or {}).get("value"),
                (market.get("travel_heat_last_year") or {}).get("value"),
                "、".join(market.get("events") or []) or None,
                prices.get("min"),
                prices.get("max"),
                prior_label,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ]
        )
    for day in report.get("performance", []):
        prior = day.get("prior_year") or {}
        budget = day.get("budget") or {}
        forecast = day.get("forecast") or {}
        totals = day.get("totals") or {}
        nights = totals.get("booked_room_nights")
        rows.append(
            [
                "历史表现",
                day["stay_date"],
                _WEEKDAY_LABELS.get(day["weekday"], str(day["weekday"])),
                nights,
                "available" if nights is not None else "unavailable",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                prior.get("mapped_date"),
                _fmt_rate(prior.get("completion") or {}),
                _fmt_rate(prior.get("revenue_completion") or {}),
                prior.get("occupancy_delta_pp"),
                _fmt_rate(prior.get("adr_ratio") or {}),
                _fmt_rate(budget.get("completion") or {}),
                _fmt_rate(forecast.get("completion") or {}),
            ]
        )
    for entry in report.get("weekday_profile", []):
        current = entry.get("current") or {}
        prior = entry.get("prior_year") or {}
        rows.append(
            [
                "星期视图",
                f"{current.get('days') or 0}天",
                entry.get("weekday_label"),
                current.get("nights_sum"),
                "available",
                None,
                _fmt_pct_value(current.get("occupancy_avg")),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                f"去年同星期{prior.get('days') or 0}天",
                _fmt_rate(entry.get("completion") or {}),
                _fmt_rate(entry.get("revenue_completion") or {}),
                entry.get("occupancy_delta_pp"),
                _fmt_rate(entry.get("adr_ratio") or {}),
                None,
                None,
            ]
        )
    return headers, rows


@router.get("/export/daily-report")
async def export_daily_report_route(
    shop_key: str = Query(pattern=r"^shop_[A-Za-z0-9_-]+$"),
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    as_of: str | None = Query(default=None),
) -> Response:
    await _require_shop(shop_key)
    async with session_scope() as session:
        report = await service.build_daily_report(
            session=session, shop_key=shop_key, as_of=_parse_as_of(as_of)
        )
    headers, rows = _flatten_report_rows(report)
    stamp = local_now().strftime("%Y%m%d-%H%M%S")
    if format == "xlsx":
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
        headers={"Content-Disposition": f'attachment; filename="bi-daily-report-{stamp}.{extension}"'},
    )
