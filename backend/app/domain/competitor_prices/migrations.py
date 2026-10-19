from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_competitor_price_schema(connection: AsyncConnection) -> None:
    """Align early SQLite/MySQL installs before Alembic is introduced."""

    if not await _table_exists(connection, "ctrip_competitor_watch_groups"):
        return
    columns = await _table_columns(connection, "ctrip_competitor_watch_groups")
    dialect = connection.dialect.name
    for column_name, column_sql in _watch_group_columns(dialect).items():
        if column_name in columns:
            continue
        await connection.execute(
            text(f"ALTER TABLE ctrip_competitor_watch_groups ADD COLUMN {column_sql}")
        )
    await _create_browser_pool_member_table(connection, dialect)
    await _create_browser_lease_table(connection, dialect)
    if await _table_exists(connection, "ctrip_competitor_hotels"):
        hotel_columns = await _table_columns(connection, "ctrip_competitor_hotels")
        for column_name, column_sql in _hotel_columns(dialect).items():
            if column_name in hotel_columns:
                continue
            await connection.execute(
                text(f"ALTER TABLE ctrip_competitor_hotels ADD COLUMN {column_sql}")
            )
    if not await _table_exists(connection, "ctrip_competitor_room_rates"):
        return
    rate_columns = await _table_columns(connection, "ctrip_competitor_room_rates")
    for column_name, column_sql in _room_rate_columns(dialect).items():
        if column_name in rate_columns:
            continue
        await connection.execute(
            text(f"ALTER TABLE ctrip_competitor_room_rates ADD COLUMN {column_sql}")
        )


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)


async def _table_columns(connection: AsyncConnection, table_name: str) -> set[str]:
    def inspect_columns(sync_connection) -> set[str]:
        return {column["name"] for column in inspect(sync_connection).get_columns(table_name)}

    return await connection.run_sync(inspect_columns)


async def _create_browser_pool_member_table(
    connection: AsyncConnection,
    dialect: str,
) -> None:
    table_name = "ctrip_competitor_watch_group_browser_pool_members"
    if not await _table_exists(connection, table_name):
        await connection.execute(text(_browser_pool_member_table_sql(dialect)))
    if not await _index_exists(
        connection,
        table_name,
        "ix_ctrip_competitor_browser_pool_group_enabled_sort",
    ):
        await connection.execute(
            text(
                "CREATE INDEX ix_ctrip_competitor_browser_pool_group_enabled_sort "
                "ON ctrip_competitor_watch_group_browser_pool_members "
                "(group_id, enabled, sort_order)"
            )
        )


async def _create_browser_lease_table(connection: AsyncConnection, dialect: str) -> None:
    table_name = "ctrip_competitor_browser_leases"
    if not await _table_exists(connection, table_name):
        await connection.execute(text(_browser_lease_table_sql(dialect)))
    if not await _index_exists(
        connection,
        table_name,
        "ix_ctrip_competitor_browser_leases_expires",
    ):
        await connection.execute(
            text(
                "CREATE INDEX ix_ctrip_competitor_browser_leases_expires "
                "ON ctrip_competitor_browser_leases (lease_expires_at)"
            )
        )


async def _index_exists(connection: AsyncConnection, table_name: str, index_name: str) -> bool:
    def inspect_indexes(sync_connection) -> bool:
        indexes = inspect(sync_connection).get_indexes(table_name)
        return any(index["name"] == index_name for index in indexes)

    return await connection.run_sync(inspect_indexes)


def _hotel_columns(dialect: str) -> dict[str, str]:
    del dialect
    return {
        "short_alias": "short_alias VARCHAR(32) NULL",
    }


def _watch_group_columns(dialect: str) -> dict[str, str]:
    if dialect in {"mysql", "mariadb"}:
        return {
            "brief_title": "brief_title VARCHAR(128) NULL",
            "price_change_alert_title": "price_change_alert_title VARCHAR(128) NULL",
            "shop_key": "shop_key VARCHAR(64) NULL",
            "browser_session_id": "browser_session_id VARCHAR(128) NULL",
            "browser_rotation_mode": (
                "browser_rotation_mode VARCHAR(32) NOT NULL DEFAULT 'fixed'"
            ),
            "pool_failover_enabled": "pool_failover_enabled TINYINT(1) NOT NULL DEFAULT 1",
            "pool_cooldown_minutes": "pool_cooldown_minutes INT NOT NULL DEFAULT 60",
            "pricing_platform_account_id": "pricing_platform_account_id VARCHAR(128) NULL",
            "pricing_browser_session_id": "pricing_browser_session_id VARCHAR(128) NULL",
            "feishu_webhook_url": "feishu_webhook_url TEXT NULL",
            "feishu_mobile_webhook_url": "feishu_mobile_webhook_url TEXT NULL",
            "feishu_desktop_webhook_url": "feishu_desktop_webhook_url TEXT NULL",
            "feishu_chat_id": "feishu_chat_id VARCHAR(128) NULL",
            "feishu_card_layout": (
                "feishu_card_layout VARCHAR(32) NOT NULL DEFAULT 'mobile_blocks'"
            ),
            "push_date_scope": (
                "push_date_scope VARCHAR(32) NOT NULL DEFAULT 'today_tomorrow'"
            ),
            "timezone": "timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai'",
            "retention_days": "retention_days INT NOT NULL DEFAULT 14",
            "query_interval_minutes": "query_interval_minutes INT NOT NULL DEFAULT 120",
            "schedule_mode": "schedule_mode VARCHAR(32) NOT NULL DEFAULT 'fixed_slots'",
            "schedule_start_time": "schedule_start_time VARCHAR(5) NOT NULL DEFAULT '06:00'",
            "schedule_end_time": "schedule_end_time VARCHAR(5) NOT NULL DEFAULT '01:00'",
            "schedule_interval_minutes": "schedule_interval_minutes INT NOT NULL DEFAULT 120",
            "auto_poll_enabled": "auto_poll_enabled TINYINT(1) NOT NULL DEFAULT 0",
            "last_polled_at": "last_polled_at DATETIME NULL",
        }
    return {
        "brief_title": "brief_title VARCHAR(128) NULL",
        "price_change_alert_title": "price_change_alert_title VARCHAR(128) NULL",
        "shop_key": "shop_key VARCHAR(64) NULL",
        "browser_session_id": "browser_session_id VARCHAR(128) NULL",
        "browser_rotation_mode": "browser_rotation_mode VARCHAR(32) NOT NULL DEFAULT 'fixed'",
        "pool_failover_enabled": "pool_failover_enabled BOOLEAN NOT NULL DEFAULT 1",
        "pool_cooldown_minutes": "pool_cooldown_minutes INTEGER NOT NULL DEFAULT 60",
        "pricing_platform_account_id": "pricing_platform_account_id VARCHAR(128) NULL",
        "pricing_browser_session_id": "pricing_browser_session_id VARCHAR(128) NULL",
        "feishu_webhook_url": "feishu_webhook_url TEXT NULL",
        "feishu_mobile_webhook_url": "feishu_mobile_webhook_url TEXT NULL",
        "feishu_desktop_webhook_url": "feishu_desktop_webhook_url TEXT NULL",
        "feishu_chat_id": "feishu_chat_id VARCHAR(128) NULL",
        "feishu_card_layout": (
            "feishu_card_layout VARCHAR(32) NOT NULL DEFAULT 'mobile_blocks'"
        ),
        "push_date_scope": (
            "push_date_scope VARCHAR(32) NOT NULL DEFAULT 'today_tomorrow'"
        ),
        "timezone": "timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai'",
        "retention_days": "retention_days INTEGER NOT NULL DEFAULT 14",
        "query_interval_minutes": "query_interval_minutes INTEGER NOT NULL DEFAULT 120",
        "schedule_mode": "schedule_mode VARCHAR(32) NOT NULL DEFAULT 'fixed_slots'",
        "schedule_start_time": "schedule_start_time VARCHAR(5) NOT NULL DEFAULT '06:00'",
        "schedule_end_time": "schedule_end_time VARCHAR(5) NOT NULL DEFAULT '01:00'",
        "schedule_interval_minutes": "schedule_interval_minutes INTEGER NOT NULL DEFAULT 120",
        "auto_poll_enabled": "auto_poll_enabled BOOLEAN NOT NULL DEFAULT 0",
        "last_polled_at": "last_polled_at DATETIME NULL",
    }


def _room_rate_columns(dialect: str) -> dict[str, str]:
    if dialect in {"mysql", "mariadb"}:
        return {
            "discount_amount_yuan": "discount_amount_yuan INT NULL",
            "discount_display": "discount_display VARCHAR(64) NULL",
            "source_discount_line": "source_discount_line VARCHAR(255) NULL",
        }
    return {
        "discount_amount_yuan": "discount_amount_yuan INTEGER NULL",
        "discount_display": "discount_display VARCHAR(64) NULL",
        "source_discount_line": "source_discount_line VARCHAR(255) NULL",
    }


def _browser_pool_member_table_sql(dialect: str) -> str:
    if dialect in {"mysql", "mariadb"}:
        return """
        CREATE TABLE ctrip_competitor_watch_group_browser_pool_members (
            id BIGINT NOT NULL AUTO_INCREMENT,
            group_id BIGINT NOT NULL,
            browser_session_id VARCHAR(128) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            sort_order INT NOT NULL DEFAULT 0,
            weight INT NOT NULL DEFAULT 1,
            last_selected_at DATETIME NULL,
            last_success_at DATETIME NULL,
            last_failure_at DATETIME NULL,
            last_status VARCHAR(32) NULL,
            cooldown_until DATETIME NULL,
            consecutive_failures INT NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_ctrip_competitor_browser_pool_member
                (group_id, browser_session_id),
            CONSTRAINT fk_ctrip_competitor_browser_pool_group
                FOREIGN KEY (group_id) REFERENCES ctrip_competitor_watch_groups (id)
        )
        """
    return """
    CREATE TABLE ctrip_competitor_watch_group_browser_pool_members (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        browser_session_id VARCHAR(128) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        weight INTEGER NOT NULL DEFAULT 1,
        last_selected_at DATETIME NULL,
        last_success_at DATETIME NULL,
        last_failure_at DATETIME NULL,
        last_status VARCHAR(32) NULL,
        cooldown_until DATETIME NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        UNIQUE (group_id, browser_session_id),
        FOREIGN KEY(group_id) REFERENCES ctrip_competitor_watch_groups (id)
    )
    """


def _browser_lease_table_sql(dialect: str) -> str:
    if dialect in {"mysql", "mariadb"}:
        return """
        CREATE TABLE ctrip_competitor_browser_leases (
            id BIGINT NOT NULL AUTO_INCREMENT,
            browser_session_id VARCHAR(128) NOT NULL,
            watch_group_key VARCHAR(64) NOT NULL,
            lease_owner VARCHAR(128) NOT NULL,
            lease_expires_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_ctrip_competitor_browser_lease_browser
                (browser_session_id)
        )
        """
    return """
    CREATE TABLE ctrip_competitor_browser_leases (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        browser_session_id VARCHAR(128) NOT NULL,
        watch_group_key VARCHAR(64) NOT NULL,
        lease_owner VARCHAR(128) NOT NULL,
        lease_expires_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE (browser_session_id)
    )
    """
