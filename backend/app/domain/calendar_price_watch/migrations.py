from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_calendar_price_watch_schema(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    for statement in _create_table_statements(dialect):
        await connection.execute(text(statement))
    for statement in _create_index_statements(dialect):
        await connection.execute(text(statement))
    if await _table_exists(connection, "calendar_price_watch_configs"):
        columns = await _table_columns(connection, "calendar_price_watch_configs")
        for column_name, column_sql in _config_columns(dialect).items():
            if column_name in columns:
                continue
            await connection.execute(
                text(f"ALTER TABLE calendar_price_watch_configs ADD COLUMN {column_sql}")
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
    bool_type = "TINYINT(1)" if dialect in {"mysql", "mariadb"} else "BOOLEAN"
    json_type = "JSON" if dialect in {"mysql", "mariadb", "sqlite"} else "JSON"
    return {
        "matrix_brief_enabled": f"matrix_brief_enabled {bool_type} NOT NULL DEFAULT 0",
        "matrix_brief_title": "matrix_brief_title VARCHAR(128) NULL",
        "matrix_brief_push_times": f"matrix_brief_push_times {json_type} NULL",
        "matrix_brief_morning_push_time": (
            "matrix_brief_morning_push_time VARCHAR(5) NOT NULL DEFAULT '10:00'"
        ),
        "matrix_brief_evening_push_time": (
            "matrix_brief_evening_push_time VARCHAR(5) NOT NULL DEFAULT '21:00'"
        ),
        "matrix_brief_last_pushed_json": f"matrix_brief_last_pushed_json {json_type} NULL",
        "browser_session_id": "browser_session_id VARCHAR(128) NULL",
        "platform_account_id": "platform_account_id VARCHAR(128) NULL",
    }


def _create_table_statements(dialect: str) -> list[str]:
    bool_type = "TINYINT(1)" if dialect in {"mysql", "mariadb"} else "BOOLEAN"
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    json_type = "JSON" if dialect in {"mysql", "mariadb", "sqlite"} else "JSON"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS calendar_price_watch_configs (
            id {int_type} PRIMARY KEY AUTOINCREMENT,
            watch_group_id {int_type} NOT NULL UNIQUE,
            future_days {int_type} NOT NULL DEFAULT 15,
            high_gap_threshold_yuan {int_type} NOT NULL DEFAULT 10,
            low_gap_threshold_yuan {int_type} NOT NULL DEFAULT 20,
            auto_poll_enabled {bool_type} NOT NULL DEFAULT 0,
            schedule_interval_minutes {int_type} NOT NULL DEFAULT 120,
            alert_title VARCHAR(128) NULL,
            browser_session_id VARCHAR(128) NULL,
            platform_account_id VARCHAR(128) NULL,
            matrix_brief_enabled {bool_type} NOT NULL DEFAULT 0,
            matrix_brief_title VARCHAR(128) NULL,
            matrix_brief_push_times {json_type} NULL,
            matrix_brief_morning_push_time VARCHAR(5) NOT NULL DEFAULT '10:00',
            matrix_brief_evening_push_time VARCHAR(5) NOT NULL DEFAULT '21:00',
            matrix_brief_last_pushed_json {json_type} NULL,
            last_polled_at DATETIME NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(watch_group_id) REFERENCES ctrip_competitor_watch_groups(id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS calendar_price_runs (
            id {int_type} PRIMARY KEY AUTOINCREMENT,
            watch_group_id {int_type} NOT NULL,
            config_id {int_type} NOT NULL,
            platform_account_id VARCHAR(128) NOT NULL,
            browser_session_id VARCHAR(128) NULL,
            actor_id VARCHAR(128) NOT NULL,
            source VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            summary TEXT NULL,
            future_days {int_type} NOT NULL,
            high_gap_threshold_yuan {int_type} NOT NULL,
            low_gap_threshold_yuan {int_type} NOT NULL,
            captured_at DATETIME NOT NULL,
            evidence_json {json_type} NULL,
            push_result_json {json_type} NULL,
            FOREIGN KEY(watch_group_id) REFERENCES ctrip_competitor_watch_groups(id),
            FOREIGN KEY(config_id) REFERENCES calendar_price_watch_configs(id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS calendar_price_points (
            id {int_type} PRIMARY KEY AUTOINCREMENT,
            run_id {int_type} NOT NULL,
            watch_group_id {int_type} NOT NULL,
            hotel_config_id {int_type} NOT NULL,
            role VARCHAR(32) NOT NULL,
            hotel_alias VARCHAR(128) NOT NULL,
            checkin_date DATE NOT NULL,
            price_amount_yuan {int_type} NULL,
            currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
            source VARCHAR(64) NOT NULL,
            is_trusted {bool_type} NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL,
            review_reason VARCHAR(128) NULL,
            source_text VARCHAR(255) NULL,
            captured_at DATETIME NOT NULL,
            FOREIGN KEY(run_id) REFERENCES calendar_price_runs(id),
            FOREIGN KEY(watch_group_id) REFERENCES ctrip_competitor_watch_groups(id),
            FOREIGN KEY(hotel_config_id) REFERENCES ctrip_competitor_hotels(id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS calendar_price_alerts (
            id {int_type} PRIMARY KEY AUTOINCREMENT,
            run_id {int_type} NOT NULL,
            watch_group_id {int_type} NOT NULL,
            alert_type VARCHAR(32) NOT NULL,
            checkin_date DATE NOT NULL,
            own_price_yuan {int_type} NOT NULL,
            reference_price_yuan {int_type} NOT NULL,
            gap_yuan {int_type} NOT NULL,
            reference_hotel_alias VARCHAR(128) NULL,
            summary TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(run_id) REFERENCES calendar_price_runs(id),
            FOREIGN KEY(watch_group_id) REFERENCES ctrip_competitor_watch_groups(id)
        )
        """.strip(),
    ]


def _create_index_statements(dialect: str) -> list[str]:
    if dialect in {"mysql", "mariadb"}:
        return [
            "CREATE INDEX IF NOT EXISTS ix_calendar_price_points_run_date ON calendar_price_points (run_id, checkin_date)",
            "CREATE INDEX IF NOT EXISTS ix_calendar_price_points_hotel_date_captured ON calendar_price_points (hotel_config_id, checkin_date, captured_at)",
        ]
    return [
        "CREATE INDEX IF NOT EXISTS ix_calendar_price_points_run_date ON calendar_price_points (run_id, checkin_date)",
        "CREATE INDEX IF NOT EXISTS ix_calendar_price_points_hotel_date_captured ON calendar_price_points (hotel_config_id, checkin_date, captured_at)",
    ]
