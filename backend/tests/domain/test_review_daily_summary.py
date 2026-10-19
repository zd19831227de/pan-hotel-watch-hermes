"""评价日报合并卡（2026-09-21 需求：三平台一张卡推送）。

- 所有到点店铺结算（成功或错过窗口）后，每个目标群推一张「评价日报」
  合并卡，当日每群去重；0 待回复平台也占一行（解决「只看到飞猪一张卡」）。
- 逐店「自动回复完成」/「待回复提醒」卡在调度链路停推（由合并卡取代）；
  手动/API 链路行为不变（process_pending_reviews 默认仍推逐店卡）。
- 自动处理结果落 review_tasks(task_type='auto_process')，供日报卡与审计。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.adapters import PlatformReviewSnapshot


def _fixed_noon_now() -> datetime:
    return datetime.combine(date.today(), time(12, 0))


class _DailyBatchAdapter:
    """够用的假适配器：fetch 恒定 pending_total，submit 返回预置 batch 行。"""

    def __init__(self, platform: str, batch_rows, pending_total: int) -> None:
        self.platform = platform
        self._batch = list(batch_rows)
        self._pending = pending_total

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        return PlatformReviewSnapshot(
            hotel_id="H1", reviews=[], pending_total=self._pending
        )

    async def fetch_history_replies(self, limit: int = 200):
        return []

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        return {"ok": True, "review_id": review_id}

    async def submit_pending_replies(self, generator, *, max_count: int = 20):
        return [dict(row) for row in self._batch]


class DailySummaryCardBuilderTests(unittest.TestCase):
    def _build(self, entries):
        from app.domain.review_management.cards import (
            build_review_daily_summary_card,
        )

        return build_review_daily_summary_card(
            entries=entries, day_label="09-20"
        )

    def _content(self, card) -> str:
        return card["elements"][0]["content"]

    def test_card_lists_all_platforms_including_zero_pending(self):
        card = self._build(
            [
                {
                    "platform": "ctrip",
                    "store_label": "静里携程ebk",
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 0},
                    "process": {
                        "status": "ok",
                        "submitted": 0,
                        "skipped": 0,
                        "by_category": {"good": 0, "medium": 0, "bad": 0},
                        "skip_reasons": {},
                    },
                },
                {
                    "platform": "fliggy",
                    "store_label": None,
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 64},
                    "process": {
                        "status": "ok",
                        "submitted": 0,
                        "skipped": 1,
                        "by_category": {"good": 0, "medium": 0, "bad": 0},
                        "skip_reasons": {"no_template": 1},
                    },
                },
            ]
        )
        content = self._content(card)
        self.assertIn("评价日报", card["header"]["title"]["content"])
        self.assertIn("携程", content)
        self.assertIn("飞猪", content)
        self.assertIn("无待回复", content)
        self.assertIn("未处理 1", content)
        self.assertIn("回复模板缺失×1", content)

    def test_card_warns_when_no_template_blocks_all_replies(self):
        # 2026-10-08 巡检 F2：全自动店铺 submitted=0 且 skipped 全因 no_template
        # 时，日报卡必须醒目告警（橙色头卡+运营可读文案+补模板指引），
        # 不能静默绿色「已结算」——否则待回复静默积压（生产曾恒 71 条）。
        card = self._build(
            [
                {
                    "platform": "fliggy",
                    "store_label": None,
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 71},
                    "process": {
                        "status": "ok",
                        "submitted": 0,
                        "skipped": 2,
                        "by_category": {"good": 0, "medium": 0, "bad": 0},
                        "skip_reasons": {"no_template": 2},
                    },
                }
            ]
        )
        content = self._content(card)
        self.assertIn("回复模板缺失", content)
        self.assertIn("补齐该店铺的回复模板", content)
        self.assertEqual(card["header"]["template"], "orange")

    def test_card_warns_when_all_page_access_failures(self):
        # 2026-10-08 巡检 F3：浏览器掉线导致全部页面会话失败（submitted=0
        # 且有失败行）时同样转橙提醒，而不是绿色静默。
        card = self._build(
            [
                {
                    "platform": "ctrip",
                    "store_label": None,
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 60},
                    "process": {
                        "status": "ok",
                        "submitted": 0,
                        "skipped": 3,
                        "by_category": {"good": 0, "medium": 0, "bad": 0},
                        "skip_reasons": {
                            "评价页访问失败（TimeoutError），请确认平台浏览器在线后重试。": 3,
                        },
                    },
                }
            ]
        )
        self.assertEqual(card["header"]["template"], "orange")

    def test_card_marks_fetch_failure(self):
        card = self._build(
            [
                {
                    "platform": "meituan",
                    "store_label": None,
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "failed", "error": "browser not ready"},
                    "process": None,
                }
            ]
        )
        self.assertIn("抓取失败", self._content(card))
        self.assertEqual(card["header"]["template"], "orange")

    def test_card_renders_submitted_breakdown_and_semi_auto(self):
        card = self._build(
            [
                {
                    "platform": "ctrip",
                    "store_label": None,
                    "mode": "full_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 2},
                    "process": {
                        "status": "ok",
                        "submitted": 2,
                        "skipped": 0,
                        "by_category": {"good": 1, "medium": 1, "bad": 0},
                        "skip_reasons": {},
                    },
                },
                {
                    "platform": "meituan",
                    "store_label": None,
                    "mode": "semi_auto",
                    "chat_id": "chat_main",
                    "fetch": {"status": "completed", "pending_count": 5},
                    "process": None,
                },
            ]
        )
        content = self._content(card)
        self.assertIn("已回复 2", content)
        self.assertIn("好 1", content)
        self.assertIn("半自动", content)


class DailySummarySchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        review_adapters._ADAPTERS.clear()

    def tearDown(self) -> None:
        review_adapters._ADAPTERS.clear()

    def test_tick_pushes_one_combined_card_after_all_stores_settled(self):
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import (
                    ReviewFetchScheduler,
                )

                await _store("ctrip", "ctrip_a", chat="chat_main")
                await _store("meituan", "meituan_a", chat="chat_main")
                await _store("fliggy", "fliggy_a", chat="chat_main")
                review_adapters.register_review_adapter(
                    _DailyBatchAdapter(
                        "ctrip",
                        [
                            {"ok": True, "score": 4.8},
                            {"ok": True, "score": 3.5},
                        ],
                        pending_total=2,
                    )
                )
                review_adapters.register_review_adapter(
                    _DailyBatchAdapter("meituan", [], pending_total=0)
                )
                review_adapters.register_review_adapter(
                    _DailyBatchAdapter(
                        "fliggy",
                        [{"ok": False, "reason": "no_template", "score": 4.2}],
                        pending_total=64,
                    )
                )

                pushes: list[tuple[str, dict]] = []

                async def pusher(chat_id: str, card: dict) -> dict:
                    pushes.append((chat_id, card))
                    return {"ok": True}

                scheduler = ReviewFetchScheduler(
                    push_card=pusher, now_fn=_fixed_noon_now
                )
                with mock.patch(
                    "app.domain.browser.sessions.get_browser_session_store",
                    return_value=_fake_session_store(),
                ):
                    result = await scheduler.tick()
                    await scheduler.tick()

                self.assertEqual(
                    result["due_platforms"],
                    ["ctrip", "meituan", "fliggy"],
                )
                # 合并卡取代逐店卡：整轮只有一张推送。
                self.assertEqual(len(pushes), 1)
                chat_id, card = pushes[0]
                self.assertEqual(chat_id, "chat_main")
                self.assertIn("评价日报", card["header"]["title"]["content"])
                content = card["elements"][0]["content"]
                self.assertIn("携程", content)
                self.assertIn("美团", content)
                self.assertIn("飞猪", content)
                self.assertIn("已回复 2", content)
                self.assertIn("无待回复", content)
                self.assertIn("未处理 1", content)

                # 自动处理结果落库：三平台各一条 auto_process 任务。
                tasks = await review_repository.list_review_tasks(limit=100)
                outcomes = [
                    t
                    for t in tasks
                    if t["task_type"] == "auto_process"
                ]
                self.assertEqual(len(outcomes), 3)
                submitted_by_platform = {
                    t["platform"]: t["result"]["submitted"]
                    for t in outcomes
                }
                self.assertEqual(
                    submitted_by_platform,
                    {"ctrip": 2, "meituan": 0, "fliggy": 0},
                )

                # 当日去重：同日第二个 tick 不再推。
                await scheduler.tick()
                self.assertEqual(len(pushes), 1)

            asyncio.run(scenario())

    def test_no_combined_card_until_all_slots_settled(self):
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import (
                    ReviewFetchScheduler,
                )

                await _store("ctrip", "ctrip_a", chat="chat_main", slot="11:45")
                # 飞猪 23:40 未到点：整晚未结算，不推合并卡。
                await _store("fliggy", "fliggy_a", chat="chat_main", slot="23:40")
                review_adapters.register_review_adapter(
                    _DailyBatchAdapter(
                        "ctrip", [{"ok": True, "score": 4.8}], pending_total=1
                    )
                )
                review_adapters.register_review_adapter(
                    _DailyBatchAdapter("fliggy", [], pending_total=0)
                )

                pushes: list[tuple[str, dict]] = []

                async def pusher(chat_id: str, card: dict) -> dict:
                    pushes.append((chat_id, card))
                    return {"ok": True}

                scheduler = ReviewFetchScheduler(
                    push_card=pusher, now_fn=_fixed_noon_now
                )
                with mock.patch(
                    "app.domain.browser.sessions.get_browser_session_store",
                    return_value=_fake_session_store(),
                ):
                    await scheduler.tick()
                self.assertEqual(pushes, [])

            asyncio.run(scenario())

    def test_combined_card_per_chat_only_lists_its_own_stores(self):
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.review_management.scheduler import (
                    ReviewFetchScheduler,
                )

                await _store("ctrip", "ctrip_a", chat="chat_a")
                await _store("meituan", "meituan_a", chat="chat_b")
                await _store("fliggy", "fliggy_a", chat="chat_b")
                for platform, rows, pending in (
                    ("ctrip", [{"ok": True, "score": 4.8}], 1),
                    ("meituan", [], 0),
                    ("fliggy", [], 0),
                ):
                    review_adapters.register_review_adapter(
                        _DailyBatchAdapter(platform, rows, pending_total=pending)
                    )

                pushes: list[tuple[str, dict]] = []

                async def pusher(chat_id: str, card: dict) -> dict:
                    pushes.append((chat_id, card))
                    return {"ok": True}

                scheduler = ReviewFetchScheduler(
                    push_card=pusher, now_fn=_fixed_noon_now
                )
                with mock.patch(
                    "app.domain.browser.sessions.get_browser_session_store",
                    return_value=_fake_session_store(),
                ):
                    await scheduler.tick()

                self.assertEqual(
                    sorted(chat for chat, _ in pushes), ["chat_a", "chat_b"]
                )
                by_chat = {chat: card for chat, card in pushes}
                self.assertIn("携程", by_chat["chat_a"]["elements"][0]["content"])
                self.assertNotIn(
                    "美团", by_chat["chat_a"]["elements"][0]["content"]
                )
                content_b = by_chat["chat_b"]["elements"][0]["content"]
                self.assertIn("美团", content_b)
                self.assertIn("飞猪", content_b)
                self.assertNotIn("携程", content_b)

            asyncio.run(scenario())


def _fake_session_store():
    """绕开店铺浏览器 CDP 端口解析（同 test_review_scheduler 的做法）。"""

    def get(sid: str):
        platform = next(
            (p for p in ("ctrip", "meituan", "fliggy") if sid.startswith(p)),
            "ctrip",
        )
        return SimpleNamespace(
            browser_session_id=sid,
            platform=platform,
            session_port=9301,
            intended_use="ebk",
            platform_account_id=sid + "_acct",
        )

    return SimpleNamespace(get=get, list=lambda: [])


async def _store(
    platform: str,
    browser_session_id: str,
    *,
    chat: str,
    slot: str = "11:45",
) -> None:
    from app.domain.review_management.types import ReviewAutoConfigPatch

    await review_repository.upsert_store_binding(
        platform=platform,
        browser_session_id=browser_session_id,
        platform_account_id=f"{browser_session_id}_acct",
        display_name="",
        feishu_chat_id=chat,
    )
    await review_repository.update_store_config(
        platform=platform,
        browser_session_id=browser_session_id,
        patch=ReviewAutoConfigPatch(
            auto_fetch_enabled=True,
            auto_reply_mode="full_auto",
            auto_process_time=slot,
        ),
    )


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
