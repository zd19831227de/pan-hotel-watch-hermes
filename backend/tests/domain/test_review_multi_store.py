"""评价管理多店铺（按 EBK 浏览器账号绑定）改造测试。

业务目标（2026-09-03 需求）：
- 评价管理与 EBK 一致，按「绑定的平台 EBK 浏览器账号」区分店铺；每店铺独立
  配置（自动回复模式/定时抓取/飞书推送机器人 chat_id）。
- 适配器按店铺浏览器的 CDP 端口 attach，不再只能连平台默认端口；
  绑定账号不存在/平台不符时必须显式报错，绝不能静默回落默认端口抓错店铺。
- 评价/模板/任务按店铺隔离；不传店铺时保持平台级视图（兼容旧前端与 Hermes）。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.types import (
    ReplyTemplateInput,
    ReviewAutoConfigPatch,
    ReviewItemInput,
)


def _session(sid: str, *, platform: str = "ctrip", port: int = 9221, use: str = "ebk"):
    return SimpleNamespace(
        browser_session_id=sid,
        platform=platform,
        session_port=port,
        intended_use=use,
        platform_account_id=sid + "_acct",
        display_name="店铺" + sid,
    )


class _FakeStore:
    def __init__(self, *sessions) -> None:
        self._sessions = {s.browser_session_id: s for s in sessions}

    def get(self, sid: str):
        return self._sessions[sid]

    def list(self):
        return list(self._sessions.values())


class _PortAwareFakeAdapter:
    """接受 session_port 构造参数的 fake 适配器（对齐真实适配器签名）。"""

    platform = "ctrip"

    def __init__(self, session_port: int | None = None, pending_total: int = 1) -> None:
        self.session_port = session_port
        self.reviews: list[ReviewItemInput] = []
        self.pending_total = pending_total

    async def fetch_reviews(self):
        return review_adapters.PlatformReviewSnapshot(
            hotel_id="H_" + str(self.session_port),
            reviews=list(self.reviews),
            pending_total=self.pending_total,
        )

    async def fetch_history_replies(self, limit: int = 200):
        return []

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        return {"ok": True, "review_id": review_id}


class AdapterStoreResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def tearDown(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def test_adapter_binds_to_store_session_port(self) -> None:
        review_adapters.register_review_adapter(_PortAwareFakeAdapter())
        store = _FakeStore(_session("ctrip_a", port=9301), _session("ctrip_b", port=9302))
        with mock.patch(
            "app.domain.browser.sessions.get_browser_session_store", return_value=store
        ):
            adapter_a = review_adapters.get_review_adapter("ctrip", "ctrip_a")
            adapter_b = review_adapters.get_review_adapter("ctrip", "ctrip_b")
        self.assertEqual(adapter_a.session_port, 9301)
        self.assertEqual(adapter_b.session_port, 9302)
        # 同店铺复用实例（保留 content cache 等会话态）。
        with mock.patch(
            "app.domain.browser.sessions.get_browser_session_store", return_value=store
        ):
            again = review_adapters.get_review_adapter("ctrip", "ctrip_a")
        self.assertIs(again, adapter_a)

    def test_missing_store_session_raises_instead_of_default_port(self) -> None:
        review_adapters.register_review_adapter(_PortAwareFakeAdapter())
        store = _FakeStore()
        with mock.patch(
            "app.domain.browser.sessions.get_browser_session_store", return_value=store
        ):
            with self.assertRaises(review_adapters.ReviewAdapterError) as ctx:
                review_adapters.get_review_adapter("ctrip", "ghost")
        self.assertIn("不存在", str(ctx.exception))

    def test_platform_mismatch_store_session_raises(self) -> None:
        review_adapters.register_review_adapter(_PortAwareFakeAdapter())
        store = _FakeStore(_session("mt_a", platform="meituan", port=9521))
        with mock.patch(
            "app.domain.browser.sessions.get_browser_session_store", return_value=store
        ):
            with self.assertRaises(review_adapters.ReviewAdapterError):
                review_adapters.get_review_adapter("ctrip", "mt_a")

    def test_no_session_keeps_platform_default_adapter(self) -> None:
        fake = _PortAwareFakeAdapter()
        review_adapters.register_review_adapter(fake)
        adapter = review_adapters.get_review_adapter("ctrip")
        self.assertIs(adapter, fake)
        self.assertIsNone(adapter.session_port)


class RepositoryStoreScopingTests(unittest.TestCase):
    def test_store_binding_upsert_and_effective_config(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                legacy = await review_repository.update_review_auto_config(
                    platform="ctrip",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="off"),
                )
                # 未绑定店铺：effective 配置回落平台级旧配置。
                effective = await review_repository.get_effective_review_config(
                    platform="ctrip", browser_session_id=None
                )
                self.assertIsNone(effective["browser_session_id"])
                self.assertEqual(effective["auto_reply_mode"], legacy["auto_reply_mode"])

                binding = await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                    feishu_chat_id="oc_store_a",
                )
                self.assertEqual(binding["browser_session_id"], "ctrip_a")
                # 绑定店铺后：effective 配置来自店铺绑定（未设置的字段用默认值）。
                effective = await review_repository.get_effective_review_config(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                self.assertEqual(effective["browser_session_id"], "ctrip_a")
                self.assertEqual(effective["auto_reply_mode"], "off")
                self.assertEqual(effective["approval_chat_id"], "oc_store_a")

                updated = await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(
                        auto_reply_mode="full_auto", auto_fetch_enabled=True
                    ),
                )
                self.assertEqual(updated["auto_reply_mode"], "full_auto")
                self.assertTrue(updated["auto_fetch_enabled"])

                bindings = await review_repository.list_store_bindings(platform="ctrip")
                self.assertEqual([b["browser_session_id"] for b in bindings], ["ctrip_a"])

            asyncio.run(scenario())

    def test_reviews_and_templates_scoped_by_store(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                items_a = [ReviewItemInput(review_id="ra1", score=4.5, content="好")]
                items_b = [ReviewItemInput(review_id="rb1", score=2.0, content="差")]
                await review_repository.upsert_review_items(
                    platform="ctrip",
                    hotel_id="HA",
                    items=items_a,
                    browser_session_id="ctrip_a",
                )
                await review_repository.upsert_review_items(
                    platform="ctrip",
                    hotel_id="HB",
                    items=items_b,
                    browser_session_id="ctrip_b",
                )
                only_a = await review_repository.list_reviews(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                self.assertEqual({r["review_id"] for r in only_a}, {"ra1"})
                self.assertTrue(
                    all(r["browser_session_id"] == "ctrip_a" for r in only_a)
                )
                # 平台级视图（不传店铺）不过滤，兼容旧前端与 Hermes。
                all_rows = await review_repository.list_reviews(platform="ctrip")
                self.assertEqual({r["review_id"] for r in all_rows}, {"ra1", "rb1"})

                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="A店好评",
                        content="感谢 {user_nickname}",
                    ),
                    browser_session_id="ctrip_a",
                )
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="平台通用好评",
                        content="感谢入住",
                    ),
                )
                tpl_a = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                # 店铺视图 = 店铺自有模板 + 平台级通用模板（NULL 店铺，兜底用），
                # 店铺自有排前。
                self.assertEqual(
                    [t["name"] for t in tpl_a], ["A店好评", "平台通用好评"]
                )
                tpl_all = await review_repository.list_templates(platform="ctrip")
                self.assertEqual(len(tpl_all), 2)

            asyncio.run(scenario())


class ServiceStoreFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def tearDown(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def test_fetch_and_process_are_store_scoped(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                adapter = _PortAwareFakeAdapter()
                adapter.reviews = [
                    ReviewItemInput(review_id="ra1", score=4.5, content="很好"),
                ]
                result = await review_service.fetch_and_store_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                )
                self.assertEqual(result["status"], "ok")
                tasks = await review_repository.list_review_tasks(platform="ctrip")
                self.assertEqual(tasks[0]["browser_session_id"], "ctrip_a")

                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                    feishu_chat_id="oc_store_a",
                )
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="A店好评",
                        content="感谢 {user_nickname}",
                    ),
                    browser_session_id="ctrip_a",
                )
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(auto_reply_mode="semi_auto"),
                )
                pushed: list[str] = []

                async def push(chat_id: str, card: dict) -> dict:
                    pushed.append(chat_id)
                    return {"ok": True}

                process = await review_service.process_pending_reviews(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=adapter,
                    push_card=push,
                )
                self.assertEqual(process["status"], "ok")
                # 半自动收敛为数量提醒：推一张提醒卡到店铺绑定的飞书群，不再逐条审批。
                self.assertEqual(process["pushed_cards"], 1)
                self.assertEqual(pushed, ["oc_store_a"])

            asyncio.run(scenario())

class MigrationLegacyColumnsTests(unittest.TestCase):
    """旧库升级：改造前建的表没有 browser_session_id，迁移必须补列建表。"""

    def test_legacy_tables_get_store_columns_and_binding_table(self) -> None:
        import sqlalchemy

        from app.domain.review_management.migrations import (
            _create_table_statements,
            ensure_review_management_schema,
        )

        # 模拟旧版建表：去掉新列，手工建出旧结构。
        legacy_statements = [
            stmt.replace("browser_session_id VARCHAR(128) NULL,\n            ", "")
            for stmt in _create_table_statements("sqlite")
            if stmt.startswith("CREATE TABLE") and "review_store_bindings" not in stmt
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            sync_engine = sqlalchemy.create_engine(f"sqlite:///{db_path.as_posix()}")
            with sync_engine.begin() as conn:
                for stmt in legacy_statements:
                    conn.execute(sqlalchemy.text(stmt))
                from sqlalchemy import inspect as sa_inspect

                columns_before = {
                    table: {col["name"] for col in sa_inspect(conn).get_columns(table)}
                    for table in (
                        "review_items",
                        "reply_templates",
                        "review_reply_approvals",
                        "review_tasks",
                    )
                }
            self.assertTrue(
                all(
                    "browser_session_id" not in cols
                    for cols in columns_before.values()
                )
            )
            sync_engine.dispose()

            from sqlalchemy.ext.asyncio import create_async_engine as create_aengine

            aengine = create_aengine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

            async def run() -> None:
                async with aengine.connect() as conn:
                    await ensure_review_management_schema(conn)
                await aengine.dispose()

            asyncio.run(run())

            from sqlalchemy import inspect as sa_inspect

            verify_engine = sqlalchemy.create_engine(f"sqlite:///{db_path.as_posix()}")
            with verify_engine.begin() as conn:
                inspector = sa_inspect(conn)
                for table in (
                    "review_items",
                    "reply_templates",
                    "review_reply_approvals",
                    "review_tasks",
                ):
                    columns = {col["name"] for col in inspector.get_columns(table)}
                    self.assertIn("browser_session_id", columns)
                self.assertTrue(inspector.has_table("review_store_bindings"))
            verify_engine.dispose()


class SchedulerStoreTests(unittest.TestCase):
    def test_tick_runs_due_store_bindings_and_skips_legacy(self) -> None:
        from datetime import date, datetime, time as dt_time

        from app.domain.review_management.scheduler import ReviewFetchScheduler

        def fixed_noon() -> datetime:
            return datetime.combine(date.today(), dt_time(12, 0))

        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                # 定时触发：auto_process_time=11:45 已到点（固定 now=12:00 在窗口内）。
                await review_repository.update_store_config(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True, auto_process_time="11:45"
                    ),
                )
                await review_repository.update_review_auto_config(
                    platform="ctrip",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True, auto_process_time="11:45"
                    ),
                )
                runs: list[dict] = []

                async def fake_pipeline(**kwargs):
                    runs.append(kwargs)
                    return {"fetch": {"status": "ok"}, "process": None}

                scheduler = ReviewFetchScheduler(now_fn=fixed_noon)
                with mock.patch.object(
                    review_service, "run_review_pipeline", fake_pipeline
                ):
                    result = await scheduler.tick()
                # 有店铺绑定的平台只按绑定跑，不再叠加平台级默认轮次。
                self.assertEqual(result["due_platforms"], ["ctrip"])
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0].get("browser_session_id"), "ctrip_a")
                self.assertEqual(runs[0].get("source"), "scheduler")

                # ctrip 店铺当日已成功（游标已写），只剩平台无绑定的 meituan 到点一轮。
                runs.clear()
                await review_repository.update_review_auto_config(
                    platform="meituan",
                    patch=ReviewAutoConfigPatch(
                        auto_fetch_enabled=True, auto_process_time="11:45"
                    ),
                )
                with mock.patch.object(
                    review_service, "run_review_pipeline", fake_pipeline
                ):
                    result = await scheduler.tick()
                self.assertEqual(result["due_platforms"], ["meituan"])
                meituan_runs = [r for r in runs if r.get("platform") == "meituan"]
                self.assertEqual(len(meituan_runs), 1)
                self.assertIsNone(meituan_runs[0].get("browser_session_id"))

            asyncio.run(scenario())


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-multi-store.db"
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
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


if __name__ == "__main__":
    unittest.main()
