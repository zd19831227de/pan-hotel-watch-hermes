from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def _has_column(connection: AsyncConnection, table: str, column: str) -> bool:
    """幂等判断列是否已存在（sqlite 查 pragma，mysql 查 information_schema）。"""
    dialect = connection.dialect.name
    if dialect == "sqlite":
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


async def ensure_refund_orders_schema(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    json_type = "JSON"
    await connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS refund_order_snapshots (
                id {int_type} PRIMARY KEY AUTOINCREMENT,
                platform VARCHAR(16) NOT NULL,
                refund_date VARCHAR(10) NOT NULL,
                total_count {int_type} NOT NULL DEFAULT 0,
                by_room_type_json {json_type} NULL,
                by_hour_json {json_type} NULL,
                time_caliber VARCHAR(24) NULL,
                source VARCHAR(16) NOT NULL DEFAULT 'push_read',
                shop_key VARCHAR(64) NULL,
                platform_account_id VARCHAR(96) NULL,
                browser_session_id VARCHAR(120) NULL,
                captured_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL
            )
            """.strip()
        )
    )
    # 既有库（表已存在但缺新列）幂等补列，不改任何已有数据；历史行保持 NULL。
    if not await _has_column(connection, "refund_order_snapshots", "by_hour_json"):
        await connection.execute(
            text("ALTER TABLE refund_order_snapshots ADD COLUMN by_hour_json JSON")
        )
    if not await _has_column(connection, "refund_order_snapshots", "time_caliber"):
        await connection.execute(
            text("ALTER TABLE refund_order_snapshots ADD COLUMN time_caliber VARCHAR(24)")
        )
    for column, ddl in (
        ("shop_key", "shop_key VARCHAR(64)"),
        ("platform_account_id", "platform_account_id VARCHAR(96)"),
        ("browser_session_id", "browser_session_id VARCHAR(120)"),
    ):
        if not await _has_column(connection, "refund_order_snapshots", column):
            await connection.execute(
                text(f"ALTER TABLE refund_order_snapshots ADD COLUMN {ddl}")
            )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_refund_order_snapshots_platform_date
                ON refund_order_snapshots (platform, refund_date)
            """.strip()
        )
    )
