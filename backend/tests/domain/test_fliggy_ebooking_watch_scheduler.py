from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.domain.ctrip_ebooking_watch.push_throttle import (
    get_last_push,
    reset_push_throttle_state,
)
from app.domain.fliggy_ebooking_watch.repository import (
    FliggyWatchGroupRuntime,
    FliggyWatchGroupCreate,
    FliggyWatchGroupPatch,
)
from app.domain.fliggy_ebooking_watch.scheduler import FliggyEbookingWatchScheduler
from app.domain.fliggy_ebooking_watch.service import FliggyEbookingWatchService
from app.domain.platform_ebk_watch.push_policy import (
    platform_throttle_key,
    reset_failure_alert_state,
)


def _registered_session_store():
    class FakeStore:
        def get(self, session_id: str):
            return SimpleNamespace(platform="fliggy", browser_session_id=session_id)

    return patch(
        "app.domain.fliggy_ebooking_watch.service.get_browser_session_store",
        return_value=FakeStore(),
    )


def _runtime_group(**overrides: Any) -> FliggyWatchGroupRuntime:
    values: dict[str, Any] = {
        "group_key": "fliggy_ebk_main",
        "name": "飞猪 EBK 盯盘",
        "brief_title": "飞猪 EBK 数据中心盯盘",
        "platform_account_id": "fliggy-ebk-1",
        "browser_session_id": "browser-fliggy-ebk",
        "ctrip_watch_group_key": "ctrip_store_main",
        "timezone": "Asia/Shanghai",
        "schedule_mode": "fixed_slots",
        "schedule_start_time": "06:00",
        "schedule_end_time": "01:00",
        "schedule_interval_minutes": 120,
        "auto_poll_enabled": True,
        "enabled": True,
        "last_polled_at": None,
    }
    values.update(overrides)
    return FliggyWatchGroupRuntime(**values)


def _anchor_runtime(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "group_key": "ctrip_store_main",
        "name": "门店A·携程盯盘",
        "feishu_webhook_url": "https://open.feishu.cn/webhook/ctrip",
        "feishu_chat_id": "oc_ctrip_main",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _anchor_lookup(anchor: Any = None):
    async def fake_get(*, watch_group_key: str) -> Any:
        if anchor is not None and watch_group_key == anchor.group_key:
            return anchor
        return anchor

    return patch(
        "app.domain.fliggy_ebooking_watch.service.get_data_center_watch_group_runtime",
        fake_get,
    )


class FliggySchedulerTickTests(unittest.TestCase):
    """A1 — tick 遍历 due 组、节流与异常隔离。"""

    def test_tick_runs_due_groups_and_reports_counts(self) -> None:
        scheduler_module = importlib.import_module(
            "app.domain.fliggy_ebooking_watch.scheduler"
        )
        group = _runtime_group()
        calls: list[str] = []

        async def fake_list_due() -> list[Any]:
            return [group]

        class FakeService:
            async def run_watch_group(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs["group"].group_key)
                return {
                    "watch_group_key": kwargs["group"].group_key,
                    "status": "success",
                    "summary": "飞猪 EBK 已读取并保存。",
                }

        def fake_throttle(group_key: str) -> tuple[bool, str]:
            return False, ""

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.list_due_fliggy_watch_groups",
                fake_list_due,
            ),
            patch.object(
                scheduler_module, "get_fliggy_ebooking_watch_service", FakeService
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.should_throttle_scheduled_push",
                fake_throttle,
            ),
        ):
            result = asyncio.run(scheduler_module.FliggyEbookingWatchScheduler(tick_seconds=1).tick())
        self.assertEqual(result["due_group_count"], 1)
        self.assertEqual(calls, ["fliggy_ebk_main"])
        self.assertEqual(result["results"][0]["status"], "success")

    def test_tick_uses_platform_scoped_throttle_key(self) -> None:
        """节流 key 必须带平台前缀，避免与携程组/飞猪组同名互串冷却。"""

        group = _runtime_group()
        seen_keys: list[str] = []

        async def fake_list_due() -> list[Any]:
            return [group]

        def fake_throttle(group_key: str) -> tuple[bool, str]:
            seen_keys.append(group_key)
            return False, ""

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.list_due_fliggy_watch_groups",
                fake_list_due,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.should_throttle_scheduled_push",
                fake_throttle,
            ),
            patch.object(
                FliggyEbookingWatchService,
                "run_watch_group",
                _fake_run_watch_group,
            ),
        ):
            asyncio.run(FliggyEbookingWatchScheduler(tick_seconds=1).tick())
        self.assertEqual(
            seen_keys, [platform_throttle_key("fliggy", "fliggy_ebk_main")]
        )

    def test_tick_throttles_group_inside_cooldown_window(self) -> None:
        group = _runtime_group()

        async def fake_list_due() -> list[Any]:
            return [group]

        def fake_throttle(group_key: str) -> tuple[bool, str]:
            return True, "手动推送冷却窗口内，跳过定时推送。"

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.list_due_fliggy_watch_groups",
                fake_list_due,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.should_throttle_scheduled_push",
                fake_throttle,
            ),
        ):
            result = asyncio.run(FliggyEbookingWatchScheduler(tick_seconds=1).tick())
        self.assertEqual(result["results"][0]["status"], "throttled")

    def test_tick_continues_when_one_group_fails(self) -> None:
        good = _runtime_group(group_key="fliggy_good")
        bad = _runtime_group(group_key="fliggy_bad")

        async def fake_list_due() -> list[Any]:
            return [bad, good]

        def fake_throttle(group_key: str) -> tuple[bool, str]:
            return False, ""

        class FakeService:
            async def run_watch_group(self, **kwargs: Any) -> dict[str, Any]:
                if kwargs["group"].group_key == "fliggy_bad":
                    raise RuntimeError("browser attach failed")
                return {
                    "watch_group_key": kwargs["group"].group_key,
                    "status": "success",
                    "summary": "ok",
                }

        scheduler_module = importlib.import_module(
            "app.domain.fliggy_ebooking_watch.scheduler"
        )
        with (
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.list_due_fliggy_watch_groups",
                fake_list_due,
            ),
            patch.object(
                scheduler_module, "get_fliggy_ebooking_watch_service", FakeService
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.scheduler.should_throttle_scheduled_push",
                fake_throttle,
            ),
        ):
            result = asyncio.run(FliggyEbookingWatchScheduler(tick_seconds=1).tick())
        statuses = {item["watch_group_key"]: item["status"] for item in result["results"]}
        self.assertEqual(statuses["fliggy_bad"], "failed")
        self.assertEqual(statuses["fliggy_good"], "success")


async def _fake_run_watch_group(self, **kwargs: Any) -> dict[str, Any]:
    return {
        "watch_group_key": kwargs["group"].group_key,
        "status": "success",
        "summary": "ok",
    }


class FliggyWatchServicePushTests(unittest.TestCase):
    """锚定携程组投递 + P0 失败告警冷却。"""

    def setUp(self) -> None:
        reset_push_throttle_state()
        reset_failure_alert_state()

    def tearDown(self) -> None:
        reset_push_throttle_state()
        reset_failure_alert_state()

    def test_success_push_uses_ctrip_anchor_webhook_and_records_throttle(self) -> None:
        group = _runtime_group(brief_title=None)
        marked: list[str] = []
        pushed_titles: list[str] = []
        pushed_cards: list[dict[str, Any]] = []

        async def fake_read(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "success",
                "summary": "飞猪 EBK 已读取 7 个数据集并保存。",
                "snapshot_id": 42,
                "dataset_count": 7,
                "payload": {"datasets": {}},
            }

        async def fake_mark(**kwargs: Any) -> None:
            marked.append(kwargs["watch_group_key"])

        async def fake_push_card(**kwargs: Any) -> dict[str, Any]:
            pushed_titles.append(kwargs["title"])
            pushed_cards.append(kwargs["card"])
            return SimpleNamespace(
                status="success",
                data={},
                model_dump=lambda mode: {"status": "success", "summary": "推送成功。"},
            )

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fake_read,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.mark_fliggy_watch_group_polled",
                fake_mark,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.push_interactive_card_to_feishu_webhook",
                fake_push_card,
            ),
            _anchor_lookup(_anchor_runtime()),
            _registered_session_store(),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["snapshot_id"], 42)
        self.assertEqual(marked, ["fliggy_ebk_main"])
        self.assertEqual(result["feishu_push"]["status"], "success")
        # 标题带店铺（携程组名）+ 平台，三平台同群投递时可区分；卡片正文含店铺行。
        self.assertEqual(pushed_titles, ["门店A·携程盯盘·飞猪 EBK 数据中心盯盘"])
        card_text = str(pushed_cards[0]["elements"][0]["text"]["content"])
        self.assertIn("门店A·携程盯盘", card_text)
        # 成功推送按平台前缀 key 记录，供人工推送冷却判断。
        self.assertIsNotNone(
            get_last_push(platform_throttle_key("fliggy", "fliggy_ebk_main"))
        )

    def test_failure_sends_alert_once_then_cooldown(self) -> None:
        group = _runtime_group()
        push_calls: list[str] = []

        async def fake_read(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "error",
                "summary": "飞猪 EBK 读取失败：登录失效。",
                "snapshot_id": None,
                "dataset_count": 0,
                "payload": {},
            }

        async def fake_push_card(**kwargs: Any) -> dict[str, Any]:
            push_calls.append(kwargs["title"])
            return SimpleNamespace(
                status="success",
                data={},
                model_dump=lambda mode: {"status": "success", "summary": "推送成功。"},
            )

        async def fake_mark(**kwargs: Any) -> None:
            raise AssertionError("失败结果不得推进调度游标")

        service = FliggyEbookingWatchService()
        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fake_read,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.mark_fliggy_watch_group_polled",
                fake_mark,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.push_interactive_card_to_feishu_webhook",
                fake_push_card,
            ),
            _anchor_lookup(_anchor_runtime()),
            _registered_session_store(),
        ):
            first = asyncio.run(
                service.run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
            second = asyncio.run(
                service.run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["feishu_push"]["status"], "success")
        self.assertEqual(first["feishu_push"].get("alert_kind"), "failure_alert")
        # 冷却窗口内的第二次失败：不再重复提醒。
        self.assertEqual(second["feishu_push"]["status"], "alert_cooldown")
        self.assertEqual(len(push_calls), 1)

    def test_failure_without_anchor_reports_config_required(self) -> None:
        group = _runtime_group(ctrip_watch_group_key=None)

        async def fake_read(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "human_required",
                "summary": "登录失效。",
                "snapshot_id": None,
                "dataset_count": 0,
                "payload": {},
            }

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fake_read,
            ),
            _registered_session_store(),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(result["feishu_push"]["status"], "config_required")

    def test_success_without_anchor_webhook_returns_config_required(self) -> None:
        group = _runtime_group()

        async def fake_read(**kwargs: Any) -> dict[str, Any]:
            return {
                "status": "success",
                "summary": "ok",
                "snapshot_id": 1,
                "dataset_count": 7,
                "payload": {"datasets": {}},
            }

        marked: list[str] = []

        async def fake_mark(**kwargs: Any) -> None:
            marked.append(kwargs["watch_group_key"])

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fake_read,
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.mark_fliggy_watch_group_polled",
                fake_mark,
            ),
            _anchor_lookup(_anchor_runtime(feishu_webhook_url="")),
            _registered_session_store(),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="operator", source="api"
                )
            )
        self.assertEqual(result["feishu_push"]["status"], "config_required")
        self.assertEqual(marked, ["fliggy_ebk_main"])

    def test_fallback_account_resolution_rejects_non_fliggy_session(self) -> None:
        """旧数据按 platform_account_id 兜底解析时必须校验平台，防跨平台串号。"""

        group = _runtime_group(browser_session_id=None, platform_account_id="shared-acc")

        class FakeStore:
            def get(self, session_id: str):
                return SimpleNamespace(
                    platform="ctrip", browser_session_id=session_id
                )

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.get_browser_session_store",
                return_value=FakeStore(),
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.get_browser_session_id_for_platform_account",
                lambda account_id: "ctrip-session-1",
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                _forbid_read,
            ),
            _anchor_lookup(_anchor_runtime()),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(result["status"], "human_required")
        self.assertIn("飞猪 EBK 浏览器账号", result["summary"])

    def test_run_watch_group_unregistered_session_degrades_to_human_required(self) -> None:
        group = _runtime_group(browser_session_id="stale-session")

        class FakeStore:
            def get(self, session_id: str):
                raise KeyError(session_id)

        async def fail_read(**kwargs: Any) -> dict[str, Any]:
            raise AssertionError("read must not be called for unregistered session")

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.get_browser_session_store",
                return_value=FakeStore(),
            ),
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fail_read,
            ),
            _anchor_lookup(_anchor_runtime()),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(result["status"], "human_required")
        self.assertIn("会话未注册", result["summary"])

    def test_run_watch_group_read_failure_reports_error(self) -> None:
        group = _runtime_group()

        async def fake_read(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("chrome not running")

        with (
            patch(
                "app.domain.fliggy_ebooking_watch.service.read_fliggy_platform_snapshot",
                fake_read,
            ),
            _anchor_lookup(_anchor_runtime()),
            _registered_session_store(),
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group(
                    group=group, actor_id="scheduler", source="scheduler"
                )
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("读取失败", result["summary"])

    def test_run_watch_group_now_handles_not_found_and_disabled(self) -> None:
        async def fake_get(*, watch_group_key: str) -> None:
            return None

        with patch(
            "app.domain.fliggy_ebooking_watch.service.get_fliggy_watch_group_runtime",
            fake_get,
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group_now(
                    watch_group_key="missing"
                )
            )
        self.assertEqual(result["status"], "not_found")

        async def fake_get_disabled(*, watch_group_key: str) -> Any:
            return _runtime_group(enabled=False)

        with patch(
            "app.domain.fliggy_ebooking_watch.service.get_fliggy_watch_group_runtime",
            fake_get_disabled,
        ):
            result = asyncio.run(
                FliggyEbookingWatchService().run_watch_group_now(
                    watch_group_key="fliggy_ebk_main"
                )
            )
        self.assertEqual(result["status"], "skipped")


async def _forbid_read(**kwargs: Any) -> dict[str, Any]:
    raise AssertionError("read must not be called without a valid fliggy session")


class FliggyWatchScheduleIntegrationTests(unittest.TestCase):
    """锚定携程组模型：建/改校验、唯一性、账号钩子与级联清理。"""

    def test_due_groups_follow_shared_schedule_and_auto_poll_gates(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_due"))
            created = asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="fliggy_due",
                        name="飞猪盯盘",
                        ctrip_watch_group_key="ctrip_due",
                        schedule_start_time="00:00",
                        schedule_end_time="23:59",
                        schedule_interval_minutes=60,
                        auto_poll_enabled=True,
                    )
                )
            )
            self.assertIsNotNone(created)
            self.assertEqual(created["schedule_mode"], "fixed_slots")
            self.assertEqual(created["ctrip_watch_group_key"], "ctrip_due")
            asyncio.run(_seed_ctrip_group("ctrip_other"))
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="fliggy_no_poll",
                        name="未启用轮询",
                        ctrip_watch_group_key="ctrip_other",
                        auto_poll_enabled=False,
                    )
                )
            )
            due = asyncio.run(repository.list_due_fliggy_watch_groups())
            self.assertEqual([group.group_key for group in due], ["fliggy_due"])
            marked = asyncio.run(
                repository.mark_fliggy_watch_group_polled(
                    watch_group_key="fliggy_due"
                )
            )
            self.assertIsNotNone(marked["last_polled_at"])
            # fixed_slots + interval 60min：刚标记 polled 后同一 slot 内不再 due。
            due_after = asyncio.run(repository.list_due_fliggy_watch_groups())
            self.assertEqual([group.group_key for group in due_after], [])

    def test_create_rejects_missing_ctrip_anchor(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            with self.assertRaises(ValueError):
                asyncio.run(
                    repository.create_fliggy_watch_group(
                        FliggyWatchGroupCreate(
                            group_key="bad_anchor",
                            name="无锚点",
                            ctrip_watch_group_key="ctrip_missing",
                        )
                    )
                )

    def test_one_ctrip_anchor_allows_only_one_fliggy_schedule(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="mt_one",
                        name="门店A飞猪",
                        ctrip_watch_group_key="ctrip_store_a",
                    )
                )
            )
            with self.assertRaises(ValueError):
                asyncio.run(
                    repository.create_fliggy_watch_group(
                        FliggyWatchGroupCreate(
                            group_key="mt_two",
                            name="重复锚定",
                            ctrip_watch_group_key="ctrip_store_a",
                        )
                    )
                )

    def test_update_group_schedule_and_anchor_fields(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))
            asyncio.run(_seed_ctrip_group("ctrip_store_b"))
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="fliggy_sched",
                        name="飞猪盯盘",
                        ctrip_watch_group_key="ctrip_store_a",
                    )
                )
            )
            updated = asyncio.run(
                repository.update_fliggy_watch_group(
                    watch_group_key="fliggy_sched",
                    patch=FliggyWatchGroupPatch(
                        ctrip_watch_group_key="ctrip_store_b",
                        schedule_mode="last_run_interval",
                        schedule_start_time="08:00",
                        schedule_end_time="20:00",
                        schedule_interval_minutes=30,
                        auto_poll_enabled=True,
                    ),
                )
            )
            self.assertEqual(updated["ctrip_watch_group_key"], "ctrip_store_b")
            self.assertEqual(updated["schedule_mode"], "last_run_interval")
            self.assertEqual(updated["schedule_interval_minutes"], 30)
            self.assertTrue(updated["auto_poll_enabled"])

    def test_create_group_rejects_non_fliggy_browser_session(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            sessions = importlib.import_module("app.domain.browser.sessions")
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))

            class FakeStore:
                def get(self, session_id: str):
                    return SimpleNamespace(
                        platform="ctrip",
                        intended_use="ebk",
                        platform_account_id="ctrip-acc",
                        browser_session_id=session_id,
                    )

            with patch.object(
                repository,
                "get_browser_session_store",
                return_value=FakeStore(),
            ):
                with self.assertRaises(ValueError) as ctx:
                    asyncio.run(
                        repository.create_fliggy_watch_group(
                            FliggyWatchGroupCreate(
                                group_key="bad_bind",
                                name="错平台绑定",
                                ctrip_watch_group_key="ctrip_store_a",
                                browser_session_id="ctrip-session",
                            )
                        )
                    )
            self.assertIn("只能绑定飞猪浏览器账号", str(ctx.exception))

    def test_create_group_rejects_unsupported_timezone(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))
            with self.assertRaises(ValueError):
                asyncio.run(
                    repository.create_fliggy_watch_group(
                        FliggyWatchGroupCreate(
                            group_key="bad_timezone",
                            name="fliggy timezone",
                            ctrip_watch_group_key="ctrip_store_a",
                            timezone="UTC",
                        )
                    )
                )

    def test_browser_session_binding_hooks_clear_migrate_restore(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            sessions = importlib.import_module("app.domain.browser.sessions")
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))

            class FakeStore:
                def get(self, session_id: str):
                    return SimpleNamespace(
                        platform="fliggy",
                        intended_use="ebk",
                        platform_account_id="mt-acc-1",
                        browser_session_id=session_id,
                    )

            with patch.object(
                repository, "get_browser_session_store", return_value=FakeStore()
            ):
                asyncio.run(
                    repository.create_fliggy_watch_group(
                        FliggyWatchGroupCreate(
                            group_key="mt_hook",
                            name="钩子组",
                            ctrip_watch_group_key="ctrip_store_a",
                            browser_session_id="mt-session-1",
                            auto_poll_enabled=True,
                        )
                    )
                )
            snapshots = asyncio.run(
                repository.snapshot_fliggy_watch_bindings_for_browser_session(
                    platform_account_id="mt-acc-1",
                    browser_session_id="mt-session-1",
                )
            )
            self.assertEqual(len(snapshots), 1)
            cleared = asyncio.run(
                repository.clear_fliggy_watch_bindings_for_browser_session(
                    platform_account_id="mt-acc-1",
                    browser_session_id="mt-session-1",
                )
            )
            self.assertEqual(cleared, 1)
            after_clear = asyncio.run(
                repository.get_fliggy_watch_group_runtime(watch_group_key="mt_hook")
            )
            assert after_clear is not None
            self.assertIsNone(after_clear.browser_session_id)
            self.assertEqual(after_clear.platform_account_id, "")
            # 账号删除后只解绑，保留 enabled/auto_poll：下一轮 tick 失败走告警冷却，
            # 向锚定携程组机器人提醒，而非静默停摆（设计 ③）。
            self.assertTrue(after_clear.enabled)
            self.assertTrue(after_clear.auto_poll_enabled)
            self.assertEqual(after_clear.ctrip_watch_group_key, "ctrip_store_a")

            restored = asyncio.run(
                repository.restore_fliggy_watch_bindings(snapshots)
            )
            self.assertEqual(restored, 1)
            after_restore = asyncio.run(
                repository.get_fliggy_watch_group_runtime(watch_group_key="mt_hook")
            )
            assert after_restore is not None
            self.assertEqual(after_restore.browser_session_id, "mt-session-1")
            self.assertTrue(after_restore.enabled)

            migrated = asyncio.run(
                repository.migrate_fliggy_watch_bindings_for_browser_session(
                    old_platform_account_id="mt-acc-1",
                    browser_session_id="mt-session-1",
                    new_platform_account_id="mt-acc-2",
                )
            )
            self.assertEqual(migrated, 1)
            after_migrate = asyncio.run(
                repository.get_fliggy_watch_group_runtime(watch_group_key="mt_hook")
            )
            assert after_migrate is not None
            self.assertEqual(after_migrate.platform_account_id, "mt-acc-2")

    def test_delete_groups_by_ctrip_anchor_cascades(self) -> None:
        """v2 §2：直连组（有 shop_key）幸存且 legacy 键清空；纯挂载旧组级联删除。"""

        async def _insert_legacy_mount(group_key: str, anchor_key: str) -> None:
            from app.db.session import ensure_db_ready, session_scope
            from app.domain.fliggy_ebooking_watch.models import (
                FliggyEbookingWatchGroup,
            )

            await ensure_db_ready()
            async with session_scope() as session:
                session.add(
                    FliggyEbookingWatchGroup(
                        group_key=group_key,
                        name=f"{group_key} 纯挂载旧组",
                        platform_account_id=f"acc-{group_key}",
                        ctrip_watch_group_key=anchor_key,
                    )
                )

        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))
            asyncio.run(_seed_ctrip_group("ctrip_store_b"))
            # 直连组：挂载带店锚点自动派生 shop_key，随后清空 legacy 键。
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="mt_a",
                        name="A店飞猪",
                        ctrip_watch_group_key="ctrip_store_a",
                    )
                )
            )
            asyncio.run(
                repository.update_fliggy_watch_group(
                    watch_group_key="mt_a",
                    patch=FliggyWatchGroupPatch(ctrip_watch_group_key=""),
                )
            )
            # 纯挂载旧组：迁移前存量（shop_key 为空），直插模拟。
            asyncio.run(_insert_legacy_mount("mt_legacy", "ctrip_store_a"))
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="mt_b",
                        name="B店飞猪",
                        ctrip_watch_group_key="ctrip_store_b",
                    )
                )
            )
            removed = asyncio.run(
                repository.delete_fliggy_watch_groups_by_ctrip_anchor(
                    ctrip_watch_group_key="ctrip_store_a"
                )
            )
            self.assertEqual(
                [item["group_key"] for item in removed], ["mt_legacy"]
            )
            remaining = asyncio.run(repository.list_fliggy_watch_groups())
            self.assertEqual(
                sorted(item["group_key"] for item in remaining),
                ["mt_a", "mt_b"],
            )
            survived = next(
                item for item in remaining if item["group_key"] == "mt_a"
            )
            self.assertIsNone(survived["ctrip_watch_group_key"])
            self.assertIsNotNone(survived["shop_key"])



    def test_runtime_lookup_by_ctrip_anchor(self) -> None:
        with _isolated_sqlite_database():
            repository = importlib.import_module(
                "app.domain.fliggy_ebooking_watch.repository"
            )
            asyncio.run(_seed_ctrip_group("ctrip_store_a"))
            asyncio.run(
                repository.create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="mt_a",
                        name="A店飞猪",
                        ctrip_watch_group_key="ctrip_store_a",
                    )
                )
            )
            found = asyncio.run(
                repository.get_fliggy_watch_group_runtime_by_ctrip_anchor(
                    ctrip_watch_group_key="ctrip_store_a"
                )
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.group_key, "mt_a")


async def _seed_ctrip_group(group_key: str) -> None:
    from app.db.session import ensure_db_ready, session_scope
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
    from app.domain.shops import service as shops_service

    await ensure_db_ready()
    # v2 §2：锚点组带店铺归属，挂载组据此派生 shop_key。
    shop = await shops_service.create_shop({"name": f"{group_key} 店铺"})
    async with session_scope() as session:
        session.add(
            CtripEbookingWatchGroup(
                group_key=group_key,
                name=f"{group_key} 门店",
                platform_account_id=f"ctrip-acc-{group_key}",
                feishu_chat_id=f"oc_{group_key}",
                shop_key=shop["shop_key"],
            )
        )


class FliggyAnchorMigrationTests(unittest.TestCase):
    """旧组 feishu_chat_id 唯一匹配携程组时自动回填锚点。"""

    def test_backfill_anchors_legacy_group_by_chat_id(self) -> None:
        import sqlalchemy.ext.asyncio as saio

        engine = saio.create_async_engine("sqlite+aiosqlite:///:memory:")

        async def scenario() -> None:
            from sqlalchemy.ext.asyncio import AsyncConnection

            async with engine.begin() as connection:

                def _create(sync_conn) -> None:
                    from app.db.base import Base

                    Base.metadata.create_all(
                        sync_conn,
                        tables=[
                            Base.metadata.tables["ctrip_ebooking_watch_groups"],
                            Base.metadata.tables["fliggy_ebooking_watch_groups"],
                        ],
                    )

                await connection.run_sync(_create)
                await connection.exec_driver_sql(
                    "INSERT INTO ctrip_ebooking_watch_groups "
                    "(group_key, name, platform_account_id, feishu_chat_id, timezone, "
                    " auto_poll_enabled, enabled, retention_days, query_interval_minutes, "
                    " created_at, updated_at, daily_brief_push_time, daily_brief_enabled) VALUES "
                    "('ctrip_store_a','A店','ctrip-acc','oc_chat_a','Asia/Shanghai',"
                    "0,1,14,60,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'08:30',0)"
                )
                await connection.exec_driver_sql(
                    "INSERT INTO fliggy_ebooking_watch_groups "
                    "(group_key, name, platform_account_id, feishu_chat_id, timezone, "
                    " schedule_mode, schedule_start_time, schedule_end_time, "
                    " schedule_interval_minutes, auto_poll_enabled, enabled, "
                    " created_at, updated_at) VALUES "
                    "('mt_legacy','旧组','mt-acc','oc_chat_a','Asia/Shanghai',"
                    "'fixed_slots','06:00','01:00',120,0,1,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
                from app.domain.fliggy_ebooking_watch.migrations import (
                    ensure_fliggy_ebooking_watch_schema,
                )

                await ensure_fliggy_ebooking_watch_schema(connection)
                row = (
                    await connection.exec_driver_sql(
                        "SELECT ctrip_watch_group_key FROM fliggy_ebooking_watch_groups "
                        "WHERE group_key = 'mt_legacy'"
                    )
                ).fetchone()
                assert row is not None
                assert row[0] == "ctrip_store_a", row

        try:
            asyncio.run(scenario())
        finally:
            asyncio.run(engine.dispose())


class FliggyAnchorUniquenessTests(unittest.TestCase):
    """锚点唯一性 DB 约束：去重兜底 + 唯一索引拒绝重复锚点（应用层先查后写的竞态后盾）。"""

    def test_duplicate_anchors_deduped_then_unique_index_enforced(self) -> None:
        import sqlalchemy.ext.asyncio as saio
        from sqlalchemy import exc as sa_exc, select
        from app.db.base import Base
        from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
        from app.domain.fliggy_ebooking_watch.models import FliggyEbookingWatchGroup

        engine = saio.create_async_engine("sqlite+aiosqlite:///:memory:")
        SessionLocal = saio.async_sessionmaker(engine, expire_on_commit=False)

        async def scenario() -> None:
            async with engine.begin() as c:
                def _create(sync_conn) -> None:
                    Base.metadata.create_all(
                        sync_conn,
                        tables=[
                            Base.metadata.tables["ctrip_ebooking_watch_groups"],
                            Base.metadata.tables["fliggy_ebooking_watch_groups"],
                        ],
                    )

                await c.run_sync(_create)
            # 旧数据/手工改库残留：两行重复锚定同一携程组。
            async with SessionLocal() as s:
                s.add(CtripEbookingWatchGroup(
                    group_key="ctrip_a", name="A", platform_account_id="a",
                    feishu_chat_id="oc_a",
                ))
                s.add(FliggyEbookingWatchGroup(
                    group_key="mt_1", name="one", platform_account_id="a",
                    ctrip_watch_group_key="ctrip_a",
                ))
                s.add(FliggyEbookingWatchGroup(
                    group_key="mt_2", name="two", platform_account_id="a",
                    ctrip_watch_group_key="ctrip_a",
                ))
                await s.commit()
            async with engine.begin() as c:
                from app.domain.fliggy_ebooking_watch.migrations import (
                    ensure_fliggy_ebooking_watch_schema,
                )

                await ensure_fliggy_ebooking_watch_schema(c)
            # 迁移去重后只剩一个锚定行；唯一索引随后阻止再插入重复锚点。
            async with SessionLocal() as s:
                rows = list(
                    (await s.scalars(
                        select(FliggyEbookingWatchGroup).order_by(
                            FliggyEbookingWatchGroup.id
                        )
                    )).all()
                )
                anchored = [r for r in rows if r.ctrip_watch_group_key == "ctrip_a"]
                self.assertEqual(len(anchored), 1)
                s.add(FliggyEbookingWatchGroup(
                    group_key="mt_3", name="three", platform_account_id="a",
                    ctrip_watch_group_key="ctrip_a",
                ))
                with self.assertRaises(sa_exc.IntegrityError):
                    await s.commit()

        try:
            asyncio.run(scenario())
        finally:
            asyncio.run(engine.dispose())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.sqlite3"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        _reload_db_session()
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self.directory.cleanup()


def _reload_db_session() -> None:
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings_module.get_settings()
    db_session._database_ready_engine = None
    db_session.engine = db_session.create_async_engine(
        os.environ["DATABASE_URL"], pool_pre_ping=True
    )
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    importlib.import_module("app.domain.fliggy_ebooking_watch.models")
    importlib.import_module("app.domain.ctrip_ebooking_watch.models")


class FliggyWatchApiSmokeTests(unittest.TestCase):
    """A1 - manual-trigger / listing endpoint smoke tests (mocked)."""

    def test_list_and_run_now_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(importlib.import_module("app.main").app)

        async def fake_list_groups() -> list[dict[str, Any]]:
            return [
                {
                    "group_key": "fliggy_ebk_main",
                    "name": "飞猪 EBK 盯盘",
                    "ctrip_watch_group_key": "ctrip_store_main",
                    "schedule_mode": "fixed_slots",
                    "enabled": True,
                }
            ]

        async def fake_run_now(self, watch_group_key, **kwargs: Any) -> dict[str, Any]:
            return {
                "watch_group_key": watch_group_key,
                "status": "success",
                "summary": "飞猪 EBK 已读取并保存。",
            }

        async def fake_delete(*, watch_group_key: str) -> dict[str, Any] | None:
            return {"group_key": watch_group_key, "name": "fliggy watch"}

        with (
            patch(
                "app.api.v1.fliggy_ebooking_watch.list_fliggy_watch_groups",
                fake_list_groups,
            ),
            patch.object(
                FliggyEbookingWatchScheduler,
                "run_watch_group_now",
                fake_run_now,
            ),
            patch(
                "app.api.v1.fliggy_ebooking_watch.delete_fliggy_watch_group",
                fake_delete,
            ),
        ):
            listing = client.get("/api/v1/fliggy-ebk-watch/watch-groups")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(
                listing.json()["watch_groups"][0]["group_key"], "fliggy_ebk_main"
            )
            self.assertIn("scheduler_status", listing.json()["watch_groups"][0])

            run = client.post(
                "/api/v1/fliggy-ebk-watch/watch-groups/fliggy_ebk_main/run-now"
            )
            self.assertEqual(run.status_code, 200)
            self.assertEqual(run.json()["status"], "success")

            deleted = client.delete("/api/v1/fliggy-ebk-watch/watch-groups/fliggy_ebk_main")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json()["watch_group"]["group_key"], "fliggy_ebk_main")

    def test_delete_missing_group_returns_clean_404_text(self) -> None:
        """404 文案不得乱码（§2 问题⑨）。"""

        from fastapi.testclient import TestClient

        client = TestClient(importlib.import_module("app.main").app)

        async def fake_delete(*, watch_group_key: str) -> dict[str, Any] | None:
            return None

        with patch(
            "app.api.v1.fliggy_ebooking_watch.delete_fliggy_watch_group",
            fake_delete,
        ):
            deleted = client.delete(
                "/api/v1/fliggy-ebk-watch/watch-groups/missing_group"
            )
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(deleted.json()["detail"], "飞猪 EBK 监控组不存在。")

    def test_run_now_returns_404_for_missing_group(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(importlib.import_module("app.main").app)

        async def fake_run_now(self, watch_group_key, **kwargs: Any) -> dict[str, Any]:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到飞猪 EBK 监控组。",
            }

        with patch.object(
            FliggyEbookingWatchScheduler, "run_watch_group_now", fake_run_now
        ):
            run = client.post(
                "/api/v1/fliggy-ebk-watch/watch-groups/missing_group/run-now"
            )
            self.assertEqual(run.status_code, 404)

    def test_worker_and_router_registered(self) -> None:
        worker_source = Path(
            "backend/app/workers/read_only_scheduler.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FliggyEbookingWatchScheduler", worker_source)
        main_source = Path("backend/app/main.py").read_text(encoding="utf-8")
        self.assertIn("fliggy_ebooking_watch_router", main_source)


if __name__ == "__main__":
    unittest.main()
