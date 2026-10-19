"""群资料归档服务：飞书群消息/附件摄取入库 + 查询。

设计要点（2026-10-10 需求）：
- 运营每天把「日期实施经营数据」（文本/截图/Excel 等）人工丢进群，系统
  自动永久归档，供 AI 做跨日积累与分析；任何清理任务都不得触碰这两张表
  与附件目录。
- 存储形态：SQLite（真库 pan_hotel_watch.db）存结构化元数据与文本，原始
  附件落专用目录 data/feishu-group-archive/（路径回写库里），二者永不清理。
- 摄取尽力而为：元数据必先落库；附件下载失败只置 download_failed，记录
  与原始事件永不丢失，事后可重试。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.settings import get_settings
from app.db.session import session_scope
from app.domain.group_archive.models import (
    FeishuGroupArchiveAttachment,
    FeishuGroupArchiveMessage,
)

logger = logging.getLogger(__name__)

# 单附件下载上限：运营日报/截图远小于此；超限只记元数据不落盘。
ATTACHMENT_MAX_BYTES = 64 * 1024 * 1024

# downloader(message_id=..., file_key=..., resource_type=..., chat_id=...)
# -> {"status": "success", "data": bytes} | {"status": "failed", "summary": str}
AttachmentDownloader = Callable[..., Awaitable[dict[str, Any]]]

_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>\s*", re.IGNORECASE | re.DOTALL)
# 接收事件里 @人 在 text 中是 "@_user_N" 占位符（mentions 数组另附实名）。
_AT_PLACEHOLDER_RE = re.compile(r"@_user_\d+\s*")
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _safe_filename(name: str, *, fallback: str = "attachment") -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("_", str(name or "").strip()).strip("._ ")
    cleaned = cleaned[:180]
    return cleaned or fallback


def _image_extension(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:3] == b"GIF":
        return ".gif"
    return ".bin"


def _parse_content(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return raw if isinstance(raw, dict) else None


def _post_text_and_images(content: dict[str, Any]) -> tuple[str, list[str]]:
    texts: list[str] = []
    image_keys: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            tag = str(node.get("tag") or "")
            if tag == "text":
                piece = str(node.get("text") or "").strip()
                if piece:
                    texts.append(piece)
            elif tag == "img":
                key = str(node.get("image_key") or "").strip()
                if key:
                    image_keys.append(key)
            elif tag == "a":
                piece = str(node.get("text") or "").strip()
                if piece:
                    texts.append(piece)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    body = content.get("content")
    if isinstance(body, list):
        _walk(body)
    title = str(content.get("title") or "").strip()
    joined = "\n".join([title] + texts if title else texts)
    return joined.strip(), image_keys


def _collect_attachments(message_type: str, content: dict[str, Any] | None) -> list[dict[str, str]]:
    """从消息 content 提取附件描述（不下载）。"""
    if not isinstance(content, dict):
        return []
    attachments: list[dict[str, str]] = []
    if message_type == "image":
        key = str(content.get("image_key") or "").strip()
        if key:
            attachments.append(
                {"file_key": key, "file_name": "", "attachment_type": "image"}
            )
    elif message_type == "file":
        key = str(content.get("file_key") or "").strip()
        if key:
            attachments.append(
                {
                    "file_key": key,
                    "file_name": str(content.get("file_name") or "").strip(),
                    "attachment_type": "file",
                }
            )
    elif message_type in {"media", "audio"}:
        key = str(content.get("file_key") or content.get("image_key") or "").strip()
        if key:
            attachments.append(
                {
                    "file_key": key,
                    "file_name": str(content.get("file_name") or "").strip(),
                    "attachment_type": message_type,
                }
            )
    elif message_type == "post":
        _, image_keys = _post_text_and_images(content)
        for key in image_keys:
            attachments.append(
                {"file_key": key, "file_name": "", "attachment_type": "image"}
            )
    return attachments


def _parse_archive_event(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_id = str(message.get("message_id") or "").strip()
    chat_id = str(message.get("chat_id") or "").strip()
    if not message_id or not chat_id:
        return None
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    create_time_raw = str(message.get("create_time") or "").strip()
    sent_at: datetime | None = None
    if create_time_raw.isdigit():
        sent_at = datetime.fromtimestamp(int(create_time_raw))
    message_type = str(message.get("message_type") or "text").strip() or "text"
    content = _parse_content(message.get("content"))
    if message_type == "text":
        text = str((content or {}).get("text") or "").strip()
        text = _AT_PLACEHOLDER_RE.sub("", _AT_TAG_RE.sub("", text)).strip()
    elif message_type == "post":
        text, _ = _post_text_and_images(content or {})
    else:
        text = ""
    return {
        "message_id": message_id,
        "event_id": str(header.get("event_id") or "").strip(),
        "chat_id": chat_id,
        "chat_type": str(message.get("chat_type") or "").strip(),
        "message_type": message_type,
        "sender_open_id": str(sender_id.get("open_id") or "").strip(),
        "sender_type": str(sender.get("sender_type") or "").strip(),
        "sent_at": sent_at,
        "message_date": sent_at.strftime("%Y-%m-%d") if sent_at else "",
        "text_content": text,
        "attachments": _collect_attachments(message_type, content),
        "raw_event": payload,
    }


def _archive_dir(archive_dir: str | Path | None = None) -> Path:
    if archive_dir:
        return Path(archive_dir)
    return Path(get_settings().feishu_group_archive_dir)


async def ingest_group_archive_event(
    payload: Any,
    *,
    downloader: AttachmentDownloader | None = None,
    archive_dir: str | Path | None = None,
    wait_attachments: bool = False,
) -> dict[str, Any]:
    """归档一条飞书群消息事件；重复投递按 message_id 去重。

    默认元数据落库后立即返回（附件后台下载）——调用方是网关插件的同步
    HTTP 转发（8s 超时），附件下载（图片/文件，单件至多 60s）绝不能占住
    响应。wait_attachments=True 供测试/排查同步等待下载结果。
    """
    parsed = _parse_archive_event(payload)
    if parsed is None:
        return {"status": "invalid_event", "summary": "缺少 message_id/chat_id，无法归档。"}

    message = FeishuGroupArchiveMessage(
        message_id=parsed["message_id"],
        event_id=parsed["event_id"],
        chat_id=parsed["chat_id"],
        chat_type=parsed["chat_type"],
        message_type=parsed["message_type"],
        sender_open_id=parsed["sender_open_id"],
        sender_type=parsed["sender_type"],
        sent_at=parsed["sent_at"],
        message_date=parsed["message_date"],
        text_content=parsed["text_content"],
        raw_event=json.dumps(parsed["raw_event"], ensure_ascii=False),
    )
    try:
        async with session_scope() as session:
            existing = await session.execute(
                select(FeishuGroupArchiveMessage.id).where(
                    FeishuGroupArchiveMessage.message_id == parsed["message_id"]
                )
            )
            if existing.first() is not None:
                return {
                    "status": "ok",
                    "archived": False,
                    "reason": "duplicate_message_id",
                    "message_id": parsed["message_id"],
                }
            session.add(message)
            attachment_rows: list[FeishuGroupArchiveAttachment] = []
            for item in parsed["attachments"]:
                row = FeishuGroupArchiveAttachment(
                    message_id=parsed["message_id"],
                    file_key=item["file_key"],
                    file_name=item["file_name"],
                    attachment_type=item["attachment_type"],
                    status="pending",
                )
                session.add(row)
                attachment_rows.append(row)
            await session.commit()
    except IntegrityError:
        return {
            "status": "ok",
            "archived": False,
            "reason": "duplicate_message_id",
            "message_id": parsed["message_id"],
        }

    resolved_dir = _archive_dir(archive_dir)
    if not attachment_rows:
        return {
            "status": "ok",
            "archived": True,
            "message_id": parsed["message_id"],
            "message_date": parsed["message_date"],
            "attachments": [],
        }
    if wait_attachments:
        attachment_results = await _process_attachments(
            parsed=parsed, rows=attachment_rows, downloader=downloader, archive_dir=resolved_dir
        )
    else:
        _schedule_attachment_downloads(
            parsed=parsed, rows=attachment_rows, downloader=downloader, archive_dir=resolved_dir
        )
        attachment_results = [
            {
                "file_key": row.file_key,
                "file_name": row.file_name,
                "attachment_type": row.attachment_type,
                "status": "pending",
                "saved_path": "",
                "size_bytes": 0,
            }
            for row in attachment_rows
        ]
    return {
        "status": "ok",
        "archived": True,
        "message_id": parsed["message_id"],
        "message_date": parsed["message_date"],
        "attachments": attachment_results,
    }


def _schedule_attachment_downloads(
    *,
    parsed: dict[str, Any],
    rows: list[FeishuGroupArchiveAttachment],
    downloader: AttachmentDownloader | None,
    archive_dir: Path,
) -> None:
    """当前消息附件下载 + 陈旧 pending/failed 附件有界重试，全部后台执行。"""

    async def _runner() -> None:
        try:
            await _process_attachments(
                parsed=parsed, rows=rows, downloader=downloader, archive_dir=archive_dir
            )
            await retry_stale_attachments(downloader=downloader, archive_dir=archive_dir, limit=5)
        except Exception as exc:  # 后台任务异常不影响已落库的归档记录
            logger.warning("group archive background download failed: %s", exc)

    try:
        asyncio.get_running_loop().create_task(_runner())
    except RuntimeError:
        # 无事件循环（同步调用方）：附件留 pending，由后续消息的重试治愈。
        logger.warning("group archive download deferred: no running event loop")


async def _process_attachments(
    *,
    parsed: dict[str, Any],
    rows: list[FeishuGroupArchiveAttachment],
    downloader: AttachmentDownloader | None,
    archive_dir: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        results.append(
            await _download_one(
                row,
                parsed=parsed,
                index=index,
                downloader=downloader,
                archive_dir=archive_dir,
            )
        )
    return results


async def _download_one(
    row: FeishuGroupArchiveAttachment,
    *,
    parsed: dict[str, Any],
    index: int,
    downloader: AttachmentDownloader | None,
    archive_dir: Path,
) -> dict[str, Any]:
    """下载单个附件并回写状态；失败只置 download_failed，记录永不删。"""
    summary = {
        "file_key": row.file_key,
        "file_name": row.file_name,
        "attachment_type": row.attachment_type,
        "status": row.status or "pending",
        "saved_path": "",
        "size_bytes": 0,
    }
    if downloader is None:
        await _update_attachment(row.id, status="skipped_no_downloader")
        summary["status"] = "skipped_no_downloader"
        return summary
    resource_type = "image" if row.attachment_type == "image" else "file"
    try:
        outcome = await downloader(
            message_id=parsed["message_id"],
            file_key=row.file_key,
            resource_type=resource_type,
            chat_id=parsed["chat_id"],
        )
    except Exception as exc:  # 下载异常不丢归档记录
        await _update_attachment(row.id, status="download_failed", error=str(exc)[:500])
        summary["status"] = "download_failed"
        return summary
    if not isinstance(outcome, dict) or outcome.get("status") != "success":
        reason = str((outcome or {}).get("summary") or "download failed")[:500]
        await _update_attachment(row.id, status="download_failed", error=reason)
        summary["status"] = "download_failed"
        return summary
    data = outcome.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data:
        await _update_attachment(row.id, status="download_failed", error="empty payload")
        summary["status"] = "download_failed"
        return summary
    if len(data) > ATTACHMENT_MAX_BYTES:
        await _update_attachment(
            row.id,
            status="skipped_too_large",
            error=f"{len(data)} bytes exceeds {ATTACHMENT_MAX_BYTES}",
        )
        summary["status"] = "skipped_too_large"
        return summary
    saved_path = _save_attachment_file(
        archive_dir=archive_dir,
        chat_id=parsed["chat_id"],
        message_date=parsed["message_date"] or datetime.now().strftime("%Y-%m-%d"),
        message_id=parsed["message_id"],
        index=index,
        row=row,
        data=bytes(data),
    )
    await _update_attachment(
        row.id, status="success", saved_path=str(saved_path), size_bytes=len(data)
    )
    summary.update(
        {"status": "success", "saved_path": str(saved_path), "size_bytes": len(data)}
    )
    return summary


async def retry_stale_attachments(
    *,
    downloader: AttachmentDownloader | None,
    archive_dir: str | Path | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """重试最早 N 条 pending/download_failed 附件（如权限补开后的自愈）。"""
    if downloader is None:
        return {"status": "skipped_no_downloader", "retried": 0}
    resolved_dir = _archive_dir(archive_dir)
    async with session_scope() as session:
        pairs = (
            await session.execute(
                select(FeishuGroupArchiveAttachment, FeishuGroupArchiveMessage)
                .join(
                    FeishuGroupArchiveMessage,
                    FeishuGroupArchiveMessage.message_id
                    == FeishuGroupArchiveAttachment.message_id,
                )
                .where(
                    FeishuGroupArchiveAttachment.status.in_(("pending", "download_failed"))
                )
                .order_by(FeishuGroupArchiveAttachment.id)
                .limit(limit)
            )
        ).all()
    for attachment, message in pairs:
        context = {
            "message_id": attachment.message_id,
            "chat_id": message.chat_id,
            "message_date": message.message_date or datetime.now().strftime("%Y-%m-%d"),
        }
        try:
            await _download_one(
                attachment,
                parsed=context,
                index=0,
                downloader=downloader,
                archive_dir=resolved_dir,
            )
        except Exception as exc:
            await _update_attachment(
                attachment.id, status="download_failed", error=str(exc)[:500]
            )
    return {"status": "ok", "retried": len(pairs)}


def _save_attachment_file(
    *,
    archive_dir: Path,
    chat_id: str,
    message_date: str,
    message_id: str,
    index: int,
    row: FeishuGroupArchiveAttachment,
    data: bytes,
) -> Path:
    safe_chat = _safe_filename(chat_id, fallback="chat")
    directory = archive_dir / safe_chat / message_date
    directory.mkdir(parents=True, exist_ok=True)
    if row.attachment_type == "image":
        filename = f"{message_id}_{index:02d}_image{_image_extension(data)}"
    else:
        base = _safe_filename(row.file_name, fallback="file")
        filename = f"{message_id}_{index:02d}_{base}"
    target = directory / filename
    counter = 1
    while target.exists():
        target = directory / f"{target.stem}_{counter}{target.suffix}"
        counter += 1
    target.write_bytes(data)
    return target


async def _update_attachment(
    attachment_id: int,
    *,
    status: str,
    saved_path: str = "",
    size_bytes: int = 0,
    error: str = "",
) -> None:
    from sqlalchemy import update

    async with session_scope() as session:
        await session.execute(
            update(FeishuGroupArchiveAttachment)
            .where(FeishuGroupArchiveAttachment.id == attachment_id)
            .values(
                status=status,
                saved_path=saved_path,
                size_bytes=size_bytes,
                error=error,
            )
        )


async def query_group_archive(
    *,
    query: str = "",
    date_start: str = "",
    date_end: str = "",
    chat_id: str = "",
    message_type: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """按日期区间/关键词/类型/群过滤归档消息（新→旧），带附件明细。"""
    normalized_limit = limit if isinstance(limit, int) else 50
    if normalized_limit < 1 or normalized_limit > 200:
        return {
            "status": "invalid_arguments",
            "summary": "limit 取值 1~200。",
        }
    for label, value in (("date_start", date_start), ("date_end", date_end)):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {
                "status": "invalid_arguments",
                "summary": f"{label} 需要 YYYY-MM-DD 格式。",
            }

    statement = select(FeishuGroupArchiveMessage)
    if chat_id:
        statement = statement.where(FeishuGroupArchiveMessage.chat_id == chat_id)
    if message_type:
        statement = statement.where(
            FeishuGroupArchiveMessage.message_type == message_type
        )
    if date_start:
        statement = statement.where(
            FeishuGroupArchiveMessage.message_date >= date_start
        )
    if date_end:
        statement = statement.where(FeishuGroupArchiveMessage.message_date <= date_end)
    keyword = str(query or "").strip()
    if keyword:
        statement = statement.where(
            FeishuGroupArchiveMessage.text_content.like(f"%{keyword}%")
        )
    statement = statement.order_by(
        FeishuGroupArchiveMessage.sent_at.desc().nullslast(),
        FeishuGroupArchiveMessage.id.desc(),
    ).limit(normalized_limit)

    async with session_scope() as session:
        rows = (await session.execute(statement)).scalars().all()
        message_ids = [row.message_id for row in rows]
        attachments_by_message: dict[str, list[dict[str, Any]]] = {}
        if message_ids:
            attachment_rows = (
                await session.execute(
                    select(FeishuGroupArchiveAttachment).where(
                        FeishuGroupArchiveAttachment.message_id.in_(message_ids)
                    )
                )
            ).scalars().all()
            for item in attachment_rows:
                attachments_by_message.setdefault(item.message_id, []).append(
                    {
                        "file_key": item.file_key,
                        "file_name": item.file_name,
                        "attachment_type": item.attachment_type,
                        "status": item.status,
                        "size_bytes": item.size_bytes,
                        "saved_path": item.saved_path,
                    }
                )

    def _iso(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""

    messages = [
        {
            "message_id": row.message_id,
            "chat_id": row.chat_id,
            "chat_type": row.chat_type,
            "message_type": row.message_type,
            "sender_open_id": row.sender_open_id,
            "sent_at": _iso(row.sent_at),
            "message_date": row.message_date,
            "text_content": row.text_content,
            "attachments": attachments_by_message.get(row.message_id, []),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "total": len(messages),
        "filters": {
            "query": keyword,
            "date_start": date_start,
            "date_end": date_end,
            "chat_id": chat_id,
            "message_type": message_type,
            "limit": normalized_limit,
        },
        "messages": messages,
    }


def debug_recent_messages(limit: int = 20) -> list[dict[str, Any]]:
    """同步读取最近归档（测试/排查用）。"""

    async def _scenario() -> list[dict[str, Any]]:
        async with session_scope() as session:
            statement = (
                select(FeishuGroupArchiveMessage)
                .order_by(FeishuGroupArchiveMessage.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(statement)).scalars().all()
            return [
                {
                    "message_id": row.message_id,
                    "chat_id": row.chat_id,
                    "message_type": row.message_type,
                    "message_date": row.message_date,
                    "text_content": row.text_content,
                    "sender_type": row.sender_type,
                }
                for row in rows
            ]

    return asyncio.run(_scenario())
