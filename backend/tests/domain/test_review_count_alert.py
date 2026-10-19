"""半自动收敛为「未回复数量提醒」+ fetch 只记数量不堆正文。

2026-09-03：评价列表已从控制台移除（待回复走 OTA 后台）。半自动不再逐条推
审批卡/生成草案，改为抓取后只读未回复数量、推一张数量提醒卡。fetch 不再
持久化评价正文（避免 review_items 无限堆积），只在店铺绑定上记录 last_pending_count。
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


class _CountAdapter:
    platform = "ctrip"

    def __init__(self, pending_total: int, reviews=None) -> None:
        self.pending_total = pending_total
        self.reviews = list(reviews or [])
        self.submitted: list[tuple[str, str]] = []

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        return PlatformReviewSnapshot(
            hotel_id="H1", reviews=self.reviews, pending_total=self.pending_total
        )

    async def fetch_history_replies(self, limit: int = 200):
        return []

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        self.submitted.append((review_id, reply_content))
        return {"ok": True, "review_id": review_id}


class FetchRecordsCountTests(unittest.TestCase):
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
                adapter = _CountAdapter(
                    pending_total=3,
                    reviews=[
                        ReviewItemInput(review_id="r1", score=4.5, content="很好"),
                        ReviewItemInput(review_id="r2", score=2.0, content="差"),
                    ],
                )
                result = await review_service.fetch_and_store_reviews(
                    platform="ctrip", browser_session_id="ctrip_a", adapter=adapter
                )
                self.assertEqual(result["status"], "ok")
                # 未回复数量记到店铺绑定上。
                binding = await review_repository.get_store_binding(
                    browser_session_id="ctrip_a"
                )
                self.assertEqual(binding["last_pending_count"], 3)
                # 不再持久化评价正文：review_items 不增长。
                rows = await review_repository.list_reviews(platform="ctrip")
                self.assertEqual(rows, [])

            asyncio.run(scenario())


class SemiAutoCountAlertTests(unittest.TestCase):
    def test_semi_auto_pushes_one_count_alert_not_per_review_cards(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                    feishu_chat_id="oc_group",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                adapter = _CountAdapter(pending_total=5)
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
                # 只推一张数量提醒卡，推到监控组飞书群。
                self.assertEqual(len(pushed), 1)
                self.assertEqual(pushed[0][0], "oc_group")
                header = pushed[0][1]["header"]["title"]["content"]
                self.assertIn("待回复", header)
                # 不再创建逐条审批行：半自动只推数量提醒卡。

            asyncio.run(scenario())

    def test_semi_auto_zero_pending_pushes_no_alert(self) -> None:
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
                pushed: list = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append(card)
                    return {"ok": True}

                result = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_CountAdapter(pending_total=0),
                    push_card=push,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(pushed, [])  # 0 条不推卡

            asyncio.run(scenario())


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-count.db"
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
