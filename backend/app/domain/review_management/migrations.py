from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_review_management_schema(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    for statement in _create_table_statements(dialect):
        await connection.execute(text(statement))
    await _ensure_store_columns(connection, dialect)
    await _backfill_default_store_columns(connection)


def _create_table_statements(dialect: str) -> list[str]:
    bool_type = "TINYINT(1)" if dialect in {"mysql", "mariadb"} else "BOOLEAN"
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    float_type = "DOUBLE" if dialect in {"mysql", "mariadb"} else "REAL"
    json_type = "JSON"
    # MySQL 的自增语法是 AUTO_INCREMENT；AUTOINCREMENT 只对 SQLite 有效。
    id_column = (
        f"{int_type} PRIMARY KEY AUTO_INCREMENT"
        if dialect in {"mysql", "mariadb"}
        else f"{int_type} PRIMARY KEY AUTOINCREMENT"
    )
    return [
        f"""
        CREATE TABLE IF NOT EXISTS review_items (
            id {id_column},
            platform VARCHAR(16) NOT NULL,
            browser_session_id VARCHAR(128) NULL,
            hotel_id VARCHAR(128) NOT NULL,
            review_id VARCHAR(128) NOT NULL,
            user_nickname VARCHAR(255) NULL,
            score {float_type} NULL,
            content TEXT NULL,
            review_time VARCHAR(64) NULL,
            reply_status VARCHAR(16) NOT NULL DEFAULT 'pending',
            reply_content TEXT NULL,
            replied_at DATETIME NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE (platform, hotel_id, review_id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS reply_templates (
            id {id_column},
            platform VARCHAR(16) NOT NULL,
            browser_session_id VARCHAR(128) NULL,
            category VARCHAR(16) NOT NULL,
            name VARCHAR(128) NOT NULL,
            content TEXT NOT NULL,
            score_range_json {json_type} NULL,
            priority {int_type} NOT NULL DEFAULT 100,
            enabled {bool_type} NOT NULL DEFAULT 1,
            source VARCHAR(16) NOT NULL DEFAULT 'manual',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS review_reply_approvals (
            id {id_column},
            platform VARCHAR(16) NOT NULL,
            browser_session_id VARCHAR(128) NULL,
            review_item_id {int_type} NOT NULL,
            generated_reply_content TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            decision_source VARCHAR(16) NULL,
            operator_open_id_hash VARCHAR(128) NULL,
            feishu_chat_id VARCHAR(128) NULL,
            card_message_id VARCHAR(128) NULL,
            submitted_at DATETIME NULL,
            submit_result_json {json_type} NULL,
            error_msg TEXT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(review_item_id) REFERENCES review_items(id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS review_auto_reply_configs (
            id {id_column},
            platform VARCHAR(16) NOT NULL UNIQUE,
            auto_reply_mode VARCHAR(16) NOT NULL DEFAULT 'off',
            auto_fetch_enabled {bool_type} NOT NULL DEFAULT 0,
            fetch_interval_minutes {int_type} NOT NULL DEFAULT 30,
            auto_process_time VARCHAR(5) NOT NULL DEFAULT '23:00',
            last_auto_processed_at DATETIME NULL,
            approval_chat_id VARCHAR(128) NULL,
            last_fetched_at DATETIME NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS review_store_bindings (
            id {id_column},
            platform VARCHAR(16) NOT NULL,
            browser_session_id VARCHAR(128) NOT NULL UNIQUE,
            platform_account_id VARCHAR(128) NOT NULL,
            display_name VARCHAR(128) NOT NULL DEFAULT '',
            feishu_chat_id VARCHAR(128) NULL,
            auto_reply_mode VARCHAR(16) NOT NULL DEFAULT 'off',
            auto_fetch_enabled {bool_type} NOT NULL DEFAULT 0,
            fetch_interval_minutes {int_type} NOT NULL DEFAULT 30,
            auto_process_time VARCHAR(5) NOT NULL DEFAULT '23:00',
            last_auto_processed_at DATETIME NULL,
            last_fetched_at DATETIME NULL,
            last_pending_count {int_type} NULL,
            enabled {bool_type} NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS review_tasks (
            id {id_column},
            platform VARCHAR(16) NOT NULL,
            browser_session_id VARCHAR(128) NULL,
            task_type VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            review_count {int_type} NOT NULL DEFAULT 0,
            result_json {json_type} NULL,
            error_msg TEXT NULL,
            created_at DATETIME NOT NULL,
            completed_at DATETIME NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS review_daily_card_pushes (
            id {id_column},
            chat_id VARCHAR(128) NOT NULL,
            push_date VARCHAR(10) NOT NULL,
            created_at DATETIME NOT NULL,
            UNIQUE (chat_id, push_date)
        )
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_review_items_platform_status
            ON review_items (platform, reply_status)
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_reply_templates_platform_category_priority
            ON reply_templates (platform, category, priority)
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_review_reply_approvals_platform_status
            ON review_reply_approvals (platform, status)
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_review_tasks_platform_created
            ON review_tasks (platform, created_at)
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS ix_review_store_bindings_platform_enabled
            ON review_store_bindings (platform, enabled)
        """.strip(),
    ]


# 旧库补列：多店铺改造前建的表没有 browser_session_id。
_STORE_SCOPED_COLUMNS: dict[str, str] = {
    "review_items": "browser_session_id VARCHAR(128) NULL",
    "reply_templates": "browser_session_id VARCHAR(128) NULL",
    "review_reply_approvals": "browser_session_id VARCHAR(128) NULL",
    "review_tasks": "browser_session_id VARCHAR(128) NULL",
}


# 定时触发改造（2026-09-16）后加的字段对旧库补列。
_SCHEDULED_TRIGGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "review_store_bindings": (
        "auto_process_time VARCHAR(5) NOT NULL DEFAULT '23:00'",
        "last_auto_processed_at DATETIME NULL",
    ),
    "review_auto_reply_configs": (
        "auto_process_time VARCHAR(5) NOT NULL DEFAULT '23:00'",
        "last_auto_processed_at DATETIME NULL",
    ),
}


async def _ensure_store_columns(connection: AsyncConnection, dialect: str) -> None:
    del dialect
    for table_name, column_sql in _STORE_SCOPED_COLUMNS.items():
        if not await _table_exists(connection, table_name):
            continue
        columns = await _table_columns(connection, table_name)
        if "browser_session_id" in columns:
            continue
        await connection.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        )
    # review_store_bindings 后加的字段（last_pending_count）对旧库补列。
    if await _table_exists(connection, "review_store_bindings"):
        binding_columns = await _table_columns(connection, "review_store_bindings")
        if "last_pending_count" not in binding_columns:
            await connection.execute(
                text(
                    "ALTER TABLE review_store_bindings ADD COLUMN "
                    "last_pending_count INTEGER NULL"
                )
            )
    # 定时触发字段（auto_process_time / last_auto_processed_at）对旧库补列。
    for table_name, column_sqls in _SCHEDULED_TRIGGER_COLUMNS.items():
        if not await _table_exists(connection, table_name):
            continue
        columns = await _table_columns(connection, table_name)
        for column_sql in column_sqls:
            column_name = column_sql.split()[0]
            if column_name in columns:
                continue
            await connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
            )


async def _backfill_default_store_columns(connection: AsyncConnection) -> None:
    """旧数据回填店铺归属：按平台默认端口的浏览器账号标记存量行。

    改造前所有抓取都走平台默认端口，即默认端口账号对应的店铺；
    找不到匹配账号时保留 NULL（按平台级视图展示，不丢数据）。
    """
    from .adapters import default_store_browser_session_ids

    try:
        session_ids = default_store_browser_session_ids()
    except Exception:
        return
    for platform, session_id in session_ids.items():
        for table_name in _STORE_SCOPED_COLUMNS:
            if not await _table_exists(connection, table_name):
                continue
            await connection.execute(
                text(
                    f"UPDATE {table_name} SET browser_session_id = :sid "
                    "WHERE platform = :platform AND browser_session_id IS NULL"
                ),
                {"sid": session_id, "platform": platform},
            )


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    def inspect_table(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return await connection.run_sync(inspect_table)


async def _table_columns(connection: AsyncConnection, table_name: str) -> set[str]:
    def inspect_columns(sync_connection) -> set[str]:
        return {column["name"] for column in inspect(sync_connection).get_columns(table_name)}

    return await connection.run_sync(inspect_columns)
