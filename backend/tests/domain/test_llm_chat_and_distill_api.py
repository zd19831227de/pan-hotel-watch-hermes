from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient


class _IsolatedBrowserState:
    """复用 meituan 登录测试的浏览器会话隔离套路。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {
            "BROWSER_STATE_DIR": os.environ.get("BROWSER_STATE_DIR"),
        }
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        self._reload()
        return self

    def __exit__(self, *exc):
        for key, previous in self._saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self._reload()
        self._tmp.cleanup()
        return False

    @staticmethod
    def _reload():
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))


class LlmChatApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        main = importlib.import_module("app.main")
        return TestClient(main.create_app())

    def _create_session(self, client: TestClient, **overrides: Any) -> dict:
        payload = {
            "platform_account_id": "doubao_acc",
            "display_name": "Doubao Chat",
            "platform": "doubao",
            "intended_use": "llm_chat",
            "session_port": 9611,
        }
        payload.update(overrides)
        created = client.post("/api/v1/browser-sessions", json=payload)
        assert created.status_code == 200, created.text
        return created.json()

    def test_open_login_rejects_non_llm_chat_session(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            # competitor_price 同样是非 llm_chat 用途：用它可以验证拒绝逻辑，
            # 且不需要 P2 起 EBK 建号必填的店铺归属。
            self._create_session(
                client, platform="ctrip", intended_use="competitor_price", session_port=9612
            )
            response = client.post(
                "/api/v1/doubao-chat/sessions/doubao_acc/open-login",
                json={"browser_session_id": "browser-doubao_acc"},
            )
            self.assertEqual(response.status_code, 422)

    def test_open_login_rejects_wrong_platform(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            self._create_session(client, platform="qwen", session_port=9613)
            response = client.post(
                "/api/v1/doubao-chat/sessions/doubao_acc/open-login",
                json={"browser_session_id": "browser-doubao_acc"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn("豆包", response.json()["detail"])

    def test_open_login_passes_llm_chat_session_to_service(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            session = self._create_session(client)
            doubao_api = importlib.import_module("app.api.v1.doubao_chat")
            fake_service = mock.Mock()
            fake_service.open_login = mock.AsyncMock(
                return_value={
                    "status": "human_required",
                    "summary": "请人工登录",
                    "platform": "doubao",
                    "evidence": {"session_port": 9611, "current_url": "https://www.doubao.com/chat/"},
                }
            )
            with mock.patch.object(doubao_api, "get_doubao_chat_service", return_value=fake_service):
                response = client.post(
                    "/api/v1/doubao-chat/sessions/doubao_acc/open-login",
                    json={"browser_session_id": session["browser_session_id"]},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "human_required")
            kwargs = fake_service.open_login.call_args.kwargs
            self.assertEqual(kwargs["platform_account_id"], "doubao_acc")
            self.assertEqual(kwargs["browser_session_id"], session["browser_session_id"])

    def test_ask_and_extract_returns_sanitized_payload(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            session = self._create_session(client)
            doubao_api = importlib.import_module("app.api.v1.doubao_chat")
            fake_service = mock.Mock()
            fake_service.ask_and_extract = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "summary": "ok",
                    "answer_text": "规则A：提前1天。",
                    "evidence": {"browser_mode": "os_chrome_cdp"},
                }
            )
            with mock.patch.object(doubao_api, "get_doubao_chat_service", return_value=fake_service):
                response = client.post(
                    "/api/v1/doubao-chat/sessions/doubao_acc/ask-and-extract",
                    json={
                        "browser_session_id": session["browser_session_id"],
                        "prompt": "测试提问",
                        "max_wait_seconds": 10,
                    },
                )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertIn("规则A", payload["answer_text"])
            self.assertEqual(fake_service.ask_and_extract.call_args.kwargs["prompt"], "测试提问")

    def test_qianwen_router_exists(self) -> None:
        with _IsolatedBrowserState():
            client = self._client()
            response = client.post(
                "/api/v1/qwen-chat/sessions/none/open-login",
                json={},
            )
            self.assertIn(response.status_code, {404, 422})


class DistillApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        main = importlib.import_module("app.main")
        return TestClient(main.create_app())

    def test_run_distill_delegates_to_service(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        fake_result = {
            "status": "success",
            "summary": "蒸馏完成并入库。",
            "platform": "doubao",
            "source_type": "text",
            "knowledge_title": "规则",
            "rag_document_id": 9,
        }
        with mock.patch.object(
            distill_api.service,
            "run_distill",
            new=mock.AsyncMock(return_value=fake_result),
        ) as run_mock:
            response = client.post(
                "/api/v1/distill/runs",
                json={
                    "source_type": "text",
                    "content": "携程活动报名规则",
                    "platform": "doubao",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rag_document_id"], 9)
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["source_type"], "text")
        self.assertEqual(kwargs["platform"], "doubao")

    def test_run_distill_validates_payload(self) -> None:
        client = self._client()
        response = client.post(
            "/api/v1/distill/runs",
            json={"source_type": "audio", "content": "x", "platform": "doubao"},
        )
        self.assertEqual(response.status_code, 422)

    def test_sources_crud_with_stub_repository(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        source = {
            "id": 1,
            "source_type": "text",
            "content": "每日规则",
            "platform": "qwen",
            "title": "规则",
            "distill_prompt": "",
            "enabled": True,
            "schedule_interval_minutes": 60,
            "last_run_at": None,
        }
        with mock.patch.object(
            distill_api.repository,
            "create_distill_source",
            new=mock.AsyncMock(return_value=source),
        ), mock.patch.object(
            distill_api.repository,
            "list_distill_sources",
            new=mock.AsyncMock(return_value=[source]),
        ), mock.patch.object(
            distill_api.repository,
            "update_distill_source_fields",
            new=mock.AsyncMock(return_value={**source, "enabled": False}),
        ), mock.patch.object(
            distill_api.repository,
            "delete_distill_source",
            new=mock.AsyncMock(return_value=True),
        ):
            created = client.post(
                "/api/v1/distill/sources",
                json={
                    "source_type": "text",
                    "content": "每日规则",
                    "platform": "qwen",
                    "schedule_interval_minutes": 60,
                },
            )
            self.assertEqual(created.status_code, 201)
            listed = client.get("/api/v1/distill/sources")
            self.assertEqual(listed.json()["count"], 1)
            disabled = client.patch("/api/v1/distill/sources/1", json={"enabled": False})
            self.assertFalse(disabled.json()["enabled"])
            deleted = client.delete("/api/v1/distill/sources/1")
            self.assertEqual(deleted.status_code, 200)

    def test_meta_endpoint_lists_supported_values(self) -> None:
        client = self._client()
        response = client.get("/api/v1/distill/meta")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["platforms"], ["doubao", "qwen"])

    def test_patch_source_updates_schedule_interval(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        source = {
            "id": 7,
            "source_type": "text",
            "content": "每日规则",
            "platform": "doubao",
            "title": "规则",
            "distill_prompt": "",
            "enabled": True,
            "schedule_interval_minutes": 0,
            "last_run_at": None,
        }
        updated = {**source, "schedule_interval_minutes": 120}
        with mock.patch.object(
            distill_api.repository,
            "update_distill_source_fields",
            new=mock.AsyncMock(return_value=updated),
        ) as update_mock:
            response = client.patch(
                "/api/v1/distill/sources/7",
                json={"schedule_interval_minutes": 120},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schedule_interval_minutes"], 120)
        update_mock.assert_awaited_once()
        self.assertEqual(
            update_mock.call_args.kwargs.get("schedule_interval_minutes"), 120
        )

    # ------------------------------------------------------------------
    # UI-Fix-F4F — 定时默认 120 分钟 + 最小间隔保护（0 或 30~10080）
    # ------------------------------------------------------------------

    def test_create_source_defaults_interval_to_120(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        source = {
            "id": 21,
            "source_type": "text",
            "content": "规则",
            "platform": "doubao",
            "title": "",
            "distill_prompt": "",
            "enabled": True,
            "schedule_interval_minutes": 120,
            "last_run_at": None,
        }
        with mock.patch.object(
            distill_api.repository,
            "create_distill_source",
            new=mock.AsyncMock(return_value=source),
        ) as create_mock:
            response = client.post(
                "/api/v1/distill/sources",
                json={
                    "source_type": "text",
                    "content": "携程活动报名规则",
                    "platform": "doubao",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            create_mock.call_args.kwargs.get("schedule_interval_minutes"), 120
        )
        self.assertEqual(response.json()["schedule_interval_minutes"], 120)

    def test_create_source_explicit_zero_keeps_manual_only(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        source = {
            "id": 22,
            "source_type": "text",
            "content": "规则",
            "platform": "qwen",
            "title": "",
            "distill_prompt": "",
            "enabled": True,
            "schedule_interval_minutes": 0,
            "last_run_at": None,
        }
        with mock.patch.object(
            distill_api.repository,
            "create_distill_source",
            new=mock.AsyncMock(return_value=source),
        ) as create_mock:
            response = client.post(
                "/api/v1/distill/sources",
                json={
                    "source_type": "text",
                    "content": "手动规则",
                    "platform": "qwen",
                    "schedule_interval_minutes": 0,
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            create_mock.call_args.kwargs.get("schedule_interval_minutes"), 0
        )

    def test_create_source_rejects_interval_below_30(self) -> None:
        client = self._client()
        response = client.post(
            "/api/v1/distill/sources",
            json={
                "source_type": "text",
                "content": "过频",
                "platform": "doubao",
                "schedule_interval_minutes": 5,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_patch_source_rejects_interval_below_30(self) -> None:
        client = self._client()
        response = client.patch(
            "/api/v1/distill/sources/7",
            json={"schedule_interval_minutes": 5},
        )
        self.assertEqual(response.status_code, 422)

    def test_patch_source_accepts_boundary_values(self) -> None:
        client = self._client()
        distill_api = importlib.import_module("app.api.v1.distill")
        source = {
            "id": 23,
            "source_type": "text",
            "content": "边界",
            "platform": "doubao",
            "title": "",
            "distill_prompt": "",
            "enabled": True,
            "schedule_interval_minutes": 30,
            "last_run_at": None,
        }
        with mock.patch.object(
            distill_api.repository,
            "update_distill_source_fields",
            new=mock.AsyncMock(return_value=source),
        ):
            accepted = client.patch(
                "/api/v1/distill/sources/23",
                json={"schedule_interval_minutes": 30},
            )
            zero_ok = client.patch(
                "/api/v1/distill/sources/23",
                json={"schedule_interval_minutes": 0},
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(zero_ok.status_code, 200)


class BrowserSessionLlmChatUseTests(unittest.TestCase):
    def test_normalize_aliases(self) -> None:
        sessions = importlib.import_module("app.domain.browser.sessions")
        self.assertEqual(sessions.normalize_browser_intended_use("llm_chat"), "llm_chat")
        self.assertEqual(sessions.normalize_browser_intended_use("llm-chat"), "llm_chat")
        self.assertEqual(sessions.normalize_browser_intended_use("chat"), "llm_chat")
        self.assertEqual(sessions.normalize_browser_intended_use("ebk"), "ebk")

    def test_store_accepts_llm_chat_session(self) -> None:
        with _IsolatedBrowserState():
            sessions = importlib.import_module("app.domain.browser.sessions")
            store = sessions.get_browser_session_store()
            created = store.create(
                sessions.BrowserSessionCreate(
                    platform_account_id="qwen_acc",
                    display_name="Qwen Chat",
                    platform="qwen",
                    intended_use="llm_chat",
                    session_port=9621,
                )
            )
            self.assertEqual(created.intended_use, "llm_chat")
            listed = store.list()
            self.assertTrue(
                any(s.platform == "qwen" and s.intended_use == "llm_chat" for s in listed)
            )

    def test_patch_accepts_llm_chat(self) -> None:
        with _IsolatedBrowserState():
            sessions = importlib.import_module("app.domain.browser.sessions")
            store = sessions.get_browser_session_store()
            store.create(
                sessions.BrowserSessionCreate(
                    platform_account_id="doubao_acc2",
                    display_name="Doubao",
                    platform="doubao",
                    intended_use="ebk",
                    shop_key="shop_llm_patch_test",
                    session_port=9622,
                )
            )
            updated = store.update(
                "browser-doubao_acc2",
                sessions.BrowserSessionPatch(intended_use="llm_chat"),
            )
            self.assertEqual(updated.intended_use, "llm_chat")


class DistillSchedulerLoopTests(unittest.TestCase):
    def test_distill_loop_registered_in_run_forever(self) -> None:
        source = Path(
            importlib.import_module("app.workers.read_only_scheduler").__file__
        ).read_text(encoding="utf-8")
        self.assertIn("_run_distill_loop()", source)
        self.assertIn("DISTILL_TICK_SECONDS", source)

    def test_distill_loop_handles_human_required_with_alert(self) -> None:
        scheduler = importlib.import_module("app.workers.read_only_scheduler")

        async def _once(tick_seconds=1):
            from app.domain.distill import repository as distill_repository
            from app.domain.distill import service as distill_service
            from app.domain.notifications import feishu as feishu_module
            from app.domain.ops import worker_heartbeat as heartbeat_module

            sources = [
                {"id": 1, "title": "规则源", "platform": "doubao", "source_type": "text"}
            ]
            push_calls: list[tuple[str, str]] = []

            async def fake_list_due(*args, **kwargs):
                return sources

            async def fake_run(source_id):
                return {"status": "human_required", "summary": "需要登录"}

            async def fake_push(title, text):
                push_calls.append((title, text))
                return type("R", (), {"status": "success"})()

            heartbeat_calls: list[tuple[str, str]] = []

            def fake_heartbeat(name, event, **kwargs):
                heartbeat_calls.append((name, event))
                if event in {"tick_finished", "tick_failed"}:
                    # loop 的 except BaseException 会吞掉 tick_finished 的异常并
                    # 记 tick_failed；两个事件都抛出才能终止 loop（仅测试用）。
                    raise _StopLoop()

            class _StopLoop(Exception):
                pass

            with mock.patch.object(distill_repository, "list_due_distill_sources", fake_list_due), \
                    mock.patch.object(distill_service, "run_distill_source", fake_run), \
                    mock.patch.object(feishu_module, "push_text_to_feishu", fake_push), \
                    mock.patch.object(heartbeat_module, "write_worker_heartbeat", fake_heartbeat):
                try:
                    await scheduler._run_distill_loop(tick_seconds=1)
                except _StopLoop:
                    pass
            return push_calls, heartbeat_calls

        push_calls, heartbeat_calls = asyncio.run(_once())
        self.assertTrue(any("蒸馏任务暂停" in title for title, _text in push_calls))
        self.assertIn(("distill_scheduler", "started"), heartbeat_calls)


if __name__ == "__main__":
    unittest.main()
