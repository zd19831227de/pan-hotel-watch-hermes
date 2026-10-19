from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.adapters import ReviewAdapterNotReadyError
from app.domain.review_management.types import ReviewAutoConfigPatch, ReviewItemInput


class _FakeReviewAdapter:
    platform = "ctrip"

    def __init__(self, reviews=(), history=(), submit_not_ready=False, pending_total=2) -> None:
        self.reviews = list(reviews)
        self.history = list(history)
        self.submit_not_ready = submit_not_ready
        self.pending_total = pending_total
        self.submitted: list[tuple[str, str]] = []

    async def fetch_reviews(self):
        return review_adapters.PlatformReviewSnapshot(
            hotel_id="H1", reviews=list(self.reviews), pending_total=self.pending_total
        )

    async def fetch_history_replies(self, limit: int = 200):
        return self.history[:limit]

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        if self.submit_not_ready:
            raise ReviewAdapterNotReadyError(
                "ctrip 提交回复接口尚未接入（等待逆向），未提交任何内容。"
            )
        self.submitted.append((review_id, reply_content))
        return {"ok": True, "review_id": review_id}


def _default_fake_adapter() -> _FakeReviewAdapter:
    return _FakeReviewAdapter(
        reviews=[
            ReviewItemInput(review_id="r1", user_nickname="张三", score=4.5, content="很好"),
            ReviewItemInput(review_id="r2", user_nickname="李四", score=2.0, content="太差"),
        ]
    )


class ReviewManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        # .env 开启真实评价适配器时清空注册表，保证 not_ready 用例不连真实浏览器。
        from app.domain.review_management import adapters

        adapters._ADAPTERS.clear()

    def test_fetch_returns_pending_count_not_review_list(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            fetch = client.post("/api/v1/reviews/fetch", json={"platform": "ctrip"})
            self.assertEqual(fetch.status_code, 200)
            self.assertEqual(fetch.json()["fetch"]["status"], "ok")
            # 半自动收敛后抓取只返回未回复数量，不再落评价正文。
            self.assertIn("pending_count", fetch.json()["fetch"])
            reviews = client.get("/api/v1/reviews", params={"platform": "ctrip"}).json()["reviews"]
            self.assertEqual(reviews, [])

    def test_fetch_platform_not_ready_returns_readable_payload(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(None):
            client = _reload_app(db_url)
            response = client.post("/api/v1/reviews/fetch", json={"platform": "meituan"})
            self.assertEqual(response.status_code, 200)
            payload = response.json()["fetch"]
            self.assertEqual(payload["status"], "not_ready")
            self.assertIn("未接入", payload["error"])

    def test_template_crud_and_import_history(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(
            _FakeReviewAdapter(
                history=[
                    review_adapters.HistoryReply(
                        review_id="h1",
                        hotel_id="H1",
                        score=4.8,
                        content="很好",
                        reply_content="感谢您的入住与认可",
                    )
                ]
            )
        ):
            client = _reload_app(db_url)
            created = client.post(
                "/api/v1/reviews/templates",
                json={
                    "platform": "ctrip",
                    "category": "good",
                    "name": "好评通用",
                    "content": "感谢您 {user_nickname} 的认可",
                },
            )
            self.assertEqual(created.status_code, 200)
            template_id = created.json()["template"]["id"]

            updated = client.post(
                "/api/v1/reviews/templates",
                json={
                    "id": template_id,
                    "platform": "ctrip",
                    "category": "good",
                    "name": "好评通用改",
                    "content": "谢谢 {user_nickname}",
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["template"]["name"], "好评通用改")

            imported = client.post(
                "/api/v1/reviews/import-history", json={"platform": "ctrip"}
            )
            self.assertEqual(imported.status_code, 200)
            self.assertEqual(imported.json()["result"]["templates_created"], 1)

            templates = client.get(
                "/api/v1/reviews/templates", params={"platform": "ctrip"}
            ).json()["templates"]
            self.assertEqual(len(templates), 2)

            deleted = client.delete(f"/api/v1/reviews/templates/{template_id}")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["deleted"])

    def test_process_pending_mode_off(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            client.post("/api/v1/reviews/fetch", json={"platform": "ctrip"})
            process = client.post("/api/v1/reviews/process-pending", json={"platform": "ctrip"})
            self.assertEqual(process.status_code, 200)
            self.assertEqual(process.json()["process"]["status"], "mode_off")

    def test_auto_config_get_put_and_semi_auto_count_alert(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            default = client.get("/api/v1/reviews/auto-config", params={"platform": "ctrip"})
            self.assertEqual(default.status_code, 200)
            self.assertEqual(default.json()["config"]["auto_reply_mode"], "off")

            updated = client.put(
                "/api/v1/reviews/auto-config",
                json={"platform": "ctrip", "auto_reply_mode": "semi_auto", "approval_chat_id": "oc_x"},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["config"]["auto_reply_mode"], "semi_auto")

            client.post("/api/v1/reviews/fetch", json={"platform": "ctrip"})
            with patch.object(review_service, "_default_push_card", _noop_push):
                process = client.post(
                    "/api/v1/reviews/process-pending", json={"platform": "ctrip"}
                )
            self.assertEqual(process.status_code, 200)
            # 半自动收敛为数量提醒：不再创建逐条审批行。
            self.assertEqual(process.json()["process"]["pushed_cards"], 1)

            tasks = client.get("/api/v1/reviews/tasks", params={"platform": "ctrip"})
            self.assertEqual(tasks.status_code, 200)
            self.assertGreaterEqual(len(tasks.json()["tasks"]), 1)

    def test_fetch_endpoint_does_not_trigger_auto_process(self) -> None:
        """半自动/全自动改为定时触发：抓取端点只抓取，不再顺带处理。"""
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            client.put(
                "/api/v1/reviews/auto-config",
                json={"platform": "ctrip", "auto_reply_mode": "semi_auto", "approval_chat_id": "oc_x"},
            )
            with patch.object(review_service, "_default_push_card", _noop_push):
                response = client.post("/api/v1/reviews/fetch", json={"platform": "ctrip"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["fetch"]["status"], "ok")
            self.assertEqual(response.json()["process"]["status"], "skipped")

    def test_auto_config_accepts_and_validates_auto_process_time(self) -> None:
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            updated = client.put(
                "/api/v1/reviews/auto-config",
                json={"platform": "ctrip", "auto_process_time": "23:00"},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["config"]["auto_process_time"], "23:00")

            invalid = client.put(
                "/api/v1/reviews/auto-config",
                json={"platform": "ctrip", "auto_process_time": "24:00"},
            )
            self.assertEqual(invalid.status_code, 422)


class ReviewPendingCountChatResolutionTests(unittest.TestCase):
    """飞书问答链路：pending-count 按群绑定的监控组解析店铺，不再回落平台级空配置。"""

    def test_pending_count_resolves_store_by_feishu_chat_id(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                    display_name="店铺A",
                )
                await review_repository.set_last_fetched_at(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    pending_count=7,
                )
                from app.api.v1 import reviews as reviews_api
                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

                async def fake_by_chat(chat_id):
                    return [
                        {
                            "group_key": "g1",
                            "name": "监控组1",
                            "browser_session_id": "ctrip_a",
                            "feishu_chat_id": chat_id,
                        }
                    ]

                async def fake_summaries(*, include_secrets=False):
                    return [
                        {
                            "group_key": "g1",
                            "name": "监控组1",
                            "browser_session_id": "ctrip_a",
                            "feishu_chat_id": "oc_monitor_group",
                        }
                    ]

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    fake_by_chat,
                ), patch.object(
                    ctrip_repo,
                    "list_data_center_watch_group_summaries",
                    fake_summaries,
                ):
                    payload = await reviews_api.get_pending_count_route(
                        platform="ctrip",
                        ctrip_watch_group_key=None,
                        browser_session_id=None,
                        chat_id="oc_monitor_group",
                    )
                self.assertEqual(payload["pending_count"], 7)
                self.assertIsNotNone(payload["last_fetched_at"])
                self.assertEqual(len(payload["stores"]), 1)
                self.assertEqual(payload["stores"][0]["ctrip_watch_group_key"], "g1")
                self.assertEqual(payload["stores"][0]["pending_count"], 7)

            asyncio.run(scenario())

    def test_pending_count_chat_without_bound_group_returns_hint(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.api.v1 import reviews as reviews_api
                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

                async def fake_by_chat(chat_id):
                    return []

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    fake_by_chat,
                ):
                    payload = await reviews_api.get_pending_count_route(
                        platform="ctrip",
                        ctrip_watch_group_key=None,
                        browser_session_id=None,
                        chat_id="oc_no_bind",
                    )
                self.assertIsNone(payload["pending_count"])
                self.assertEqual(payload["stores"], [])
                self.assertIn("监控组", str(payload.get("summary") or ""))

            asyncio.run(scenario())

    def test_pending_count_bound_group_without_platform_mount_returns_accurate_hint(self) -> None:
        """群已绑定监控组但该平台未挂载：不得回落平台级旧数量，摘要须说明
        是「未挂载」而不是误报「未绑定监控组」（2026-09-09 审查修复）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                # 平台级旧配置：若误回落会把 09-01 的旧数量当成答案。
                await review_repository.update_review_auto_config(
                    platform="meituan",
                    patch=ReviewAutoConfigPatch(auto_fetch_enabled=True),
                )
                await review_repository.set_last_fetched_at(
                    platform="meituan", pending_count=99
                )
                from app.api.v1 import reviews as reviews_api
                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

                async def fake_by_chat(chat_id):
                    return [
                        {
                            "group_key": "g1",
                            "name": "监控组1",
                            "browser_session_id": "browser-x",
                            "feishu_chat_id": chat_id,
                        }
                    ]

                async def fake_resolve(group_key, platform):
                    return None, "oc_group"  # meituan 未挂载

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    fake_by_chat,
                ), patch.object(
                    review_service,
                    "resolve_store_browser_session",
                    fake_resolve,
                ):
                    payload = await reviews_api.get_pending_count_route(
                        platform="meituan",
                        ctrip_watch_group_key=None,
                        browser_session_id=None,
                        chat_id="oc_bound",
                    )
                self.assertIsNone(payload["pending_count"])
                self.assertEqual(payload["stores"], [])
                summary = str(payload.get("summary") or "")
                self.assertIn("未挂载", summary)
                self.assertNotIn("未绑定", summary)

            asyncio.run(scenario())

    def test_auto_config_get_fills_approval_chat_id_from_monitor_group(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.upsert_store_binding(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    platform_account_id="ctrip_a_acct",
                )
                from app.api.v1 import reviews as reviews_api
                from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

                async def fake_summaries(*, include_secrets=False):
                    return [
                        {
                            "group_key": "g_ctrip",
                            "browser_session_id": "ctrip_a",
                            "feishu_chat_id": "oc_monitor_group",
                        }
                    ]

                with patch.object(
                    ctrip_repo,
                    "list_data_center_watch_group_summaries",
                    fake_summaries,
                ):
                    payload = await reviews_api.get_auto_config_route(
                        platform="ctrip", browser_session_id="ctrip_a"
                    )
                self.assertEqual(payload["config"]["approval_chat_id"], "oc_monitor_group")

            asyncio.run(scenario())

    def test_group_key_save_does_not_clear_inherited_feishu_chat_id(self) -> None:
        """按监控组保存（表单飞书群为空）不得清掉监控组继承的飞书群。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                from app.api.v1 import reviews as reviews_api

                async def fake_resolve(group_key, platform):
                    return "ctrip_a", "oc_monitor_group"

                with patch.object(
                    review_service,
                    "resolve_store_browser_session",
                    fake_resolve,
                ):
                    await reviews_api.update_auto_config_route(
                        request=reviews_api.ReviewAutoConfigRequest(
                            platform="ctrip",
                            ctrip_watch_group_key="g1",
                            auto_reply_mode="semi_auto",
                            approval_chat_id=None,
                        )
                    )
                binding = await review_repository.get_store_binding(
                    browser_session_id="ctrip_a"
                )
                self.assertEqual(binding["feishu_chat_id"], "oc_monitor_group")
                self.assertEqual(binding["auto_reply_mode"], "semi_auto")

            asyncio.run(scenario())

    def test_fetch_by_unbound_chat_id_does_not_fall_back_to_default_store(self) -> None:
        """chat_id 传了但解析为空（未绑组/平台未挂载）时不回落平台默认店铺抓取。"""
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

            async def fake_by_chat_empty(chat_id):
                return []

            pipeline_calls: list[dict[str, Any]] = []

            async def refuse_pipeline(**kwargs):
                pipeline_calls.append(kwargs)
                raise AssertionError(
                    "unbound chat must not run the review pipeline on the default store"
                )

            with patch.object(
                ctrip_repo,
                "list_data_center_watch_groups_by_feishu_chat_id",
                fake_by_chat_empty,
            ), patch.object(
                review_service,
                "run_review_pipeline",
                refuse_pipeline,
            ):
                response = client.post(
                    "/api/v1/reviews/fetch",
                    json={"platform": "ctrip", "chat_id": "oc_unbound"},
                )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["fetch"]["status"], "skipped")
            self.assertIn("未挂载", payload["fetch"]["summary"])
            self.assertEqual(payload["process"]["status"], "skipped")
            self.assertEqual(pipeline_calls, [])

    def test_fetch_by_feishu_chat_id_resolves_bound_store(self) -> None:
        """飞书「刷新读取评价」：POST fetch 带 chat_id 按群绑监控组解析店铺现读，
        自动回复关闭时也允许手动触发一次抓取（只抓数量，不提交回复）。"""
        with _isolated_sqlite_database() as db_url, _fake_adapter(_default_fake_adapter()):
            client = _reload_app(db_url)
            from app.domain.ctrip_ebooking_watch import repository as ctrip_repo

            async def fake_by_chat(chat_id):
                return [
                    {
                        "group_key": "g1",
                        "name": "监控组1",
                        "browser_session_id": "ctrip_a",
                        "feishu_chat_id": chat_id,
                    }
                ]

            with patch.object(
                ctrip_repo,
                "list_data_center_watch_groups_by_feishu_chat_id",
                fake_by_chat,
            ), patch.object(
                review_service,
                "resolve_store_browser_session",
                AsyncMock(return_value=("ctrip_a", "oc_monitor_group")),
            ), patch.object(
                review_service,
                "get_review_adapter",
                lambda platform, session_id=None: _default_fake_adapter(),
            ):
                response = client.post(
                    "/api/v1/reviews/fetch",
                    json={"platform": "ctrip", "chat_id": "oc_monitor_group"},
                )
            self.assertEqual(response.status_code, 200)
            fetch = response.json()["fetch"]
            self.assertEqual(fetch["status"], "ok")
            self.assertEqual(fetch["browser_session_id"], "ctrip_a")


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _fake_adapter:
    """在 registry 中注册/清理 fake 适配器的上下文管理器。"""

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def __enter__(self):
        if self.adapter is not None:
            review_adapters.register_review_adapter(self.adapter)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        review_adapters._ADAPTERS.pop("ctrip", None)


async def _noop_push(chat_id: str, card: dict) -> dict:
    return {"ok": True}


def _reload_app(db_url: str) -> TestClient:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)
    app_module = importlib.import_module("app.main")
    app_module = importlib.reload(app_module)
    return TestClient(app_module.create_app())


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
