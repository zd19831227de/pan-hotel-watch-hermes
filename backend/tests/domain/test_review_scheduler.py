"""评价调度器：半自动/全自动链路改为每日 HH:MM 定时触发（2026-09-16 需求）。

- 触发条件：now >= 当日 auto_process_time 且当日未成功跑过（last_auto_processed_at）。
- 防重复：成功（fetch ok）后写游标，当日不再触发；失败在 30 分钟容忍窗口内重试。
- 开关保留：auto_fetch_enabled=False 的店铺/平台不参与定时触发。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.types import ReviewItemInput


class _FakeReviewAdapter:
    platform = "ctrip"

    def __init__(self) -> None:
        self.fetches = 0

    async def fetch_reviews(self):
        self.fetches += 1
        return review_adapters.PlatformReviewSnapshot(
            hotel_id="H1",
            reviews=[
                ReviewItemInput(review_id="r1", user_nickname="张三", score=4.5, content="很好")
            ],
            pending_total=1,
        )

    async def fetch_history_replies(self, limit: int = 200):
        return []

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        return {"ok": True}


async def _noop_push(chat_id: str, card: dict) -> dict:
    return {"ok": True}


def _fixed_noon_now() -> datetime:
    """固定到当日 12:00：11:45 在容忍窗口内（视为到点）、23:30 必未到点。"""
    return datetime.combine(date.today(), time(12, 0))


class ReviewSchedulerScheduledDueTests(unittest.TestCase):
    """_scheduled_run_due 纯逻辑：到点/游标/错过窗口。"""

    def setUp(self) -> None:
        from app.domain.review_management import adapters

        adapters._ADAPTERS.clear()

    def _scheduler(self):
        from app.domain.review_management.scheduler import ReviewFetchScheduler

        return ReviewFetchScheduler(push_card=_noop_push, now_fn=_fixed_noon_now)

    def test_due_after_time_without_cursor(self) -> None:
        scheduler = self._scheduler()
        row = {"auto_process_time": "11:45", "last_auto_processed_at": None}
        self.assertTrue(scheduler._scheduled_run_due(row))

    def test_not_due_before_time(self) -> None:
        scheduler = self._scheduler()
        row = {"auto_process_time": "23:30", "last_auto_processed_at": None}
        self.assertFalse(scheduler._scheduled_run_due(row))

    def test_not_due_again_same_day_after_success(self) -> None:
        scheduler = self._scheduler()
        row = {
            "auto_process_time": "11:45",
            "last_auto_processed_at": datetime.combine(date.today(), time(11, 50)),
        }
        self.assertFalse(scheduler._scheduled_run_due(row))

    def test_due_again_next_day(self) -> None:
        scheduler = self._scheduler()
        row = {
            "auto_process_time": "11:45",
            "last_auto_processed_at": datetime.combine(
                date.today() - timedelta(days=1), time(11, 50)
            ),
        }
        self.assertTrue(scheduler._scheduled_run_due(row))

    def test_due_again_after_cross_midnight_completion(self) -> None:
        # 昨晚报次拖过午夜、游标落在今日 00:01（完成时刻）：今日档位尚未跑，
        # 到点后应继续触发（2026-10-08 巡检 F1：按日期比较会误判「今天已跑」
        # 整晚跳过，形成 48 小时一跑）。
        scheduler = self._scheduler()
        row = {
            "auto_process_time": "11:45",
            "last_auto_processed_at": datetime.combine(date.today(), time(0, 1)),
        }
        self.assertTrue(scheduler._scheduled_run_due(row))

    def test_missed_beyond_window_not_due(self) -> None:
        scheduler = self._scheduler()
        # 12:00 已超过 11:00+30min 容忍窗口：错过本日时段，不补跑。
        row = {"auto_process_time": "11:00", "last_auto_processed_at": None}
        self.assertFalse(scheduler._scheduled_run_due(row))

    def test_within_window_after_time_is_due(self) -> None:
        scheduler = self._scheduler()
        row = {"auto_process_time": "11:45", "last_auto_processed_at": None}
        self.assertTrue(scheduler._scheduled_run_due(row))

    def test_invalid_time_falls_back_to_default(self) -> None:
        scheduler = self._scheduler()
        # 异常值回落默认 23:00（未到点）而不是炸掉 tick。
        row = {"auto_process_time": "25:99", "last_auto_processed_at": None}
        self.assertFalse(scheduler._scheduled_run_due(row))


class ReviewSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        # .env 开启真实评价适配器时清空注册表，保证 not_ready 用例不连真实浏览器。
        from app.domain.review_management import adapters

        adapters._ADAPTERS.clear()

    def test_tick_without_enabled_platforms_does_nothing(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler

                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, now_fn=_fixed_noon_now
                )
                result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], [])
                self.assertEqual(await review_repository.list_review_tasks(), [])

            asyncio.run(scenario())

    def test_tick_runs_scheduled_pipeline_once_per_day(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                adapter = _FakeReviewAdapter()
                review_adapters.register_review_adapter(adapter)
                await review_repository.update_review_auto_config(
                    platform="ctrip",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True,
                        auto_reply_mode="off",
                        auto_process_time="11:45",
                    ),
                )

                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, now_fn=_fixed_noon_now
                )
                result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], ["ctrip"])
                self.assertEqual(result["results"][0]["fetch"]["status"], "ok")
                self.assertEqual(adapter.fetches, 1)
                # 成功后写游标：当日第二个 tick 不再触发。
                config = await review_repository.get_review_auto_config(platform="ctrip")
                self.assertIsNotNone(config["last_auto_processed_at"])
                again = await scheduler.tick()
                self.assertEqual(again["due_platforms"], [])
                self.assertEqual(adapter.fetches, 1)

            asyncio.run(scenario())

    def test_tick_skips_store_with_switch_off(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                # 开关保留：auto_fetch_enabled=False 不参与定时触发。
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=False,
                        auto_reply_mode="semi_auto",
                        auto_process_time="11:45",
                    ),
                )
                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, now_fn=_fixed_noon_now
                )
                result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], [])

            asyncio.run(scenario())

    def test_tick_runs_store_binding_and_marks_cursor(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                from types import SimpleNamespace
                from unittest import mock

                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                adapter = _FakeReviewAdapter()
                review_adapters.register_review_adapter(adapter)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True,
                        auto_reply_mode="semi_auto",
                        auto_process_time="11:45",
                    ),
                )
                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, now_fn=_fixed_noon_now
                )
                store = SimpleNamespace(
                    get=lambda sid: SimpleNamespace(
                        browser_session_id=sid,
                        platform="ctrip",
                        session_port=9301,
                        intended_use="ebk",
                        platform_account_id=sid + "_acct",
                    ),
                    list=lambda: [],
                )
                with mock.patch(
                    "app.domain.browser.sessions.get_browser_session_store",
                    return_value=store,
                ):
                    result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], ["ctrip"])
                self.assertEqual(result["results"][0]["fetch"]["status"], "ok")
                self.assertEqual(adapter.fetches, 1)
                binding = await review_repository.get_store_binding(
                    browser_session_id="ctrip_a"
                )
                self.assertIsNotNone(binding["last_auto_processed_at"])

            asyncio.run(scenario())

    def test_tick_survives_adapter_not_ready_and_retries_within_window(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                await review_repository.update_review_auto_config(
                    platform="meituan",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True,
                        auto_process_time="11:45",
                    ),
                )
                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, now_fn=_fixed_noon_now
                )
                result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], ["meituan"])
                self.assertEqual(result["results"][0]["fetch"]["status"], "not_ready")
                # 失败不写游标：容忍窗口内下一 tick 仍会重试。
                again = await scheduler.tick()
                self.assertEqual(again["due_platforms"], ["meituan"])

            asyncio.run(scenario())

    def test_worker_entrypoint_importable(self) -> None:
        from app.workers import review_scheduler

        self.assertTrue(callable(review_scheduler.main))


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-management.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
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
        review_adapters._ADAPTERS.clear()
        self._tmp.cleanup()


def _reload_db(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


if __name__ == "__main__":
    unittest.main()


class ReviewSchedulerMutexTests(unittest.TestCase):
    """评价抓取与 EBK 推送互斥（2026-09-02 需求：优先 EBK 推送）。"""

    def setUp(self) -> None:
        from app.domain.review_management import adapters

        adapters._ADAPTERS.clear()

    def test_tick_yields_when_mutex_held(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                import asyncio as aio

                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                await review_repository.update_review_auto_config(
                    platform="meituan",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True,
                        auto_process_time="11:45",
                    ),
                )
                lock = aio.Lock()
                await lock.acquire()
                try:
                    scheduler = ReviewFetchScheduler(
                        push_card=_noop_push, mutex=lock, now_fn=_fixed_noon_now
                    )
                    result = await scheduler.tick()
                    self.assertTrue(result.get("skipped_for_push"))
                    self.assertEqual(result["due_platforms"], [])
                finally:
                    lock.release()

            asyncio.run(scenario())

    def test_tick_holds_mutex_during_platform_run(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                import asyncio as aio

                _reload_modules(db_url)
                from app.domain.review_management.scheduler import ReviewFetchScheduler
                from app.domain.review_management.types import ReviewAutoConfigPatch

                await review_repository.update_review_auto_config(
                    platform="meituan",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True,
                        auto_process_time="11:45",
                    ),
                )
                lock = aio.Lock()
                held: list[bool] = []

                async def fake_run(platform, *, source="manual"):
                    held.append(lock.locked())
                    return {"platform": platform, "fetch": {"status": "ok"}}

                scheduler = ReviewFetchScheduler(
                    push_card=_noop_push, mutex=lock, now_fn=_fixed_noon_now
                )
                scheduler.run_platform_now = fake_run
                result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], ["meituan"])
                self.assertEqual(held, [True])
                self.assertFalse(lock.locked())

            asyncio.run(scenario())
