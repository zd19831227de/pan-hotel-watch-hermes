"""评价定时处理链路（2026-09-16 需求）：

- fetch 按实际读取到的未回复评价统计好/中/差（不落正文口径不变）。
- 全自动批处理后向监控组绑定飞书群推「已处理统计卡」：全部未回复评价已处理，
  其中好评 X、中评 Y，注意：差评 Z；数量以实际读取/提交结果为准。
- 半自动数量提醒卡带本轮实际读取的好/中/差明细。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.adapters import PlatformReviewSnapshot
from app.domain.review_management.types import ReviewAutoConfigPatch, ReviewItemInput


class _SnapshotAdapter:
    platform = "ctrip"

    def __init__(self, reviews=(), pending_total=None) -> None:
        self.reviews = list(reviews)
        self._pending_total = pending_total
        self.fetched = 0

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        self.fetched += 1
        pending = sum(1 for r in self.reviews if r.reply_status == "pending")
        return PlatformReviewSnapshot(
            hotel_id="H1",
            reviews=list(self.reviews),
            pending_total=self._pending_total
            if self._pending_total is not None
            else pending,
        )

    async def fetch_history_replies(self, limit: int = 200):
        return []

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        return {"ok": True, "review_id": review_id}


class _BatchAdapter(_SnapshotAdapter):
    def __init__(self, batch_rows, reviews=(), pending_total=None) -> None:
        super().__init__(reviews=reviews, pending_total=pending_total)
        self.batch_rows = list(batch_rows)

    async def submit_pending_replies(self, generator, *, max_count: int = 5):
        return list(self.batch_rows)


def _pending(review_id: str, score: float | None) -> ReviewItemInput:
    return ReviewItemInput(review_id=review_id, score=score, reply_status="pending")


async def _store(platform: str, browser_session_id: str, *, mode: str, feishu: str | None) -> None:
    await review_repository.upsert_store_binding(
        platform=platform,
        browser_session_id=browser_session_id,
        platform_account_id=f"{browser_session_id}_acct",
        display_name="店铺A",
        feishu_chat_id=feishu,
    )
    await review_repository.update_store_config(
        platform=platform,
        browser_session_id=browser_session_id,
        patch=ReviewAutoConfigPatch(auto_reply_mode=mode),
    )


def _card_text(card: dict) -> str:
    return "\n".join(
        str(el.get("content") or "")
        for el in card.get("elements", [])
        if isinstance(el, dict) and el.get("tag") == "markdown"
    )


class FetchPendingByCategoryTests(unittest.TestCase):
    def test_fetch_computes_pending_by_category_from_actual_read(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="off", feishu=None)
                adapter = _SnapshotAdapter(
                    reviews=[
                        _pending("r1", 4.5),
                        _pending("r2", 3.0),
                        _pending("r3", None),
                        _pending("r4", 2.0),
                        ReviewItemInput(
                            review_id="r5", score=4.8, reply_status="replied"
                        ),
                    ],
                    pending_total=6,  # 平台侧未回复总数可大于本轮实际读取数
                )
                result = await review_service.fetch_and_store_reviews(
                    platform="ctrip", browser_session_id="ctrip_a", adapter=adapter
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["pending_count"], 6)
                self.assertEqual(result["pending_read"], 4)
                self.assertEqual(
                    result["pending_by_category"],
                    {"good": 1, "medium": 2, "bad": 1},
                )
                # 口径不变：不落评价正文。
                self.assertEqual(
                    await review_repository.list_reviews(platform="ctrip"), []
                )

            asyncio.run(scenario())


class FullAutoSummaryCardTests(unittest.TestCase):
    def test_full_auto_pushes_summary_card_after_batch_submit(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="full_auto", feishu="oc_group")
                adapter = _BatchAdapter(
                    batch_rows=[
                        {"ok": True, "score": 4.5},
                        {"ok": True, "score": 2.0},
                        {"ok": True, "score": None},
                        {"ok": False, "reason": "no_template", "score": 3.0},
                    ],
                    pending_total=4,
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["submitted"], 3)
                self.assertEqual(
                    result["by_category"], {"good": 1, "medium": 1, "bad": 1}
                )
                # 一张统计卡，推到监控组绑定飞书群。
                self.assertEqual(len(pushed), 1)
                self.assertEqual(pushed[0][0], "oc_group")
                card = pushed[0][1]
                self.assertIn("自动回复", card["header"]["title"]["content"])
                text = _card_text(card)
                self.assertIn("全部未回复评价已处理", text)
                self.assertIn("好评 1 条", text)
                self.assertIn("中评 1 条", text)
                self.assertIn("注意：差评 1 条", text)
                self.assertIn("1 条未", text)
                self.assertEqual(result["summary_push"]["status"], "ok")

            asyncio.run(scenario())

    def test_full_auto_without_chat_id_records_missing_push(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="full_auto", feishu=None)
                adapter = _BatchAdapter(
                    batch_rows=[{"ok": True, "score": 5.0}, {"ok": True, "score": 1.0}],
                    pending_total=2,
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                async def _no_chat(platform, session_id):
                    return None

                with mock.patch.object(
                    review_service, "resolve_store_feishu_chat_id", _no_chat
                ):
                    result = await review_service.process_pending_reviews(
                        platform="ctrip",
                        browser_session_id="ctrip_a",
                        adapter=adapter,
                        push_card=push,
                    )
                # 回复照常提交，统计卡只记录未推送原因。
                self.assertEqual(result["submitted"], 2)
                self.assertEqual(pushed, [])
                self.assertEqual(result["summary_push"]["status"], "missing_chat_id")

            asyncio.run(scenario())

    def test_full_auto_empty_batch_pushes_nothing(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="full_auto", feishu="oc_group")
                adapter = _BatchAdapter(batch_rows=[], pending_total=0)
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["submitted"], 0)
                self.assertEqual(pushed, [])

            asyncio.run(scenario())


class SemiAutoBreakdownTests(unittest.TestCase):
    def test_semi_auto_alert_includes_breakdown_from_fresh_fetch(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="semi_auto", feishu="oc_group")
                await review_repository.set_last_fetched_at(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    pending_count=5,
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_SnapshotAdapter(pending_total=5),
                    push_card=push,
                    fetch_result={
                        "pending_by_category": {"good": 2, "medium": 1, "bad": 2},
                        "pending_read": 5,
                    },
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(pushed), 1)
                text = _card_text(pushed[0][1])
                self.assertIn("好评 2 条", text)
                self.assertIn("中评 1 条", text)
                self.assertIn("注意：差评 2 条", text)

            asyncio.run(scenario())

    def test_semi_auto_live_read_includes_breakdown(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="semi_auto", feishu="oc_group")
                adapter = _SnapshotAdapter(
                    reviews=[_pending("r1", 4.5), _pending("r2", 2.0)]
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(pushed), 1)
                text = _card_text(pushed[0][1])
                self.assertIn("好评 1 条", text)
                self.assertIn("注意：差评 1 条", text)

            asyncio.run(scenario())

    def test_semi_auto_manual_run_keeps_plain_count_card(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="semi_auto", feishu="oc_group")
                await review_repository.set_last_fetched_at(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    pending_count=3,
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_SnapshotAdapter(pending_total=3),
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(pushed), 1)
                # 无本轮读取明细时不编造分类数量。
                self.assertNotIn("好评", _card_text(pushed[0][1]))

            asyncio.run(scenario())


class PipelineBreakdownWiringTests(unittest.TestCase):
    def test_run_review_pipeline_feeds_breakdown_into_semi_alert(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await _store("ctrip", "ctrip_a", mode="semi_auto", feishu="oc_group")
                adapter = _SnapshotAdapter(
                    reviews=[_pending("r1", 4.5), _pending("r2", 1.5)]
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                result = await review_service.run_review_pipeline(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                    push_card=push,
                    source="scheduler",
                )
                self.assertEqual(result["fetch"]["status"], "ok")
                self.assertEqual(result["process"]["status"], "ok")
                self.assertEqual(len(pushed), 1)
                text = _card_text(pushed[0][1])
                self.assertIn("好评 1 条", text)
                self.assertIn("注意：差评 1 条", text)

            asyncio.run(scenario())


class SubmitPendingRepliesBatchSizeTests(unittest.TestCase):
    """2026-09-17：默认每批上限从 5 提到 20——每日一次定时不足以消化余量，
    美团实际只回复 5 条就停。一次跑完不可实现（单 session 约 14s × 60 次
    超时风险），按用户要求提到 20 条。"""

    def test_meituan_default_max_count_is_20(self) -> None:
        import inspect

        from app.domain.review_management.meituan_review_adapter import (
            MeituanReviewAdapter,
        )

        sig = inspect.signature(MeituanReviewAdapter.submit_pending_replies)
        self.assertEqual(sig.parameters["max_count"].default, 20)

    def test_fliggy_default_max_count_is_20(self) -> None:
        import inspect

        from app.domain.review_management.fliggy_review_adapter import (
            FliggyReviewAdapter,
        )

        sig = inspect.signature(FliggyReviewAdapter.submit_pending_replies)
        self.assertEqual(sig.parameters["max_count"].default, 20)

    def test_ctrip_default_max_count_is_20(self) -> None:
        import inspect

        from app.domain.review_management.ctrip_review_adapter import (
            CtripReviewAdapter,
        )

        sig = inspect.signature(CtripReviewAdapter.submit_pending_replies)
        self.assertEqual(sig.parameters["max_count"].default, 20)


class FliggyPendingCardParseTests(unittest.TestCase):
    """2026-09-17：飞猪完全不处理根因——submit_pending_replies 用
    _parse_fliggy_reviews(text)[0] 解析单卡，待回复卡无子维度行时切卡逻辑
    找不到卡起点（cards=[]）→ IndexError 被外层 except 吞掉，整批跳过。
    改用 _parse_fliggy_card 直接解析单卡文本。"""

    def test_parse_pending_card_without_dim_lines(self) -> None:
        """_parse_fliggy_card 能直接解析无子维度行的待回复卡（回归锁定）。"""
        from app.domain.review_management.fliggy_review_adapter import (
            _parse_fliggy_card,
        )

        card_text = "\n".join([
            "l***", "5.0", "很好住", "2026年9月15日发表",
            "芮享舒适房", "浏览次数 0", "有用次数 0", "发表回复",
        ])
        parsed = _parse_fliggy_card(card_text, None)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.score, 5.0)
        self.assertEqual(parsed.reply_status, "pending")
        self.assertIn("很好住", parsed.content or "")

    def test_submit_pending_replies_handles_card_without_dim_lines(self) -> None:
        """无 dim 行的待回复卡不应让 submit 抛 IndexError 被吞。

        修复前：_parse_fliggy_reviews(text) 返回 [] → [0] 抛 IndexError →
        外层 except 追加一条 ok=False → 整批非 ok，看起来「完全不处理」。
        """
        from app.domain.review_management.fliggy_review_adapter import (
            FliggyReviewAdapter,
        )

        card_text = "\n".join([
            "l***", "5.0", "很好住", "2026年9月15日发表",
            "芮享舒适房", "浏览次数 0", "有用次数 0", "发表回复",
        ])

        class _FakePage:
            async def eval_on_selector_all(self, sel: str, script: str):
                return True

            async def evaluate(self, script, *args):
                # mark_card_js：(handled) => {...}；handled 跨会话持久（真实 JS
                # 用 handled.includes(key) 跳过已处理卡）。不能用实例字段——
                # fake_with_review_page 每条会话 new 新 _FakePage。
                if "data-rv-target" in script and "for(const ta" in script:
                    key = card_text[:40]
                    handled = list(args[0]) if args else []
                    if key in handled:
                        return None
                    return {"key": key, "text": card_text}
                # target_still_js：回复后卡消失。
                if "document.querySelector('textarea" in script:
                    return False
                return None

            # Playwright 的 locator(sel) 是同步方法返回 Locator 对象（不是 coroutine）；
            # 写成 async def 会返回 coroutine 触发 .fill AttributeError 被外层吞掉。
            def locator(self, sel: str):
                class _L:
                    async def fill(self, t): return None
                    async def click(self): return None
                    async def count(self): return 1
                return _L()

            async def wait_for_timeout(self, ms): return None

        adapter = FliggyReviewAdapter()

        async def fake_with_review_page(fn, **kwargs):
            return await fn(_FakePage())

        adapter._with_review_page = fake_with_review_page

        # generator 是同步函数（同 service 层 _generator 口径）：
        # 适配器内 reply = generator({...}) 不 await，async def 会返回 coroutine
        # 触发 len(coroutine) TypeError 被外层吞掉。
        def gen(info):
            self.assertIsNotNone(info)
            self.assertEqual(info["score"], 5.0)
            # 飞猪要求 10-500 字，返回 ≥10 字。
            return "感谢您入住本酒店，期待下次再见面。"

        results = asyncio.run(adapter.submit_pending_replies(gen))
        self.assertEqual(len(results), 1)
        self.assertTrue(
            results[0].get("ok"), f"expected ok, got {results[0]}"
        )


class StoreLabelFallbackTests(unittest.TestCase):
    """2026-09-17：卡片店铺名错误——监控组路径保存 binding 时
    display_name 写空串，卡片回落到平台名（「携程」）而非真实店名。
    修复：store_label 为空时回落 browser_session.display_name。"""

    def test_full_auto_card_uses_session_display_name_when_binding_empty(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                # 模拟监控组保存路径：display_name 写空串
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="",
                    feishu_chat_id="oc_group",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="full_auto"),
                )
                adapter = _BatchAdapter(
                    batch_rows=[{"ok": True, "score": 5.0}],
                    pending_total=1,
                )
                pushed: list[tuple[str, dict]] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append((chat_id, card))
                    return {"ok": True}

                from app.domain.browser import sessions as sess_mod

                class _FakeSession:
                    platform = "ctrip"
                    intended_use = "ebk"
                    browser_session_id = "ctrip_a"
                    platform_account_id = "ctrip_a_acct"
                    display_name = "静里酒店"

                fake_store = mock.MagicMock()
                fake_store.get.return_value = _FakeSession()
                fake_store.list.return_value = [_FakeSession()]

                with mock.patch.object(
                    sess_mod, "get_browser_session_store", lambda: fake_store
                ):
                    with mock.patch.object(
                        review_service,
                        "_ensure_review_browser_alive",
                        new=mock.AsyncMock(),
                    ):
                        result = await review_service.process_pending_reviews(
                            platform="ctrip",
                            browser_session_id="ctrip_a",
                            adapter=adapter,
                            push_card=push,
                        )
                self.assertEqual(result["status"], "ok")
                text = _card_text(pushed[0][1])
                self.assertIn("静里酒店", text)
                # 不能回落成平台名「携程」
                self.assertNotIn("店铺：携程", text)

            asyncio.run(scenario())


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-auto-process.db"
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
