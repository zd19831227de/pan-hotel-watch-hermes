"""EBK 全局推送调度器（设计文档 §8.1/§4.2 反向防护）。

每 60 秒 tick：查询启用配置 → 旧排程反向校验（冲突软停用+告警）→ 计算
fixed slot / interval due → 以唯一 schedule_key 抢占（含同配置互斥）→ 委托
service 执行 → 写运行结果、游标与心跳。默认由 ``EBK_GLOBAL_PUSH_SCHEDULER_ENABLED``
门控（第一道开关在 workers/read_only_scheduler.py）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.domain.ebk_global_push.models import local_now
from app.domain.ebk_global_push.repository import (
    claim_global_push_run,
    fail_stale_running_runs,
    finish_global_push_run,
    list_global_push_configs,
    mark_config_run_finished,
    set_config_next_run_at,
    soft_disable_global_push_config,
)
from app.domain.ebk_global_push.scheduling import (
    FIXED_SLOT_TOLERANCE_MINUTES,
    compute_first_run_at,
    compute_next_run_at,
    fixed_slot_for_now,
    interval_due,
    is_within_window,
    schedule_key_for_fixed_slot,
    schedule_key_for_interval,
)
from app.domain.ebk_global_push.service import GlobalPushDeps, default_deps
from app.domain.ops.worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger(__name__)

PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}
SCHEDULER_NAME = "ebk_global_push_scheduler"


class EbkGlobalPushScheduler:
    def __init__(
        self,
        *,
        tick_seconds: int = 60,
        deps: GlobalPushDeps | None = None,
        mutex: asyncio.Lock | None = None,
    ) -> None:
        self.tick_seconds = tick_seconds
        self.deps = deps or default_deps()
        # 与评价抓取共享的浏览器互斥锁（2026-09-02：优先 EBK 推送）。
        # 推送 tick 阻塞等锁后执行（必跑）；评价侧见锁被占跳过本轮让位。
        self.mutex = mutex
        # 仓储函数作为实例属性，便于单测注入。
        self._list_enabled = list_global_push_configs
        self._claim = claim_global_push_run
        self._finish = finish_global_push_run
        self._mark_finished = mark_config_run_finished
        self._soft_disable = soft_disable_global_push_config
        self._fail_stale = fail_stale_running_runs
        self._set_next_run = set_config_next_run_at
        self._stopped = False

    async def run_forever(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except BaseException:  # noqa: BLE001 - loop 必须持续运行
                logger.error("ebk global push tick failed; continuing", exc_info=True)
            await asyncio.sleep(self.tick_seconds)

    def stop(self) -> None:
        self._stopped = True

    async def tick(self, now=None) -> dict[str, Any]:
        if self.mutex is None:
            return await self._tick_inner(now)
        # 推送优先：阻塞等锁（评价抓取让位/短暂等待后必跑）。
        async with self.mutex:
            return await self._tick_inner(now)

    async def _tick_inner(self, now=None) -> dict[str, Any]:
        current = now or local_now()
        await self._fail_stale()
        configs = await self._list_enabled(enabled_only=True)
        summary = {
            "enabled_configs": len(configs),
            "triggered": 0,
            "conflicts": 0,
            "missed_slots": 0,
        }
        for config in configs:
            if await self._legacy_conflict_guard(config):
                summary["conflicts"] += 1
                continue
            triggered = await self._process_config(config, current)
            summary["triggered"] += int(triggered)
        write_worker_heartbeat(SCHEDULER_NAME, "tick_finished", tick_summary=summary)
        return summary

    async def _legacy_conflict_guard(self, config: dict[str, Any]) -> bool:
        """§4.2 每 tick 反向校验：旧 auto_poll_enabled 被重新打开即软停用+告警。"""

        anchor = await self.deps.get_ctrip_group(config["ctrip_watch_group_key"])
        conflicting: list[str] = []
        if anchor is not None and bool(getattr(anchor, "auto_poll_enabled", False)):
            conflicting.append("ctrip")
        for platform in ("meituan", "fliggy"):
            if platform not in (config.get("enabled_platforms") or []):
                continue
            mounted = await self.deps.find_mounted_group(
                platform, config["ctrip_watch_group_key"]
            )
            if mounted is not None and bool(getattr(mounted, "auto_poll_enabled", False)):
                conflicting.append(platform)
        if not conflicting:
            return False
        labels = "、".join(PLATFORM_LABELS.get(p, p) for p in conflicting)
        logger.warning(
            "ebk global push config %s: legacy auto poll re-enabled (%s); soft disabled",
            config["id"],
            labels,
        )
        await self._soft_disable(config["id"])
        run = await self._claim(
            config_id=config["id"],
            schedule_key=f"legacy_conflict:{config['id']}:{local_now().strftime('%Y%m%d%H%M')}",
            trigger="scheduler",
        )
        if run:
            await self._finish(
                run["id"],
                status="failed",
                error_summary=f"legacy_schedule_conflict：{labels}旧自动排程仍开启，已自动停用全局推送。",
            )
        if anchor is not None:
            webhook = str(getattr(anchor, "feishu_webhook_url", "") or "")
            if webhook:
                try:
                    await self.deps.send_failure_alert(
                        str(config["id"]),
                        "legacy",
                        str(getattr(anchor, "name", "") or ""),
                        "failed",
                        f"旧自动排程（{labels}）被重新打开，全局推送已自动停用；请先关闭旧开关再重新启用。",
                        webhook,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("legacy conflict alert failed", exc_info=True)
        return True

    async def _process_config(self, config: dict[str, Any], now) -> bool:
        if config.get("schedule_mode") == "fixed_slots":
            return await self._process_fixed(config, now)
        return await self._process_interval(config, now)

    async def _process_fixed(self, config: dict[str, Any], now) -> bool:
        slot = fixed_slot_for_now(config, now)
        if slot is not None:
            slot_hhmm = slot.strftime("%H:%M")
            key = schedule_key_for_fixed_slot(config["id"], slot, slot_hhmm)
            return await self._claim_and_run(config, key)
        await self._record_missed_slots(config, now)
        return False

    async def _record_missed_slots(self, config: dict[str, Any], now) -> None:
        """槽位超容忍窗口后的第一个 tick 内记一次 missed_slot（幂等由唯一键保证）。"""

        for raw in config.get("push_times") or []:
            try:
                hours, minutes = str(raw).split(":", 1)
                slot = now.replace(hour=int(hours), minute=int(minutes), second=0, microsecond=0)
            except (ValueError, AttributeError):
                continue
            from datetime import timedelta

            elapsed = (now - slot).total_seconds() / 60
            if FIXED_SLOT_TOLERANCE_MINUTES < elapsed <= FIXED_SLOT_TOLERANCE_MINUTES + self.tick_seconds / 60 + 1:
                key = schedule_key_for_fixed_slot(config["id"], slot, str(raw))
                run = await self._claim(config_id=config["id"], schedule_key=key)
                if run:
                    await self._finish(
                        run["id"], status="missed_slot",
                        error_summary=f"槽位 {raw} 超过容忍窗口未执行，不补发历史卡。",
                    )

    async def _process_interval(self, config: dict[str, Any], now) -> bool:
        if not is_within_window(
            now, config.get("window_start_time"), config.get("window_end_time")
        ):
            return False
        next_run_text = config.get("next_run_at")
        if not next_run_text:
            first = compute_first_run_at(config, now)
            await self._set_next_run(config["id"], first)
            config = {**config, "next_run_at": first.isoformat()}
            next_run_text = config["next_run_at"]
        from datetime import datetime

        next_run_at = datetime.fromisoformat(next_run_text)
        if not interval_due(now, next_run_at):
            return False
        key = schedule_key_for_interval(config["id"], now)
        return await self._claim_and_run(config, key)

    async def _claim_and_run(self, config: dict[str, Any], schedule_key: str) -> bool:
        run = await self._claim(
            config_id=config["id"], schedule_key=schedule_key, trigger="scheduler"
        )
        if run is None:
            return False  # already_claimed 或同配置 running 互斥
        from app.domain.ebk_global_push.service import run_global_push

        result = await run_global_push(config=config, run_id=run["id"], deps=self.deps)
        await self._finish(
            run["id"],
            status=result["status"],
            platform_results=result["platform_results"],
            card_results=result["card_results"],
            error_summary=result["error_summary"],
        )
        await self._mark_finished(
            config["id"],
            succeeded=bool(result["succeeded"]),
            next_run_at=compute_next_run_at(
                config,
                finished_at=local_now(),
                succeeded=bool(result["succeeded"]),
                consecutive_failures=int(config.get("consecutive_failure_count") or 0),
            ),
        )
        return True
