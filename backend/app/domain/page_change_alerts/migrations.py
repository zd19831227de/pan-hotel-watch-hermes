from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_page_change_alert_schema(connection: AsyncConnection) -> None:
    """V7 Step 1 — create page-change-alert tables (defensive IF NOT EXISTS)。

    新表同样由 ``Base.metadata.create_all`` 覆盖；此处保证存量库升级时补齐。
    """
    dialect = connection.dialect.name
    autoincrement = (
        "INTEGER PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    datetime_type = "DATETIME"
    statements = _create_table_statements(autoincrement, datetime_type)
    statements += _index_statements(dialect)
    for statement in statements:
        await connection.execute(text(statement))
    await _ensure_alert_index(connection)


def _create_table_statements(autoincrement: str, datetime_type: str) -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS page_structure_snapshots (
            id {autoincrement},
            platform VARCHAR(32) NOT NULL,
            target VARCHAR(128) NOT NULL,
            structure_hash VARCHAR(64) NOT NULL,
            previous_hash VARCHAR(64) NULL,
            captured_at {datetime_type} NULL,
            updated_at {datetime_type} NULL,
            CONSTRAINT uq_page_structure_target UNIQUE (platform, target)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS page_health_alerts (
            id {autoincrement},
            platform VARCHAR(32) NOT NULL,
            target VARCHAR(128) NOT NULL,
            problem_key VARCHAR(64) NOT NULL,
            level VARCHAR(16) NOT NULL,
            summary TEXT NULL,
            created_at {datetime_type} NULL
        )
        """,
    ]


def _index_statements(dialect: str) -> list[str]:
    if dialect in {"mysql", "mariadb"}:
        return [
            "CREATE INDEX ix_page_health_alert_dedupe ON page_health_alerts "
            "(platform, target, problem_key, created_at)"
        ]
    return [
        "CREATE INDEX IF NOT EXISTS ix_page_health_alert_dedupe ON page_health_alerts "
        "(platform, target, problem_key, created_at)"
    ]


async def _ensure_alert_index(connection: AsyncConnection) -> None:
    """SQLite 老库兜底：若索引缺失则补建。"""
    def _has_index(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        indexes = inspector.get_indexes("page_health_alerts")
        return any(index.get("name") == "ix_page_health_alert_dedupe" for index in indexes)

    try:
        has_index = await connection.run_sync(_has_index)
    except Exception:  # noqa: BLE001 - 表可能刚建，inspect 失败则跳过
        return
    if not has_index:
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_page_health_alert_dedupe ON page_health_alerts "
                "(platform, target, problem_key, created_at)"
            )
        )
