from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_ebk_snapshot_schema(connection: AsyncConnection) -> None:
    """Create the unified EBK snapshot table when auto-create is enabled."""

    dialect = connection.dialect.name
    for statement in _create_table_statements(dialect):
        await connection.execute(text(statement))
    # 既有库幂等补列（重构 P4：来源 run 与店铺归属），历史行保持 NULL。
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    for column, ddl in (
        ("source_watch_run_id", f"source_watch_run_id {int_type}"),
        ("shop_key", "shop_key VARCHAR(64)"),
    ):
        if not await _has_column(connection, "ebk_read_snapshots", column):
            await connection.execute(
                text(f"ALTER TABLE ebk_read_snapshots ADD COLUMN {ddl}")
            )
    for statement in _create_index_statements(dialect):
        await connection.execute(text(statement))


async def _has_column(connection: AsyncConnection, table: str, column: str) -> bool:
    if connection.dialect.name == "sqlite":
        rows = (await connection.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        return any(row[1] == column for row in rows)
    result = await connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c LIMIT 1"
        ),
        {"t": table, "c": column},
    )
    return result.first() is not None


def _create_table_statements(dialect: str) -> list[str]:
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    json_type = "JSON" if dialect in {"mysql", "mariadb", "sqlite"} else "JSON"
    autoincrement = (
        f"{int_type} PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else f"{int_type} PRIMARY KEY AUTOINCREMENT"
    )
    return [
        f"""
        CREATE TABLE IF NOT EXISTS ebk_read_snapshots (
            id {autoincrement},
            platform VARCHAR(32) NOT NULL,
            platform_account_id VARCHAR(128) NOT NULL,
            browser_session_id VARCHAR(128) NOT NULL,
            status VARCHAR(32) NOT NULL,
            dataset_count {int_type} NOT NULL DEFAULT 0,
            payload_json {json_type} NOT NULL,
            source_watch_run_id {int_type} NULL,
            shop_key VARCHAR(64) NULL,
            created_at DATETIME NOT NULL
        )
        """.strip(),
    ]


def _create_index_statements(dialect: str) -> list[str]:
    return [
        """
        CREATE INDEX IF NOT EXISTS ix_ebk_read_snapshots_platform_account_created
        ON ebk_read_snapshots (platform, platform_account_id, created_at)
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_ebk_read_snapshots_platform_account_status_created
        ON ebk_read_snapshots (platform, platform_account_id, status, created_at)
        """.strip(),
        # 同一 watch run 只补存一条快照（NULL 不受唯一约束影响）。
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ebk_read_snapshots_source_run
        ON ebk_read_snapshots (source_watch_run_id)
        """.strip(),
    ]
