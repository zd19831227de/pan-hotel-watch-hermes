from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


async def ensure_ebk_global_push_schema(connection: AsyncConnection) -> None:
    """Create the EBK global-push tables when auto-create is enabled.

    建表主路径走 Base.metadata.create_all（含唯一约束/索引）；此处仅做
    IF NOT EXISTS 兜底，与 ebk_snapshots/meituan_ebooking_watch 同一接入模式。
    """

    dialect = connection.dialect.name
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    autoincrement = (
        f"{int_type} PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else f"{int_type} PRIMARY KEY AUTOINCREMENT"
    )
    await connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS ebk_global_push_configs (
                id {autoincrement},
                ctrip_watch_group_key VARCHAR(64) NOT NULL UNIQUE,
                shop_key VARCHAR(64) NULL,
                name VARCHAR(128) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                enabled_platforms_json JSON NOT NULL,
                schedule_mode VARCHAR(32) NOT NULL DEFAULT 'fixed_slots',
                push_times_json JSON NOT NULL,
                interval_minutes {int_type} NOT NULL DEFAULT 60,
                window_start_time VARCHAR(5) NOT NULL DEFAULT '06:00',
                window_end_time VARCHAR(5) NOT NULL DEFAULT '23:00',
                timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
                card_title_business VARCHAR(128) NULL,
                card_title_promotion VARCHAR(128) NULL,
                card_title_advertising VARCHAR(128) NULL,
                last_attempted_at DATETIME NULL,
                last_successful_push_at DATETIME NULL,
                next_run_at DATETIME NULL,
                consecutive_failure_count {int_type} NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """.strip()
        )
    )
    await connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS ebk_global_push_runs (
                id {autoincrement},
                config_id {int_type} NOT NULL,
                trigger VARCHAR(32) NOT NULL DEFAULT 'scheduler',
                schedule_key VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'running',
                read_started_at DATETIME NULL,
                read_finished_at DATETIME NULL,
                push_started_at DATETIME NULL,
                push_finished_at DATETIME NULL,
                platform_results_json JSON NOT NULL,
                card_results_json JSON NOT NULL,
                error_summary TEXT NULL,
                error_fingerprint VARCHAR(64) NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_ebk_global_push_run_slot UNIQUE (config_id, schedule_key)
            )
            """.strip()
        )
    )
    await _ensure_index(
        connection,
        "ebk_global_push_runs",
        "ix_ebk_global_push_runs_config_id",
        "CREATE INDEX ix_ebk_global_push_runs_config_id ON ebk_global_push_runs (config_id)",
    )
    # 既有库幂等补列（重构 P5：店铺归属派生列），历史行 NULL。
    if await _has_column(connection, "ebk_global_push_configs", "shop_key") is False:
        await connection.execute(
            text("ALTER TABLE ebk_global_push_configs ADD COLUMN shop_key VARCHAR(64)")
        )
    await _ensure_index(
        connection,
        "ebk_global_push_configs",
        "ix_ebk_global_push_configs_enabled",
        "CREATE INDEX ix_ebk_global_push_configs_enabled ON ebk_global_push_configs (enabled)",
    )
    await _ensure_index(
        connection,
        "ebk_global_push_configs",
        "ix_ebk_global_push_configs_shop_key",
        "CREATE INDEX ix_ebk_global_push_configs_shop_key ON ebk_global_push_configs (shop_key)",
    )
    # 存量回填派生 shop_key（只补 NULL 行，幂等；非空行不重推）。锚点组改绑
    # 店铺后，既有配置的 shop_key 由其下次保存时的写时派生刷新（repository.py）。
    try:
        await connection.execute(
            text(
                """
                UPDATE ebk_global_push_configs
                SET shop_key = (
                    SELECT g.shop_key FROM ctrip_ebooking_watch_groups g
                    WHERE g.group_key = ebk_global_push_configs.ctrip_watch_group_key
                )
                WHERE shop_key IS NULL
                """
            )
        )
    except Exception:
        logger.debug("ebk global push shop_key backfill skipped", exc_info=True)


async def _has_column(connection: AsyncConnection, table: str, column: str) -> bool:
    from sqlalchemy import inspect

    def _check(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table(table):
            return True
        return any(
            item["name"] == column for item in inspector.get_columns(table)
        )

    return await connection.run_sync(_check)


async def _ensure_index(
    connection: AsyncConnection,
    table_name: str,
    index_name: str,
    create_sql: str,
) -> None:
    from sqlalchemy import inspect

    def _check(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return False
        return any(item["name"] == index_name for item in inspector.get_indexes(table_name))

    if await connection.run_sync(_check):
        return
    try:
        await connection.exec_driver_sql(create_sql)
    except Exception:
        logger.debug(
            "ebk global push index create skipped (likely already exists)", exc_info=True
        )
