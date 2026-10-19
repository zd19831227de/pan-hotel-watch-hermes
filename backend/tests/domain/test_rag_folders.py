from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-rag-folder-management-supervisor-key"


def _hash_vector(text: str, dim: int = 32) -> list[float]:
    vec = [0.0] * dim
    for token in text.lower().split():
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
        sign = 1.0 if (bucket % 2 == 0) else -1.0
        vec[bucket] += sign
    return vec


class RagFolderManagementTests(unittest.TestCase):
    """文件夹实体管理：新建/重命名/删除、自学习默认归类、按名移动文档。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "rag-folder-test.db"
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

    def _bootstrap(self, client: TestClient) -> None:
        client.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://embed.example.test",
                "api_key": "embedding-secret-key",
            },
        )
        rag_service = importlib.import_module("app.domain.rag.service")

        async def fake_embed(texts):
            return [_hash_vector(text) for text in texts]

        patcher = mock.patch.object(rag_service, "embed_texts", side_effect=fake_embed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ingest(self, client: TestClient, **overrides) -> dict:
        payload = {
            "title": "退款政策",
            "source_type": "text",
            "content": "入住前 24 小时免费取消。",
        }
        payload.update(overrides)
        response = client.post("/api/v1/rag/documents", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["document"]

    def test_folders_endpoint_requires_supervisor_key(self) -> None:
        client = self._client()
        anonymous = TestClient(client.app)
        self.assertEqual(anonymous.get("/api/v1/rag/folders").status_code, 401)

    def test_distill_ingest_defaults_to_self_learning_folder(self) -> None:
        client = self._client()
        self._bootstrap(client)
        document = self._ingest(client, source_type="distilled")
        self.assertEqual(document["folder"], "自学习")

        detail = client.get(f"/api/v1/rag/documents/{document['id']}")
        self.assertEqual(detail.json()["document"]["folder"], "自学习")

        folders = client.get("/api/v1/rag/folders").json()
        names = {folder["name"] for folder in folders["folders"]}
        self.assertIn("自学习", names)
        self.assertEqual(
            next(f["document_count"] for f in folders["folders"] if f["name"] == "自学习"),
            1,
        )
        self.assertEqual(folders["unfiled_count"], 0)

    def test_folder_crud_create_rename_delete(self) -> None:
        client = self._client()
        created = client.post("/api/v1/rag/folders", json={"name": "运营 SOP"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["folder"]["name"], "运营 SOP")
        self.assertEqual(created.json()["folder"]["document_count"], 0)

        duplicate = client.post("/api/v1/rag/folders", json={"name": "运营 SOP"})
        self.assertEqual(duplicate.status_code, 422)

        folders = client.get("/api/v1/rag/folders").json()
        self.assertIn("运营 SOP", [f["name"] for f in folders["folders"]])

        folder_id = created.json()["folder"]["id"]
        renamed = client.patch(
            f"/api/v1/rag/folders/{folder_id}", json={"name": "运营规范"}
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["folder"]["name"], "运营规范")

        deleted = client.delete(f"/api/v1/rag/folders/{folder_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn(
            "运营规范",
            [f["name"] for f in client.get("/api/v1/rag/folders").json()["folders"]],
        )

    def test_delete_folder_moves_documents_to_unfiled(self) -> None:
        client = self._client()
        self._bootstrap(client)
        folder = client.post("/api/v1/rag/folders", json={"name": "调价知识"}).json()["folder"]
        document = self._ingest(client, folder="调价知识")
        self.assertEqual(document["folder"], "调价知识")

        client.delete(f"/api/v1/rag/folders/{folder['id']}")
        detail = client.get(f"/api/v1/rag/documents/{document['id']}")
        self.assertIsNone(detail.json()["document"]["folder"])
        self.assertEqual(
            client.get("/api/v1/rag/folders").json()["unfiled_count"], 1
        )

    def test_document_moves_by_folder_name(self) -> None:
        client = self._client()
        self._bootstrap(client)
        document = self._ingest(client)
        self.assertIsNone(document["folder"])

        moved = client.patch(
            f"/api/v1/rag/documents/{document['id']}", json={"folder": "运营 SOP"}
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["document"]["folder"], "运营 SOP")

        cleared = client.patch(
            f"/api/v1/rag/documents/{document['id']}", json={"folder": ""}
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["document"]["folder"])


if __name__ == "__main__":
    unittest.main()
