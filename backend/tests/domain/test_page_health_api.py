from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient


class PageHealthApiTests(unittest.TestCase):
    """UI-Fix-F3B — GET /page-health/alerts 查询已记录的页面健康告警。"""

    def _client(self) -> TestClient:
        main = importlib.import_module("app.main")
        return TestClient(main.create_app())

    def test_list_alerts_returns_repository_records(self) -> None:
        client = self._client()
        page_health_api = importlib.import_module("app.api.v1.page_health")
        alerts: list[dict[str, Any]] = [
            {
                "id": 2,
                "platform": "meituan",
                "target": "dataset:orders",
                "problem_key": "selector_hard_fail",
                "level": "hard_fail",
                "summary": "连续 3 次未命中关键节点",
                "created_at": "2026-08-20T10:00:00",
            },
            {
                "id": 1,
                "platform": "ctrip",
                "target": "dataset:business_outline",
                "problem_key": "layout_changed",
                "level": "layout_changed",
                "summary": "页面结构指纹变化，但本轮采集成功",
                "created_at": "2026-08-20T09:00:00",
            },
        ]
        with mock.patch.object(
            page_health_api.repository,
            "list_page_health_alerts",
            new=mock.AsyncMock(return_value=alerts),
        ):
            response = client.get("/api/v1/page-health/alerts")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["alerts"][0]["platform"], "meituan")
        self.assertEqual(body["alerts"][0]["level"], "hard_fail")
        self.assertEqual(body["alerts"][1]["level"], "layout_changed")

    def test_platform_and_level_filters_are_passed_through(self) -> None:
        client = self._client()
        page_health_api = importlib.import_module("app.api.v1.page_health")
        with mock.patch.object(
            page_health_api.repository,
            "list_page_health_alerts",
            new=mock.AsyncMock(return_value=[]),
        ) as list_mock:
            response = client.get(
                "/api/v1/page-health/alerts?platform=fliggy&level=partial_fail&limit=10"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        kwargs = list_mock.call_args.kwargs
        self.assertEqual(kwargs["platform"], "fliggy")
        self.assertEqual(kwargs["level"], "partial_fail")
        self.assertEqual(kwargs["limit"], 10)

    def test_invalid_level_is_rejected(self) -> None:
        client = self._client()
        response = client.get("/api/v1/page-health/alerts?level=critical")
        self.assertEqual(response.status_code, 422)

    def test_response_fields_are_whitelisted(self) -> None:
        """响应只含业务字段，不得出现敏感键（泄漏防回归）。"""
        client = self._client()
        page_health_api = importlib.import_module("app.api.v1.page_health")
        alerts: list[dict[str, Any]] = [
            {
                "id": 3,
                "platform": "ctrip",
                "target": "dataset:room_rate",
                "problem_key": "partial_fail",
                "level": "partial_fail",
                "summary": "部分字段缺失，采集降级",
                "created_at": "2026-08-20T11:00:00",
            }
        ]
        with mock.patch.object(
            page_health_api.repository,
            "list_page_health_alerts",
            new=mock.AsyncMock(return_value=alerts),
        ):
            response = client.get("/api/v1/page-health/alerts")
        raw = response.text.lower()
        for forbidden in ("cookie", "password", "webhook", "profile_path", "api_key"):
            self.assertNotIn(forbidden, raw)


class PageHealthRepositoryListTests(unittest.TestCase):
    """repository.list_page_health_alerts 的真实 DB 过滤行为。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "page-health-list-test.db"
        self._saved_env: dict[str, str | None] = {}
        for key, value in {
            "DATABASE_URL": f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            "DATABASE_AUTO_CREATE_TABLES": "true",
        }.items():
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        db_session._database_ready_engine = None

    def tearDown(self) -> None:
        for key, previous in self._saved_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()

    def test_list_alerts_filters_by_platform_and_level(self) -> None:
        import asyncio

        from app.domain.page_change_alerts import repository

        async def _scenario() -> dict[str, Any]:
            await repository.record_alert(
                platform="ctrip",
                target="dataset:a",
                problem_key="selector_hard_fail",
                level="hard_fail",
                summary="a",
            )
            await repository.record_alert(
                platform="fliggy",
                target="dataset:b",
                problem_key="partial_fail",
                level="partial_fail",
                summary="b",
            )
            await repository.record_alert(
                platform="fliggy",
                target="dataset:c",
                problem_key="layout_changed",
                level="layout_changed",
                summary="c",
            )
            all_alerts = await repository.list_page_health_alerts()
            fliggy_only = await repository.list_page_health_alerts(platform="fliggy")
            fliggy_partial = await repository.list_page_health_alerts(
                platform="fliggy", level="partial_fail"
            )
            limited = await repository.list_page_health_alerts(limit=2)
            return {
                "all": all_alerts,
                "fliggy": fliggy_only,
                "fliggy_partial": fliggy_partial,
                "limited": limited,
            }

        result = asyncio.run(_scenario())
        self.assertEqual(len(result["all"]), 3)
        self.assertTrue(all(a["platform"] == "fliggy" for a in result["fliggy"]))
        self.assertEqual(len(result["fliggy"]), 2)
        self.assertEqual(len(result["fliggy_partial"]), 1)
        self.assertEqual(result["fliggy_partial"][0]["problem_key"], "partial_fail")
        self.assertEqual(len(result["limited"]), 2)
        # 最新记录在前（id 倒序）
        self.assertEqual(result["all"][0]["target"], "dataset:c")


class FliggyPageHealthObserverTests(unittest.TestCase):
    """UI-Fix-F3B — 飞猪读取链路接入 V7S1 页面健康观察器。"""

    def test_observe_fliggy_read_payload_adapter_exists(self) -> None:
        service = importlib.import_module("app.domain.page_change_alerts.service")
        self.assertTrue(hasattr(service, "observe_fliggy_read_payload"))

    def test_read_dataset_route_calls_fliggy_observer(self) -> None:
        with _isolated_browser_state():
            import asyncio

            from app.domain.shops import service as shops_service

            shop_key = asyncio.run(shops_service.create_shop({"name": "页面健康观察店"}))[
                "shop_key"
            ]
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_ph",
                    "display_name": "Fliggy PH",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "connection_mode": "unknown",
                    "session_port": 9543,
                    # 店铺化 P2 起 EBK 账号必须挂真实存在的店铺，否则 422。
                    "shop_key": shop_key,
                },
            )
            self.assertEqual(created.status_code, 200)
            fliggy_api = importlib.import_module("app.api.v1.fliggy_ebooking")

            class _FakeService:
                async def read_dataset(self, **kwargs: Any) -> dict[str, Any]:
                    return {
                        "status": "success",
                        "summary": "ok",
                        "platform": "fliggy",
                        "dataset": kwargs["dataset"],
                        "platform_account_id": kwargs["platform_account_id"],
                        "browser_session_id": kwargs["browser_session_id"],
                        "data": {"today_data": {"销售额": "1"}},
                        "evidence": {"parser": "fliggy_home_dom_v1"},
                    }

            original_service = fliggy_api.get_fliggy_ebooking_service
            fliggy_api.get_fliggy_ebooking_service = lambda: _FakeService()

            async def _fake_observer(target: str, payload: dict[str, Any]) -> None:
                _fake_observer.calls.append((target, payload))

            _fake_observer.calls = []
            original_observer = fliggy_api.observe_fliggy_read_payload
            fliggy_api.observe_fliggy_read_payload = _fake_observer
            try:
                response = client.post(
                    "/api/v1/fliggy-ebooking/sessions/fliggy_ph/read-dataset",
                    json={
                        "browser_session_id": created.json()["browser_session_id"],
                        "dataset": "home_v1",
                    },
                )
            finally:
                fliggy_api.get_fliggy_ebooking_service = original_service
                fliggy_api.observe_fliggy_read_payload = original_observer

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(_fake_observer.calls), 1)
            target, payload = _fake_observer.calls[0]
            self.assertEqual(target, "dataset:home_v1")
            self.assertEqual(payload["platform"], "fliggy")


class _isolated_browser_state:
    """临时 BROWSER_STATE_DIR + 临时 SQLite。

    店铺化 P2 起 EBK 账号创建要过 shop_key 存在性校验（查 shops 表），
    不隔离 DB 会打到真实库；圈入时重绑全局引擎，圈出还原。
    """

    def __enter__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in ("BROWSER_STATE_DIR", "DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
        }
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'page_health.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine,
            expire_on_commit=False,
        )
        importlib.reload(db_session)
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))

    def __exit__(self, *exc: object) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        self._tmp.cleanup()


def _reload_app():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    return main.create_app()


if __name__ == "__main__":
    unittest.main()
