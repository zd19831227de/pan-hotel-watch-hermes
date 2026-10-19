from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_group_archive_schema(connection: AsyncConnection) -> None:
    """群资料归档表（defensive IF NOT EXISTS），永不清除、无留存天数。"""
    dialect = connection.dialect.name
    autoincrement = (
        "INTEGER PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    datetime_type = "DATETIME"
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS feishu_group_archive_messages (
            id {autoincrement},
            message_id VARCHAR(64) NOT NULL,
            event_id VARCHAR(64) NOT NULL DEFAULT '',
            chat_id VARCHAR(64) NOT NULL DEFAULT '',
            chat_type VARCHAR(16) NOT NULL DEFAULT '',
            message_type VARCHAR(24) NOT NULL DEFAULT '',
            sender_open_id VARCHAR(64) NOT NULL DEFAULT '',
            sender_type VARCHAR(16) NOT NULL DEFAULT '',
            sent_at {datetime_type} NULL,
            message_date VARCHAR(10) NOT NULL DEFAULT '',
            text_content TEXT NOT NULL DEFAULT '',
            raw_event TEXT NOT NULL DEFAULT '',
            created_at {datetime_type} NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS feishu_group_archive_attachments (
            id {autoincrement},
            message_id VARCHAR(64) NOT NULL DEFAULT '',
            file_key VARCHAR(128) NOT NULL DEFAULT '',
            file_name VARCHAR(256) NOT NULL DEFAULT '',
            attachment_type VARCHAR(16) NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            saved_path TEXT NOT NULL DEFAULT '',
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            error TEXT NOT NULL DEFAULT '',
            created_at {datetime_type} NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_feishu_group_archive_messages_message_id "
        "ON feishu_group_archive_messages (message_id)",
        "CREATE INDEX IF NOT EXISTS ix_feishu_group_archive_messages_chat_id "
        "ON feishu_group_archive_messages (chat_id)",
        "CREATE INDEX IF NOT EXISTS ix_feishu_group_archive_messages_message_type "
        "ON feishu_group_archive_messages (message_type)",
        "CREATE INDEX IF NOT EXISTS ix_feishu_group_archive_messages_message_date "
        "ON feishu_group_archive_messages (message_date)",
        "CREATE INDEX IF NOT EXISTS ix_feishu_group_archive_attachments_message_id "
        "ON feishu_group_archive_attachments (message_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_archive_attachment "
        "ON feishu_group_archive_attachments (message_id, file_key)",
    ]
    for statement in statements:
        await connection.execute(text(statement))
