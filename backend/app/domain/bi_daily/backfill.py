"""bi_daily 历史缺数浏览器回填：单日自定义区间现读 → 按日快照 → ingest 进事实表。

定位（2026-10-08 用户口径）：BI 历史表现缺数时，不要求运营做历史文件导入，
直接用已登录 EBK 浏览器把缺的日期逐日读回来。实现上复用既有 period-read
自定义区间链路（区间设为单日 D~D，读到即该日聚合值），写入
``ebk_period_read_snapshots``（period=day，source=bi_backfill），再走既有
``ingest_ebk_period_day_actuals`` 进事实表——事实表仍是唯一消费口径，
本模块不另造数据路径。

硬规则：
- 一次只跑一个回填任务（浏览器 profile 单主）；进行中再触发直接拒绝。
- 只回填昨天及更早（平台页当日数据未生成）；跨度上限 92 天。
- 已存在事实的 (店铺, 平台, 日期) 跳过，不重复拉浏览器。
- human_required 时中止该平台剩余日期（登录态坏了硬读无意义），其余平台继续；
  not_available / error 逐日如实计数，不伪造数据。
- 携程预订销售页自定义视图的指标挂在「离店」组内（间夜量/销售额/平均卖价），
  落快照前归一化成 ingest 认的 离店间夜量/离店销售额 标签；美团/飞猪区间
  视图标签与 ingest 候选一致，原样透传。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from app.domain.bi_daily.models import local_now

logger = logging.getLogger(__name__)

MAX_BACKFILL_SPAN_DAYS = 92
BACKFILL_SOURCE = "bi_backfill"
BACKFILL_PLATFORMS = ("ctrip", "meituan", "fliggy")

# 模块级运行状态：后端进程内单任务；重启丢失无妨（快照逐日落库，可重跑续传）。
_RUN_STATE: dict[str, Any] = {"status": "idle"}
_STATE_LOCK = asyncio.Lock()

FetchFn = Callable[..., Awaitable[dict[str, Any]]]


def reset_run_state_for_tests() -> None:
    _RUN_STATE.clear()
    _RUN_STATE.update({"status": "idle"})


def finish_run_state_for_tests() -> None:
    reset_run_state_for_tests()


def validate_backfill_range(
    date_from: date, date_to: date, *, now: datetime | None = None
) -> str | None:
    """校验回填区间；非法返回运营可读的错误信息，合法返回 None。"""
    current = now or local_now()
    if date_to < date_from:
        return "date_from 不能晚于 date_to。"
    if date_to >= current.date():
        return "回填区间不能包含今日及未来日期（平台页当日数据未生成）。"
    span = (date_to - date_from).days + 1
    if span > MAX_BACKFILL_SPAN_DAYS:
        return f"单次回填最多 {MAX_BACKFILL_SPAN_DAYS} 天（当前 {span} 天），请分批补抓。"
    return None


def plan_missing_dates(start: date, end: date, *, existing: set[date]) -> list[date]:
    """区间内去掉已有事实的日期，按日期升序返回。"""
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)) not in existing
    ]


def normalize_custom_range_sections(sections: Any, platform: str) -> dict[str, Any]:
    """把自定义区间视图的 sections 归一成 ingest 认的按日口径标签。

    携程预订销售页三组（预订/在店/离店）各有同名「间夜量/销售额」标签，扁平
    查找会串组：保留「离店」组（改写成 离店间夜量/离店销售额，与经营概览
    昨日块同口径）与「在店」组的 出租率/间夜量/紧张度（出租率是平台明示值，
    2026-10 起进事实表）；「预订」组丢弃。美团/飞猪区间视图标签与 ingest
    候选一致，原样透传。
    """
    if not isinstance(sections, dict):
        return {}
    if platform != "ctrip":
        return sections
    departure = sections.get("离店")
    if not isinstance(departure, dict):
        return {}
    normalized: dict[str, Any] = {}
    mapping = {"间夜量": "离店间夜量", "销售额": "离店销售额"}
    for label, value in departure.items():
        if label in mapping:
            normalized[mapping[label]] = value
        elif label in ("平均卖价", "成交率"):
            normalized[label] = value
    result: dict[str, Any] = {"离店": normalized}
    staying = sections.get("在店")
    if isinstance(staying, dict):
        kept = {
            label: value
            for label, value in staying.items()
            if label in ("出租率", "间夜量", "紧张度")
        }
        if kept:
            result["在店"] = kept
    return result


async def resolve_shop_backfill_bindings(shop_key: str) -> tuple[list[dict], list[dict]]:
    """单店铺 × 三平台 → 账号绑定；解析不到的记入 issues（不猜账号）。"""
    from app.domain.shops.resolver import resolve_platform_binding

    bindings: list[dict] = []
    issues: list[dict] = []
    for platform in BACKFILL_PLATFORMS:
        resolution = await resolve_platform_binding(shop_key, platform)
        if resolution.get("status") == "resolved":
            bindings.append(
                {
                    "shop_key": shop_key,
                    "platform": platform,
                    "platform_account_id": resolution["platform_account_id"],
                    # acquire_order_page_for_binding 需要会话 ID 拉浏览器（缺键即 KeyError）。
                    "browser_session_id": resolution.get("browser_session_id"),
                }
            )
        else:
            issues.append(
                {
                    "shop_key": shop_key,
                    "platform": platform,
                    "status": resolution.get("status"),
                    "summary": resolution.get("summary"),
                }
            )
    return bindings, issues


async def _existing_fact_dates(
    shop_key: str, platform: str, start: date, end: date
) -> set[date]:
    from sqlalchemy import select

    from app.db.session import ensure_db_ready, session_scope
    from app.domain.bi_daily.models import BookingPositionFact

    await ensure_db_ready()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(BookingPositionFact.stay_date).where(
                    BookingPositionFact.shop_key == shop_key,
                    BookingPositionFact.platform == platform,
                    BookingPositionFact.stay_date >= start,
                    BookingPositionFact.stay_date <= end,
                )
            )
        ).scalars().all()
        return set(rows)


def begin_backfill_run(shop_key: str, date_from: date, date_to: date) -> bool:
    """占用全局回填槽；已有任务在跑返回 False（浏览器 profile 单主）。"""
    if _RUN_STATE.get("status") == "running":
        return False
    _RUN_STATE.clear()
    _RUN_STATE.update(
        {
            "status": "running",
            "shop_key": shop_key,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "started_at": local_now().isoformat(timespec="seconds"),
            "updated_at": local_now().isoformat(timespec="seconds"),
            "platforms": {},
            "facts_written": 0,
            "summary": "回填已启动，正在逐日读取平台页面…",
        }
    )
    return True


def backfill_status() -> dict[str, Any]:
    # 剔除下划线私有键（如 _task 的 asyncio.Task 对象不可 JSON 序列化）。
    return {key: value for key, value in _RUN_STATE.items() if not key.startswith("_")}


def _touch_state() -> None:
    _RUN_STATE["updated_at"] = local_now().isoformat(timespec="seconds")


def _platform_state(platform: str) -> dict[str, Any]:
    state = _RUN_STATE.setdefault("platforms", {}).setdefault(
        platform,
        {"ok": 0, "failed": 0, "skipped_existing": 0, "failures": [], "current_date": None},
    )
    _touch_state()
    return state


def _finalize_state(status: str, summary: str) -> dict[str, Any]:
    _RUN_STATE["status"] = status
    _RUN_STATE["summary"] = summary
    for platform_state in _RUN_STATE.get("platforms", {}).values():
        platform_state["current_date"] = None
    _touch_state()
    return dict(_RUN_STATE)


async def execute_backfill(
    *,
    shop_key: str,
    date_from: date,
    date_to: date,
    bindings: list[dict],
    fetch_fn: FetchFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """逐平台逐日现读并落快照，最后 ingest 进事实表；返回最终运行状态。

    fetch_fn 缺省用 ebk_period_reads.service.fetch_period_read（自定义区间
    单日现读，不落库）；本函数负责把 ok 结果归一后按日落快照。
    """
    from app.domain.bi_daily import ingest
    from app.domain.ebk_period_reads import repository as period_repository
    from app.domain.ebk_period_reads.service import fetch_period_read

    fetch = fetch_fn or fetch_period_read
    current = now or local_now()

    for binding in bindings:
        platform = str(binding.get("platform") or "").strip().lower()
        account_id = str(binding.get("platform_account_id") or "").strip()
        if not platform or not account_id:
            continue
        state = _platform_state(platform)
        existing = await _existing_fact_dates(shop_key, platform, date_from, date_to)
        missing = plan_missing_dates(date_from, date_to, existing=existing)
        state["skipped_existing"] = len(existing)
        aborted_status: str | None = None
        for target in missing:
            if aborted_status is not None:
                state["failures"].append(
                    {
                        "date": target.isoformat(),
                        "status": "skipped",
                        "reason": f"平台已中止（{aborted_status}），未再读取。",
                    }
                )
                state["failed"] += 1
                continue
            state["current_date"] = target.isoformat()
            _touch_state()
            try:
                result = await fetch(
                    platform=platform,
                    data_kind="business",
                    period="day",
                    binding=dict(binding),
                    date_from=target.isoformat(),
                    date_to=target.isoformat(),
                    source=BACKFILL_SOURCE,
                )
            except Exception as exc:  # noqa: BLE001 - 单日失败不断轮
                logger.exception(
                    "bi backfill fetch crashed for %s/%s", platform, target.isoformat()
                )
                result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            status = str(result.get("status") or "error")
            if status != "ok":
                state["failures"].append(
                    {
                        "date": target.isoformat(),
                        "status": status,
                        "reason": str(
                            result.get("reason")
                            or result.get("summary")
                            or "页面未返回数据。"
                        )[:200],
                    }
                )
                state["failed"] += 1
                if status == "human_required":
                    aborted_status = status
                    state["aborted_reason_status"] = status
                continue
            normalized = normalize_custom_range_sections(result.get("sections"), platform)
            metrics = ingest.extract_day_business_metrics({"sections": normalized}, platform)
            if metrics.get("booked_room_nights") is None and metrics.get("room_revenue") is None:
                state["failures"].append(
                    {
                        "date": target.isoformat(),
                        "status": "no_metrics",
                        "reason": "页面返回成功但未解析到间夜/销售额指标。",
                    }
                )
                state["failed"] += 1
                continue
            try:
                await period_repository.append_period_read_snapshot(
                    platform=platform,
                    data_kind="business",
                    period="day",
                    status="ok",
                    caliber=str(result.get("caliber") or f"回填·自定义单日 {target.isoformat()}"),
                    summary=str(result.get("summary") or ""),
                    payload={
                        "sections": normalized,
                        "data_period_label": target.isoformat(),
                        "backfill_date_range": [target.isoformat(), target.isoformat()],
                    },
                    source=BACKFILL_SOURCE,
                    business_date=target,
                    platform_account_id=account_id,
                )
                state["ok"] += 1
            except Exception as exc:  # noqa: BLE001 - 落库失败不断轮
                logger.exception(
                    "bi backfill snapshot append failed for %s/%s", platform, target.isoformat()
                )
                state["failures"].append(
                    {
                        "date": target.isoformat(),
                        "status": "persist_error",
                        "reason": f"{type(exc).__name__}: {exc}"[:200],
                    }
                )
                state["failed"] += 1
        state["current_date"] = None
        _touch_state()

    # 快照落完统一 ingest：事实表口径与日常采集完全一致。
    facts_written = 0
    if bindings:
        try:
            from app.db.session import session_scope

            async with session_scope() as session:
                facts_written = await ingest.ingest_ebk_period_day_actuals(
                    session, shop_bindings=bindings, now=current
                )
                await ingest.refresh_weekday_mappings(
                    session, start=date_from, end=date_to
                )
        except Exception:  # noqa: BLE001 - ingest 失败不抹掉已落快照
            logger.exception("bi backfill ingest failed for %s", shop_key)
    _RUN_STATE["facts_written"] = facts_written

    platforms_state = _RUN_STATE.get("platforms", {})
    total_ok = sum(int(item.get("ok") or 0) for item in platforms_state.values())
    total_failed = sum(int(item.get("failed") or 0) for item in platforms_state.values())
    if total_failed == 0:
        status, summary = (
            "success",
            f"回填完成：补抓 {total_ok} 天，事实表新增 {facts_written} 行。",
        )
    elif total_ok == 0:
        status, summary = (
            "failed",
            f"回填未补到数据（失败 {total_failed} 天）；请查看失败明细或先完成账号登录。",
        )
    else:
        status, summary = (
            "partial",
            f"回填部分完成：成功 {total_ok} 天、失败 {total_failed} 天，事实表新增 {facts_written} 行。",
        )
    return _finalize_state(status, summary)


async def _run_backfill_task(
    *, shop_key: str, date_from: date, date_to: date
) -> None:
    try:
        bindings, issues = await resolve_shop_backfill_bindings(shop_key)
        for issue in issues:
            state = _platform_state(str(issue.get("platform") or "unknown"))
            state["failures"].append(
                {
                    "date": None,
                    "status": str(issue.get("status") or "not_ready"),
                    "reason": str(issue.get("summary") or "未绑定该平台 EBK 账号。")[:200],
                }
            )
            state["failed"] += 1
        await execute_backfill(
            shop_key=shop_key, date_from=date_from, date_to=date_to, bindings=bindings
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底，状态必须落终态
        logger.exception("bi backfill task crashed for %s", shop_key)
        _finalize_state("failed", f"回填任务异常终止：{type(exc).__name__}: {exc}"[:200])


def start_backfill(
    *, shop_key: str, date_from: date, date_to: date
) -> dict[str, Any]:
    """API 入口：校验 → 占用运行槽 → 后台任务逐日现读。返回当前状态快照。

    已有任务在跑时抛 RuntimeError（API 层转 409）；区间非法抛 ValueError（422）。
    """
    error = validate_backfill_range(date_from, date_to)
    if error:
        raise ValueError(error)
    if not begin_backfill_run(shop_key, date_from, date_to):
        raise RuntimeError("已有回填任务在进行中，请等待完成后再发起。")
    task = asyncio.create_task(
        _run_backfill_task(shop_key=shop_key, date_from=date_from, date_to=date_to)
    )
    _RUN_STATE["_task"] = task
    return backfill_status()
