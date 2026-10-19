from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_price_change_alert_schema(connection: AsyncConnection) -> None:
    # The first production version is covered by Base.metadata.create_all. This hook
    # exists so later additive changes have the same lightweight migration path as
    # the other watch modules.
    await _table_exists(connection, "price_change_alert_records")


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)
