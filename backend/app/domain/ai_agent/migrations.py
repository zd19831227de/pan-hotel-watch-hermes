from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_ai_agent_schema(connection: AsyncConnection) -> None:
    if not await _table_exists(connection, "ai_agent_conversations"):
        await _create_conversations_table(connection)
    if not await _table_exists(connection, "ai_agent_messages"):
        await _create_messages_table(connection)
    await _ensure_indexes(connection)


async def _create_conversations_table(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    if dialect in {"mysql", "mariadb"}:
        sql = """
            CREATE TABLE ai_agent_conversations (
                session_id VARCHAR(260) PRIMARY KEY,
                chat_id VARCHAR(128) NOT NULL,
                open_id VARCHAR(128) NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                INDEX ix_ai_agent_conversations_chat_open (chat_id, open_id),
                INDEX ix_ai_agent_conversations_updated_at (updated_at)
            )
        """
    else:
        sql = """
            CREATE TABLE ai_agent_conversations (
                session_id VARCHAR(260) PRIMARY KEY,
                chat_id VARCHAR(128) NOT NULL,
                open_id VARCHAR(128),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """
    await connection.execute(text(sql))


async def _create_messages_table(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    if dialect in {"mysql", "mariadb"}:
        sql = """
            CREATE TABLE ai_agent_messages (
                message_id VARCHAR(128) PRIMARY KEY,
                session_id VARCHAR(260) NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NULL,
                tool_calls TEXT NULL,
                created_at DATETIME NOT NULL,
                INDEX ix_ai_agent_messages_session_created (session_id, created_at),
                FOREIGN KEY (session_id) REFERENCES ai_agent_conversations (session_id)
            )
        """
    else:
        sql = """
            CREATE TABLE ai_agent_messages (
                message_id VARCHAR(128) PRIMARY KEY,
                session_id VARCHAR(260) NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT,
                tool_calls TEXT,
                created_at DATETIME NOT NULL,
                FOREIGN KEY (session_id) REFERENCES ai_agent_conversations (session_id)
            )
        """
    await connection.execute(text(sql))


async def _ensure_indexes(connection: AsyncConnection) -> None:
    if connection.dialect.name not in {"sqlite", "sqlite3"}:
        return
    conversation_indexes = await _get_existing_indexes(connection, "ai_agent_conversations")
    if "ix_ai_agent_conversations_chat_open" not in conversation_indexes:
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_agent_conversations_chat_open "
                "ON ai_agent_conversations (chat_id, open_id)"
            )
        )
    if "ix_ai_agent_conversations_updated_at" not in conversation_indexes:
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_agent_conversations_updated_at "
                "ON ai_agent_conversations (updated_at)"
            )
        )
    message_indexes = await _get_existing_indexes(connection, "ai_agent_messages")
    if "ix_ai_agent_messages_session_created" not in message_indexes:
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_agent_messages_session_created "
                "ON ai_agent_messages (session_id, created_at)"
            )
        )


async def _get_existing_indexes(connection: AsyncConnection, table_name: str) -> set[str]:
    def _inspect_indexes(sync_connection) -> set[str]:
        inspector = inspect(sync_connection)
        return {index["name"] for index in inspector.get_indexes(table_name) if index["name"]}

    return await connection.run_sync(_inspect_indexes)


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def _inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(_inspect_table)
