"""飞书文件发送端点测试（Hermes 文档产出链路后端半边）：只发文件，无任何执行入口。"""

from __future__ import annotations

import base64
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers
from app.main import app


def _payload() -> str:
    return base64.b64encode(b"fake-xlsx-bytes").decode("ascii")


class FeishuOperatorFileSendTests(unittest.TestCase):
    def test_send_operator_file_uploads_then_sends(self) -> None:
        uploaded: dict[str, object] = {}

        class FakeClient:
            def __init__(self, *, chat_id: str | None = None) -> None:
                _ = chat_id

            async def upload_file(self, file_bytes: bytes, *, filename: str) -> dict:
                uploaded["filename"] = filename
                uploaded["size"] = len(file_bytes)
                return {"status": "success", "file_key": "fk_1"}

            async def send_file(self, *, chat_id: str, file_key: str) -> dict:
                uploaded["sent_chat"] = chat_id
                uploaded["file_key"] = file_key
                return {"status": "success", "summary": "ok"}

        with mock.patch("app.api.v1.feishu.FeishuBotClient", FakeClient):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-file-sends",
                headers=service_token_headers(),
                json={
                    "chat_id": "oc_test",
                    "doc_type": "xlsx",
                    "title": "国庆经营汇报",
                    "data_base64": _payload(),
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["filename"], "国庆经营汇报.xlsx")
        self.assertEqual(uploaded["sent_chat"], "oc_test")
        self.assertEqual(uploaded["file_key"], "fk_1")

    def test_send_operator_file_rejects_bad_base64(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/v1/feishu/operator-file-sends",
            headers=service_token_headers(),
            json={"chat_id": "oc_test", "doc_type": "xlsx", "data_base64": "!!!not-base64!!!"},
        )
        self.assertEqual(response.status_code, 422)

    def test_send_operator_file_reports_upload_failure_without_raising(self) -> None:
        class FakeClient:
            def __init__(self, *, chat_id: str | None = None) -> None:
                _ = chat_id

            async def upload_file(self, file_bytes: bytes, *, filename: str) -> dict:
                return {"status": "failed", "summary": "Feishu file upload failed."}

            async def send_file(self, *, chat_id: str, file_key: str) -> dict:  # pragma: no cover
                raise AssertionError("upload 失败时不应走到发送")

        with mock.patch("app.api.v1.feishu.FeishuBotClient", FakeClient):
            client = TestClient(app)
            response = client.post(
                "/api/v1/feishu/operator-file-sends",
                headers=service_token_headers(),
                json={
                    "chat_id": "oc_test",
                    "doc_type": "docx",
                    "filename": "report.docx",
                    "data_base64": _payload(),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertIn("上传失败", response.json()["summary"])


if __name__ == "__main__":
    unittest.main()
