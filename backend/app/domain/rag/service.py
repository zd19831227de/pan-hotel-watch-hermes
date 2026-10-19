"""V5 Step 4 — RAG knowledge base MVP.

Phase 1 (this module): vector-model config + document chunking/ingestion +
retrieval test. The vector store is a zero-dependency pure-Python cosine
similarity over embeddings stored as JSON in SQLite — adequate for a small
hotel-ops knowledge base and avoids native vector extensions on Windows.

Phase 2 (Hermes context injection) is intentionally out of scope here: the
Hermes agent does not expose a context-injection hook in this repository, so
injecting retrieved context into its conversation must be designed separately
(see docs/pan-hotel-watch-dev-plan-v5.md Step 4). The retrieval endpoint below
is the stable contract such a future hook would call.

The embedding model base_url is SSRF-validated via the same
``_validate_llm_base_url`` guard the LLM config uses.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from app.domain.config.service import _mask_secret, _validate_llm_base_url
from app.domain.rag import repository

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
DEFAULT_DISTILL_FOLDER = "自学习"
_WHITESPACE_RE = re.compile(r"\s+")


class RagConfigError(RuntimeError):
    pass


class EmbeddingConfigUpdate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    dimensions: int | None = Field(default=None, ge=1, le=8192)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_llm_base_url(value)


class EmbeddingConfigSnapshot(BaseModel):
    provider: str
    model_name: str
    base_url: str
    api_key_configured: bool
    api_key_masked: str | None = None
    dimensions: int | None = None


class RagDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    source_type: str = Field(default="text")
    folder: str | None = Field(default=None, max_length=128)
    content: str = Field(min_length=1)


class RagDocumentUpdate(BaseModel):
    """Partial update. ``content`` changes re-chunk and re-embed; title/folder
    changes are metadata-only and never call the embedding model."""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    folder: str | None = Field(default=None, max_length=128)
    content: str | None = Field(default=None, min_length=1)


class RagFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)


async def get_embedding_config_snapshot() -> EmbeddingConfigSnapshot | None:
    config = await repository.get_persisted_embedding_config()
    if config is None:
        return None
    return _snapshot_from_config(config)


async def update_embedding_config(
    update: EmbeddingConfigUpdate,
) -> EmbeddingConfigSnapshot:
    config = await repository.upsert_persisted_embedding_config(
        provider=update.provider.strip(),
        model_name=update.model_name.strip(),
        base_url=update.base_url,
        api_key=update.api_key,
        dimensions=update.dimensions,
    )
    return _snapshot_from_config(config)


def _snapshot_from_config(config: dict[str, Any]) -> EmbeddingConfigSnapshot:
    api_key = str(config.get("api_key") or "")
    return EmbeddingConfigSnapshot(
        provider=config["provider"],
        model_name=config["model_name"],
        base_url=config["base_url"],
        api_key_configured=bool(api_key),
        api_key_masked=_mask_secret(api_key),
        dimensions=config.get("dimensions"),
    )


# --- chunking ---

def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split ``text`` into overlapping windows by character count.

    Whitespace is collapsed so embeddings are not skewed by formatting, and
    windows step by ``size - overlap``.
    """

    normalized = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    step = max(1, size - overlap)
    chunks: list[str] = []
    for start in range(0, len(normalized), step):
        chunk = normalized[start : start + size]
        if chunk:
            chunks.append(chunk)
        if start + size >= len(normalized):
            break
    return chunks


# --- embeddings ---

def _embeddings_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/embeddings"):
        return normalized
    return f"{normalized}/embeddings"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` via the configured embedding model.

    Raises :class:`RagConfigError` when no model is configured or the request
    fails. Callers that need offline/deterministic behavior (tests) patch
    ``app.domain.rag.service.embed_texts``.
    """

    if not texts:
        return []
    config = await repository.get_persisted_embedding_config()
    if config is None or not config.get("api_key"):
        raise RagConfigError("向量模型未配置或缺少 API key。")
    endpoint = _embeddings_endpoint(config["base_url"])
    payload: dict[str, Any] = {"model": config["model_name"], "input": texts}
    if config.get("dimensions"):
        payload["dimensions"] = config["dimensions"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise RagConfigError(f"向量模型请求失败：{type(exc).__name__}") from exc
    if not response.is_success:
        raise RagConfigError(f"向量模型返回错误（HTTP {response.status_code}）。")
    return _parse_embeddings_response(response)


def _parse_embeddings_response(response: httpx.Response) -> list[list[float]]:
    try:
        body = response.json()
    except ValueError as exc:
        raise RagConfigError("向量模型返回非 JSON 响应。") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise RagConfigError("向量模型响应缺少 data 数组。")
    ordered = sorted(data, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)
    vectors: list[list[float]] = []
    for entry in ordered:
        embedding = entry.get("embedding") if isinstance(entry, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise RagConfigError("向量模型响应缺少 embedding 字段。")
        vectors.append([float(value) for value in embedding])
    return vectors


# --- documents ---

def normalize_folder(folder: str | None) -> str | None:
    """Blank/whitespace folders mean 未分组 (NULL)."""

    normalized = str(folder or "").strip()
    return normalized or None


async def ingest_document(request: RagDocumentCreate) -> dict[str, Any]:
    content = request.content.strip()
    chunks_raw = chunk_text(content)
    if not chunks_raw:
        raise RagConfigError("文档内容为空，无法切片入库。")
    vectors = await embed_texts(chunks_raw)
    if len(vectors) != len(chunks_raw):
        raise RagConfigError("向量模型返回的向量数量与切片数量不一致。")
    chunks = [
        {"text": text, "embedding_json": json.dumps(vector, ensure_ascii=False)}
        for text, vector in zip(chunks_raw, vectors, strict=True)
    ]
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    folder = normalize_folder(request.folder)
    if folder is None and request.source_type == "distilled":
        folder = DEFAULT_DISTILL_FOLDER
    return await repository.create_rag_document(
        title=request.title.strip(),
        source_type=request.source_type,
        folder=folder,
        content_hash=content_hash,
        raw_content=content,
        chunks=chunks,
    )


async def get_document_detail(document_id: int) -> dict[str, Any] | None:
    return await repository.get_rag_document(document_id)


async def list_folders() -> dict[str, Any]:
    return await repository.list_rag_folders()


async def create_folder(request: RagFolderCreate) -> dict[str, Any]:
    return await repository.create_rag_folder(request.name.strip())


async def rename_folder(folder_id: int, request: RagFolderCreate) -> dict[str, Any] | None:
    return await repository.rename_rag_folder(folder_id, request.name.strip())


async def delete_folder(folder_id: int) -> bool:
    return await repository.delete_rag_folder(folder_id)


async def update_document(document_id: int, update: RagDocumentUpdate) -> dict[str, Any] | None:
    current = await repository.get_rag_document(document_id)
    if current is None:
        return None

    changed_fields = update.model_fields_set
    if "title" in changed_fields and update.title is not None:
        title = update.title.strip()
        if not title:
            raise RagConfigError("文档标题不能为空。")
    else:
        title = current["title"]
    if "folder" in changed_fields:
        folder = normalize_folder(update.folder)
    else:
        folder = normalize_folder(current.get("folder"))

    if "content" not in changed_fields or update.content is None:
        return await repository.update_rag_document(document_id, title=title, folder=folder)

    content = update.content.strip()
    chunks_raw = chunk_text(content)
    if not chunks_raw:
        raise RagConfigError("文档内容为空，无法切片更新。")
    vectors = await embed_texts(chunks_raw)
    if len(vectors) != len(chunks_raw):
        raise RagConfigError("向量模型返回的向量数量与切片数量不一致。")
    chunks = [
        {"text": text, "embedding_json": json.dumps(vector, ensure_ascii=False)}
        for text, vector in zip(chunks_raw, vectors, strict=True)
    ]
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return await repository.update_rag_document(
        document_id,
        title=title,
        folder=folder,
        raw_content=content,
        content_hash=content_hash,
        chunks=chunks,
    )


async def list_documents() -> list[dict[str, Any]]:
    return await repository.list_rag_documents()


async def delete_document(document_id: int) -> bool:
    return await repository.delete_rag_document(document_id)


# --- retrieval ---

async def search(request: RagSearchRequest) -> list[dict[str, Any]]:
    query_vector = (await embed_texts([request.query.strip()]))[0]
    rows = await repository.list_rag_chunks_with_documents()
    if not rows:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        try:
            chunk_vector = json.loads(row["embedding_json"])
        except (TypeError, ValueError):
            continue
        score = cosine_similarity(query_vector, chunk_vector)
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[dict[str, Any]] = []
    for score, row in scored[: request.top_k]:
        results.append(
            {
                "document_id": row["document_id"],
                "title": row["title"],
                "chunk_text": row["text"],
                "score": round(score, 6),
            }
        )
    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
