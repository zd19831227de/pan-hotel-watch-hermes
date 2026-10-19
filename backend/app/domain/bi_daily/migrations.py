from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# create_all 已建表与 __table_args__ 索引；此处仅幂等补建跨列查询索引，
# 与既有域 ensure_<domain>_schema 模式一致（全部 IF NOT EXISTS，可重复执行）。
_BI_DAILY_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_booking_position_facts_shop_stay_asof"
    " ON booking_position_facts (shop_key, stay_date, as_of_time)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_position_facts_source_row_ref"
    " ON booking_position_facts (source_row_ref)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_competitor_market_facts_source_row_ref"
    " ON competitor_market_facts (source_row_ref)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_forecast_shop_stay_room_version"
    " ON budget_forecast_facts (shop_key, stay_date, room_type, forecast_version)",
    "CREATE INDEX IF NOT EXISTS ix_competitor_market_facts_shop_stay"
    " ON competitor_market_facts (shop_key, stay_date)",
    "CREATE INDEX IF NOT EXISTS ix_competitor_market_facts_hotel_stay_captured"
    " ON competitor_market_facts (competitor_hotel_id, stay_date, captured_at)",
    "CREATE INDEX IF NOT EXISTS ix_budget_forecast_facts_shop_stay"
    " ON budget_forecast_facts (shop_key, stay_date)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_weekday_date_mapping_current"
    " ON weekday_date_mappings (current_stay_date)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_bi_collection_run_date_source"
    " ON bi_collection_runs (run_date, source)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_bi_daily_card_push_shop_date"
    " ON bi_daily_card_pushes (shop_key, push_date)",
    "CREATE INDEX IF NOT EXISTS ix_market_demand_facts_shop_stay_captured"
    " ON market_demand_facts (shop_key, stay_date, captured_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_demand_facts_source_row_ref"
    " ON market_demand_facts (source_row_ref)",
)


async def ensure_bi_daily_schema(connection: AsyncConnection) -> None:
    """幂等补建 bi_daily 域索引（新表本体由 create_all 创建）。"""
    for statement in _BI_DAILY_INDEX_STATEMENTS:
        await connection.execute(text(statement))
