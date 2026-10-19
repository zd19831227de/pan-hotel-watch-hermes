from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.api.security import require_watch_service_token
from app.domain.notifications.feishu_bot import handle_event
from app.domain.notifications.feishu_client import FeishuBotClient
from app.domain.notifications.operator_feishu_card import (
    deliver_operator_briefing_card,
    deliver_operator_text_as_card,
)

router = APIRouter(prefix="/feishu", tags=["feishu"])


class OperatorCardReplyRequest(BaseModel):
    message_id: str = Field(min_length=1)
    chat_id: str | None = None
    card: dict[str, Any]
    fallback_text: str | None = None


class OperatorFileSendRequest(BaseModel):
    """Hermes 渲染的办公文档（xlsx/docx/pptx）发到当前群；只发文件不触发任何执行。"""

    chat_id: str = Field(min_length=1)
    title: str = ""
    filename: str = ""
    doc_type: str = Field(default="xlsx", pattern=r"^(xlsx|docx|pptx|pdf)$")
    data_base64: str = Field(min_length=1)
    store_hint: str | None = None


class OperatorCardSendRequest(BaseModel):
    chat_id: str = Field(min_length=1)
    card: dict[str, Any] | None = None
    text: str | None = None
    title: str | None = None
    fallback_text: str | None = None
    store_hint: str | None = None
    status: str | None = None

    @model_validator(mode="after")
    def require_card_or_text(self) -> OperatorCardSendRequest:
        has_card = isinstance(self.card, dict) and bool(self.card)
        has_text = bool(str(self.text or "").strip())
        if not has_card and not has_text:
            raise ValueError("card 与 text 至少提供一个")
        return self


# 飞书卡片回调未及时 2xx 会按同一 event_id 重投；审批类事件重投/并发重投
# 可能把同一条真实写入执行两次。按 event_id 做进程内短期幂等（写入链路
# 不应依赖概率），TTL 覆盖飞书重试窗口。
_EVENT_DEDUP_TTL_SECONDS = 900.0
_EVENT_CACHE_LIMIT = 500
_event_results: dict[str, tuple[float, dict[str, Any]]] = {}
_event_locks: dict[str, asyncio.Lock] = {}
_event_cache_guard = asyncio.Lock()


def _extract_feishu_event_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    header = payload.get("header")
    if isinstance(header, dict):
        event_id = str(header.get("event_id") or "").strip()
        if event_id:
            return event_id
    for key in ("event_id", "uuid"):
        event_id = str(payload.get(key) or "").strip()
        if event_id:
            return event_id
    return None


async def _event_lock(event_id: str) -> asyncio.Lock:
    async with _event_cache_guard:
        lock = _event_locks.get(event_id)
        if lock is None:
            lock = asyncio.Lock()
            _event_locks[event_id] = lock
        return lock


def _cached_event_response(event_id: str) -> dict[str, Any] | None:
    cached = _event_results.get(event_id)
    if cached is None:
        return None
    stored_at, response = cached
    if time.monotonic() - stored_at > _EVENT_DEDUP_TTL_SECONDS:
        _event_results.pop(event_id, None)
        return None
    return response


def _store_event_response(event_id: str, response: dict[str, Any]) -> None:
    now = time.monotonic()
    if len(_event_results) >= _EVENT_CACHE_LIMIT:
        for key in [
            key for key, (stored_at, _) in _event_results.items()
            if now - stored_at > _EVENT_DEDUP_TTL_SECONDS
        ]:
            _event_results.pop(key, None)
        if len(_event_results) >= _EVENT_CACHE_LIMIT:
            oldest = min(_event_results, key=lambda key: _event_results[key][0])
            _event_results.pop(oldest, None)
    _event_results[event_id] = (now, response)
    if len(_event_locks) > _EVENT_CACHE_LIMIT:
        for key in [
            key for key, lock in _event_locks.items()
            if not lock.locked() and key not in _event_results
        ][: len(_event_locks) - _EVENT_CACHE_LIMIT]:
            _event_locks.pop(key, None)


def _event_response(result: Any) -> dict[str, Any]:
    callback_response = (
        result.get("card_callback_response") if isinstance(result, dict) else None
    )
    if isinstance(callback_response, dict):
        return callback_response
    return result if isinstance(result, dict) else {"status": str(result)}


@router.post("/events", dependencies=[Depends(require_watch_service_token)])
async def handle_feishu_event(request: Request) -> dict[str, Any]:
    payload = await request.json()
    event_id = _extract_feishu_event_id(payload)
    if not event_id:
        return _event_response(await handle_event(payload))
    lock = await _event_lock(event_id)
    async with lock:
        cached = _cached_event_response(event_id)
        if cached is not None:
            return cached
        result = await handle_event(payload)
        response = _event_response(result)
        _store_event_response(event_id, response)
        return response


@router.post(
    "/operator-card-replies",
    dependencies=[Depends(require_watch_service_token)],
)
async def reply_operator_card(request: OperatorCardReplyRequest) -> dict[str, Any]:
    """Hermes MCP 代发运营回复卡片；不触发 OTA 写价或审批执行。"""
    return await FeishuBotClient(chat_id=request.chat_id).reply_interactive_card(
        request.message_id,
        request.card,
        fallback_text=request.fallback_text,
    )


@router.post(
    "/operator-card-sends",
    dependencies=[Depends(require_watch_service_token)],
)
async def send_operator_card(request: OperatorCardSendRequest) -> dict[str, Any]:
    """Hermes 由应用机器人在当前群发送简报式卡片；不走监控组 webhook。"""
    if isinstance(request.card, dict) and request.card:
        return await deliver_operator_briefing_card(
            chat_id=request.chat_id,
            card=request.card,
            fallback_text=request.fallback_text,
            store_hint=request.store_hint,
        )
    try:
        return await deliver_operator_text_as_card(
            chat_id=request.chat_id,
            text=str(request.text or ""),
            title=request.title,
            fallback_text=request.fallback_text,
            store_hint=request.store_hint,
            status=str(request.status or "success"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/operator-file-sends",
    dependencies=[Depends(require_watch_service_token)],
)
async def send_operator_file(request: OperatorFileSendRequest) -> dict[str, Any]:
    """Hermes 渲染的办公文档（xlsx/docx/pptx）发到当前群；只发文件，不触发 OTA 写价或执行。"""
    import base64 as _base64

    try:
        file_bytes = _base64.b64decode(request.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="data_base64 不是合法 base64") from exc
    if not file_bytes:
        raise HTTPException(status_code=422, detail="文档内容为空")
    filename = str(request.filename or "").strip() or (
        f"{request.title or '运营文档'}.{request.doc_type}"
    )
    bot = FeishuBotClient(chat_id=request.chat_id)
    upload = await bot.upload_file(file_bytes, filename=filename)
    if upload.get("status") != "success":
        return {
            "status": "failed",
            "summary": f"飞书文件上传失败：{upload.get('summary') or '未知错误'}",
            "evidence": upload.get("evidence"),
            "next_action": "请稍后重试或改用文字回复。",
        }
    sent = await bot.send_file(chat_id=request.chat_id, file_key=str(upload.get("file_key")))
    return {
        "status": "success" if sent.get("status") == "success" else "failed",
        "summary": (
            f"已向本群发送文件 {filename}"
            if sent.get("status") == "success"
            else f"文件已上传但消息发送失败：{sent.get('summary') or '未知错误'}"
        ),
        "filename": filename,
        "doc_type": request.doc_type,
    }
