from __future__ import annotations

import logging

from sqlalchemy import column, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

_WATCH_TABLE = "meituan_ebooking_watch_groups"
_CTRIP_TABLE = "ctrip_ebooking_watch_groups"


async def ensure_meituan_ebooking_watch_schema(connection: AsyncConnection) -> None:
    """Create the Meituan EBK watch-group table when auto-create is enabled.

    建表主路径走 Base.metadata.create_all（含唯一约束/索引）；此处仅做
    IF NOT EXISTS 兜底，与 ebk_snapshots.migrations 同一接入模式。

    2026-08-25 锚定携程组重构：新增 ``ctrip_watch_group_key`` 列，并对旧数据
    做「feishu_chat_id 唯一匹配携程组」的自动回填（零/多匹配保持 NULL，由
    运营在控制台重新锚定）。旧组的 feishu_chat_id/feishu_webhook_url 列保留
    但不再被读取（投递一律走锚定携程组）。
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
            CREATE TABLE IF NOT EXISTS meituan_ebooking_watch_groups (
                id {autoincrement},
                group_key VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(128) NOT NULL,
                brief_title VARCHAR(128) NULL,
                platform_account_id VARCHAR(128) NOT NULL,
                browser_session_id VARCHAR(128) NULL,
                ctrip_watch_group_key VARCHAR(64) NULL,
                feishu_chat_id VARCHAR(128) NULL,
                feishu_webhook_url TEXT NULL,
                timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
                schedule_mode VARCHAR(32) NOT NULL DEFAULT 'fixed_slots',
                schedule_start_time VARCHAR(5) NOT NULL DEFAULT '06:00',
                schedule_end_time VARCHAR(5) NOT NULL DEFAULT '01:00',
                schedule_interval_minutes {int_type} NOT NULL DEFAULT 120,
                auto_poll_enabled BOOLEAN NOT NULL DEFAULT 0,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                last_polled_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """.strip()
        )
    )
    await _ensure_ctrip_anchor_column(connection)
    await _backfill_ctrip_anchor_from_legacy_chat_binding(connection)
    await _ensure_unique_ctrip_anchor_index(connection)
    await _ensure_shop_key_column(connection)
    await _backfill_shop_key_from_anchor(connection)
    await _ensure_unique_shop_key_index(connection)


async def _table_has_column(
    connection: AsyncConnection, table_name: str, column_name: str
) -> bool:
    from sqlalchemy import inspect

    def _check(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return False
        return any(
            item["name"] == column_name for item in inspector.get_columns(table_name)
        )

    return bool(await connection.run_sync(_check))


async def _ensure_ctrip_anchor_column(connection: AsyncConnection) -> None:
    if await _table_has_column(connection, _WATCH_TABLE, "ctrip_watch_group_key"):
        return
    await connection.execute(
        text("ALTER TABLE meituan_ebooking_watch_groups ADD COLUMN ctrip_watch_group_key VARCHAR(64) NULL")
    )


async def _backfill_ctrip_anchor_from_legacy_chat_binding(connection: AsyncConnection) -> None:
    """旧组 feishu_chat_id 与携程组唯一匹配时自动锚定；零/多匹配保持 NULL。"""

    if not await _table_has_column(connection, _WATCH_TABLE, "ctrip_watch_group_key"):
        return
    if not await _table_has_column(connection, _CTRIP_TABLE, "feishu_chat_id"):
        return
    watch_table = table(
        _WATCH_TABLE,
        column("group_key"),
        column("feishu_chat_id"),
        column("ctrip_watch_group_key"),
    )
    ctrip_table = table(
        _CTRIP_TABLE,
        column("group_key"),
        column("feishu_chat_id"),
    )
    legacy_rows = (
        await connection.execute(
            select(watch_table.c.group_key, watch_table.c.feishu_chat_id).where(
                watch_table.c.ctrip_watch_group_key.is_(None),
                watch_table.c.feishu_chat_id.is_not(None),
            )
        )
    ).fetchall()
    if not legacy_rows:
        return
    ctrip_rows = (
        await connection.execute(
            select(ctrip_table.c.group_key, ctrip_table.c.feishu_chat_id).where(
                ctrip_table.c.feishu_chat_id.is_not(None)
            )
        )
    ).fetchall()
    chat_to_ctrip_keys: dict[str, list[str]] = {}
    for ctrip_key, chat_id in ctrip_rows:
        chat = str(chat_id or "").strip()
        if chat:
            chat_to_ctrip_keys.setdefault(chat, []).append(str(ctrip_key))
    taken_anchors: set[str] = set()
    anchored_count = 0
    for watch_key, chat_id in legacy_rows:
        candidates = chat_to_ctrip_keys.get(str(chat_id or "").strip(), [])
        if len(candidates) != 1:
            continue
        anchor = candidates[0]
        if anchor in taken_anchors:
            continue
        await connection.execute(
            update(watch_table)
            .where(watch_table.c.group_key == watch_key)
            .values(ctrip_watch_group_key=anchor)
        )
        taken_anchors.add(anchor)
        anchored_count += 1
    if anchored_count:
        logger.info(
            "meituan ebooking watch: backfilled ctrip anchor for %s legacy group(s)",
            anchored_count,
        )


_ANCHOR_UNIQUE_INDEX_NAME = "uq_meituan_ebk_ctrip_anchor"
_SHOP_KEY_UNIQUE_INDEX_NAME = "ux_meituan_ebk_groups_shop_key"


async def _ensure_shop_key_column(connection: AsyncConnection) -> None:
    """v2 §2 店铺直连：新增可空 shop_key 列（既有表 ADD COLUMN）。"""
    if await _table_has_column(connection, _WATCH_TABLE, "shop_key"):
        return
    await connection.execute(
        text("ALTER TABLE meituan_ebooking_watch_groups ADD COLUMN shop_key VARCHAR(64) NULL")
    )


async def _backfill_shop_key_from_anchor(connection: AsyncConnection) -> None:
    """存量回填（v2 §2）：shop_key = 挂载携程组的 shop_key；回填不了的保持
    NULL 归「未分组」待运营补绑。同店铺同平台多组时只回填最小 id 一条，
    避免撞唯一索引。"""

    if not await _table_has_column(connection, _WATCH_TABLE, "shop_key"):
        return
    if not await _table_has_column(connection, _CTRIP_TABLE, "shop_key"):
        return
    rows = (
        await connection.exec_driver_sql(
            f"""
            SELECT w.group_key AS watch_key, c.shop_key AS shop_key
            FROM {_WATCH_TABLE} w
            JOIN {_CTRIP_TABLE} c ON c.group_key = w.ctrip_watch_group_key
            WHERE w.shop_key IS NULL AND c.shop_key IS NOT NULL
            ORDER BY c.shop_key, w.id
            """
        )
    ).fetchall()
    taken_shops: set[str] = set()
    backfilled = 0
    for watch_key, shop_key in rows:
        shop = str(shop_key or "").strip()
        if not shop or shop in taken_shops:
            continue
        await connection.execute(
            text(
                f"UPDATE {_WATCH_TABLE} SET shop_key = :shop_key "
                f"WHERE group_key = :watch_key AND shop_key IS NULL"
            ),
            {"shop_key": shop, "watch_key": watch_key},
        )
        taken_shops.add(shop)
        backfilled += 1
    if backfilled:
        logger.info(
            "meituan ebooking watch: backfilled shop_key for %s group(s)", backfilled
        )


async def _ensure_unique_shop_key_index(connection: AsyncConnection) -> None:
    """每店至多一个美团监控组（NULL 不受约束）；重复归属先去重保最小 id。"""

    if not await _table_has_column(connection, _WATCH_TABLE, "shop_key"):
        return
    if await _index_exists(connection, _WATCH_TABLE, _SHOP_KEY_UNIQUE_INDEX_NAME):
        return
    dupes = (
        await connection.exec_driver_sql(
            f"SELECT shop_key, MIN(id) AS keep_id FROM {_WATCH_TABLE} "
            f"WHERE shop_key IS NOT NULL GROUP BY shop_key HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for shop_key, keep_id in dupes:
        await connection.exec_driver_sql(
            f"UPDATE {_WATCH_TABLE} SET shop_key = NULL "
            f"WHERE shop_key = :shop_key AND id <> :keep_id",
            {"shop_key": shop_key, "keep_id": keep_id},
        )
    try:
        await connection.exec_driver_sql(
            f"CREATE UNIQUE INDEX {_SHOP_KEY_UNIQUE_INDEX_NAME} "
            f"ON {_WATCH_TABLE} (shop_key)"
        )
    except Exception:
        logger.debug(
            "meituan shop_key unique index create skipped (likely already exists)",
            exc_info=True,
        )


async def _ensure_unique_ctrip_anchor_index(connection: AsyncConnection) -> None:
    """一个携程组只能挂一个本平台排程：DB 级唯一索引兜底应用层先查后写的竞态。

    唯一索引允许多个 NULL（未锚定的旧组不受影响，SQLite/MySQL 均如此）。
    旧数据/手工改库若已存在重复锚点，建索引前先去重（保留最小 id 行，其余置 NULL）。
    """

    if not await _table_has_column(connection, _WATCH_TABLE, "ctrip_watch_group_key"):
        return
    if not await _table_exists(connection, _WATCH_TABLE):
        return
    if await _index_exists(connection, _WATCH_TABLE, _ANCHOR_UNIQUE_INDEX_NAME):
        return
    await _dedupe_duplicate_anchors(connection)
    try:
        await connection.exec_driver_sql(
            f"CREATE UNIQUE INDEX {_ANCHOR_UNIQUE_INDEX_NAME} "
            f"ON {_WATCH_TABLE} (ctrip_watch_group_key)"
        )
    except Exception:
        logger.debug(
            "meituan anchor unique index create skipped (likely already exists)",
            exc_info=True,
        )


async def _dedupe_duplicate_anchors(connection: AsyncConnection) -> None:
    rows = (
        await connection.exec_driver_sql(
            f"SELECT ctrip_watch_group_key AS anchor, MIN(id) AS keep_id "
            f"FROM {_WATCH_TABLE} WHERE ctrip_watch_group_key IS NOT NULL "
            f"GROUP BY ctrip_watch_group_key HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if not rows:
        return
    for row in rows:
        anchor = row[0]
        keep_id = row[1]
        await connection.exec_driver_sql(
            f"UPDATE {_WATCH_TABLE} SET ctrip_watch_group_key = NULL "
            f"WHERE ctrip_watch_group_key = :anchor AND id <> :keep_id",
            {"anchor": anchor, "keep_id": keep_id},
        )
    logger.info(
        "meituan ebooking watch: deduplicated %s duplicate anchor group(s)",
        len(rows),
    )


async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
    from sqlalchemy import inspect

    def _check(sync_connection) -> bool:
        return inspect(sync_connection).has_table(table_name)

    return bool(await connection.run_sync(_check))


async def _index_exists(
    connection: AsyncConnection, table_name: str, index_name: str
) -> bool:
    from sqlalchemy import inspect

    def _check(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return False
        return any(
            item["name"] == index_name for item in inspector.get_indexes(table_name)
        )

    return bool(await connection.run_sync(_check))
