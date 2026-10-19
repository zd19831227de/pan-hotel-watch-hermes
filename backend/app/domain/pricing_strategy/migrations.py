from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_pricing_strategy_schema(connection: AsyncConnection) -> None:
    if not await _table_exists(connection, "pricing_strategy_configs"):
        return
    columns = await _table_columns(connection, "pricing_strategy_configs")
    dialect = connection.dialect.name
    for column_name, column_sql in _strategy_columns(dialect).items():
        if column_name in columns:
            continue
        await connection.execute(text(f"ALTER TABLE pricing_strategy_configs ADD COLUMN {column_sql}"))


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)


async def _table_columns(connection: AsyncConnection, table_name: str) -> set[str]:
    def inspect_columns(sync_connection) -> set[str]:
        return {column["name"] for column in inspect(sync_connection).get_columns(table_name)}

    return await connection.run_sync(inspect_columns)


def _strategy_columns(dialect: str) -> dict[str, str]:
    if dialect in {"mysql", "mariadb"}:
        return {
            "recommendation_step_yuan": "recommendation_step_yuan INT NOT NULL DEFAULT 10",
            "max_suggested_change_yuan": "max_suggested_change_yuan INT NOT NULL DEFAULT 30",
            "allow_downward_suggestion": (
                "allow_downward_suggestion TINYINT(1) NOT NULL DEFAULT 0"
            ),
            "updated_by": "updated_by VARCHAR(128) NULL",
            "last_config_json": "last_config_json JSON NULL",
        }
    return {
        "recommendation_step_yuan": "recommendation_step_yuan INTEGER NOT NULL DEFAULT 10",
        "max_suggested_change_yuan": "max_suggested_change_yuan INTEGER NOT NULL DEFAULT 30",
        "allow_downward_suggestion": "allow_downward_suggestion BOOLEAN NOT NULL DEFAULT 0",
        "updated_by": "updated_by VARCHAR(128) NULL",
        "last_config_json": "last_config_json JSON NULL",
    }
