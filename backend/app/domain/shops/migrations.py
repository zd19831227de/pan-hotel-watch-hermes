from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_shops_schema(connection: AsyncConnection) -> None:
    """shops 表幂等迁移（新建表；列级补齐在后续阶段接入归属列时扩展）。"""
    dialect = connection.dialect.name
    int_type = "INT" if dialect in {"mysql", "mariadb"} else "INTEGER"
    await connection.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS shops (
                id {int_type} PRIMARY KEY AUTOINCREMENT,
                shop_key VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                short_name VARCHAR(32) NULL,
                sort_order {int_type} NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """.strip()
        )
    )
    await connection.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_shops_shop_key ON shops (shop_key)
            """.strip()
        )
    )
