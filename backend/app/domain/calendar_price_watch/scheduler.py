from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.browser.ctrip_ebooking import (
    CtripBrowserProfileLockedError,
    get_ctrip_ebooking_service,
)
from app.domain.browser.ctrip_public_hotel_dates import _public_hotel_url_for_check_in
from app.domain.calendar_price_watch.browser_binding import (
    resolve_calendar_browser_binding_for_config,
)
from app.domain.calendar_price_watch.cards import (
    build_calendar_price_alert_fallback_text,
    build_calendar_price_alert_feishu_card,
    build_calendar_price_matrix_fallback_text,
    build_calendar_price_matrix_feishu_cards,
    calendar_price_alert_title,
    calendar_price_matrix_title,
)
from app.domain.calendar_price_watch.evaluator import evaluate_calendar_price_alerts
from app.domain.calendar_price_watch.repository import (
    get_calendar_price_watch_config,
    get_latest_calendar_price_matrix,
    list_due_calendar_price_watch_configs,
    list_due_matrix_brief_configs,
    mark_matrix_brief_slot_pushed,
    save_calendar_price_run,
    update_calendar_price_run_push_result,
)
from app.domain.calendar_price_watch.scheduler_status import (
    write_calendar_price_scheduler_heartbeat,
)
from app.domain.competitor_prices.browser_pool import (
    record_competitor_browser_pool_run_result,
    release_competitor_browser_pool_lease,
)
from app.domain.competitor_prices.public_browser_poll_gate import calendar_profile_poll
from app.domain.competitor_prices.repository import (
    get_competitor_watch_group_detail,
    list_competitor_hotels,
)
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook

logger = logging.getLogger(__name__)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class CalendarPriceWatchScheduler:
    """Poll configured competitor watch groups for read-only calendar prices."""

    # 落库前失败（浏览器账号不存在/组被删/绑定缺失等）不会保存 run，
    # last_polled_at 不推进 → 配置每分钟都 due 重试刷日志（2026-10-08 巡检
    # F4：browser-trip-1 死配置每天 1440 条失败日志）。失败后 60 分钟内
    # 本进程不再重试该组；worker 重启即清零，不改任何持久语义。
    POLL_FAILURE_BACKOFF_MINUTES = 60

    def __init__(self, *, tick_seconds: int = 60) -> None:
        self.tick_seconds = tick_seconds
        self._poll_failure_backoff_until: dict[str, datetime] = {}

    async def run_forever(self) -> None:
        _record_scheduler_heartbeat("started")
        while True:
            _record_scheduler_heartbeat("tick_started")
            try:
                result = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _record_scheduler_heartbeat("tick_failed", status="error", error=exc)
                logger.exception("calendar price scheduler tick failed; continuing")
                await asyncio.sleep(self.tick_seconds)
                continue
            _record_scheduler_heartbeat("tick_finished", tick_result=result)
            if result["due_group_count"]:
                logger.info("calendar price scheduler tick: %s", result)
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> dict[str, Any]:
        poll_result = await self._tick_polls()
        brief_result = await self._tick_matrix_briefs()
        return {
            **poll_result,
            "matrix_brief_results": brief_result["results"],
            "due_matrix_brief_count": brief_result["due_group_count"],
        }

    async def _tick_polls(self) -> dict[str, Any]:
        configs = await list_due_calendar_price_watch_configs()
        results = []
        now = datetime.now(UTC)
        for config in configs:
            watch_group_key = str(config["watch_group_key"])
            backoff_until = self._poll_failure_backoff_until.get(watch_group_key)
            if backoff_until is not None and now < backoff_until:
                continue
            try:
                result = await self.run_watch_group_now(
                    watch_group_key,
                    push_to_feishu=True,
                    push_kind="alert",
                    actor_id="scheduler",
                    source="scheduler",
                )
            except CtripBrowserProfileLockedError as exc:
                logger.warning("calendar price scheduled browser profile is locked: %s", exc)
                result = {
                    "watch_group_key": watch_group_key,
                    "status": "human_required",
                    "summary": str(exc),
                    "error_type": type(exc).__name__,
                }
            except Exception as exc:
                logger.exception("calendar price scheduled poll failed")
                result = {
                    "watch_group_key": watch_group_key,
                    "status": "failed",
                    "summary": str(exc)[:300],
                    "error_type": type(exc).__name__,
                }
            results.append(result)
            # 有 run_id ＝ 本轮已落库、last_polled_at 已推进，正常按间隔调度；
            # 无 run_id 的失败进退避，避免永远 due 的死配置每分钟撞墙。
            if result.get("run_id") is not None:
                self._poll_failure_backoff_until.pop(watch_group_key, None)
            else:
                self._poll_failure_backoff_until[watch_group_key] = now + timedelta(
                    minutes=self.POLL_FAILURE_BACKOFF_MINUTES
                )
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "due_group_count": len(configs),
            "results": results,
        }

    async def _tick_matrix_briefs(self) -> dict[str, Any]:
        configs = await list_due_matrix_brief_configs()
        results = []
        for config in configs:
            watch_group_key = str(config["watch_group_key"])
            slot_time = str(config.get("due_slot_time") or "")
            slot_label = f"{slot_time} 简报" if slot_time else "矩阵简报"
            try:
                result = await self.run_matrix_brief_now(
                    watch_group_key,
                    actor_id="matrix_brief_scheduler",
                    source="matrix_brief",
                    slot_time=slot_time,
                    slot_label=slot_label,
                )
                results.append(result)
            except CtripBrowserProfileLockedError as exc:
                logger.warning("calendar matrix brief browser profile is locked: %s", exc)
                results.append(
                    {
                        "watch_group_key": watch_group_key,
                        "status": "human_required",
                        "summary": str(exc),
                        "push_kind": "matrix_brief",
                        "error_type": type(exc).__name__,
                    }
                )
            except Exception as exc:
                logger.exception("calendar matrix brief scheduled push failed")
                results.append(
                    {
                        "watch_group_key": watch_group_key,
                        "status": "failed",
                        "summary": str(exc)[:300],
                        "push_kind": "matrix_brief",
                        "error_type": type(exc).__name__,
                    }
                )
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "due_group_count": len(configs),
            "results": results,
        }

    async def run_matrix_brief_now(
        self,
        watch_group_key: str,
        *,
        actor_id: str = "operator",
        source: str = "api",
        slot_time: str | None = None,
        slot_label: str = "矩阵简报",
    ) -> dict[str, Any]:
        result = await self.run_watch_group_now(
            watch_group_key,
            push_to_feishu=True,
            push_kind="matrix_brief",
            actor_id=actor_id,
            source=source,
            matrix_brief_slot_label=slot_label,
        )
        result["push_kind"] = "matrix_brief"
        if slot_time and result.get("status") not in {"failed", "not_found", "skipped"}:
            await mark_matrix_brief_slot_pushed(
                watch_group_key=watch_group_key,
                slot_time=slot_time,
            )
            result["matrix_brief_slot"] = slot_time
        return result

    async def run_watch_group_now(
        self,
        watch_group_key: str,
        *,
        push_to_feishu: bool = True,
        push_kind: str = "alert",
        actor_id: str = "operator",
        source: str = "api",
        matrix_brief_slot_label: str = "矩阵简报",
    ) -> dict[str, Any]:
        group = await get_competitor_watch_group_detail(
            watch_group_key=watch_group_key,
            include_secrets=True,
            create_if_missing=False,
        )
        if group is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到竞品监控组。",
            }
        if not group.get("enabled", True):
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": "竞品监控组已停用。",
            }
        try:
            return await self._poll_group(
                group,
                push_to_feishu=push_to_feishu,
                push_kind=push_kind,
                actor_id=actor_id,
                source=source,
                matrix_brief_slot_label=matrix_brief_slot_label,
            )
        except CtripBrowserProfileLockedError as exc:
            logger.warning("calendar price browser profile is locked: %s", exc)
            return {
                "watch_group_key": watch_group_key,
                "status": "human_required",
                "summary": str(exc),
                "error_type": type(exc).__name__,
            }
        except Exception as exc:
            logger.exception("calendar price manual poll failed")
            return {
                "watch_group_key": watch_group_key,
                "status": "failed",
                "summary": f"日历价读取失败：{type(exc).__name__}: {str(exc)[:240]}",
                "error_type": type(exc).__name__,
            }

    async def _poll_group(
        self,
        group: dict[str, Any],
        *,
        push_to_feishu: bool,
        push_kind: str = "alert",
        actor_id: str,
        source: str,
        matrix_brief_slot_label: str = "矩阵简报",
    ) -> dict[str, Any]:
        watch_group_key = str(group["group_key"])
        config = await get_calendar_price_watch_config(watch_group_key=watch_group_key)
        if config is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到竞品监控组。",
            }

        platform_account_id = str(config.get("platform_account_id") or "")
        browser_binding = await resolve_calendar_browser_binding_for_config(
            config=config,
            watch_group_key=watch_group_key,
        )
        if not browser_binding.ok or browser_binding.selection is None:
            return {
                "watch_group_key": watch_group_key,
                "status": browser_binding.status,
                "summary": browser_binding.summary,
                "browser_session_id": config.get("browser_session_id"),
                "platform_account_id": platform_account_id,
                "evidence": browser_binding.evidence,
                "next_action": browser_binding.next_action,
            }

        selection = browser_binding.selection
        try:
            async with calendar_profile_poll(
                selection.platform_account_id,
                selection.browser_session_id,
                enabled=source in {"scheduler", "matrix_brief"},
            ):
                target_hotels = await list_competitor_hotels(
                    watch_group_key=watch_group_key,
                    include_disabled=False,
                )
                if not any(item["role"] == "own" for item in target_hotels):
                    return {
                        "watch_group_key": watch_group_key,
                        "status": "config_required",
                        "summary": "日历价监测需要至少 1 个启用本店酒店。",
                    }

                start_date = datetime.now(LOCAL_TIMEZONE).date()
                future_days = int(config["future_days"])
                collected_points: list[dict[str, Any]] = []
                hotel_reads: list[dict[str, Any]] = []
                overall_status = "success"
                summaries: list[str] = []
                trusted_point_count = 0

                for index, hotel in enumerate(target_hotels):
                    if index > 0:
                        await _wait_between_calendar_hotel_reads()
                    target = {
                        "role": "own" if hotel["role"] == "own" else "competitor",
                        "label": hotel["alias"],
                        "url": _public_hotel_url_for_check_in(
                            hotel["detail_url"],
                            check_in_date=start_date,
                        ),
                    }
                    read_result = await get_ctrip_ebooking_service().read_public_calendar_prices(
                        platform_account_id=selection.platform_account_id,
                        browser_session_id=selection.browser_session_id,
                        target=target,
                        start_date=start_date,
                        days=future_days,
                    )
                    read_status = str(read_result.get("status") or "needs_review")
                    if read_status in {"human_required", "needs_review", "failed"}:
                        overall_status = read_status if overall_status == "success" else overall_status
                    if read_status == "human_required":
                        overall_status = "human_required"
                    summaries.append(f"{hotel['alias']}: {read_result.get('summary') or read_status}")
                    hotel_reads.append(
                        {
                            "hotel_alias": hotel["alias"],
                            "role": hotel["role"],
                            "status": read_status,
                            "review_reason": read_result.get("review_reason"),
                        }
                    )
                    for point in read_result.get("points") or []:
                        if not isinstance(point, dict):
                            continue
                        trusted_point_count += 1 if point.get("is_trusted") else 0
                        collected_points.append(
                            {
                                "hotel_config_id": hotel["id"],
                                "role": hotel["role"],
                                "hotel_alias": hotel["alias"],
                                "checkin_date": point.get("checkin_date"),
                                "price_amount_yuan": point.get("price_amount_yuan"),
                                "currency": point.get("currency") or "CNY",
                                "source": point.get("source") or "calendar_dom_cell_price",
                                "is_trusted": bool(point.get("is_trusted")),
                                "status": read_status if point.get("is_trusted") else read_status,
                                "review_reason": read_result.get("review_reason"),
                                "source_text": point.get("source_text"),
                            }
                        )

                alerts = evaluate_calendar_price_alerts(
                    points=collected_points,
                    high_gap_threshold_yuan=int(config["high_gap_threshold_yuan"]),
                    low_gap_threshold_yuan=int(config["low_gap_threshold_yuan"]),
                )
                if alerts:
                    run_status = "alert"
                elif trusted_point_count:
                    run_status = overall_status if overall_status != "success" else "success"
                else:
                    run_status = overall_status if overall_status != "success" else "needs_review"
                await record_competitor_browser_pool_run_result(
                    selection=selection,
                    status=run_status,
                    trusted_rate_count=trusted_point_count,
                )

                summary = (
                    f"未来{future_days}天有{len(alerts)}个日期触发日历价预警。"
                    if alerts
                    else "；".join(summaries[:3]) or "未读取到可信日历价。"
                )
                persistence = await save_calendar_price_run(
                    watch_group_key=watch_group_key,
                    config=config,
                    result={
                        "status": run_status,
                        "summary": summary,
                        "platform_account_id": selection.platform_account_id,
                        "browser_session_id": selection.browser_session_id,
                        "points": collected_points,
                        "alerts": alerts,
                        "start_date": start_date.isoformat(),
                        "evidence": {
                            "start_date": start_date.isoformat(),
                            "future_days": future_days,
                            "hotel_reads": hotel_reads,
                            "browser_selection": selection.evidence(),
                        },
                    },
                    actor_id=actor_id,
                    source=source,
                )
                push_payload = await _resolve_calendar_push_payload(
                    group=group,
                    config=config,
                    alerts=alerts,
                    future_days=future_days,
                    summary=summary,
                    push_to_feishu=push_to_feishu,
                    push_kind=push_kind,
                    watch_group_key=watch_group_key,
                    matrix_brief_slot_label=matrix_brief_slot_label,
                )
                run_id = persistence.get("run_id")
                if run_id is not None:
                    await update_calendar_price_run_push_result(
                        run_id=int(run_id),
                        push_result=push_payload,
                    )
                return {
                    "watch_group_key": watch_group_key,
                    "status": run_status,
                    "summary": summary,
                    "future_days": future_days,
                    "alert_count": len(alerts),
                    "alerts": alerts,
                    "run_id": run_id,
                    "feishu_push": push_payload,
                    "persistence": persistence,
                }
        finally:
            if _selection_uses_pool(selection):
                try:
                    await release_competitor_browser_pool_lease(selection=selection)
                except Exception:
                    logger.exception("failed to release calendar price browser pool lease")


def _selection_uses_pool(selection: Any) -> bool:
    evidence = selection.evidence() if hasattr(selection, "evidence") else {}
    return bool(evidence.get("pool_enabled"))


async def _wait_between_calendar_hotel_reads() -> None:
    await asyncio.sleep(random.uniform(1.2, 2.8))


async def _resolve_calendar_push_payload(
    *,
    group: dict[str, Any],
    config: dict[str, Any],
    alerts: list[dict[str, Any]],
    future_days: int,
    summary: str,
    push_to_feishu: bool,
    push_kind: str,
    watch_group_key: str,
    matrix_brief_slot_label: str,
) -> dict[str, Any]:
    if not push_to_feishu:
        return {
            "status": "skipped",
            "summary": "本次未发送飞书卡片。",
            "watch_group_key": watch_group_key,
        }
    if push_kind == "matrix_brief":
        matrix = await get_latest_calendar_price_matrix(watch_group_key=watch_group_key)
        if matrix is None:
            matrix = {
                "status": "needs_review",
                "summary": summary,
                "future_days": future_days,
                "dates": [],
            }
        return await _push_calendar_matrix_brief(
            group,
            config,
            matrix,
            slot_label=matrix_brief_slot_label,
        )
    if alerts:
        return await _push_calendar_alert(group, config, alerts, future_days, summary)
    return {
        "status": "skipped",
        "summary": "本次未发送飞书预警卡片。",
        "watch_group_key": watch_group_key,
        "push_kind": push_kind,
    }


async def _push_calendar_matrix_brief(
    group: dict[str, Any],
    config: dict[str, Any],
    matrix: dict[str, Any],
    *,
    slot_label: str,
) -> dict[str, Any]:
    # legacy 字段为空时回退到竞品推送使用的 mobile webhook，保持推送群一致
    # （2026-10-09 生产：静里组仅配置 feishu_mobile_webhook_url）。
    webhook_url = str(
        group.get("feishu_webhook_url") or group.get("feishu_mobile_webhook_url") or ""
    ).strip()
    if not webhook_url:
        return {
            "status": "config_required",
            "summary": "未配置飞书推送地址，已保存本次日历价矩阵读取结果。",
            "watch_group_key": group.get("group_key"),
            "push_kind": "matrix_brief",
        }
    title = calendar_price_matrix_title(config.get("matrix_brief_title") or group.get("name"))
    cards = build_calendar_price_matrix_feishu_cards(
        matrix,
        title=title,
        slot_label=slot_label,
    )
    fallback = build_calendar_price_matrix_fallback_text(matrix)
    push_results = []
    for index, card in enumerate(cards):
        card_title = title if len(cards) == 1 else f"{title} ({index + 1}/{len(cards)})"
        push_result = await push_interactive_card_to_feishu_webhook(
            title=card_title,
            card=card,
            webhook_url=webhook_url,
            fallback_text=fallback,
        )
        push_results.append(push_result.model_dump(mode="json"))
    primary = push_results[0] if push_results else {"status": "failed", "summary": "矩阵卡片为空。"}
    return {
        **primary,
        "push_kind": "matrix_brief",
        "card_count": len(cards),
        "cards": push_results,
    }


async def _push_calendar_alert(
    group: dict[str, Any],
    config: dict[str, Any],
    alerts: list[dict[str, Any]],
    future_days: int,
    summary: str,
) -> dict[str, Any]:
    # 与 _push_calendar_matrix_brief 相同的回退：legacy 空则用 mobile webhook。
    webhook_url = str(
        group.get("feishu_webhook_url") or group.get("feishu_mobile_webhook_url") or ""
    ).strip()
    if not webhook_url:
        return {
            "status": "config_required",
            "summary": "未配置飞书推送地址，已保存本次日历价预警。",
            "watch_group_key": group.get("group_key"),
        }
    title = calendar_price_alert_title(config.get("alert_title") or group.get("name"))
    result = {
        "status": "alert",
        "summary": summary,
        "future_days": future_days,
        "alerts": alerts,
    }
    card = build_calendar_price_alert_feishu_card(result, title=title)
    fallback = build_calendar_price_alert_fallback_text(result)
    push_result = await push_interactive_card_to_feishu_webhook(
        title=title,
        card=card,
        webhook_url=webhook_url,
        fallback_text=fallback,
    )
    return push_result.model_dump(mode="json")


def _record_scheduler_heartbeat(
    event: str,
    *,
    status: str = "running",
    tick_result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    try:
        write_calendar_price_scheduler_heartbeat(
            event,
            status=status,
            tick_result=tick_result,
            error=error,
        )
    except Exception:
        logger.exception("failed to write calendar price scheduler heartbeat")
