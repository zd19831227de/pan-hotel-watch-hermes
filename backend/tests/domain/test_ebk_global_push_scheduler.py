"""EBK 全局推送调度器单测（fake 仓储/服务依赖，无 DB、无浏览器）。"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.domain.ebk_global_push.scheduler import EbkGlobalPushScheduler
from app.domain.ebk_global_push.service import GlobalPushDeps


def _anchor(**overrides):
    base = dict(
        group_key="anchor_group",
        name="示例门店",
        enabled=True,
        auto_poll_enabled=False,
        platform_account_id="ctrip_account",
        browser_session_id="ctrip_session",
        feishu_webhook_url="https://open.feishu.cn/hook/fake",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _deps(anchor=None, mounted=None):
    anchor = anchor if anchor is not None else _anchor()
    mounted = mounted if mounted is not None else {
        "meituan": SimpleNamespace(enabled=True, auto_poll_enabled=False,
                                   platform_account_id="m", browser_session_id="m"),
        "fliggy": SimpleNamespace(enabled=True, auto_poll_enabled=False,
                                  platform_account_id="f", browser_session_id="f"),
    }
    alerts: list[tuple] = []

    async def get_ctrip_group(group_key):
        return anchor

    async def find_mounted_group(platform, anchor_key):
        return mounted.get(platform)

    async def noop(*args, **kwargs):
        return {}

    async def send_failure_alert(*args):
        alerts.append(args)

    return (
        GlobalPushDeps(
            get_ctrip_group=get_ctrip_group,
            find_mounted_group=find_mounted_group,
            read_ctrip=noop,
            read_platform=noop,
            push_card=noop,
            send_failure_alert=send_failure_alert,
        ),
        alerts,
    )


class _FakeRepo:
    def __init__(self, configs):
        self.configs = [dict(c) for c in configs]
        self.claims: list[tuple[int, str]] = []
        self.finished: list[dict] = []
        self.soft_disabled: list[int] = []
        self.next_run_set: list[tuple[int, datetime]] = []
        self.cursor_updates: list[dict] = []
        self.claim_result = True

    async def list_enabled(self, *, enabled_only=True):
        return [dict(c) for c in self.configs]

    async def claim(self, *, config_id, schedule_key, trigger="scheduler"):
        if not self.claim_result:
            return None
        self.claims.append((config_id, schedule_key))
        return {"id": len(self.claims), "config_id": config_id, "status": "running"}

    async def finish(self, run_id, **kwargs):
        record = {"id": run_id, **kwargs}
        self.finished.append(record)
        return record

    async def mark_finished(self, config_id, *, succeeded, next_run_at):
        self.cursor_updates.append(
            {"config_id": config_id, "succeeded": succeeded, "next_run_at": next_run_at}
        )

    async def soft_disable(self, config_id):
        self.soft_disabled.append(config_id)

    async def fail_stale(self, *, now=None):
        return 0

    async def set_next_run(self, config_id, next_run_at):
        self.next_run_set.append((config_id, next_run_at))


_AT = datetime(2026, 8, 28, 8, 10)


def _scheduler(configs, deps):
    scheduler = EbkGlobalPushScheduler(tick_seconds=60, deps=deps)
    repo = _FakeRepo(configs)
    scheduler._list_enabled = repo.list_enabled
    scheduler._claim = repo.claim
    scheduler._finish = repo.finish
    scheduler._mark_finished = repo.mark_finished
    scheduler._soft_disable = repo.soft_disable
    scheduler._fail_stale = repo.fail_stale
    scheduler._set_next_run = repo.set_next_run
    return scheduler, repo


def _fixed_config(**overrides):
    base = dict(
        id=7,
        ctrip_watch_group_key="anchor_group",
        enabled=True,
        enabled_platforms=["ctrip"],
        schedule_mode="fixed_slots",
        push_times=["08:00"],
        interval_minutes=60,
        window_start_time="06:00",
        window_end_time="23:00",
        next_run_at=None,
        consecutive_failure_count=0,
    )
    base.update(overrides)
    return base


class SchedulerTickTests(unittest.TestCase):
    def test_fixed_slot_due_triggers_run(self):
        deps, _ = _deps()
        scheduler, repo = _scheduler([_fixed_config()], deps)
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["triggered"], 1)
        self.assertTrue(repo.claims[0][1].startswith("fixed:7:"))
        # fake push 未返回 success → 三卡投递失败 → 聚合 partial（读取侧无异常）
        self.assertEqual(repo.finished[0]["status"], "partial")

    def test_fixed_slot_not_due_no_run(self):
        deps, _ = _deps()
        scheduler, repo = _scheduler([_fixed_config(push_times=["23:00"])], deps)
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["triggered"], 0)
        self.assertEqual(repo.claims, [])

    def test_legacy_conflict_soft_disables_and_alerts(self):
        deps, alerts = _deps(anchor=_anchor(auto_poll_enabled=True))
        scheduler, repo = _scheduler([_fixed_config()], deps)
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["conflicts"], 1)
        self.assertEqual(repo.soft_disabled, [7])
        self.assertIn("legacy_schedule_conflict", repo.finished[0]["error_summary"])
        self.assertEqual(len(alerts), 1)

    def test_mutex_claim_failure_skips_run(self):
        deps, _ = _deps()
        scheduler, repo = _scheduler([_fixed_config()], deps)
        repo.claim_result = False
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["triggered"], 0)
        self.assertEqual(repo.finished, [])

    def test_interval_first_run_initialized_then_due(self):
        deps, _ = _deps()
        config = _fixed_config(
            schedule_mode="last_run_interval",
            push_times=[],
            window_start_time="00:00",
            window_end_time="23:59",
        )
        scheduler, repo = _scheduler([config], deps)
        asyncio.run(scheduler.tick(now=_AT))
        # 首跑：next_run_at 为空 → 初始化为当前分钟并立即 due
        self.assertEqual(len(repo.next_run_set), 1)
        self.assertEqual(summary_triggered(repo), 1)

    def test_interval_not_due_when_next_run_in_future(self):
        deps, _ = _deps()
        config = _fixed_config(
            schedule_mode="last_run_interval",
            push_times=[],
            window_start_time="00:00",
            window_end_time="23:59",
            next_run_at="2099-01-01T00:00:00",
        )
        scheduler, repo = _scheduler([config], deps)
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["triggered"], 0)

    def test_interval_outside_window_skipped(self):
        deps, _ = _deps()
        config = _fixed_config(
            schedule_mode="last_run_interval",
            push_times=[],
            window_start_time="23:00",
            window_end_time="23:30",
        )
        scheduler, repo = _scheduler([config], deps)
        summary = asyncio.run(scheduler.tick(now=_AT))
        self.assertEqual(summary["triggered"], 0)


def summary_triggered(repo) -> int:
    return int(bool(repo.claims))


if __name__ == "__main__":
    unittest.main()
