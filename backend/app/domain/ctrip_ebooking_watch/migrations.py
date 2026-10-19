from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_data_center_watch_schema(connection: AsyncConnection) -> None:
    """Align early SQLite/MySQL installs before Alembic is introduced."""

    if not await _table_exists(connection, "ctrip_ebooking_watch_groups"):
        return
    columns = await _table_columns(connection, "ctrip_ebooking_watch_groups")
    for column_name, column_sql in _watch_group_columns().items():
        if column_name in columns:
            continue
        await connection.execute(
            text(f"ALTER TABLE ctrip_ebooking_watch_groups ADD COLUMN {column_sql}")
        )
    # 店铺归属唯一索引（每店最多一个主携程锚点）；NULL 不受约束，重复归属在
    # repository 层给可读冲突提示，索引兜底防并发写漏网。
    await connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ctrip_ebk_groups_shop_key "
            "ON ctrip_ebooking_watch_groups (shop_key)"
        )
    )


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)


async def _table_columns(connection: AsyncConnection, table_name: str) -> set[str]:
    def inspect_columns(sync_connection) -> set[str]:
        return {column["name"] for column in inspect(sync_connection).get_columns(table_name)}

    return await connection.run_sync(inspect_columns)


def _watch_group_columns() -> dict[str, str]:
    return {
        "brief_title": "brief_title VARCHAR(128) NULL",
        "daily_brief_enabled": "daily_brief_enabled BOOLEAN NOT NULL DEFAULT 0",
        "daily_brief_title": "daily_brief_title VARCHAR(128) NULL",
        "daily_brief_push_time": "daily_brief_push_time VARCHAR(5) NOT NULL DEFAULT '08:30'",
        "daily_brief_last_pushed_at": "daily_brief_last_pushed_at DATETIME NULL",
        "shop_key": "shop_key VARCHAR(64) NULL",
    }
