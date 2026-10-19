from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.review_management.adapters import (
    HistoryReply,
    PlatformReviewSnapshot,
    ReviewAdapterNotReadyError,
)
from app.domain.review_management import service as review_service
from app.domain.review_management import repository as review_repository
from app.domain.review_management.types import (
    ReplyTemplateInput,
    ReviewAutoConfigPatch,
)


class _FakeReviewAdapter:
    """半自动=数量提醒、全自动=批量提交 的测试适配器。"""

    platform = "ctrip"

    def __init__(self, *, pending_total: int | None = 0, history=(), batch=None) -> None:
        self.pending_total = pending_total
        self.history = list(history)
        self._batch = batch
        self.fetched = 0

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        self.fetched += 1
        return PlatformReviewSnapshot(
            hotel_id="H1", reviews=[], pending_total=self.pending_total
        )

    async def fetch_history_replies(self, limit: int = 200):
        return self.history[:limit]

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        return {"ok": True, "review_id": review_id}

    async def submit_pending_replies(self, generator, *, max_count: int = 5):
        return list(self._batch or [])


def _good_history() -> list[HistoryReply]:
    return [
        HistoryReply(
            review_id="hr1", hotel_id="H1", score=5.0, content="好",
            reply_content="感谢 {user_nickname} 的认可",
        ),
        HistoryReply(
            review_id="hr2", hotel_id="H1", score=2.0, content="差",
            reply_content="抱歉给您带来不便",
        ),
    ]


class ReviewManagementServicePureTests(unittest.TestCase):
    def test_classify_review_score_thresholds(self) -> None:
        classify = review_service.classify_review
        self.assertEqual(classify(5), "good")
        self.assertEqual(classify(4), "good")
        self.assertEqual(classify(3.9), "medium")
        self.assertEqual(classify(3), "medium")
        self.assertEqual(classify(2.9), "bad")
        self.assertEqual(classify(None), "medium")

    def test_pick_reply_template_prefers_enabled_range_and_priority(self) -> None:
        templates = [
            {"id": 1, "category": "good", "enabled": False, "priority": 1, "score_range": None},
            {"id": 2, "category": "good", "enabled": True, "priority": 50, "score_range": {"min": 4.5, "max": 5}},
            {"id": 3, "category": "good", "enabled": True, "priority": 10, "score_range": {"min": 3, "max": 4.4}},
            {"id": 4, "category": "good", "enabled": True, "priority": 20, "score_range": None},
        ]
        self.assertEqual(review_service.pick_reply_template(templates, score=4.6)["id"], 2)
        self.assertEqual(review_service.pick_reply_template(templates, score=None)["id"], 4)
        self.assertIsNone(review_service.pick_reply_template([], score=4.5))

    def test_render_template_replaces_nickname_placeholder(self) -> None:
        self.assertEqual(review_service.render_template("感谢入住", None), "感谢入住")
        self.assertEqual(
            review_service.render_template("感谢您 {user_nickname}", "张三"),
            "感谢您 张三",
        )


class ReviewManagementServiceFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.domain.review_management import adapters
        adapters._ADAPTERS.clear()
        adapters._PORT_ADAPTERS.clear()

    def test_fetch_records_pending_count_not_review_content(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                result = await review_service.fetch_and_store_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(pending_total=3),
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pending_count"], 3)
                binding = await review_repository.get_store_binding(
                    browser_session_id="ctrip_a"
                )
                self.assertEqual(binding["last_pending_count"], 3)

            asyncio.run(scenario())

    def test_fetch_adapter_not_ready_returns_readable_status(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)

                class _NotReady:
                    platform = "ctrip"

                    async def fetch_reviews(self):
                        raise ReviewAdapterNotReadyError("ctrip 评价列表接口尚未接入")

                result = await review_service.fetch_and_store_reviews(
                    platform="ctrip", adapter=_NotReady()
                )
                self.assertEqual(result["status"], "not_ready")
                self.assertIn("未接入", result["error"])

            asyncio.run(scenario())

    def test_import_history_as_templates_groups_and_dedupes(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                result = await review_service.import_history_as_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(history=_good_history()),
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["templates_created"], 2)
                self.assertEqual(result["by_category"]["good"], 1)
                self.assertEqual(result["by_category"]["bad"], 1)

            asyncio.run(scenario())

    def test_process_pending_mode_off_does_nothing(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(),
                )
                self.assertEqual(result["status"], "mode_off")

            asyncio.run(scenario())

    def test_semi_auto_pushes_one_count_alert(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    feishu_chat_id="oc_group",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id, card):
                    pushed.append((chat_id, card))
                    return {"ok": True}

                await review_service.fetch_and_store_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(pending_total=5),
                )
                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(pending_total=5),
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pushed_cards"], 1)
                self.assertEqual(pushed[0][0], "oc_group")
                self.assertIn("5", pushed[0][1]["header"]["title"]["content"])

            asyncio.run(scenario())

    def test_semi_auto_requires_chat_id(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_FakeReviewAdapter(pending_total=2),
                )
                self.assertEqual(result["status"], "missing_chat_id")

            asyncio.run(scenario())

    def test_semi_auto_falls_back_to_monitor_group_feishu_chat_id(self) -> None:
        """店铺绑定缺飞书群时，回落到浏览器账号所在监控组的飞书推送群。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id, card):
                    pushed.append((chat_id, card))
                    return {"ok": True}

                async def fake_summaries(*, include_secrets=False):
                    return [
                        {
                            "group_key": "g_ctrip",
                            "browser_session_id": "ctrip_a",
                            "feishu_chat_id": "oc_monitor_group",
                        }
                    ]

                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_group_summaries",
                    fake_summaries,
                ):
                    await review_service.fetch_and_store_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_FakeReviewAdapter(pending_total=4),
                    )
                    result = await review_service.process_pending_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_FakeReviewAdapter(pending_total=4),
                        push_card=push,
                    )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pushed_cards"], 1)
                self.assertEqual(pushed[0][0], "oc_monitor_group")

            asyncio.run(scenario())

    def test_resolve_store_feishu_chat_id_follows_platform_anchor(self) -> None:
        """携程店铺=所在携程组；美团/飞猪店铺=挂载组的携程锚点组。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo
                from app.domain.meituan_ebooking_watch import repository as meituan_repo

                async def fake_summaries(*, include_secrets=False):
                    return [
                        {
                            "group_key": "g_ctrip",
                            "browser_session_id": "ctrip_a",
                            "feishu_chat_id": "oc_monitor_group",
                        },
                        {
                            "group_key": "g_other",
                            "browser_session_id": "ctrip_b",
                            "feishu_chat_id": None,
                        },
                    ]

                async def fake_meituan_groups():
                    return [
                        {
                            "group_key": "m1",
                            "browser_session_id": "meituan_a",
                            "ctrip_watch_group_key": "g_ctrip",
                        }
                    ]

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_group_summaries",
                    fake_summaries,
                ), patch.object(
                    meituan_repo,
                    "list_meituan_watch_groups",
                    fake_meituan_groups,
                ):
                    self.assertEqual(
                        await review_service.resolve_store_feishu_chat_id(
                            "ctrip", "ctrip_a"
                        ),
                        "oc_monitor_group",
                    )
                    self.assertEqual(
                        await review_service.resolve_store_feishu_chat_id(
                            "meituan", "meituan_a"
                        ),
                        "oc_monitor_group",
                    )
                    # 组未配飞书群 / 账号不属于任何组：返回 None（提示人工配置）。
                    self.assertIsNone(
                        await review_service.resolve_store_feishu_chat_id(
                            "ctrip", "ctrip_b"
                        )
                    )
                    self.assertIsNone(
                        await review_service.resolve_store_feishu_chat_id(
                            "meituan", "ghost"
                        )
                    )

            asyncio.run(scenario())

    def test_full_auto_batch_submits_and_resyncs_count(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip", category="good", name="好评",
                        content="感谢 {user_nickname}",
                    ),
                    browser_session_id="ctrip_a",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="full_auto"),
                )
                adapter = _FakeReviewAdapter(
                    pending_total=1, batch=[{"ok": True}, {"ok": False}]
                )
                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["submitted"], 1)

            asyncio.run(scenario())

    def test_full_auto_without_batch_adapter_is_not_ready(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="full_auto"),
                )

                class _NoBatch:
                    platform = "ctrip"

                    async def fetch_reviews(self):
                        return PlatformReviewSnapshot(hotel_id="H1", reviews=[], pending_total=0)

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_NoBatch(),
                )
                self.assertEqual(result["status"], "not_ready")

            asyncio.run(scenario())

    def test_fetch_wakes_browser_via_ebooking_service_before_adapter(self) -> None:
        """评价抓取前先复用 EBK 浏览器服务拉起/复用店铺浏览器，解决
        适配器裸 CDP attach 在浏览器未开时失败、开了也未必可控的问题。"""
        from types import SimpleNamespace

        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="acct_a",
                )
                alive_calls: list[tuple[str, str, bool]] = []

                class _FakeEbkService:
                    async def _get_or_create_page(
                        self, *, platform_account_id, browser_session_id, headless
                    ):
                        alive_calls.append(
                            (platform_account_id, browser_session_id, headless)
                        )
                        return SimpleNamespace(is_closed=lambda: False)

                fetch_order: list[str] = []

                class _TrackingAdapter(_FakeReviewAdapter):
                    async def fetch_reviews(self):
                        fetch_order.append("adapter")
                        return PlatformReviewSnapshot(
                            hotel_id="H1", reviews=[], pending_total=3
                        )

                def fake_service(platform: str):
                    if platform == "ctrip":
                        fetch_order.append("ensure_alive")
                        return _FakeEbkService()
                    return None

                with patch.object(
                    review_service, "_platform_ebooking_service", fake_service
                ):
                    result = await review_service.fetch_and_store_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_TrackingAdapter(),
                    )
                self.assertEqual(result["status"], "ok")
                # 拉起先于适配器抓取；headless=False 让操作员可见浏览器跳转。
                self.assertEqual(fetch_order, ["ensure_alive", "adapter"])
                self.assertEqual(alive_calls, [("acct_a", "ctrip_a", False)])

            asyncio.run(scenario())

    def test_fetch_tolerates_browser_wake_failure(self) -> None:
        """EBK 服务拉起失败（如 profile 被占用）不应阻断评价抓取——适配器仍尝试 CDP attach。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="acct_a",
                )

                class _BrokenService:
                    async def _get_or_create_page(self, **_):
                        raise RuntimeError("profile locked")

                with patch.object(
                    review_service,
                    "_platform_ebooking_service",
                    lambda p: _BrokenService() if p == "ctrip" else None,
                ):
                    result = await review_service.fetch_and_store_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_FakeReviewAdapter(pending_total=2),
                    )
                # 拉起失败被吞，适配器照常抓取成功。
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pending_count"], 2)

            asyncio.run(scenario())

    def test_process_with_cached_count_does_not_wake_browser(self) -> None:
        """semi_auto 且已有缓存计数：只推数量提醒卡，不读浏览器——不应无谓
        拉起可见浏览器窗口（2026-09-09 审查修复）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="acct_a",
                    feishu_chat_id="oc_group",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                await review_repository.set_last_fetched_at(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    pending_count=3,
                )

                def no_service(platform: str):
                    raise AssertionError("缓存计数路径不应触碰浏览器服务")

                async def push(chat_id, card):
                    return {"ok": True}

                with patch.object(review_service, "_platform_ebooking_service", no_service):
                    result = await review_service.process_pending_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_FakeReviewAdapter(pending_total=3),
                        push_card=push,
                    )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pushed_cards"], 1)

            asyncio.run(scenario())

    def test_process_without_cached_count_wakes_browser(self) -> None:
        """semi_auto 无缓存计数需要现场读取：此时才拉起店铺浏览器。"""
        from types import SimpleNamespace

        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="acct_a",
                    feishu_chat_id="oc_group",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                alive: list[str] = []

                class _FakeEbkService:
                    async def _get_or_create_page(self, **_):
                        alive.append("woken")
                        return SimpleNamespace(is_closed=lambda: False)

                async def push(chat_id, card):
                    return {"ok": True}

                with patch.object(
                    review_service,
                    "_platform_ebooking_service",
                    lambda p: _FakeEbkService() if p == "ctrip" else None,
                ):
                    result = await review_service.process_pending_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=_FakeReviewAdapter(pending_total=5),
                        push_card=push,
                    )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(alive, ["woken"])

            asyncio.run(scenario())


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-service.db"
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
        self._tmp.cleanup()


def _reload_db(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine, expire_on_commit=False
    )
    return importlib.reload(db_session)


if __name__ == "__main__":
    unittest.main()
