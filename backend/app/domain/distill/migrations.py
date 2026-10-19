from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_distill_schema(connection: AsyncConnection) -> None:
    """V6 Step 2 — create distill tables (defensive IF NOT EXISTS)。"""
    dialect = connection.dialect.name
    autoincrement = (
        "INTEGER PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    datetime_type = "DATETIME"
    boolean_type = "TINYINT(1)" if dialect in {"mysql", "mariadb"} else "BOOLEAN"
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS distill_sources (
            id {autoincrement},
            source_type VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            platform VARCHAR(16) NOT NULL,
            title VARCHAR(256) NOT NULL DEFAULT '',
            distill_prompt TEXT NOT NULL DEFAULT '',
            enabled {boolean_type} NOT NULL DEFAULT 1,
            schedule_interval_minutes INTEGER NOT NULL DEFAULT 0,
            last_run_at {datetime_type} NULL,
            created_at {datetime_type} NULL,
            updated_at {datetime_type} NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS distill_runs (
            id {autoincrement},
            source_id BIGINT NULL,
            platform VARCHAR(16) NOT NULL,
            source_type VARCHAR(16) NOT NULL,
            status VARCHAR(32) NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            extracted_text TEXT NOT NULL DEFAULT '',
            knowledge_title VARCHAR(256) NOT NULL DEFAULT '',
            rag_document_id BIGINT NULL,
            reject_reason TEXT NULL,
            created_at {datetime_type} NULL
        )
        """,
    ]
    for statement in statements:
        await connection.execute(text(statement))
