from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security import require_operator_api_key
from app.domain.ebk_period_reads import service
from app.domain.ebk_period_reads.service import PERIOD_READ_PLATFORMS
from app.domain.order_browser import resolve_order_account_binding

router = APIRouter(prefix="/ebk-period-reads", tags=["ebk-period-reads"])

_PLATFORM_QUERY_OR_NONE = Query(default=None, pattern="^(ctrip|meituan|fliggy)$")
_KIND_QUERY = Query(pattern="^(business|ranking|pyramid|flow)$")
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _ensure_valid_calendar_dates(*values: str) -> None:
    """正则只校验形状，这里拦下 2026-02-31 这类非法日历日期（否则落库层 500）。"""
    for value in values:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"日期非法：{value}（需为真实存在的 YYYY-MM-DD 日期）。",
            ) from None


class PeriodReadFetchRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    data_kind: str = Field(pattern="^(business|ranking|pyramid|flow)$")
    period: str = Field(pattern="^(day|week|month)$")
    chat_id: str | None = None
    # 自定义历史区间（2026-09-16）：现读平台页自定义时间控件，不落库。
    date_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("")
async def get_period_read_route(
    data_kind: str = _KIND_QUERY,
    period: str = Query(default="week", pattern="^(day|week|month)$"),
    platform: str | None = _PLATFORM_QUERY_OR_NONE,
    date_from: str | None = Query(default=None, pattern=_DATE_PATTERN),
    date_to: str | None = Query(default=None, pattern=_DATE_PATTERN),
    chat_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """读当日快照（period=day/week/month）；无当日数据 404（问答端触发重读）。

    chat_id 给定时按「平台组直绑 → 携程锚点链」解析店铺账号，只读该店铺的
    快照（多店铺互不串读）；群未绑定该平台账号时 404 并提示绑定。

    历史范围模式：date_from/date_to 按业务日期返回已落库快照列表（忽略
    period 过滤，只读持久化数据，不触发浏览器重读）。day 快照业务日期 =
    抓取日前一天；week/month 按抓取时间过滤，口径见各快照 caliber。
    """
    scoped_accounts: dict[str, str] = {}
    if chat_id:
        for p in PERIOD_READ_PLATFORMS:
            if platform and p != platform:
                continue
            binding = await resolve_order_account_binding(p, chat_id=chat_id)
            if binding is not None:
                scoped_accounts[p] = binding["platform_account_id"]
        if platform and platform not in scoped_accounts:
            raise HTTPException(
                status_code=404,
                detail=(
                    "当前飞书群未绑定该平台 EBK 账号，无法读取该店铺数据；"
                    "请先在「账号与浏览器」把监控组挂载到本群绑定的携程 EBK 组。"
                ),
            )
    if date_from or date_to:
        start = date_from or date_to
        end = date_to or date_from
        _ensure_valid_calendar_dates(start, end)
        if start > end:
            raise HTTPException(status_code=422, detail="date_from 不能晚于 date_to。")
        if chat_id and not scoped_accounts:
            # 群整条锚点链都解析不到账号：按未绑定处理，不能回落成全部店铺
            # 的历史数据（否则未绑定群跨店串读）。
            raise HTTPException(
                status_code=404,
                detail=(
                    "当前飞书群未绑定任何平台 EBK 账号，无法读取店铺数据；"
                    "请先在「账号与浏览器」把监控组挂载到本群绑定的携程 EBK 组。"
                ),
            )
        from app.domain.ebk_period_reads import repository

        snapshots = await repository.list_period_reads_by_captured_range(
            platform=platform,
            data_kind=data_kind,
            date_from=start,
            date_to=end,
            platform_accounts=scoped_accounts or None,
        )
        return {
            "date_range": {"from": start, "to": end},
            "snapshots": snapshots,
        }
    if platform:
        snapshot = await service.get_period_read(
            platform=platform,
            data_kind=data_kind,
            period=period,
            platform_account_id=scoped_accounts.get(platform),
        )
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="该维度还没有当天的读取记录（过期或未读取），请触发重读。",
            )
        return {"snapshot": snapshot}
    snapshots = []
    for p in PERIOD_READ_PLATFORMS:
        if chat_id and p not in scoped_accounts:
            continue
        snapshot = await service.get_period_read(
            platform=p,
            data_kind=data_kind,
            period=period,
            platform_account_id=scoped_accounts.get(p),
        )
        if snapshot is not None:
            snapshots.append(snapshot)
    return {"snapshots": snapshots}


@router.get("/day-snapshot/runs")
async def get_day_snapshot_runs_route(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """昨日快照日终轮次运行记录（前端「最新运行日志」展示用，只读）。"""
    from app.domain.ebk_period_reads import repository

    return {"runs": await repository.list_day_snapshot_runs(limit=limit)}


@router.post("/fetch", dependencies=[Depends(require_operator_api_key)])
async def fetch_period_read_route(request: PeriodReadFetchRequest) -> dict[str, Any]:
    if request.date_from or request.date_to:
        _ensure_valid_calendar_dates(
            str(request.date_from or request.date_to or ""),
            str(request.date_to or request.date_from or ""),
        )
    result = await service.fetch_period_read(
        platform=request.platform,
        data_kind=request.data_kind,
        period=request.period,
        chat_id=request.chat_id,
        source="manual",
        date_from=request.date_from,
        date_to=request.date_to,
    )
    return {"result": result}
