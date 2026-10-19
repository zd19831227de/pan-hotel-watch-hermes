from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.browser.ctrip_ebooking import (
    CtripBrowserProfileLockedError,
    get_ctrip_ebooking_service,
)
from app.domain.browser.ctrip_ebooking_session_helpers import _is_stale_playwright_page_error
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.operator_payload import sanitize_operator_payload
from app.domain.ctrip_ebooking_watch.messages import (
    build_data_center_watch_push_card,
    build_data_center_watch_push_text,
    compute_daily_brief_day_over_day,
)
from app.domain.ctrip_ebooking_watch.repository import (
    DAILY_BRIEF_REQUIRED_DATASET_IDS,
    DataCenterWatchDatasetRuntime,
    DataCenterWatchGroupRuntime,
    get_data_center_watch_group_runtime,
    get_previous_business_outline_runs,
    mark_data_center_watch_group_polled,
    save_data_center_watch_run,
    update_data_center_watch_run_push_result,
)
from app.domain.ctrip_ebooking_watch.operator_text import operator_failure_reason
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook

logger = logging.getLogger(__name__)
_PUSH_TITLE = "携程 EBK 数据中心盯盘"


class DataCenterWatchService:
    async def run_watch_group_now(
        self,
        *,
        watch_group_key: str,
        actor_id: str = "operator",
        source: str = "api",
        push_to_feishu: bool = True,
    ) -> dict[str, Any]:
        group = await get_data_center_watch_group_runtime(watch_group_key=watch_group_key)
        if group is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到 EBK 数据中心监控组。",
            }
        if not group.enabled:
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": "EBK 数据中心监控组已停用。",
            }
        return await self.run_watch_group(
            group=group,
            actor_id=actor_id,
            source=source,
            push_to_feishu=push_to_feishu,
        )

    async def run_watch_group(
        self,
        *,
        group: DataCenterWatchGroupRuntime,
        actor_id: str,
        source: str,
        push_to_feishu: bool = True,
        dataset_ids: tuple[str, ...] | None = None,
        push_title: str | None = None,
        run_kind: str = "data_center_watch",
        include_disabled_datasets: bool = False,
        force_push_enabled: bool = False,
    ) -> dict[str, Any]:
        requested_dataset_ids = set(dataset_ids or ())
        datasets = [
            dataset
            for dataset in group.datasets
            if (include_disabled_datasets or dataset.enabled)
            and (not requested_dataset_ids or dataset.dataset_id in requested_dataset_ids)
        ]
        if not datasets:
            await mark_data_center_watch_group_polled(watch_group_key=group.group_key)
            return {
                "watch_group_key": group.group_key,
                "status": "skipped",
                "summary": "没有启用的 EBK 数据集。",
                "dataset_results": [],
            }

        browser_session_id = (
            group.browser_session_id
            or get_browser_session_id_for_platform_account(group.platform_account_id)
        )
        if not browser_session_id:
            dataset_results = [
                _dataset_human_required_result(
                    dataset,
                    "未绑定可用的 EBK 浏览器账号。",
                )
                for dataset in datasets
            ]
            return await self._finalize_run(
                group=group,
                browser_session_id=None,
                actor_id=actor_id,
                source=source,
                dataset_results=dataset_results,
                push_to_feishu=push_to_feishu,
                push_title=push_title,
                run_kind=run_kind,
            )

        if not _browser_session_registered(browser_session_id):
            # The bound session id is stale/unknown in BrowserSessionStore.
            # Do not attempt the browser read (which would raise
            # "Unknown browser session: ..."); degrade to a clear human_required
            # hint so the operator knows to log in to Ctrip EBK first.
            unregistered_hint = (
                "携程 EBK 浏览器会话未注册，请先在浏览器管理中登录携程 EBK 账号。"
            )
            dataset_results = [
                _dataset_human_required_result(dataset, unregistered_hint)
                for dataset in datasets
            ]
            return await self._finalize_run(
                group=group,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                source=source,
                dataset_results=dataset_results,
                push_to_feishu=push_to_feishu,
                push_title=push_title,
                run_kind=run_kind,
            )

        dataset_results = []
        for dataset in datasets:
            dataset_results.append(
                await self._read_dataset(
                    group=group,
                    browser_session_id=browser_session_id,
                    dataset=dataset,
                    actor_id=actor_id,
                )
            )
        if force_push_enabled:
            for result in dataset_results:
                result["push_enabled"] = True
        return await self._finalize_run(
            group=group,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            source=source,
            dataset_results=dataset_results,
            push_to_feishu=push_to_feishu,
            push_title=push_title,
            run_kind=run_kind,
        )

    async def run_daily_brief(
        self,
        *,
        group: DataCenterWatchGroupRuntime,
        actor_id: str = "scheduler",
        source: str = "scheduler",
    ) -> dict[str, Any]:
        return await self.run_watch_group(
            group=group,
            actor_id=actor_id,
            source=source,
            push_to_feishu=True,
            dataset_ids=DAILY_BRIEF_REQUIRED_DATASET_IDS,
            push_title=group.daily_brief_title or group.brief_title or "EBK 数据简报",
            run_kind="daily_brief",
            include_disabled_datasets=True,
            force_push_enabled=True,
        )

    async def _read_dataset(
        self,
        *,
        group: DataCenterWatchGroupRuntime,
        browser_session_id: str,
        dataset: DataCenterWatchDatasetRuntime,
        actor_id: str,
    ) -> dict[str, Any]:
        try:
            read_timeout = 60.0
            payload: dict[str, Any]
            if dataset.dataset_id == "comment_monitor":
                result = await asyncio.wait_for(
                    get_ctrip_ebooking_service().read_comment_monitor(
                        platform_account_id=group.platform_account_id,
                        browser_session_id=browser_session_id,
                        filters={
                            "platform": "总平台",
                            "scope": "comment_monitor",
                            "time_range": "今日",
                        },
                        actor_id=actor_id,
                        push_to_feishu=False,
                    ),
                    timeout=read_timeout,
                )
                payload = result.model_dump(mode="json")
            elif dataset.dataset_id == "pyramid_bid":
                # 2026-09-10：金字塔出价列表作为数据集接入盯盘读取/推送，
                # 复用「金字塔出价」指令的只读抓取链路（同一浏览器会话）。
                pyramid_read = await asyncio.wait_for(
                    get_ctrip_ebooking_service().read_pyramid_promotions(
                        platform_account_id=group.platform_account_id,
                        browser_session_id=browser_session_id,
                        actor_id=actor_id,
                    ),
                    timeout=read_timeout,
                )
                # flow 返回纯 dict（非 pydantic）；promotions 并入 data，
                # 与其它数据集共用统一的结果封装。
                payload = dict(pyramid_read)
                payload["data"] = {
                    **(payload.get("data") or {}),
                    "promotions": list(payload.get("promotions") or []),
                }
            else:
                filters = {"platform": "总平台", "scope": dataset.dataset_id}
                if dataset.dataset_id == "ad_pyramid":
                    filters["time_range"] = "今日"
                result = await asyncio.wait_for(
                    get_ctrip_ebooking_service().read_dataset(
                        platform_account_id=group.platform_account_id,
                        browser_session_id=browser_session_id,
                        dataset_id=dataset.dataset_id,
                        filters=filters,
                        actor_id=actor_id,
                        push_to_feishu=False,
                    ),
                    timeout=read_timeout,
                )
                payload = result.model_dump(mode="json")
        except TimeoutError:
            logger.warning("data-center watch dataset read timed out after %.0fs", read_timeout)
            return _dataset_human_required_result(
                dataset,
                "浏览器读取超时，请确认本机 Chrome 已打开且 EBK 已登录。",
            )
        except KeyError as exc:
            return _dataset_failed_result(dataset, "human_required", str(exc), error_type="KeyError")
        except CtripBrowserProfileLockedError as exc:
            return _dataset_failed_result(
                dataset,
                "human_required",
                str(exc),
                error_type=type(exc).__name__,
            )
        except Exception as exc:
            logger.exception("data-center watch dataset read failed")
            summary = f"{type(exc).__name__}: {str(exc)[:240]}" if str(exc) else type(exc).__name__
            if _is_browser_runtime_start_failure(exc):
                return _dataset_failed_result(
                    dataset,
                    "needs_review",
                    summary,
                    error_type=type(exc).__name__,
                )
            return _dataset_failed_result(
                dataset,
                "failed",
                summary,
                error_type=type(exc).__name__,
            )
        status = str(payload.get("status") or "failed")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        evidence.pop("persistent_profile_dir", None)
        evidence["profile_path_returned"] = False
        return {
            "dataset_id": dataset.dataset_id,
            "dataset_label": dataset.dataset_label,
            "status": status,
            "summary": payload.get("summary"),
            "failure_reason": _failure_reason(status, evidence, payload.get("summary"), data),
            "parser_version": evidence.get("parser_version"),
            "captured_at": data.get("captured_at") or _now_text(),
            "data": data,
            "evidence": evidence,
            "push_enabled": dataset.push_enabled,
        }

    async def _finalize_run(
        self,
        *,
        group: DataCenterWatchGroupRuntime,
        browser_session_id: str | None,
        actor_id: str,
        source: str,
        dataset_results: list[dict[str, Any]],
        push_to_feishu: bool,
        push_title: str | None = None,
        run_kind: str = "data_center_watch",
    ) -> dict[str, Any]:
        # V7 Step 1 — 采集后校验层（selector 健康/结构指纹）；异常不影响读取结果
        for result in dataset_results:
            try:
                from app.domain.page_change_alerts.service import (
                    observe_ctrip_dataset_result,
                )

                await observe_ctrip_dataset_result(result)
            except Exception:
                logger.debug("page change observation failed", exc_info=True)
        status = _overall_status(dataset_results)
        summary = _summary(status, dataset_results)
        captured_at = _now_datetime()
        persistence = await save_data_center_watch_run(
            watch_group_key=group.group_key,
            platform_account_id=group.platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            source=source,  # type: ignore[arg-type]
            status=status,
            summary=summary,
            dataset_results=dataset_results,
            push_result=None,
            captured_at=captured_at,
        )
        day_over_day: dict[str, Any] = {}
        if run_kind == "daily_brief":
            day_over_day = await _load_daily_brief_day_over_day(
                group=group,
                dataset_results=dataset_results,
                captured_at=captured_at,
            )
        push_result = (
            await _safe_push_to_feishu(
                group,
                status,
                summary,
                dataset_results,
                captured_at=captured_at,
                title_override=push_title,
                run_kind=run_kind,
                day_over_day=day_over_day,
            )
            if push_to_feishu
            else {
                "status": "skipped",
                "summary": "本次读取未发送飞书推送。",
                "watch_group_key": group.group_key,
            }
        )
        for result in dataset_results:
            result["pushed"] = (
                bool(result.get("push_enabled"))
                and push_result.get("status") == "success"
            )
            result["push_result"] = (
                push_result
                if result.get("push_enabled")
                else None
            )
        run_id = persistence.get("run_id")
        if isinstance(run_id, int):
            try:
                await update_data_center_watch_run_push_result(
                    run_id=run_id,
                    dataset_results=dataset_results,
                    push_result=push_result,
                )
            except Exception:
                logger.exception("data-center watch push-result persistence update failed")
        # V5 Step 6 — record the last successful push so the scheduler can apply
        # the 2h manual-trigger cooldown. Daily-brief pushes are ignored by the
        # recorder so they never throttle the regular scheduled push.
        if push_to_feishu and push_result.get("status") == "success":
            try:
                from app.domain.ctrip_ebooking_watch.push_throttle import record_push

                record_push(
                    group.group_key, source=source or "", run_kind=run_kind or ""
                )
            except Exception:
                logger.debug("push throttle record failed", exc_info=True)
        return sanitize_operator_payload({
            "watch_group_key": group.group_key,
            "status": status,
            "summary": summary,
            "platform_account_id": group.platform_account_id,
            "browser_session_id": browser_session_id,
            "dataset_results": dataset_results,
            "feishu_push": push_result,
            "persistence": persistence,
            "run_kind": run_kind,
        })


async def _load_daily_brief_day_over_day(
    *,
    group: DataCenterWatchGroupRuntime,
    dataset_results: list[dict[str, Any]],
    captured_at: datetime,
) -> dict[str, Any]:
    """日环比（城市排名/PSI）：只与前一日成功读取对比，否则省略。

    官方简报的「较前一日」页面上不存在，必须来自两次真实成功读取的差值。"""
    try:
        baseline_runs = await get_previous_business_outline_runs(
            watch_group_key=group.group_key,
            before_captured_at=captured_at,
            limit=5,
        )
    except Exception:
        logger.exception("daily brief day-over-day baseline load failed")
        return {}
    yesterday_date = captured_at.date() - timedelta(days=1)
    for run in baseline_runs:
        run_captured = run.get("captured_at")
        if not isinstance(run_captured, datetime):
            continue
        if run_captured.date() != yesterday_date:
            continue
        return compute_daily_brief_day_over_day(
            current_results=dataset_results,
            previous_run=run,
        )
    return {}


def get_data_center_watch_service() -> DataCenterWatchService:
    return DataCenterWatchService()


async def _push_to_feishu(
    group: DataCenterWatchGroupRuntime,
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    *,
    captured_at: str | datetime | None = None,
    title_override: str | None = None,
    run_kind: str = "data_center_watch",
    day_over_day: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pushable = [result for result in dataset_results if result.get("push_enabled")]
    if not pushable:
        return {
            "status": "skipped",
            "summary": "没有配置需要推送的数据集。",
            "watch_group_key": group.group_key,
        }
    push_title = title_override or group.brief_title or group.name or _PUSH_TITLE
    cards = build_data_center_watch_push_card(
        watch_group_key=group.group_key,
        status=status,
        summary=summary,
        dataset_results=pushable,
        title=push_title,
        captured_at=captured_at,
    )
    webhook_url = str(group.feishu_webhook_url or "").strip()
    if webhook_url:
        fallback_text = build_data_center_watch_push_text(
            watch_group_key=group.group_key,
            status=status,
            summary=summary,
            dataset_results=pushable,
            title=push_title,
            captured_at=captured_at,
            include_title=False,
        )
        # 多卡连续推送（同竞品盯盘机制）：逐卡发送互不中断，任一成功即算
        # 推送成功；全部失败才用第一张卡降级重发纯文本。
        card_results = [
            await push_interactive_card_to_feishu_webhook(
                title=push_title,
                card=card,
                webhook_url=webhook_url,
                fallback_text=None,
            )
            for card in cards
        ]
        if any(item.status == "success" for item in card_results):
            payload = card_results[0].model_dump(mode="json")
            payload["status"] = "success"
            succeeded = sum(1 for item in card_results if item.status == "success")
            payload["summary"] = f"已发送 {succeeded}/{len(card_results)} 张盯盘卡片。"
        else:
            push_result = await push_interactive_card_to_feishu_webhook(
                title=push_title,
                card=cards[0],
                webhook_url=webhook_url,
                fallback_text=fallback_text,
            )
            payload = push_result.model_dump(mode="json")
        payload["watch_group_key"] = group.group_key
        payload["delivery"] = "webhook"
        payload["card_count"] = len(card_results)
        payload["card_results"] = [
            {
                "status": item.status,
                "feishu_code": item.data.get("feishu_code"),
                "feishu_msg": item.data.get("feishu_msg"),
                "msg_type": item.data.get("msg_type"),
            }
            for item in card_results
        ]
        return payload
    return {
        "status": "config_required",
        "summary": "未配置飞书机器人地址，已保存本次读取结果。",
        "watch_group_key": group.group_key,
        "next_action": "在 EBK 数据中心监控组中配置飞书机器人地址。",
    }


async def _safe_push_to_feishu(
    group: DataCenterWatchGroupRuntime,
    status: str,
    summary: str,
    dataset_results: list[dict[str, Any]],
    *,
    captured_at: str | datetime | None = None,
    title_override: str | None = None,
    run_kind: str = "data_center_watch",
    day_over_day: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return await _push_to_feishu(
            group,
            status,
            summary,
            dataset_results,
            captured_at=captured_at,
            title_override=title_override,
            run_kind=run_kind,
            day_over_day=day_over_day,
        )
    except Exception as exc:
        logger.exception("data-center watch Feishu push failed")
        return {
            "status": "failed",
            "summary": f"飞书推送失败：{type(exc).__name__}: {str(exc)[:240]}",
            "watch_group_key": group.group_key,
            "error_type": type(exc).__name__,
        }


def _browser_session_registered(browser_session_id: str | None) -> bool:
    """Return True iff the given browser session id exists in BrowserSessionStore.

    Used to short-circuit EBK reads before they hit the browser layer, so a
    stale/unregistered session id surfaces a clear human_required hint instead
    of "Unknown browser session: ..." leaking into run status/summary.
    """
    if not browser_session_id:
        return False
    try:
        get_browser_session_store().get(browser_session_id)
        return True
    except KeyError:
        return False


def _dataset_human_required_result(
    dataset: DataCenterWatchDatasetRuntime,
    summary: str,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_label": dataset.dataset_label,
        "status": "human_required",
        "summary": summary,
        "failure_reason": summary,
        "parser_version": None,
        "captured_at": _now_text(),
        "data": {"dataset_id": dataset.dataset_id},
        "evidence": {"real_browser_required": True, "read_only": True},
        "push_enabled": dataset.push_enabled,
    }


def _dataset_failed_result(
    dataset: DataCenterWatchDatasetRuntime,
    status: str,
    summary: str,
    *,
    error_type: str,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_label": dataset.dataset_label,
        "status": status,
        "summary": summary,
        "failure_reason": summary,
        "parser_version": None,
        "captured_at": _now_text(),
        "data": {"dataset_id": dataset.dataset_id},
        "evidence": {"error_type": error_type, "error": summary, "read_only": True},
        "push_enabled": dataset.push_enabled,
    }


def _is_browser_runtime_start_failure(exc: Exception) -> bool:
    if isinstance(exc, NotImplementedError):
        return True
    if _is_stale_playwright_page_error(exc):
        return True
    message = str(exc).lower()
    return "playwright" in message and "subprocess" in message


def _overall_status(dataset_results: list[dict[str, Any]]) -> str:
    statuses = [str(result.get("status") or "failed") for result in dataset_results]
    if not statuses:
        return "skipped"
    if all(status == "success" for status in statuses):
        return "success"
    if any(status == "human_required" for status in statuses):
        return "human_required"
    if any(status == "browser_busy" for status in statuses) and not any(
        status == "success" for status in statuses
    ):
        # P8：整轮都被浏览器租约挡下，如实记 browser_busy，不算读取失败。
        return "browser_busy"
    if any(status == "success" for status in statuses):
        return "partial"
    if any(status in {"needs_review", "page_structure_changed", "partial"} for status in statuses):
        return "needs_review"
    return "failed"


def _summary(status: str, dataset_results: list[dict[str, Any]]) -> str:
    total = len(dataset_results)
    success = sum(1 for result in dataset_results if result.get("status") == "success")
    human = sum(1 for result in dataset_results if result.get("status") == "human_required")
    if status == "success":
        return f"EBK 数据中心读取成功：成功 {success}/{total}。"
    if status == "human_required":
        return f"EBK 数据中心需要人工处理：{human}/{total} 个数据集受阻。"
    if status == "partial":
        return f"EBK 数据中心部分成功：成功 {success}/{total}。"
    return f"EBK 数据中心读取完成：{_status_label(status)}，成功 {success}/{total}。"


def _status_label(status: str) -> str:
    return {
        "success": "成功",
        "partial": "部分成功",
        "needs_review": "需要复核",
        "page_structure_changed": "页面结构变化",
        "human_required": "需要人工处理",
        "failed": "失败",
        "skipped": "已跳过",
        "config_required": "需要配置",
    }.get(status, status)


def _failure_reason(
    status: str,
    evidence: dict[str, Any],
    summary: Any,
    data: dict[str, Any] | None = None,
) -> str | None:
    if status == "success":
        return None
    data = data or {}
    notes = data.get("data_quality_notes")
    if isinstance(notes, list):
        for note in notes:
            if note:
                return operator_failure_reason(note, status=status)
    for key in ("review_reason", "error", "error_type"):
        value = evidence.get(key)
        if value:
            return operator_failure_reason(value, status=status)
    page_state = evidence.get("page_state")
    if page_state and str(page_state) != "authenticated":
        return operator_failure_reason(page_state, status=status)
    return operator_failure_reason(summary, status=status)


def _now_text() -> str:
    return _now_datetime().isoformat()


def _now_datetime() -> datetime:
    return datetime.now(UTC).astimezone()
