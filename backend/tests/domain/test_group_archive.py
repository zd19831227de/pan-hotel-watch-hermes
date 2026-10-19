"""群资料归档域测试：事件摄取、去重、附件落盘、下载失败容忍、查询过滤。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.tests._db_isolation import isolated_test_db

# create_all 只建「已导入」的模型：必须在任何 ensure_db_ready 之前导入域模型。
from app.domain.group_archive import models as _group_archive_models  # noqa: F401


@contextlib.contextmanager
def _tolerant_isolated_db():
    """isolated_test_db 的容忍版（Windows 下临时库句柄清理 PermissionError 无害）。"""
    try:
        with isolated_test_db():
            yield
    except PermissionError:
        pass


def _text_event(
    message_id: str = "om_a",
    chat_id: str = "oc_g",
    text: str = "10月10日 实施：出租率85% ADR 420",
    create_time: str = "1760000000",
) -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": "evt-1", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u"}, "sender_type": "user"},
            "message": {
                "chat_id": chat_id,
                "chat_type": "group",
                "message_id": message_id,
                "message_type": "text",
                "create_time": create_time,
                "content": json.dumps(
                    {"text": "@_user_1 每日实施数据 " + text}, ensure_ascii=False
                ),
            },
        },
    }


def _file_event(
    message_id: str = "om_f",
    file_key: str = "fk_1",
    file_name: str = "经营日报1009.xlsx",
    create_time: str = "1760000100",
) -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": "evt-2", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u"}, "sender_type": "user"},
            "message": {
                "chat_id": "oc_g",
                "chat_type": "group",
                "message_id": message_id,
                "message_type": "file",
                "create_time": create_time,
                "content": json.dumps(
                    {"file_key": file_key, "file_name": file_name}, ensure_ascii=False
                ),
            },
        },
    }


def _post_event(message_id: str = "om_p") -> dict:
    content = {
        "title": "每日经营实施",
        "content": [
            [
                {"tag": "text", "text": "10月10日 出租率86%"},
                {"tag": "img", "image_key": "img_inline"},
            ],
            [{"tag": "text", "text": "ADR 430"}],
        ],
    }
    return {
        "schema": "2.0",
        "header": {"event_id": "evt-3", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u"}, "sender_type": "user"},
            "message": {
                "chat_id": "oc_g",
                "chat_type": "group",
                "message_id": message_id,
                "message_type": "post",
                "create_time": "1760000200",
                "content": json.dumps(content, ensure_ascii=False),
            },
        },
    }


class _FakeDownloader:
    """下载器桩：data 命中返回 success，fail_keys 返回 failed。"""

    def __init__(self, data: dict[str, bytes] | None = None, fail_keys: set[str] | None = None):
        self.data = data or {}
        self.fail_keys = fail_keys or set()
        self.calls: list[dict[str, str]] = []

    async def __call__(
        self,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
        chat_id: str = "",
    ) -> dict:
        self.calls.append(
            {
                "message_id": message_id,
                "file_key": file_key,
                "resource_type": resource_type,
                "chat_id": chat_id,
            }
        )
        if file_key in self.fail_keys:
            return {"status": "failed", "summary": "permission denied"}
        return {"status": "success", "data": self.data.get(file_key, b"stub-bytes")}


class GroupArchiveIngestTests(unittest.TestCase):
    def test_ingest_text_message_persists_and_dedups(self) -> None:
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            async def scenario() -> None:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                first = await service.ingest_group_archive_event(
                    _text_event(), archive_dir=archive_dir
                )
                self.assertEqual(first["status"], "ok")
                self.assertTrue(first["archived"])

                duplicate = await service.ingest_group_archive_event(
                    _text_event(), archive_dir=archive_dir
                )
                self.assertEqual(duplicate["status"], "ok")
                self.assertFalse(duplicate["archived"])

            asyncio.run(scenario())

            from app.domain.group_archive import service

            rows = service.debug_recent_messages()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["message_id"], "om_a")
            self.assertNotIn("@_user_1", row["text_content"])
            self.assertIn("出租率85%", row["text_content"])
            self.assertRegex(row["message_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_ingest_file_message_downloads_attachment_to_archive_dir(self) -> None:
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            downloader = _FakeDownloader(data={"fk_1": b"PK-xlsx-bytes"})

            async def scenario() -> dict:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                return await service.ingest_group_archive_event(
                    _file_event(),
                    downloader=downloader,
                    archive_dir=archive_dir,
                    wait_attachments=True,
                )

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "ok")
            attachments = result.get("attachments") or []
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["status"], "success")
            self.assertEqual(attachments[0]["file_name"], "经营日报1009.xlsx")

            saved = Path(attachments[0]["saved_path"])
            self.assertTrue(saved.is_file(), saved)
            self.assertEqual(saved.read_bytes(), b"PK-xlsx-bytes")
            # 附件必须落在归档目录内（chat/日期 分层），不得写入仓库其他位置。
            self.assertTrue(str(saved).startswith(str(Path(archive_dir))))
            self.assertIn("om_f", saved.name)

    def test_ingest_returns_fast_and_downloads_in_background(self) -> None:
        """生产路径：元数据先落库立即返回，附件后台下载（防网关 8s 转发超时）。"""
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            downloader = _FakeDownloader(data={"fk_1": b"PK-xlsx-bytes"})

            async def scenario() -> None:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                result = await service.ingest_group_archive_event(
                    _file_event(), downloader=downloader, archive_dir=archive_dir
                )
                # 接口必须立即返回（附件仍 pending，不等下载）。
                self.assertEqual(result["attachments"][0]["status"], "pending")

                # 后台任务完成（轮询有界等待）。
                for _ in range(100):
                    payload = await service.query_group_archive(limit=5)
                    atts = payload["messages"][0]["attachments"] if payload["messages"] else []
                    if atts and atts[0]["status"] != "pending":
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(atts[0]["status"], "success")
                self.assertTrue(Path(atts[0]["saved_path"]).is_file())

            asyncio.run(scenario())

    def test_retry_stale_attachments_heals_failed_rows(self) -> None:
        """下载失败（如权限未开）的附件事后可被重试治愈，元数据从未丢。"""
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            failing = _FakeDownloader(fail_keys={"fk_1"})

            async def scenario() -> None:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                result = await service.ingest_group_archive_event(
                    _file_event(),
                    downloader=failing,
                    archive_dir=archive_dir,
                    wait_attachments=True,
                )
                self.assertEqual(result["attachments"][0]["status"], "download_failed")

                healed = await service.retry_stale_attachments(
                    downloader=_FakeDownloader(data={"fk_1": b"healed-bytes"}),
                    archive_dir=archive_dir,
                )
                self.assertEqual(healed["retried"], 1)

                payload = await service.query_group_archive(limit=5)
                atts = payload["messages"][0]["attachments"]
                self.assertEqual(atts[0]["status"], "success")
                self.assertEqual(Path(atts[0]["saved_path"]).read_bytes(), b"healed-bytes")

            asyncio.run(scenario())

    def test_ingest_attachment_download_failure_keeps_metadata(self) -> None:
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            downloader = _FakeDownloader(fail_keys={"fk_1"})

            async def scenario() -> dict:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                return await service.ingest_group_archive_event(
                    _file_event(),
                    downloader=downloader,
                    archive_dir=archive_dir,
                    wait_attachments=True,
                )

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "ok")
            attachments = result.get("attachments") or []
            self.assertEqual(attachments[0]["status"], "download_failed")

            from app.domain.group_archive import service

            rows = service.debug_recent_messages()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["message_type"], "file")

    def test_ingest_post_message_extracts_text_and_inline_images(self) -> None:
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            downloader = _FakeDownloader(data={"img_inline": b"png-bytes"})

            async def scenario() -> dict:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                return await service.ingest_group_archive_event(
                    _post_event(),
                    downloader=downloader,
                    archive_dir=archive_dir,
                    wait_attachments=True,
                )

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "ok")
            attachments = result.get("attachments") or []
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["attachment_type"], "image")
            image_calls = [call for call in downloader.calls if call["file_key"] == "img_inline"]
            self.assertEqual(len(image_calls), 1)
            self.assertEqual(image_calls[0]["resource_type"], "image")

            from app.domain.group_archive import service

            rows = service.debug_recent_messages()
            self.assertIn("出租率86%", rows[0]["text_content"])
            self.assertIn("ADR 430", rows[0]["text_content"])

    def test_ingest_rejects_payload_without_message_identity(self) -> None:
        with _tolerant_isolated_db():
            async def scenario() -> dict:
                from app.domain.group_archive import service

                return await service.ingest_group_archive_event({"foo": "bar"})

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "invalid_event")


class GroupArchiveQueryTests(unittest.TestCase):
    def test_query_filters_by_date_keyword_and_type(self) -> None:
        with _tolerant_isolated_db(), tempfile.TemporaryDirectory() as archive_dir:
            async def scenario() -> None:
                from app.db import session as db_session
                from app.domain.group_archive import service

                await db_session.ensure_db_ready()
                await service.ingest_group_archive_event(
                    _text_event(message_id="om_d1", text="10月08日 实施：出租率80%", create_time="1760000000"),
                    archive_dir=archive_dir,
                )
                await service.ingest_group_archive_event(
                    _file_event(message_id="om_d2", file_key="fk_2", create_time="1760086400"),
                    archive_dir=archive_dir,
                )
                await service.ingest_group_archive_event(
                    _text_event(message_id="om_d3", text="10月10日 实施：出租率85%", create_time="1760172800"),
                    archive_dir=archive_dir,
                )

                by_keyword = await service.query_group_archive(query="出租率85%")
                self.assertEqual(by_keyword["total"], 1)
                self.assertEqual(by_keyword["messages"][0]["message_id"], "om_d3")

                by_type = await service.query_group_archive(message_type="file")
                self.assertEqual(by_type["total"], 1)
                self.assertEqual(by_type["messages"][0]["message_id"], "om_d2")

                by_chat = await service.query_group_archive(chat_id="oc_other")
                self.assertEqual(by_chat["total"], 0)

                limited = await service.query_group_archive(limit=2)
                self.assertEqual(limited["total"], 2)

                invalid_limit = await service.query_group_archive(limit=0)
                self.assertEqual(invalid_limit["status"], "invalid_arguments")

                # 日期区间过滤（本地时区口径，与实现一致）。
                from datetime import datetime

                day3 = datetime.fromtimestamp(1760172800).strftime("%Y-%m-%d")
                day1 = datetime.fromtimestamp(1760000000).strftime("%Y-%m-%d")
                only_day3 = await service.query_group_archive(
                    date_start=day3, date_end=day3
                )
                self.assertEqual(only_day3["total"], 1)
                self.assertEqual(only_day3["messages"][0]["message_id"], "om_d3")
                span = await service.query_group_archive(date_start=day1, date_end=day3)
                self.assertEqual(span["total"], 3)

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
