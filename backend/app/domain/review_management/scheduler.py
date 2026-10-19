"""Scheduler for scheduled (daily HH:MM) review fetching and auto-reply processing.

半自动/全自动触发链路改为每日定时触发（2026-09-16 需求，参照 EBK 定时推送的
时间设置）：每店铺/平台配置 `auto_process_time`（HH:MM，默认 23:00），60s tick
检查「now >= 当日到点 且 当日未成功跑过」即执行一次 fetch->process 流水线，
成功（fetch ok）后写 `last_auto_processed_at` 游标，当日不再触发；失败在
`AUTO_PROCESS_MISSED_WINDOW_MINUTES` 容忍窗口内每分钟重试，超窗视为错过
本日时段不补跑（与 EBK 全局推送 missed_slot 同口径）。

只处理开关开启（`auto_fetch_enabled=True`）的店铺/平台；模式语义
（off/semi_auto/full_auto）由 service.process_pending_reviews 执行。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from . import repository, service
from .cards import build_review_daily_summary_card
from .models import local_now
from .types import REVIEW_PLATFORMS, normalize_auto_process_time

logger = logging.getLogger(__name__)

PushCardFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# 到点后错过容忍窗口（分钟）：超窗不补跑，等次日到点。
AUTO_PROCESS_MISSED_WINDOW_MINUTES = 30


class ReviewFetchScheduler:
    def __init__(
        self,
        *,
        tick_seconds: int = 60,
        push_card: PushCardFn | None = None,
        mutex: asyncio.Lock | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.tick_seconds = tick_seconds
        self.push_card = push_card
        # 与 EBK 全局推送共享的浏览器互斥锁（2026-09-02 需求：优先 EBK 推送）。
        # 评价 tick 见锁被占（推送在跑）即跳过本轮让位，60s 后下轮再试；
        # 评价抓取执行期间持锁，推送 tick 阻塞等锁后执行。
        self.mutex = mutex
        # 时间源可注入（测试固定时刻）；缺省用本地时间。
        self.now_fn = now_fn or local_now

    async def run_forever(self) -> None:
        logger.info("review fetch scheduler started (tick=%ss)", self.tick_seconds)
        while True:
            try:
                result = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("review fetch scheduler tick failed; continuing")
                await asyncio.sleep(self.tick_seconds)
                continue
            if result["due_platforms"]:
                logger.info("review fetch scheduler tick: %s", result["summary"])
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> dict[str, Any]:
        """跑一轮到点店铺/平台；单个失败不断循环。

        互斥（优先 EBK 推送）：锁被推送/其他工作持有时整轮跳过（skipped_for_push），
        不阻塞、不与推送叠加操作同一浏览器。

        多店铺：平台有店铺绑定时只跑各绑定店铺（各自定时时间），不再叠加平台级
        默认轮次；没有绑定的平台保持旧的平台级行为。
        """
        if self.mutex is not None and self.mutex.locked():
            return {
                "due_platforms": [],
                "results": [],
                "skipped_for_push": True,
                "summary": {},
            }
        due_platforms: list[str] = []
        results: list[dict[str, Any]] = []
        for platform in REVIEW_PLATFORMS:
            bindings = await repository.list_store_bindings(platform=platform)
            # 该平台有店铺绑定时只跑各启用绑定、不再回落平台级默认店铺（修复
            # 「某店铺关、平台总开关开」时默认店铺仍被抓取的歧义）；无任何绑定时
            # 才走平台级默认轮次（兼容未配置监控组的平台/附属平台）。
            if bindings:
                platform_due = False
                for binding_row in bindings:
                    if not (
                        binding_row.get("enabled")
                        and binding_row.get("auto_fetch_enabled")
                    ):
                        continue
                    if not self._scheduled_run_due(binding_row):
                        continue
                    platform_due = True
                    session_id = binding_row["browser_session_id"]
                    try:
                        if self.mutex is not None:
                            async with self.mutex:
                                result = await self.run_platform_now(
                                    platform,
                                    browser_session_id=session_id,
                                    source="scheduler",
                                    push_summary_card=False,
                                )
                        else:
                            result = await self.run_platform_now(
                                platform,
                                browser_session_id=session_id,
                                source="scheduler",
                                push_summary_card=False,
                            )
                        results.append(result)
                        await self._mark_if_succeeded(platform, session_id, result)
                    except Exception:
                        logger.exception(
                            "review pipeline failed for platform %s store %s",
                            platform,
                            session_id,
                        )
                        results.append(
                            {
                                "platform": platform,
                                "browser_session_id": session_id,
                                "fetch": {"status": "error"},
                            }
                        )
                if platform_due:
                    due_platforms.append(platform)
                continue
            config = await repository.get_review_auto_config(platform=platform)
            if not config["auto_fetch_enabled"]:
                continue
            if not self._scheduled_run_due(config):
                continue
            due_platforms.append(platform)
            try:
                if self.mutex is not None:
                    async with self.mutex:
                        result = await self.run_platform_now(
                            platform, source="scheduler"
                        )
                else:
                    result = await self.run_platform_now(platform, source="scheduler")
                results.append(result)
                await self._mark_if_succeeded(platform, None, result)
            except Exception:
                logger.exception("review pipeline failed for platform %s", platform)
                results.append({"platform": platform, "fetch": {"status": "error"}})
                continue
        # 日报合并卡（2026-09-21）：所有到点店铺结算后每群推一张三平台汇总卡，
        # 取代逐店统计卡；失败只记日志，不影响下一轮 tick。
        try:
            daily_card = await self._push_daily_summary_if_complete()
        except Exception:
            logger.exception("review daily summary card push failed")
            daily_card = None
        return {
            "due_platforms": due_platforms,
            "results": results,
            "daily_card": daily_card,
            "summary": {
                platform: (result.get("fetch") or {}).get("status")
                for platform, result in zip(due_platforms, results)
            },
        }

    async def _push_daily_summary_if_complete(self) -> dict[str, Any] | None:
        """所有启用的到点店铺当日结算（成功或超窗错过）后推一张日报合并卡。

        每个目标群当日去重（review_daily_card_pushes）；卡内容只列该群自己的
        店铺。当日没有任何店铺成功跑过则不推（避免重启后推陈旧空卡）。
        """
        now = self.now_fn()
        today = now.date()
        active: list[dict[str, Any]] = []
        for platform in REVIEW_PLATFORMS:
            for row in await repository.list_store_bindings(platform=platform):
                if row.get("enabled") and row.get("auto_fetch_enabled"):
                    row = dict(row)
                    row["platform"] = platform
                    active.append(row)
        if not active:
            return None
        ran_today = False
        all_settled = True
        for row in active:
            try:
                push_time = normalize_auto_process_time(row.get("auto_process_time"))
            except ValueError:
                push_time = "23:00"
            hour, minute = (int(part) for part in push_time.split(":", 1))
            due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            last = row.get("last_auto_processed_at")
            done_today = False
            if last:
                try:
                    # 与 _scheduled_run_due 同口径：跨午夜完成的报次不算
                    # 「今日档位已跑」，当日结算判定不被游标日期误导。
                    done_today = datetime.fromisoformat(str(last)) >= due_at
                except ValueError:
                    done_today = False
            if done_today:
                ran_today = True
                continue
            if (
                self.now_fn()
                >= due_at + timedelta(minutes=AUTO_PROCESS_MISSED_WINDOW_MINUTES)
            ):
                # 超窗错过：当日不再补跑，视为已结算（卡上如实显示抓取失败/未跑）。
                continue
            all_settled = False
        if not all_settled or not ran_today:
            return None

        summary = await service.collect_daily_review_summary(
            today_start=now.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        entries = summary["entries"]
        chat_ids = list(
            dict.fromkeys(
                entry["chat_id"] for entry in entries if entry.get("chat_id")
            )
        )
        chatless = [
            entry for entry in entries if not entry.get("chat_id")
        ]
        if chatless:
            logger.warning(
                "review daily summary: %d store(s) without feishu chat id skipped",
                len(chatless),
            )
        pusher = self.push_card or service._default_push_card
        pushed_chats: list[str] = []
        day_label = f"{today:%m-%d}"
        for chat_id in chat_ids:
            if not await repository.mark_daily_card_pushed(
                chat_id=chat_id, push_date=today.isoformat()
            ):
                continue
            card = build_review_daily_summary_card(
                entries=[
                    entry
                    for entry in entries
                    if entry.get("chat_id") == chat_id
                ],
                day_label=day_label,
            )
            await pusher(chat_id, card)
            pushed_chats.append(chat_id)
        if pushed_chats:
            logger.info(
                "review daily summary card pushed to %s", pushed_chats
            )
            return {"pushed_chats": pushed_chats}
        return None

    async def _mark_if_succeeded(
        self, platform: str, browser_session_id: str | None, result: dict[str, Any]
    ) -> None:
        """fetch 成功才写游标：失败保留重试机会（容忍窗口内）。

        游标时间与调度器时钟同源（now_fn）：测试注入固定时钟时判定与游标
        不会因两个时钟错位误判；生产 now_fn 即 local_now，行为不变。
        """
        if (result.get("fetch") or {}).get("status") == "ok":
            await repository.mark_auto_processed(
                platform=platform,
                browser_session_id=browser_session_id,
                processed_at=self.now_fn(),
            )

    async def run_platform_now(
        self,
        platform: str,
        *,
        browser_session_id: str | None = None,
        source: str = "manual",
        push_summary_card: bool = True,
    ) -> dict[str, Any]:
        result = await service.run_review_pipeline(
            platform=platform,
            browser_session_id=browser_session_id,
            push_card=self.push_card,
            source=source,
            push_summary_card=push_summary_card,
        )
        return {"platform": platform, "browser_session_id": browser_session_id, **result}

    def _scheduled_run_due(self, row: dict[str, Any]) -> bool:
        """每日到点判定：到点且当日未成功跑过；超过容忍窗口视为错过不补跑。

        非法时间值回落默认 23:00（缺省即未到点通常不触发），不让单条脏配置
        炸掉整个 tick。
        """
        try:
            push_time = normalize_auto_process_time(row.get("auto_process_time"))
        except ValueError:
            logger.warning(
                "invalid auto_process_time %r; falling back to 23:00",
                row.get("auto_process_time"),
            )
            push_time = "23:00"
        now = self.now_fn()
        hour, minute = (int(part) for part in push_time.split(":", 1))
        due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < due_at:
            return False
        if now >= due_at + timedelta(minutes=AUTO_PROCESS_MISSED_WINDOW_MINUTES):
            return False
        last = row.get("last_auto_processed_at")
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last))
        except ValueError:
            return True
        # 「当日已跑」= 游标不早于今日档位时刻（游标记的是完成时刻）。
        # 按日期比较会把跨午夜完成的报次（游标落在次日 00:0x）误判成
        # 「今天已跑」，当晚整档被跳过，形成 48 小时一跑（2026-10-08 F1）。
        return last_dt < due_at
