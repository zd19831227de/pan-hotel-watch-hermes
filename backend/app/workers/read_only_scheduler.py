from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import re
from contextlib import suppress
from pathlib import Path

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.db.session import ensure_db_ready
from app.domain.browser.cache_cleanup import (
    cleanup_browser_state,
    cleanup_playwright_screenshots,
)
from app.domain.ebk_snapshots.repository import (
    cleanup_expired_ebk_business_data,
)
from app.domain.calendar_price_watch.scheduler import CalendarPriceWatchScheduler
from app.domain.calendar_price_watch.scheduler_status import (
    write_calendar_price_scheduler_heartbeat,
)
from app.domain.competitor_prices.scheduler import CompetitorPriceScheduler
from app.domain.competitor_prices.scheduler_status import (
    write_competitor_price_scheduler_heartbeat,
)
from app.domain.ctrip_ebooking_watch.scheduler import CtripEbookingDataCenterWatchScheduler
from app.domain.meituan_ebooking_watch.scheduler import MeituanEbookingWatchScheduler
from app.domain.fliggy_ebooking_watch.scheduler import FliggyEbookingWatchScheduler
from app.domain.order_browser import list_order_account_bindings

logger = logging.getLogger(__name__)
PUBLIC_POLL_TICK_SECONDS = 60
# V6 Step 2 — 蒸馏 loop 周期（实际执行频率由每个数据源的
# schedule_interval_minutes 控制，这里只是扫描间隔）。
DISTILL_TICK_SECONDS = 300
WORKER_PID_FILENAME = "read-only-worker.pid"
# Daily runtime-artifacts cleanup (V4-Step4): once a day around 03:00 local,
# prune (a) browser profile WEB cache and (b) old Playwright OTA screenshots.
# Retention is read from settings (.env: BROWSER_CACHE_RETENTION_DAYS /
# PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS) so it changes without code edits.
# Browser login state and Chromium models are never affected; non-PNG
# artifacts under the playwright dir are preserved.
SCHEDULED_CACHE_CLEANUP_HOUR = 3


def _worker_pid_path() -> Path:
    settings = get_settings()
    return Path(settings.browser_state_dir).parent / WORKER_PID_FILENAME


def _write_worker_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="ascii")


def _remove_worker_pid(path: Path) -> None:
    with suppress(FileNotFoundError):
        if path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()


async def _run_calendar_and_competitor_scheduler_loop() -> None:
    calendar = CalendarPriceWatchScheduler(tick_seconds=PUBLIC_POLL_TICK_SECONDS)
    competitor = CompetitorPriceScheduler(tick_seconds=PUBLIC_POLL_TICK_SECONDS)
    write_calendar_price_scheduler_heartbeat("started")
    write_competitor_price_scheduler_heartbeat("started")
    while True:
        write_calendar_price_scheduler_heartbeat("tick_started")
        write_competitor_price_scheduler_heartbeat("tick_started")
        calendar_result, competitor_result = await asyncio.gather(
            calendar.tick(),
            competitor.tick(),
            return_exceptions=True,
        )
        if isinstance(calendar_result, asyncio.CancelledError):
            raise calendar_result
        if isinstance(competitor_result, asyncio.CancelledError):
            raise competitor_result
        if isinstance(calendar_result, BaseException):
            write_calendar_price_scheduler_heartbeat(
                "tick_failed", status="error", error=calendar_result
            )
            logger.error("calendar price scheduler tick failed; continuing", exc_info=(
                type(calendar_result),
                calendar_result,
                calendar_result.__traceback__,
            ))
            calendar_result = {"due_group_count": 0, "due_matrix_brief_count": 0}
        else:
            write_calendar_price_scheduler_heartbeat("tick_finished", tick_result=calendar_result)
        if isinstance(competitor_result, BaseException):
            write_competitor_price_scheduler_heartbeat(
                "tick_failed", status="error", error=competitor_result
            )
            logger.error("competitor price scheduler tick failed; continuing", exc_info=(
                type(competitor_result),
                competitor_result,
                competitor_result.__traceback__,
            ))
            competitor_result = {"due_group_count": 0}
        else:
            write_competitor_price_scheduler_heartbeat("tick_finished", tick_result=competitor_result)
        if calendar_result["due_group_count"] or calendar_result.get("due_matrix_brief_count"):
            logger.info("calendar price scheduler tick: %s", calendar_result)
        if competitor_result["due_group_count"]:
            logger.info("competitor price scheduler tick: %s", competitor_result)

        await asyncio.sleep(PUBLIC_POLL_TICK_SECONDS)


def _shanghai_now() -> _dt.datetime:
    """全链路业务时区 Asia/Shanghai 的 naive now（与快照落库/推送调度一致）。"""
    return _dt.datetime.now(_dt.timezone.utc).astimezone(
        _dt.timezone(_dt.timedelta(hours=8))
    ).replace(tzinfo=None)


def _seconds_until_next_cache_cleanup_hour() -> float:
    now = _dt.datetime.now()
    next_run = now.replace(hour=SCHEDULED_CACHE_CLEANUP_HOUR, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += _dt.timedelta(days=1)
    return (next_run - now).total_seconds()


async def _run_runtime_cleanup_loop() -> None:
    """Once a day (~03:00 local), prune browser web-cache + old screenshots.

    Runs synchronous cleanup in worker threads so the polling loop is never
    blocked. Retention is read from settings (.env), not hardcoded:
      - BROWSER_CACHE_RETENTION_DAYS: browser profile WEB cache only
        (login state + Chromium models are never touched).
      - PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS: OTA-read PNG screenshots
        (non-PNG artifacts are preserved).
    """
    while True:
        delay = _seconds_until_next_cache_cleanup_hour()
        await asyncio.sleep(delay)
        settings = get_settings()
        bs_root = Path(settings.browser_state_dir)
        shot_root = Path(settings.playwright_artifact_dir)
        try:
            result = await asyncio.to_thread(
                cleanup_browser_state,
                bs_root,
                min_age_days=settings.browser_cache_retention_days,
            )
            logger.info(
                "scheduled browser web-cache cleanup freed %s bytes across %s profiles (retention=%sd)",
                result.get("bytes_freed"),
                result.get("profiles_cleaned"),
                settings.browser_cache_retention_days,
            )
        except BaseException as exc:  # noqa: BLE001 - scheduler must keep running
            logger.error("scheduled browser cache cleanup failed; continuing", exc_info=exc)
        try:
            shot = await asyncio.to_thread(
                cleanup_playwright_screenshots,
                shot_root,
                settings.playwright_screenshot_retention_days,
            )
            logger.info(
                "scheduled screenshot cleanup removed %s files (%s bytes, retention=%sd)",
                shot.get("files_removed"),
                shot.get("bytes_freed"),
                settings.playwright_screenshot_retention_days,
            )
        except BaseException as exc:  # noqa: BLE001 - scheduler must keep running
            logger.error("scheduled screenshot cleanup failed; continuing", exc_info=exc)
        try:
            ebk_cleanup = await cleanup_expired_ebk_business_data(
                retention_days=settings.ebk_business_retention_days,
            )
            logger.info(
                "scheduled EBK business cleanup removed %s snapshots, %s runs, %s dataset results (retention=%sd)",
                ebk_cleanup.get("deleted_snapshots"),
                ebk_cleanup.get("deleted_runs"),
                ebk_cleanup.get("deleted_dataset_results"),
                settings.ebk_business_retention_days,
            )
        except BaseException as exc:  # noqa: BLE001 - scheduler must keep running
            logger.error("scheduled EBK business cleanup failed; continuing", exc_info=exc)


def _seconds_until_next_period_day_snapshot() -> float:
    """距下一次「昨日经营/排名快照」轮次的秒数（settings 可配 HH:MM，默认 09:30）。"""
    raw = str(get_settings().period_read_day_snapshot_time or "09:30").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if match is None:
        logger.warning(
            "period_read_day_snapshot_time 配置非法（%r，需 HH:MM），回退默认 09:30", raw
        )
        hour, minute = 9, 30
    else:
        hour, minute = int(match.group(1)), int(match.group(2))
    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    now = _shanghai_now()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += _dt.timedelta(days=1)
    return (next_run - now).total_seconds()


_PERIOD_DAY_DIM_STATUS_LABELS = {
    "ok": "成功",
    "not_available": "无数据",
    "error": "失败",
    "not_ready": "未绑定账号",
    "human_required": "需人工登录",
}


async def _execute_period_read_day_snapshot_round() -> dict[str, object]:
    """执行一轮昨日快照：三平台 × 全部绑定店铺 × (经营, 排名) × day。

    读的是前一日完整口径（美团按日前日数据/携程飞猪昨日块），当日实时由
    自动推送覆盖。任何失败只计数记日志，不阻断后续店铺/平台/维度。
    2026-09-16 起按店铺遍历（此前只读第一个绑定组，新店铺没有昨日快照）；
    落库行带 platform_account_id，问答按店铺过滤互不串读。
    summary 附带逐平台 platform_results（detail 按店铺列出两维度结果）。
    """
    from app.domain.ebk_period_reads import service as period_read_service

    started_at = _shanghai_now()
    summary: dict[str, object] = {
        "ok": 0,
        "not_available": 0,
        "not_ready": 0,
        "error": 0,
        "total": 0,
        "errors": [],
        "started_at": started_at,
        "store_dimensions": {},
    }
    errors = summary["errors"]
    store_dimensions = summary["store_dimensions"]
    for platform in ("ctrip", "meituan", "fliggy"):
        try:
            bindings = await list_order_account_bindings(platform)
        except BaseException as exc:  # noqa: BLE001 - 轮次必须继续
            logger.error(
                "period day snapshot list bindings crashed for %s; continuing",
                platform,
                exc_info=exc,
            )
            bindings = []
        platform_stores: list[dict[str, object]] = store_dimensions.setdefault(
            platform, []
        )
        if not bindings:
            # 平台一个绑定店铺都没有：记两维度 not_ready，运行日志可见。
            platform_stores.append(
                {"label": "（无绑定店铺）", "business": "not_ready", "ranking": "not_ready"}
            )
            summary["not_ready"] = int(summary["not_ready"]) + 2
            summary["total"] = int(summary["total"]) + 2
            continue
        for binding in bindings:
            label = str(binding.get("label") or binding["platform_account_id"])
            entry: dict[str, object] = {"label": label}
            for data_kind in ("business", "ranking"):
                summary["total"] = int(summary["total"]) + 1
                try:
                    result = await period_read_service.fetch_period_read(
                        platform=platform,
                        data_kind=data_kind,
                        period="day",
                        source="day_end_snapshot",
                        binding=binding,
                    )
                    status = str(result.get("status") or "error")
                except BaseException as exc:  # noqa: BLE001 - 轮次必须继续
                    status = "error"
                    errors.append(f"{platform}/{label}/{data_kind}: {type(exc).__name__}")
                    logger.error(
                        "period day snapshot %s/%s/%s crashed; continuing",
                        platform,
                        label,
                        data_kind,
                        exc_info=exc,
                    )
                entry[data_kind] = status
                if status == "ok":
                    summary["ok"] = int(summary["ok"]) + 1
                elif status == "not_ready":
                    summary["not_ready"] = int(summary["not_ready"]) + 1
                elif status in ("not_available", "human_required"):
                    summary["not_available"] = int(summary["not_available"]) + 1
                else:
                    summary["error"] = int(summary["error"]) + 1
                    errors.append(f"{platform}/{label}/{data_kind}: {status}")
                    logger.warning(
                        "period day snapshot %s/%s/%s returned %s",
                        platform,
                        label,
                        data_kind,
                        status,
                    )
            platform_stores.append(entry)
    summary["platform_results"] = _period_day_platform_results(store_dimensions)
    logger.info("period day snapshot round finished: %s", summary)
    return summary


def _period_day_platform_results(
    store_dimensions: dict[str, list[dict[str, object]]],
) -> dict[str, dict[str, str]]:
    """逐平台聚合本轮各店铺两维度的读取结果（运行日志展示用）。

    detail 按店铺拼接（如「静里…：经营成功·排名成功；立瑞…：经营失败·排名成功"）；
    status 为全店铺全维度成功才 success，任一成功即 partial，否则 failed。
    """
    kind_labels = {"business": "经营", "ranking": "排名"}
    results: dict[str, dict[str, str]] = {}
    for platform in ("ctrip", "meituan", "fliggy"):
        stores = store_dimensions.get(platform) or []
        ok_count = 0
        total = 0
        store_texts: list[str] = []
        for store in stores:
            parts: list[str] = []
            for kind in ("business", "ranking"):
                status = str(store.get(kind) or "")
                total += 1
                ok_count += int(status == "ok")
                parts.append(
                    f"{kind_labels[kind]}{_PERIOD_DAY_DIM_STATUS_LABELS.get(status, '未知')}"
                )
            store_texts.append(f"{store.get('label')}：{'·'.join(parts)}")
        results[platform] = {
            "status": (
                "success" if total > 0 and ok_count == total
                else ("partial" if ok_count > 0 else "failed")
            ),
            "detail": "；".join(store_texts) if store_texts else "无绑定店铺",
        }
    return results


def _period_day_snapshot_run_record(summary: dict[str, object]) -> dict[str, object]:
    """从轮次 summary 生成运行记录字段（纯函数）。

    ok=total 成功；有 ok 部分完成；否则失败。not_ready（店铺未绑定）计入
    not_available 口径（合法状态，不告警）。
    """
    ok = int(summary.get("ok") or 0)
    total = int(summary.get("total") or 0)
    status = (
        "success" if total > 0 and ok == total else ("partial" if ok >= 1 else "failed")
    )
    errors = summary.get("errors") or []
    return {
        "status": status,
        "platform_results": summary.get("platform_results") or {},
        "ok_count": ok,
        "not_available_count": int(summary.get("not_available") or 0)
        + int(summary.get("not_ready") or 0),
        "error_count": int(summary.get("error") or 0),
        "error_summary": ("; ".join(str(e) for e in errors[:6]) or None),
        "triggered_at": summary.get("started_at"),
    }


async def _run_period_read_day_snapshot_loop(
    mutex: asyncio.Lock | None = None,
) -> None:
    """每天定时（默认 09:30）读前一日的经营+排名数据落库，供历史查询。

    与 EBK 全局推送/评价抓取共用互斥锁：三者都驱动同一批平台 EBK 页面，
    并发导航同一 Page 会互相踩页面状态。每轮结束落一条运行记录（供前端
    「最新运行日志」展示）；记录写入失败只记日志，绝不影响轮次本身。
    """
    while True:
        delay = _seconds_until_next_period_day_snapshot()
        await asyncio.sleep(delay)
        try:
            if mutex is None:
                summary = await _execute_period_read_day_snapshot_round()
            else:
                async with mutex:
                    summary = await _execute_period_read_day_snapshot_round()
            try:
                from app.domain.ebk_period_reads import (
                    repository as period_read_repository,
                )

                await period_read_repository.append_day_snapshot_run(
                    **_period_day_snapshot_run_record(summary)
                )
            except BaseException:  # noqa: BLE001 - 运行记录失败不得影响轮次
                logger.error(
                    "period day snapshot run record persist failed; continuing", exc_info=True
                )
        except BaseException as exc:  # noqa: BLE001 - loop 必须持续运行
            logger.error("period day snapshot loop round failed; continuing", exc_info=exc)


async def _run_distill_loop(tick_seconds: int = DISTILL_TICK_SECONDS) -> None:
    """V6 Step 2.6 — 蒸馏调度 loop。

    每个周期取「启用中且到期」的蒸馏数据源逐个执行；human_required /
    登录失效按「跳过本轮 + 飞书告警」容错（每平台每小时最多提醒一次），
    其余失败只记 run 记录与心跳，绝不打断 loop。
    """
    from app.domain.distill import repository as distill_repository
    from app.domain.distill.service import STATUS_HUMAN_REQUIRED, run_distill_source
    from app.domain.notifications.feishu import push_text_to_feishu
    from app.domain.ops.worker_heartbeat import write_worker_heartbeat

    last_login_alert: dict[str, float] = {}
    LOGIN_ALERT_REPEAT_SECONDS = 3600
    write_worker_heartbeat("distill_scheduler", "started")
    while True:
        tick_summary: dict[str, object] = {"due_source_count": 0, "success_count": 0, "failed_count": 0}
        try:
            due_sources = await distill_repository.list_due_distill_sources()
            tick_summary["due_source_count"] = len(due_sources)
            for source in due_sources:
                try:
                    result = await run_distill_source(source["id"])
                except Exception:  # noqa: BLE001 - 单源失败不影响其他源
                    logger.exception("distill source %s run failed", source.get("id"))
                    tick_summary["failed_count"] = int(tick_summary["failed_count"]) + 1
                    continue
                status = str(result.get("status") or "")
                if status == STATUS_HUMAN_REQUIRED:
                    tick_summary["failed_count"] = int(tick_summary["failed_count"]) + 1
                    platform = str(source.get("platform") or "llm_chat")
                    now = asyncio.get_running_loop().time()
                    if now - last_login_alert.get(platform, -LOGIN_ALERT_REPEAT_SECONDS) >= LOGIN_ALERT_REPEAT_SECONDS:
                        last_login_alert[platform] = now
                        push = await push_text_to_feishu(
                            "蒸馏任务暂停",
                            f"蒸馏数据源 {source.get('title') or source.get('id')} 需要 {platform} 人工登录，"
                            "本轮已跳过；完成登录后将自动恢复。",
                        )
                        logger.info("distill login alert pushed: %s", getattr(push, "status", "unknown"))
                elif status == "success":
                    tick_summary["success_count"] = int(tick_summary["success_count"]) + 1
                else:
                    tick_summary["failed_count"] = int(tick_summary["failed_count"]) + 1
            write_worker_heartbeat("distill_scheduler", "tick_finished", tick_summary=tick_summary)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - loop 必须持续运行
            write_worker_heartbeat("distill_scheduler", "tick_failed", status="error", error=exc)
            logger.error("distill scheduler tick failed; continuing", exc_info=exc)
        await asyncio.sleep(tick_seconds)


async def run_forever() -> None:
    await ensure_db_ready()
    # 评价抓取与 EBK 推送互斥（2026-09-02：优先 EBK 推送）——共享锁：
    # 推送 tick 阻塞等锁必跑；评价 tick 见锁被占跳过本轮让位。日终快照轮次
    # 也驱动同一批 EBK 页面，一并共用。
    review_push_mutex = asyncio.Lock()
    loops = [
        CtripEbookingDataCenterWatchScheduler(tick_seconds=60).run_forever(),
        MeituanEbookingWatchScheduler(tick_seconds=60).run_forever(),
        FliggyEbookingWatchScheduler(tick_seconds=60).run_forever(),
        _run_calendar_and_competitor_scheduler_loop(),
        _run_runtime_cleanup_loop(),
        _run_distill_loop(),
    ]
    if get_settings().period_read_day_snapshot_enabled:
        loops.append(_run_period_read_day_snapshot_loop(mutex=review_push_mutex))
    if get_settings().ebk_global_push_scheduler_enabled:
        from app.domain.ebk_global_push.scheduler import EbkGlobalPushScheduler

        loops.append(
            EbkGlobalPushScheduler(tick_seconds=60, mutex=review_push_mutex).run_forever()
        )
    if getattr(get_settings(), "review_scheduler_enabled", False):
        from app.domain.review_management.scheduler import ReviewFetchScheduler

        loops.append(
            ReviewFetchScheduler(tick_seconds=60, mutex=review_push_mutex).run_forever()
        )
    if getattr(get_settings(), "bi_daily_collection_enabled", True):
        from app.domain.bi_daily.scheduler import run_bi_daily_loop

        loops.append(run_bi_daily_loop(tick_seconds=60))
    await asyncio.gather(*loops)


def main() -> None:
    configure_windows_subprocess_event_loop_policy()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    pid_path = _worker_pid_path()
    _write_worker_pid(pid_path)
    try:
        print(f"{settings.app_name} monitoring scheduler worker")
        print("This worker runs EBK data-center, competitor-price, and calendar-price polling.")
        print("Calendar and competitor scheduled polls run in parallel when using different browser profiles.")
        print("Bind calendar-price and competitor-price to separate browser accounts to avoid profile contention.")
        print("Feishu-approved pricing execution is handled by the Feishu bot callback flow.")
        asyncio.run(run_forever())
    finally:
        _remove_worker_pid(pid_path)


if __name__ == "__main__":
    main()
