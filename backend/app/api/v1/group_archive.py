"""群资料归档 API：网关插件转发事件入库（POST）+ MCP 查询（GET）。

均为内部服务接口（service token 保护）；数据永久保留，无删除入口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.security import require_watch_service_token
from app.domain.group_archive import service
from app.domain.notifications.feishu_client import FeishuBotClient

router = APIRouter(prefix="/group-archive", tags=["group-archive"])


def _attachment_downloader():
    """下载闭包：按消息所属群解析对应飞书应用凭据（多店多应用映射兼容）。"""

    async def _download(
        *, message_id: str, file_key: str, resource_type: str, chat_id: str = ""
    ) -> dict[str, Any]:
        client = FeishuBotClient(chat_id=chat_id)
        return await client.download_message_resource(
            message_id=message_id,
            file_key=file_key,
            resource_type=resource_type,
        )

    return _download


@router.post("/events", dependencies=[Depends(require_watch_service_token)])
async def ingest_group_archive_event(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return await service.ingest_group_archive_event(
        payload,
        downloader=_attachment_downloader(),
    )


@router.get("/messages", dependencies=[Depends(require_watch_service_token)])
async def list_group_archive_messages(
    query: str = "",
    date_start: str = "",
    date_end: str = "",
    chat_id: str = "",
    message_type: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return await service.query_group_archive(
        query=query,
        date_start=date_start,
        date_end=date_end,
        chat_id=chat_id,
        message_type=message_type,
        limit=limit,
    )
