from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status

from app.api.security import (
    SERVICE_TOKEN_HEADER,
    require_supervisor_api_key,
    require_watch_service_token,
)
from app.core.settings import get_settings
from app.domain.config.service import _mask_secret
from app.domain.rag import repository
from app.domain.rag.service import (
    EmbeddingConfigUpdate,
    RagConfigError,
    RagDocumentCreate,
    RagDocumentUpdate,
    RagFolderCreate,
    RagSearchRequest,
    create_folder,
    delete_document,
    delete_folder,
    get_document_detail,
    get_embedding_config_snapshot,
    ingest_document,
    list_documents,
    list_folders,
    rename_folder,
    search,
    update_document,
    update_embedding_config,
)

router = APIRouter(prefix="/rag", tags=["rag"])
_EMBEDDING_TEST_TEXT = "pan-hotel-watch embedding model connectivity test"


def _rag_http_exception(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


async def _require_rag_search_access(
    service_token: Annotated[str | None, Header(alias=SERVICE_TOKEN_HEADER)] = None,
) -> None:
    settings = get_settings()
    environment = str(settings.environment or "").strip().lower()
    auth_required = bool(
        settings.pan_hotel_enforce_api_auth
        or environment not in {"", "local", "development", "test"}
    )
    if not auth_required:
        return
    await require_watch_service_token(service_token)


def _embeddings_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/embeddings"):
        return normalized
    return f"{normalized}/embeddings"


@router.get("/embedding-config", dependencies=[Depends(require_supervisor_api_key)])
async def get_embedding_config_route() -> dict[str, Any]:
    snapshot = await get_embedding_config_snapshot()
    return {"embedding_config": snapshot.model_dump(mode="json") if snapshot else None}


@router.post(
    "/embedding-config/test",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def test_embedding_config_route(
    update: EmbeddingConfigUpdate,
) -> dict[str, Any]:
    persisted = await repository.get_persisted_embedding_config()
    config = dict(persisted or {})
    config["provider"] = update.provider.strip()
    config["model_name"] = update.model_name.strip()
    config["base_url"] = update.base_url
    if update.api_key:
        config["api_key"] = update.api_key
    if update.dimensions is not None:
        config["dimensions"] = update.dimensions

    api_key = str(config.get("api_key") or "")
    result: dict[str, Any] = {
        "status": "config_required",
        "summary": "向量模型未配置或缺少 API key。",
        "provider": str(config.get("provider") or ""),
        "model_name": str(config.get("model_name") or ""),
        "base_url": str(config.get("base_url") or ""),
        "api_key_configured": bool(api_key),
        "api_key_masked": _mask_secret(api_key),
    }
    if not api_key:
        return result

    endpoint = _embeddings_endpoint(str(config["base_url"]))
    payload: dict[str, Any] = {
        "model": str(config["model_name"]),
        "input": [_EMBEDDING_TEST_TEXT],
    }
    if config.get("dimensions"):
        payload["dimensions"] = config["dimensions"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        result["status"] = "failed"
        result["summary"] = f"向量模型请求失败：{type(exc).__name__}"
        return result
    if not response.is_success:
        result["status"] = "failed"
        result["summary"] = f"向量模型返回错误（HTTP {response.status_code}）。"
        return result
    try:
        body = response.json()
    except ValueError:
        result["status"] = "failed"
        result["summary"] = "向量模型返回非 JSON 响应。"
        return result
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        result["status"] = "failed"
        result["summary"] = "向量模型响应缺少 data 数组。"
        return result
    first = data[0] if isinstance(data[0], dict) else None
    embedding = first.get("embedding") if first else None
    if not isinstance(embedding, list) or not embedding:
        result["status"] = "failed"
        result["summary"] = "向量模型未返回有效 embedding。"
        return result
    result["status"] = "success"
    result["summary"] = "向量模型连接测试成功。"
    result["vector_length"] = len(embedding)
    return result


@router.put("/embedding-config", dependencies=[Depends(require_supervisor_api_key)])
async def update_embedding_config_route(update: EmbeddingConfigUpdate) -> dict[str, Any]:
    try:
        snapshot = await update_embedding_config(update)
    except ValueError as exc:
        raise _rag_http_exception(exc) from exc
    return {"embedding_config": snapshot.model_dump(mode="json")}


@router.get("/documents", dependencies=[Depends(require_supervisor_api_key)])
async def list_documents_route() -> dict[str, Any]:
    return {"documents": await list_documents()}


@router.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supervisor_api_key)],
)
async def create_document_route(request: RagDocumentCreate) -> dict[str, Any]:
    try:
        document = await ingest_document(request)
    except (ValueError, RagConfigError) as exc:
        raise _rag_http_exception(exc) from exc
    return {"document": document}


@router.get(
    "/documents/{document_id}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def get_document_route(
    document_id: Annotated[int, Path(ge=1)],
) -> dict[str, Any]:
    document = await get_document_detail(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该知识库文档。")
    return {"document": document}


@router.patch(
    "/documents/{document_id}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def update_document_route(
    document_id: Annotated[int, Path(ge=1)],
    update: RagDocumentUpdate,
) -> dict[str, Any]:
    try:
        document = await update_document(document_id, update)
    except (ValueError, RagConfigError) as exc:
        raise _rag_http_exception(exc) from exc
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该知识库文档。")
    return {"document": document}


@router.delete("/documents/{document_id}", dependencies=[Depends(require_supervisor_api_key)])
async def delete_document_route(
    document_id: Annotated[int, Path(ge=1)],
) -> dict[str, Any]:
    deleted = await delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该知识库文档。")
    return {"status": "deleted"}


@router.get("/folders", dependencies=[Depends(require_supervisor_api_key)])
async def list_folders_route() -> dict[str, Any]:
    return await list_folders()


@router.post(
    "/folders",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supervisor_api_key)],
)
async def create_folder_route(request: RagFolderCreate) -> dict[str, Any]:
    try:
        folder = await create_folder(request)
    except ValueError as exc:
        raise _rag_http_exception(exc) from exc
    return {"folder": folder}


@router.patch(
    "/folders/{folder_id}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def rename_folder_route(
    folder_id: Annotated[int, Path(ge=1)],
    request: RagFolderCreate,
) -> dict[str, Any]:
    try:
        folder = await rename_folder(folder_id, request)
    except ValueError as exc:
        raise _rag_http_exception(exc) from exc
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该文件夹。")
    return {"folder": folder}


@router.delete("/folders/{folder_id}", dependencies=[Depends(require_supervisor_api_key)])
async def delete_folder_route(
    folder_id: Annotated[int, Path(ge=1)],
) -> dict[str, Any]:
    deleted = await delete_folder(folder_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该文件夹。")
    return {"status": "deleted"}


@router.post("/search", dependencies=[Depends(_require_rag_search_access)])
async def search_route(request: RagSearchRequest) -> dict[str, Any]:
    try:
        results = await search(request)
    except (ValueError, RagConfigError) as exc:
        raise _rag_http_exception(exc) from exc
    return {"results": results}
