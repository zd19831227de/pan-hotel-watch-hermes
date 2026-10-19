from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_runtime_integration_config_schema(connection: AsyncConnection) -> None:
    if not await _table_exists(connection, "runtime_integration_configs"):
        return
    columns = await _table_columns(connection, "runtime_integration_configs")
    dialect = connection.dialect.name
    for column_name, column_sql in _config_columns(dialect).items():
        if column_name in columns:
            continue
        await connection.execute(text(f"ALTER TABLE runtime_integration_configs ADD COLUMN {column_sql}"))


async def ensure_runtime_llm_models_schema(connection: AsyncConnection) -> None:
    """V5 Step 1 — create the multi-model fallback-chain table.

    New tables are normally created by ``Base.metadata.create_all``; this
    migration is a defensive ``CREATE TABLE IF NOT EXISTS`` so an existing
    production database that ran an older ``create_all`` still receives the
    table, and so the per-model health-check columns can be added later via
    ``ALTER TABLE`` without a full re-create.
    """

    dialect = connection.dialect.name
    if dialect in {"mysql", "mariadb"}:
        autoincrement = "INTEGER PRIMARY KEY AUTO_INCREMENT"
        datetime_type = "DATETIME"
    else:
        autoincrement = "INTEGER PRIMARY KEY AUTOINCREMENT"
        datetime_type = "DATETIME"
    await connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS runtime_llm_models (
                id {autoincrement},
                config_key VARCHAR(64) NOT NULL DEFAULT 'default',
                provider VARCHAR(64) NOT NULL,
                model_name VARCHAR(128) NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_health_status VARCHAR(16) NULL,
                last_health_summary TEXT NULL,
                last_health_checked_at {datetime_type} NULL,
                created_at {datetime_type} NULL,
                updated_at {datetime_type} NULL
            )
            """.strip()
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_runtime_llm_models_config_priority
            ON runtime_llm_models (config_key, priority)
            """.strip()
        )
    )

    if await _table_exists(connection, "runtime_llm_models"):
        columns = await _table_columns(connection, "runtime_llm_models")
        for column_name, column_sql in _llm_model_columns(dialect).items():
            if column_name in columns:
                continue
            await connection.execute(
                text(f"ALTER TABLE runtime_llm_models ADD COLUMN {column_sql}")
            )


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)


async def _table_columns(connection: AsyncConnection, table_name: str) -> set[str]:
    def inspect_columns(sync_connection) -> set[str]:
        return {column["name"] for column in inspect(sync_connection).get_columns(table_name)}

    return await connection.run_sync(inspect_columns)


def _config_columns(dialect: str) -> dict[str, str]:
    text_type = "TEXT"
    datetime_type = "DATETIME" if dialect in {"mysql", "mariadb"} else "DATETIME"
    return {
        "llm_provider": "llm_provider VARCHAR(64) NOT NULL DEFAULT 'deepseek'",
        "llm_base_url": f"llm_base_url {text_type} NULL",
        "llm_model": "llm_model VARCHAR(128) NOT NULL DEFAULT 'deepseek-chat'",
        "llm_api_key": f"llm_api_key {text_type} NULL",
        "created_at": f"created_at {datetime_type} NULL",
        "updated_at": f"updated_at {datetime_type} NULL",
    }


def _llm_model_columns(dialect: str) -> dict[str, str]:
    """Health-check columns added incrementally to existing tables."""

    datetime_type = "DATETIME" if dialect in {"mysql", "mariadb"} else "DATETIME"
    return {
        "last_health_status": "last_health_status VARCHAR(16) NULL",
        "last_health_summary": "last_health_summary TEXT NULL",
        "last_health_checked_at": f"last_health_checked_at {datetime_type} NULL",
    }
