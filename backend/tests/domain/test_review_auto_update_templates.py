"""「自动更新评价」（按店铺重建评价模板）服务测试。

2026-09-03 需求：新增店铺或已添加店铺可通过小按钮「自动更新评价」：
1. 抓取浏览器中已回复过的评价（好/中/差三类）；
2. 提交 Hermes 同源 LLM 分析，整理成评价模板；
3. 覆盖更新该店铺的全部评价模板（前端弹窗确认后才允许触发）。

安全口径：
- LLM 不可用/输出不合法时回退规则式去重（与历史导入一致），不阻断；
- 样本为空或整理结果为空时不清空现有模板（防误清空）；
- LLM 不能凭空发明类别样本（无样本的类别丢弃其输出）。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.domain.review_management import adapters as review_adapters
from app.domain.review_management import repository as review_repository
from app.domain.review_management import service as review_service
from app.domain.review_management.types import ReplyTemplateInput


class _HistoryAdapter:
    platform = "ctrip"

    def __init__(self, history) -> None:
        self.history = list(history)

    async def fetch_reviews(self):
        raise AssertionError("auto-update 只走历史回复，不应触发整页抓取")

    async def fetch_history_replies(self, limit: int = 200):
        return self.history[:limit]

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        raise AssertionError("auto-update 不允许提交任何回复")


def _history_seed() -> list[review_adapters.HistoryReply]:
    return [
        review_adapters.HistoryReply(
            review_id="hr1",
            hotel_id="HA",
            score=5.0,
            content="非常好",
            reply_content="感谢 {user_nickname} 的认可，欢迎再次入住",
        ),
        review_adapters.HistoryReply(
            review_id="hr2",
            hotel_id="HA",
            score=4.0,
            content="不错",
            reply_content="感谢入住，我们会持续改进",
        ),
        review_adapters.HistoryReply(
            review_id="hr3",
            hotel_id="HA",
            score=2.0,
            content="太吵",
            reply_content="抱歉给您带来不便，已反馈工程部处理",
        ),
        review_adapters.HistoryReply(
            review_id="hr4",
            hotel_id="HA",
            score=1.0,
            content="空调坏了",
            reply_content="抱歉给您带来不便，已反馈工程部处理",  # 与 hr3 去重后同模板
        ),
        review_adapters.HistoryReply(
            review_id="hr5",
            hotel_id="HA",
            score=3.0,
            content="一般般",
            reply_content="感谢反馈，我们会持续提升入住体验",
        ),
    ]


def _llm_json(content: dict) -> str:
    return "```json\n" + json.dumps(content, ensure_ascii=False) + "\n```"


class AutoUpdateTemplatesTests(unittest.TestCase):
    def setUp(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def tearDown(self) -> None:
        review_adapters._ADAPTERS.clear()
        review_adapters._PORT_ADAPTERS.clear()

    def test_llm_distill_overwrites_store_templates(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="旧模板",
                        content="旧内容",
                    ),
                    browser_session_id="ctrip_a",
                )
                # 其他店铺/平台级模板不受影响。
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="别家店铺模板",
                        content="别家内容",
                    ),
                    browser_session_id="ctrip_b",
                )

                async def llm_chat(messages) -> str | None:
                    return _llm_json(
                        {
                            "good": ["感谢 {user_nickname} 的认可，期待再会"],
                            "medium": ["感谢反馈，我们会持续提升入住体验"],
                            "bad": ["深表歉意，已安排相关团队跟进处理"],
                        }
                    )

                result = await review_service.auto_update_store_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_HistoryAdapter(_history_seed()),
                    llm_chat=llm_chat,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["history_reviews"], 5)
                self.assertEqual(result["templates_created"], 3)

                templates = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                names = {t["name"] for t in templates}
                self.assertNotIn("旧模板", names)
                self.assertEqual(len(templates), 3)
                self.assertTrue(all(t["source"] == "auto" for t in templates))
                other = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_b"
                )
                self.assertEqual([t["name"] for t in other], ["别家店铺模板"])

                # 历史评价按已回复落库（供后续审计/再整理）。
                replied = await review_repository.list_reviews(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                self.assertEqual(len(replied), 5)
                self.assertTrue(all(r["reply_status"] == "replied" for r in replied))

                tasks = await review_repository.list_review_tasks(platform="ctrip")
                self.assertEqual(tasks[0]["task_type"], "auto_update")
                self.assertEqual(tasks[0]["status"], "completed")

            asyncio.run(scenario())

    def test_llm_output_without_samples_is_dropped(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                # 只有差评样本。
                seed = [row for row in _history_seed() if (row.score or 0) < 3]

                async def llm_chat(messages) -> str | None:
                    return _llm_json(
                        {
                            "good": ["凭空发明的好评模板"],
                            "bad": ["深表歉意，已安排跟进"],
                        }
                    )

                result = await review_service.auto_update_store_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_HistoryAdapter(seed),
                    llm_chat=llm_chat,
                )
                self.assertEqual(result["status"], "ok")
                templates = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                categories = {t["category"] for t in templates}
                self.assertEqual(categories, {"bad"})

            asyncio.run(scenario())

    def test_llm_failure_falls_back_to_rule_based_dedupe(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)

                async def llm_chat(messages) -> str | None:
                    raise RuntimeError("llm upstream 500")

                result = await review_service.auto_update_store_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_HistoryAdapter(_history_seed()),
                    llm_chat=llm_chat,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["generator"], "rule_based")
                templates = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                # hr3/hr4 回复内容去重后差评只剩 1 条；中评/好评各按样本去重。
                by_category: dict[str, int] = {}
                for tpl in templates:
                    by_category[tpl["category"]] = by_category.get(tpl["category"], 0) + 1
                self.assertEqual(by_category.get("good"), 2)
                self.assertEqual(by_category.get("bad"), 1)
                self.assertEqual(by_category.get("medium"), 1)

            asyncio.run(scenario())

    def test_categories_without_samples_keep_existing_templates(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                # 店铺已有中评模板；本轮历史样本只有好评（携程已回复记录常为纯好评）。
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="medium",
                        name="旧中评模板",
                        content="旧中评内容",
                    ),
                    browser_session_id="ctrip_a",
                )
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="旧好评模板",
                        content="旧好评内容",
                    ),
                    browser_session_id="ctrip_a",
                )
                seed = [row for row in _history_seed() if (row.score or 0) >= 4]

                result = await review_service.auto_update_store_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_HistoryAdapter(seed),
                )
                self.assertEqual(result["status"], "ok")
                templates = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                by_category: dict[str, list[str]] = {}
                for tpl in templates:
                    by_category.setdefault(tpl["category"], []).append(tpl["name"])
                # 无样本的类别保留旧模板；有样本的类别覆盖重建。
                self.assertEqual(by_category.get("medium"), ["旧中评模板"])
                self.assertEqual(len(by_category.get("good", [])), 2)
                self.assertNotIn("旧好评模板", by_category.get("good", []))

            asyncio.run(scenario())

    def test_empty_history_keeps_existing_templates(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_modules(db_url)
                await review_repository.save_template(
                    template=ReplyTemplateInput(
                        platform="ctrip",
                        category="good",
                        name="保留我",
                        content="内容",
                    ),
                    browser_session_id="ctrip_a",
                )
                result = await review_service.auto_update_store_templates(
                    platform="ctrip",
                    browser_session_id="ctrip_a",
                    adapter=_HistoryAdapter([]),
                )
                self.assertEqual(result["status"], "no_history")
                templates = await review_repository.list_templates(
                    platform="ctrip", browser_session_id="ctrip_a"
                )
                self.assertEqual([t["name"] for t in templates], ["保留我"])

            asyncio.run(scenario())


def _reload_modules(db_url: str) -> None:
    _reload_db(db_url)
    importlib.reload(review_repository)
    importlib.reload(review_service)


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review-auto-update.db"
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
