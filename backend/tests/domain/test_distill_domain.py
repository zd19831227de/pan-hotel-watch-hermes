from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DistillAdmissionTests(unittest.TestCase):
    """V6 Step 2.4 — 蒸馏准入硬约束：只允许知识类内容入库，拒绝实时数据。"""

    def setUp(self) -> None:
        self.service = importlib.import_module("app.domain.distill.service")

    def test_knowledge_text_admitted(self) -> None:
        reason = self.service.check_distill_admission(
            "携程EBK活动报名规则：需在数据中心-营销推广模块中报名，活动开始前1天截止。"
        )
        self.assertIsNone(reason)

    def test_price_rejected(self) -> None:
        for text in (
            "今日竞品价格 ¥420，本店 431 元。",
            "昨晚均价431元。",
        ):
            self.assertIsNotNone(self.service.check_distill_admission(text), text)

    def test_occupancy_rejected(self) -> None:
        self.assertIsNotNone(
            self.service.check_distill_admission("上周出租率 82%，RevPAR 350。")
        )

    def test_room_state_rejected(self) -> None:
        self.assertIsNotNone(self.service.check_distill_admission("今日大床房仅剩 3 间，已满房。"))

    def test_revenue_rejected(self) -> None:
        self.assertIsNotNone(self.service.check_distill_admission("昨日营收 12800，间夜 56。"))

    def test_empty_rejected(self) -> None:
        self.assertIsNotNone(self.service.check_distill_admission("  "))

    def test_english_realtime_data_rejected(self) -> None:
        for text in (
            "Only 5 rooms left tonight.",
            "Deluxe king room: 3 rooms left.",
            "The hotel is sold out this weekend.",
            "RevPAR improved to 350 this month.",
            "ADR stayed at 420 in July.",
            "Occupancy: 92 (weekly average).",
            "Best available rate is $139 per night.",
        ):
            self.assertIsNotNone(self.service.check_distill_admission(text), text)


def _fake_chat_result(status: str = "success", answer: str = "规则A：报名需提前1天。") -> dict:
    return {
        "status": status,
        "summary": "ok",
        "answer_text": answer if status == "success" else "",
        "platform": "doubao",
        "platform_account_id": "doubao_acc",
        "browser_session_id": "browser-doubao_acc",
        "evidence": {},
    }


class DistillServiceTests(unittest.TestCase):
    """V6 Step 2.3 — 蒸馏流程：chat 提取 → 准入 → RAG 入库（DB 隔离）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "distill-test.db"
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
        self.service = importlib.import_module("app.domain.distill.service")
        self.repository = importlib.import_module("app.domain.distill.repository")

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

    def _patch_chat(self, result: dict) -> mock._patch:
        async def fake_ask(*args: object, **kwargs: object) -> dict:
            return result

        return mock.patch.object(
            self.service, "ask_llm_chat", side_effect=fake_ask
        )

    def _patch_session_resolution(self) -> mock._patch:
        async def fake_resolve(*args: object, **kwargs: object) -> tuple[str, str]:
            return ("doubao_acc", "browser-doubao_acc")

        return mock.patch.object(
            self.service, "resolve_llm_chat_session", side_effect=fake_resolve
        )

    def test_successful_run_ingests_into_rag(self) -> None:
        with self._patch_chat(_fake_chat_result()), self._patch_session_resolution():
            with mock.patch.object(self.service, "ingest_rag_document", new=mock.AsyncMock(return_value={"id": 7})) as ingest:
                run = asyncio.run(
                    self.service.run_distill(
                        source_type="text",
                        content="携程活动报名规则是什么",
                        platform="doubao",
                    )
                )
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["rag_document_id"], 7)
        self.assertTrue(run["knowledge_title"])
        ingest.assert_awaited_once()
        kwargs = ingest.call_args.kwargs
        self.assertEqual(kwargs["source_type"], "distilled")

    def test_realtime_answer_rejected_before_ingest(self) -> None:
        with self._patch_chat(_fake_chat_result(answer="今日大床房 ¥420。")), self._patch_session_resolution():
            with mock.patch.object(self.service, "ingest_rag_document", new=mock.AsyncMock()) as ingest:
                run = asyncio.run(
                    self.service.run_distill(
                        source_type="text",
                        content="问价格",
                        platform="doubao",
                    )
                )
        self.assertEqual(run["status"], "rejected")
        self.assertIn("实时数据", run["reject_reason"])
        ingest.assert_not_called()

    def test_human_required_skips_and_reports(self) -> None:
        with self._patch_chat(_fake_chat_result(status="human_required")), self._patch_session_resolution():
            with mock.patch.object(self.service, "ingest_rag_document", new=mock.AsyncMock()) as ingest:
                run = asyncio.run(
                    self.service.run_distill(
                        source_type="text",
                        content="测试",
                        platform="doubao",
                    )
                )
        self.assertEqual(run["status"], "human_required")
        ingest.assert_not_called()

    def test_parse_failed_recorded(self) -> None:
        with self._patch_chat(_fake_chat_result(status="parse_failed")), self._patch_session_resolution():
            run = asyncio.run(
                self.service.run_distill(
                    source_type="image_url",
                    content="https://example.com/pic.png",
                    platform="qwen",
                )
            )
        self.assertEqual(run["status"], "parse_failed")

    def test_invalid_platform_rejected(self) -> None:
        run = asyncio.run(
            self.service.run_distill(
                source_type="text",
                content="测试",
                platform="gpt",
            )
        )
        self.assertEqual(run["status"], "failed")
        self.assertIn("platform", run["summary"])

    def test_url_sources_reject_non_http_content(self) -> None:
        # 非 http(s) 内容在会话解析前即拒绝（计划 2.3：image_url/video_url 仅支持 HTTP/HTTPS 链接）
        for source_type, content in (
            ("image_url", "E:\\图片.png"),
            ("video_url", "not-a-url"),
            ("image_url", "ftp://example.com/pic.png"),
        ):
            run = asyncio.run(
                self.service.run_distill(
                    source_type=source_type, content=content, platform="doubao"
                )
            )
            self.assertEqual(run["status"], "failed", content)
            self.assertIn("http", run["summary"])

        # 合法 https 链接不应在 URL 校验阶段失败（无已登记会话时按 config_required 处理）
        run_ok = asyncio.run(
            self.service.run_distill(
                source_type="image_url",
                content="https://example.com/pic.png",
                platform="doubao",
            )
        )
        self.assertNotEqual(run_ok["status"], "failed")

    def test_url_prompt_composition(self) -> None:
        captured: dict[str, str] = {}

        async def fake_ask(*args: object, prompt: str = "", **kwargs: object) -> dict:
            captured["prompt"] = prompt
            return _fake_chat_result()

        with mock.patch.object(self.service, "ask_llm_chat", side_effect=fake_ask):
            with self._patch_session_resolution():
                with mock.patch.object(
                    self.service, "ingest_rag_document", new=mock.AsyncMock(return_value={"id": 1})
                ):
                    asyncio.run(
                        self.service.run_distill(
                            source_type="image_url",
                            content="https://example.com/pic.png",
                            platform="doubao",
                            distill_prompt="提取运营规则要点",
                        )
                    )
        self.assertIn("https://example.com/pic.png", captured["prompt"])
        self.assertIn("提取运营规则要点", captured["prompt"])

    def test_no_llm_chat_session_configured(self) -> None:
        async def fake_resolve(*args: object, **kwargs: object) -> tuple[str, str] | None:
            return None

        with mock.patch.object(self.service, "resolve_llm_chat_session", side_effect=fake_resolve):
            run = asyncio.run(
                self.service.run_distill(
                    source_type="text",
                    content="测试",
                    platform="doubao",
                )
            )
        self.assertEqual(run["status"], "config_required")

    def test_persistent_source_crud_and_enabled_filter(self) -> None:
        async def _scenario():
            created = await self.repository.create_distill_source(
                source_type="text",
                content="每日规则更新",
                platform="doubao",
                title="运营规则",
                schedule_interval_minutes=60,
            )
            sources = await self.repository.list_distill_sources()
            due = await self.repository.list_due_distill_sources(now_override=None)
            await self.repository.set_distill_source_enabled(created["id"], False)
            disabled = await self.repository.list_distill_sources()
            return created, sources, due, disabled

        created, sources, due, disabled = asyncio.run(_scenario())
        self.assertEqual(len(sources), 1)
        self.assertEqual(created["source_type"], "text")
        self.assertTrue(created["enabled"])
        self.assertEqual(len(due), 1)
        self.assertFalse(disabled[0]["enabled"])

    def test_update_source_fields_changes_schedule_interval(self) -> None:
        async def _scenario():
            created = await self.repository.create_distill_source(
                source_type="text",
                content="间隔修改验证",
                platform="qwen",
                title="间隔",
                schedule_interval_minutes=0,
            )
            self.assertEqual(created["schedule_interval_minutes"], 0)
            updated = await self.repository.update_distill_source_fields(
                created["id"], schedule_interval_minutes=120
            )
            fetched = await self.repository.get_distill_source(created["id"])
            both = await self.repository.update_distill_source_fields(
                created["id"], enabled=False, schedule_interval_minutes=240
            )
            return updated, fetched, both

        updated, fetched, both = asyncio.run(_scenario())
        self.assertEqual(updated["schedule_interval_minutes"], 120)
        self.assertEqual(fetched["schedule_interval_minutes"], 120)
        self.assertFalse(both["enabled"])
        self.assertEqual(both["schedule_interval_minutes"], 240)

    def test_runs_recorded(self) -> None:
        async def _scenario():
            await self.repository.create_distill_run(
                source_id=None,
                platform="doubao",
                source_type="text",
                status="success",
                summary="ok",
                extracted_text="规则A",
                knowledge_title="规则",
                rag_document_id=3,
                reject_reason=None,
            )
            runs = await self.repository.list_distill_runs(limit=10)
            return runs

        runs = asyncio.run(_scenario())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertEqual(runs[0]["rag_document_id"], 3)


class ResolveLlmChatSessionTests(unittest.TestCase):
    def test_resolves_llm_chat_session_for_platform(self) -> None:
        service = importlib.import_module("app.domain.distill.service")
        session = type(
            "S",
            (),
            {
                "browser_session_id": "browser-doubao",
                "platform": "doubao",
                "intended_use": "llm_chat",
                "platform_account_id": "doubao_acc",
                "login_state": "logged_in",
            },
        )
        other = type(
            "S",
            (),
            {
                "browser_session_id": "browser-ctrip",
                "platform": "ctrip",
                "intended_use": "ebk",
                "platform_account_id": "ctrip_acc",
                "login_state": "logged_in",
            },
        )
        store = type("Store", (), {"list": lambda self: [other, session]})()
        with mock.patch.object(service, "get_browser_session_store", return_value=store):
            result = asyncio.run(service.resolve_llm_chat_session(platform="doubao"))
        self.assertEqual(result, ("doubao_acc", "browser-doubao"))

    def test_returns_none_when_no_match(self) -> None:
        service = importlib.import_module("app.domain.distill.service")
        store = type("Store", (), {"list": lambda self: []})()
        with mock.patch.object(service, "get_browser_session_store", return_value=store):
            result = asyncio.run(service.resolve_llm_chat_session(platform="qwen"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
