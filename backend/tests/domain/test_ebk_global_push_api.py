"""EBK 全局推送 API 测试（隔离 sqlite + TestClient）。

写操作走操作员鉴权；本地/测试环境默认放行（security.py 的 local 分支），
此处仅覆盖校验/冲突/幂等语义，不做真实读取与推送。
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


class EbkGlobalPushApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_url = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'global-push-api.db').as_posix()}"
        )
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = self.db_url
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"

    def tearDown(self) -> None:
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

    def _reload_app(self):
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(self.db_url, pool_pre_ping=True)
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        main = importlib.import_module("app.main")
        return importlib.reload(main).create_app()

    def _create_body(self, **overrides):
        body = {
            "ctrip_watch_group_key": "anchor_group",
            "name": "示例门店全局推送",
            "enabled": False,
            "enabled_platforms": ["ctrip", "meituan", "fliggy"],
            "schedule_mode": "fixed_slots",
            "push_times": ["08:00", "20:00"],
            "interval_minutes": 60,
            "window_start_time": "06:00",
            "window_end_time": "23:00",
        }
        body.update(overrides)
        return body

    def test_config_crud_roundtrip(self):
        client = TestClient(self._reload_app())
        created = client.post("/api/v1/ebk-global-push/configs", json=self._create_body())
        self.assertEqual(created.status_code, 200)
        config = created.json()["config"]
        self.assertEqual(config["push_times"], ["08:00", "20:00"])
        self.assertFalse(config["enabled"])

        duplicate = client.post(
            "/api/v1/ebk-global-push/configs", json=self._create_body(name="第二条")
        )
        self.assertEqual(duplicate.status_code, 409)

        patched = client.patch(
            f"/api/v1/ebk-global-push/configs/{config['id']}",
            json={"name": "改名", "schedule_mode": "last_run_interval", "push_times": []},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["config"]["name"], "改名")

        listed = client.get("/api/v1/ebk-global-push/configs")
        self.assertEqual(len(listed.json()["configs"]), 1)

        deleted = client.delete(f"/api/v1/ebk-global-push/configs/{config['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(client.get(f"/api/v1/ebk-global-push/configs/{config['id']}").json()["config"]["enabled"])

    def test_invalid_schedule_rejected(self):
        client = TestClient(self._reload_app())
        bad = client.post(
            "/api/v1/ebk-global-push/configs",
            json=self._create_body(schedule_mode="last_run_interval", interval_minutes=5),
        )
        self.assertEqual(bad.status_code, 422)

    def test_enable_with_legacy_poll_on_is_rejected(self):
        client = TestClient(self._reload_app())
        import asyncio as _asyncio

        async def _conflict(anchor_key):
            return ["携程", "美团"]

        with mock.patch(
            "app.api.v1.ebk_global_push._legacy_still_enabled_reasons",
            side_effect=_conflict,
        ):
            response = client.post(
                "/api/v1/ebk-global-push/configs",
                json=self._create_body(enabled=True),
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("legacy_schedule_still_enabled", response.json()["detail"])

    def test_run_now_conflict_returns_409(self):
        client = TestClient(self._reload_app())
        created = client.post("/api/v1/ebk-global-push/configs", json=self._create_body())
        config_id = created.json()["config"]["id"]
        with mock.patch(
            "app.api.v1.ebk_global_push.claim_global_push_run",
            return_value=None,
        ):
            response = client.post(f"/api/v1/ebk-global-push/configs/{config_id}/run-now")
        self.assertEqual(response.status_code, 409)

    def test_run_now_marks_run_failed_on_exception_no_orphan(self):
        """问题 2（实测）：run_global_push 抛异常时不能留下 running 孤儿记录，
        否则后续所有 run-now 被互斥 409（之前 500 崩溃遗留就是这个根因）。"""
        client = TestClient(self._reload_app())
        created = client.post("/api/v1/ebk-global-push/configs", json=self._create_body())
        config_id = created.json()["config"]["id"]
        with mock.patch(
            "app.api.v1.ebk_global_push.run_global_push",
            side_effect=RuntimeError("boom 读取失败"),
        ):
            response = client.post(f"/api/v1/ebk-global-push/configs/{config_id}/run-now")
        self.assertEqual(response.status_code, 500)
        runs = client.get(f"/api/v1/ebk-global-push/configs/{config_id}/runs").json()["runs"]
        self.assertEqual(runs[0]["status"], "failed")
        self.assertTrue(runs[0]["error_summary"])
        # 再次 run-now 不应被上一次崩溃的孤儿互斥（claim 前先回收 stale/failed）
        with mock.patch(
            "app.api.v1.ebk_global_push.run_global_push",
            side_effect=RuntimeError("boom2"),
        ):
            again = client.post(f"/api/v1/ebk-global-push/configs/{config_id}/run-now")
        self.assertEqual(again.status_code, 500)

    def test_scheduler_status_reports_missing_when_no_heartbeat_and_disabled(self):
        """无心跳 + 本地开关关 → 原样透出心跳状态（missing），enabled=false。
        心跳与开关都必须 patch：测试进程会读到仓库真实 .env / .runtime。"""
        client = TestClient(self._reload_app())
        flag_off = mock.Mock()
        flag_off.ebk_global_push_scheduler_enabled = False
        with (
            mock.patch(
                "app.api.v1.ebk_global_push.get_worker_heartbeat_status",
                return_value={
                    "name": "ebk_global_push_scheduler",
                    "status": "missing",
                    "heartbeat_age_seconds": None,
                    "last_heartbeat_at": None,
                },
            ),
            mock.patch("app.api.v1.ebk_global_push.get_settings", return_value=flag_off),
        ):
            response = client.get("/api/v1/ebk-global-push/scheduler-status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "missing")
        self.assertFalse(body["enabled"])

    def test_scheduler_status_reports_healthy_when_worker_heartbeat_fresh(self):
        """后端与 worker 是独立进程、各自缓存 settings：worker 是否真的在跑
        只能由心跳判定。心跳新鲜时，即使后端本地开关副本是 false（后端先于
        .env 变更启动），状态也必须是 healthy 且 enabled=true。"""
        client = TestClient(self._reload_app())
        flag_off = mock.Mock()
        flag_off.ebk_global_push_scheduler_enabled = False
        with (
            mock.patch(
                "app.api.v1.ebk_global_push.get_worker_heartbeat_status",
                return_value={
                    "name": "ebk_global_push_scheduler",
                    "status": "healthy",
                    "heartbeat_age_seconds": 5,
                    "last_heartbeat_at": "2026-08-30T17:41:59+00:00",
                },
            ),
            mock.patch("app.api.v1.ebk_global_push.get_settings", return_value=flag_off),
        ):
            response = client.get("/api/v1/ebk-global-push/scheduler-status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "healthy")
        self.assertTrue(body["enabled"])

    def test_scheduler_status_keeps_flag_on_with_stale_heartbeat(self):
        """开关开但心跳过期 → status=stale、enabled=true（配置开启但 worker
        异常，提示去看 worker 而不是误报“未开启”）。"""
        client = TestClient(self._reload_app())
        flag_on = mock.Mock()
        flag_on.ebk_global_push_scheduler_enabled = True
        with (
            mock.patch(
                "app.api.v1.ebk_global_push.get_worker_heartbeat_status",
                return_value={
                    "name": "ebk_global_push_scheduler",
                    "status": "stale",
                    "heartbeat_age_seconds": 9999,
                    "last_heartbeat_at": "2026-08-28T07:49:06+00:00",
                },
            ),
            mock.patch("app.api.v1.ebk_global_push.get_settings", return_value=flag_on),
        ):
            response = client.get("/api/v1/ebk-global-push/scheduler-status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "stale")
        self.assertTrue(body["enabled"])

    def test_get_config_returns_resolved_platform_targets(self):
        """T2.4（设计 §9/§10.1）：详情返回解析后的三平台账号状态，脱敏不泄漏内部值。"""
        client = TestClient(self._reload_app())
        created = client.post("/api/v1/ebk-global-push/configs", json=self._create_body())
        config_id = created.json()["config"]["id"]

        detail = client.get(f"/api/v1/ebk-global-push/configs/{config_id}")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertIn("config", body)
        targets = {item["platform"]: item for item in body["platform_targets"]}
        self.assertEqual(set(targets), {"ctrip", "meituan", "fliggy"})
        # 隔离库中无锚点组/挂载组 → 全部 config_required
        for platform in ("ctrip", "meituan", "fliggy"):
            self.assertEqual(targets[platform]["status"], "config_required")
            self.assertTrue(targets[platform]["summary"])
        # 脱敏：不回显 browser_session_id / webhook / cookie
        for item in body["platform_targets"]:
            self.assertNotIn("browser_session_id", item)
            self.assertNotIn("webhook", json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
