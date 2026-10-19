from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-v5-step4-bootstrap-supervisor-key"


def _hash_vector(text: str, dim: int = 32) -> list[float]:
    """Deterministic pseudo-embedding: bag-of-hashed-tokens projected to a
    fixed-size vector. Good enough to make ``cosine_similarity`` reward shared
    tokens between a query and a chunk — i.e. retrieval is meaningful without
    any network embedding model."""

    vec = [0.0] * dim
    for token in text.lower().split():
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
        sign = 1.0 if (bucket % 2 == 0) else -1.0
        vec[bucket] += sign
    return vec


class RagKnowledgeBaseTests(unittest.TestCase):
    """V5 Step 4 — RAG MVP: config + ingest + retrieval (offline embeddings)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "rag-test.db"
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
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        main = importlib.import_module("app.main")
        app = importlib.reload(main).create_app()
        client = TestClient(app)
        client.headers["X-Pan-Hotel-API-Key"] = BOOTSTRAP_SUPERVISOR_API_KEY
        return client

    def _patch_embedder(self, client: TestClient) -> None:
        rag_service = importlib.import_module("app.domain.rag.service")

        async def fake_embed(texts):
            return [_hash_vector(text) for text in texts]

        self._embedder_patcher = mock.patch.object(rag_service, "embed_texts", side_effect=fake_embed)
        self._embedder_patcher.start()

    def tearDownPatch(self) -> None:  # not used; patcher per-test via addCleanup
        pass

    def test_rag_endpoints_require_supervisor_key(self) -> None:
        client = self._client()
        anonymous = TestClient(client.app)
        self.assertEqual(anonymous.get("/api/v1/rag/documents").status_code, 401)

    def test_embedding_config_validates_base_url_and_masks_key(self) -> None:
        client = self._client()
        rejected = client.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://internal-relay.intranet",
                "api_key": "some-key",
            },
        )
        self.assertEqual(rejected.status_code, 422)

        accepted = client.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://embed.example.test",
                "api_key": "embedding-secret-key",
            },
        )
        self.assertEqual(accepted.status_code, 200)
        snapshot = accepted.json()["embedding_config"]
        self.assertTrue(snapshot["api_key_configured"])
        self.assertEqual(snapshot["api_key_masked"], "embe...-key")
        self.assertNotIn("embedding-secret-key", accepted.text)

    def test_ingest_then_search_retrieves_relevant_chunk(self) -> None:
        client = self._client()
        client.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://embed.example.test",
                "api_key": "embedding-secret-key",
            },
        )
        self._patch_embedder(client)
        self.addCleanup(self._embedder_patcher.stop)

        ingested = client.post(
            "/api/v1/rag/documents",
            json={
                "title": "静里退款政策",
                "source_type": "text",
                "content": "静里酒店退款政策：入住前 24 小时免费取消。曼馨酒店退款政策：不可退款。",
            },
        )
        self.assertEqual(ingested.status_code, 201)
        document = ingested.json()["document"]
        self.assertGreater(document["chunk_count"], 0)

        listed = client.get("/api/v1/rag/documents")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["documents"]), 1)

        results = client.post(
            "/api/v1/rag/search",
            json={"query": "静里 退款 取消", "top_k": 5},
        )
        self.assertEqual(results.status_code, 200)
        hits = results.json()["results"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "静里退款政策")
        self.assertIn("退款", hits[0]["chunk_text"])

    def test_ingest_without_config_returns_clear_error(self) -> None:
        client = self._client()
        # No embedding config set → ingest must fail closed with a clear message.
        response = client.post(
            "/api/v1/rag/documents",
            json={"title": "无配置", "source_type": "text", "content": "内容"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("向量模型", response.text)

    def test_delete_document_removes_chunks(self) -> None:
        client = self._client()
        client.put(
            "/api/v1/rag/embedding-config",
            json={
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "base_url": "https://embed.example.test",
                "api_key": "embedding-secret-key",
            },
        )
        self._patch_embedder(client)
        self.addCleanup(self._embedder_patcher.stop)

        created = client.post(
            "/api/v1/rag/documents",
            json={"title": "待删除", "source_type": "text", "content": "一段要被删除的文本内容。"},
        )
        document_id = created.json()["document"]["id"]

        deleted = client.delete(f"/api/v1/rag/documents/{document_id}")
        self.assertEqual(deleted.status_code, 200)
        missing = client.delete(f"/api/v1/rag/documents/{document_id}")
        self.assertEqual(missing.status_code, 404)

    def test_chunk_text_helper_splits_with_overlap(self) -> None:
        rag_service = importlib.import_module("app.domain.rag.service")
        text = "词 " * 300  # ~600 chars
        chunks = rag_service.chunk_text(text, size=200, overlap=40)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))

    def test_cosine_similarity_is_zero_for_orthogonal_vectors(self) -> None:
        rag_service = importlib.import_module("app.domain.rag.service")
        self.assertEqual(rag_service.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertAlmostEqual(
            rag_service.cosine_similarity([1.0, 2.0], [2.0, 4.0]), 1.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
