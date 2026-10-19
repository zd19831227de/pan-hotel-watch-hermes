from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_ebk_period_read_schema(connection: AsyncConnection) -> None:
    """为 ebk_period_read_snapshots 幂等补齐 business_date / platform_account_id 列。

    该表此前由 Base.metadata.create_all 创建、无显式迁移；新增列后老库不会
    自动加列（create_all 不 ALTER 已存在表），需此处补齐。新库由 create_all
    直接带列，此处 ALTER 由列存在性守卫跳过。

    platform_account_id（2026-09-16 多店铺日终快照）：存量 NULL 行按
    「该平台第一个有效绑定监控组」回填——历史上写入端只解析过第一个组
    （单店铺语义），该回填与旧读取行为等价，不会改写口径归属。
    """

    def _ensure(sync_connection) -> None:
        inspector = sync_connection.dialect
        if not inspector.has_table(sync_connection, "ebk_period_read_snapshots"):
            return
        columns = {
            str(column["name"])
            for column in inspector.get_columns(sync_connection, "ebk_period_read_snapshots")
        }
        if "business_date" not in columns:
            sync_connection.execute(
                text("ALTER TABLE ebk_period_read_snapshots ADD COLUMN business_date DATE")
            )
        if "platform_account_id" not in columns:
            sync_connection.execute(
                text(
                    "ALTER TABLE ebk_period_read_snapshots "
                    "ADD COLUMN platform_account_id VARCHAR(64)"
                )
            )

    await connection.run_sync(_ensure)

    # 回填存量行：与旧「第一个有效绑定组」解析规则一致（组序第一个、账号
    # 非空且非 __ 前缀占位——用 substr 判前缀，避免 LIKE ESCAPE 反斜杠在
    # Python 源码/SQL 两层转义出错）。组表缺失/无有效绑定时保持 NULL
    # （读取端按未过滤兜底，行为不劣化）。
    group_tables = {
        "ctrip": "ctrip_ebooking_watch_groups",
        "meituan": "meituan_ebooking_watch_groups",
        "fliggy": "fliggy_ebooking_watch_groups",
    }
    def _has_table(sync_connection, table: str) -> bool:
        return sync_connection.dialect.has_table(sync_connection, table)

    existing_tables = await connection.run_sync(
        lambda sync_connection: {
            platform: _has_table(sync_connection, table)
            for platform, table in group_tables.items()
        }
    )
    for platform, table in group_tables.items():
        if not existing_tables.get(platform):
            continue
        await connection.execute(
            text(
                f"""
                UPDATE ebk_period_read_snapshots
                SET platform_account_id = (
                    SELECT platform_account_id FROM {table}
                    WHERE platform_account_id IS NOT NULL
                      AND TRIM(platform_account_id) != ''
                      AND substr(platform_account_id, 1, 2) <> '__'
                    ORDER BY group_key ASC
                    LIMIT 1
                )
                WHERE platform = :platform AND platform_account_id IS NULL
                """.strip()
            ),
            {"platform": platform},
        )
