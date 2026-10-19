from __future__ import annotations

import hashlib
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-rag-folder-management-supervisor-key"

_LEGACY_RAG_DOCUMENTS_DDL = """
CREATE TABLE rag_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title VARCHAR(256) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'text',
    content_hash VARCHAR(64) NOT NULL,
    raw_content TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NULL,
    updated_at DATETIME NULL
)
"""


def _hash_vector(text: str, dim: int = 32) -> list[float]:
    vec = [0.0] * dim
    for token in text.lower().split():
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
        sign = 1.0 if (bucket % 2 == 0) else -1.0
        vec[bucket] += sign
    return vec


class RagDocumentManagementTests(unittest.TestCase):
    """RAG 文档管理：详情查看、编辑（仅元数据不重嵌入）、文件夹归属与老库迁移。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "rag-mgmt-test.db"
        self._saved_env: dict[str, str | None] = {}
        for key, value in {
            "DATABASE_URL": f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            "DATABASE_AUTO_CREATE_TABLES": "true",
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY": BOOTSTRAP_SUPERVISOR_API_KEY,
            "LLM_STARTUP_HEALTH_CHECK_ENABLED": "false",
            "LLM_ALLOWED_HOSTS": "api.deepseek.com,embed.example.test",
        }.items():
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value
        self.embed_calls: list[list[str]] = []

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

    def _client(self) -> TestClient:
        for modname in (
            "app.core.settings",
            "app.api.security",
            "app.db.session",
            "app.domain.config.service",
        ):
            module = importlib.import_module(modname)
            get_settings = getattr(module, "get_settings", None)
            if get_settings is not None and hasattr(get_settings, "cache_clear"):
                get_settings.cache_clear()
        settings = importlib.import_module("app.core.settings")
        db_session = importlib.import_module("app.db.session")
        db_url = os.environ["DATABASE_URL"]
        db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        main = importlib.import_module("app.main")
        app = importlib.reload(main).create_app()
        client = TestClient(app)
        client.headers["X-Pan-Hotel-API-Key"] = BOOTSTRAP_SUPERVISOR_API_KEY
        return client

    def _configure_and_patch_embedder(self) -> None:
        client_holder = self._client()
        client_holder.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://embed.example.test",
                "api_key": "embedding-secret-key",
            },
        )
        rag_service = importlib.import_module("app.domain.rag.service")
        calls = self.embed_calls

        async def fake_embed(texts):
            calls.append(list(texts))
            return [_hash_vector(text) for text in texts]

        patcher = mock.patch.object(rag_service, "embed_texts", side_effect=fake_embed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ingest(self, client: TestClient, **overrides) -> dict:
        payload = {
            "title": "退款政策",
            "source_type": "text",
            "content": "静里酒店退款政策：入住前 24 小时免费取消。",
            "folder": "运营 SOP",
        }
        payload.update(overrides)
        response = client.post("/api/v1/rag/documents", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["document"]

    def test_ingest_with_folder_detail_and_missing_404(self) -> None:
        self._configure_and_patch_embedder()
        client = self._client()
        document = self._ingest(client)
        self.assertEqual(document["folder"], "运营 SOP")

        listed = client.get("/api/v1/rag/documents")
        self.assertEqual(listed.status_code, 200)
        documents = listed.json()["documents"]
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["folder"], "运营 SOP")
        self.assertNotIn("raw_content", documents[0])

        detail = client.get(f"/api/v1/rag/documents/{document['id']}")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()["document"]
        self.assertEqual(body["folder"], "运营 SOP")
        self.assertEqual(body["raw_content"], "静里酒店退款政策：入住前 24 小时免费取消。")
        self.assertIn("updated_at", body)

        missing = client.get("/api/v1/rag/documents/999999")
        self.assertEqual(missing.status_code, 404)

    def test_patch_title_and_folder_skips_embedding(self) -> None:
        self._configure_and_patch_embedder()
        client = self._client()
        document = self._ingest(client)
        embed_calls_after_ingest = len(self.embed_calls)
        self.assertGreaterEqual(embed_calls_after_ingest, 1)

        patched = client.patch(
            f"/api/v1/rag/documents/{document['id']}",
            json={"title": "退款政策 v2", "folder": "调价知识"},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        body = patched.json()["document"]
        self.assertEqual(body["title"], "退款政策 v2")
        self.assertEqual(body["folder"], "调价知识")
        self.assertEqual(body["chunk_count"], document["chunk_count"])
        # 仅元数据变更不得触发重嵌入。
        self.assertEqual(len(self.embed_calls), embed_calls_after_ingest)

        cleared = client.patch(
            f"/api/v1/rag/documents/{document['id']}",
            json={"folder": ""},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["document"]["folder"])

    def test_patch_content_rechunks_and_reembeds(self) -> None:
        self._configure_and_patch_embedder()
        client = self._client()
        document = self._ingest(client)
        embed_calls_after_ingest = len(self.embed_calls)

        new_content = "调价安全契约：直连账号仅允许生成建议，不允许自动化改价。 " * 20
        patched = client.patch(
            f"/api/v1/rag/documents/{document['id']}",
            json={"content": new_content},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        body = patched.json()["document"]
        self.assertEqual(body["raw_content"], new_content.strip())
        self.assertGreater(body["chunk_count"], document["chunk_count"])
        self.assertGreater(len(self.embed_calls), embed_calls_after_ingest)

        detail = client.get(f"/api/v1/rag/documents/{document['id']}")
        self.assertEqual(detail.json()["document"]["chunk_count"], body["chunk_count"])

        results = client.post(
            "/api/v1/rag/search",
            json={"query": "直连 账号 建议", "top_k": 3},
        )
        self.assertEqual(results.status_code, 200)
        self.assertTrue(results.json()["results"])

    def test_patch_missing_or_invalid_returns_errors(self) -> None:
        self._configure_and_patch_embedder()
        client = self._client()
        missing = client.patch(
            "/api/v1/rag/documents/999999",
            json={"title": "无此文档"},
        )
        self.assertEqual(missing.status_code, 404)

        document = self._ingest(client)
        invalid = client.patch(
            f"/api/v1/rag/documents/{document['id']}",
            json={"title": " "},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_folder_column_migration_on_legacy_table(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute(_LEGACY_RAG_DOCUMENTS_DDL)
        connection.commit()
        connection.close()

        self._configure_and_patch_embedder()
        client = self._client()
        document = self._ingest(client, title="老库文档", folder="迁移文件夹")

        probe = sqlite3.connect(self.db_path)
        columns = [row[1] for row in probe.execute("PRAGMA table_info(rag_documents)")]
        probe.close()
        self.assertIn("folder_id", columns)

        detail = client.get(f"/api/v1/rag/documents/{document['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["document"]["folder"], "迁移文件夹")


if __name__ == "__main__":
    unittest.main()
