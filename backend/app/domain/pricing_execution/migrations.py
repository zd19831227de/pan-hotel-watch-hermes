from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_pricing_execution_schema(connection: AsyncConnection) -> None:
    # The first migration for this module is covered by Base.metadata.create_all.
    # Keep this hook so future column additions can be applied in-place for SQLite/MySQL.
    for table_name in (
        "pricing_preview_records",
        "pricing_approval_records",
        "pricing_execution_records",
    ):
        await _table_exists(connection, table_name)


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)
