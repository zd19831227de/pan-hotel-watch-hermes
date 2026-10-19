from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.db.base import Base

settings = get_settings()


def _create_engine(database_url: str):
    engine_options: dict[str, object] = {
        "pool_pre_ping": True,
    }
    if database_url.startswith("sqlite+"):
        engine_options["connect_args"] = {"timeout": 30}
    return create_async_engine(database_url, **engine_options)


engine = _create_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
_database_ready_engine = None
# Each event loop gets its own initialization lock so asyncio locks are never
# awaited across loops while concurrent requests share one engine setup.
_database_ready_locks: WeakKeyDictionary[object, asyncio.Lock] = WeakKeyDictionary()


def _database_ready_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _database_ready_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _database_ready_locks[loop] = lock
    return lock


async def ensure_db_ready() -> None:
    global _database_ready_engine

    if not get_settings().database_auto_create_tables:
        return

    current_engine = engine
    if _database_ready_engine is current_engine:
        return

    async with _database_ready_lock():
        if _database_ready_engine is current_engine:
            return
        await _initialize_database(current_engine)
        _database_ready_engine = current_engine


async def _initialize_database(database_engine) -> None:
    from app.domain.config import models as _config_models  # noqa: F401
    from app.domain.ai_agent import models as _ai_agent_models  # noqa: F401
    from app.domain.ctrip_ebooking_watch import models as _ctrip_ebooking_watch_models  # noqa: F401
    from app.domain.competitor_prices import models as _competitor_prices_models  # noqa: F401
    from app.domain.price_change_alerts import models as _price_change_alert_models  # noqa: F401
    from app.domain.pricing_execution import models as _pricing_execution_models  # noqa: F401
    from app.domain.pricing_strategy import models as _pricing_strategy_models  # noqa: F401
    from app.domain.calendar_price_watch import models as _calendar_price_watch_models  # noqa: F401
    from app.domain.ebk_snapshots import models as _ebk_snapshot_models  # noqa: F401
    from app.domain.meituan_ebooking_watch import models as _meituan_ebooking_watch_models  # noqa: F401
    from app.domain.fliggy_ebooking_watch import models as _fliggy_ebooking_watch_models  # noqa: F401
    from app.domain.ebk_global_push import models as _ebk_global_push_models  # noqa: F401
    from app.domain.rag import models as _rag_models  # noqa: F401
    from app.domain.page_change_alerts import models as _page_change_alert_models  # noqa: F401
    from app.domain.distill import models as _distill_models  # noqa: F401
    from app.domain.review_management import models as _review_management_models  # noqa: F401
    from app.domain.refund_orders import models as _refund_orders_models  # noqa: F401
    from app.domain.reservation_orders import models as _reservation_orders_models  # noqa: F401
    from app.domain.shops import models as _shops_models  # noqa: F401
    from app.domain.browser.operation_leases import (  # noqa: F401
        BrowserOperationLease as _browser_operation_lease_model,
    )
    from app.domain.ebk_period_reads import models as _ebk_period_reads_models  # noqa: F401
    from app.domain.pyramid_bidding import models as _pyramid_bidding_models  # noqa: F401
    from app.domain.promotion_adjust import models as _promotion_adjust_models  # noqa: F401
    from app.domain.bi_daily import models as _bi_daily_models  # noqa: F401
    from app.domain.group_archive import models as _group_archive_models  # noqa: F401

    async with database_engine.begin() as connection:
        if database_engine.sync_engine.url.get_backend_name() == "sqlite":
            await connection.exec_driver_sql("PRAGMA busy_timeout=30000")
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await connection.run_sync(Base.metadata.create_all)
        from app.domain.ai_agent.migrations import ensure_ai_agent_schema
        from app.domain.config.migrations import ensure_runtime_integration_config_schema
        from app.domain.config.migrations import ensure_runtime_llm_models_schema
        from app.domain.ctrip_ebooking_watch.migrations import ensure_data_center_watch_schema
        from app.domain.competitor_prices.migrations import ensure_competitor_price_schema
        from app.domain.price_change_alerts.migrations import ensure_price_change_alert_schema
        from app.domain.pricing_execution.migrations import ensure_pricing_execution_schema
        from app.domain.pricing_strategy.migrations import ensure_pricing_strategy_schema
        from app.domain.calendar_price_watch.migrations import ensure_calendar_price_watch_schema
        from app.domain.ebk_snapshots.migrations import ensure_ebk_snapshot_schema
        from app.domain.meituan_ebooking_watch.migrations import ensure_meituan_ebooking_watch_schema
        from app.domain.fliggy_ebooking_watch.migrations import ensure_fliggy_ebooking_watch_schema
        from app.domain.ebk_global_push.migrations import ensure_ebk_global_push_schema
        from app.domain.rag.migrations import ensure_rag_schema
        from app.domain.page_change_alerts.migrations import ensure_page_change_alert_schema
        from app.domain.distill.migrations import ensure_distill_schema
        from app.domain.review_management.migrations import ensure_review_management_schema
        from app.domain.ebk_period_reads.migrations import ensure_ebk_period_read_schema
        from app.domain.refund_orders.migrations import ensure_refund_orders_schema
        from app.domain.reservation_orders.migrations import (
            ensure_reservation_orders_schema,
        )
        from app.domain.shops.migrations import ensure_shops_schema
        from app.domain.bi_daily.migrations import ensure_bi_daily_schema
        from app.domain.group_archive.migrations import ensure_group_archive_schema

        await ensure_ai_agent_schema(connection)
        await ensure_runtime_integration_config_schema(connection)
        await ensure_runtime_llm_models_schema(connection)
        await ensure_data_center_watch_schema(connection)
        await ensure_competitor_price_schema(connection)
        await ensure_pricing_strategy_schema(connection)
        await ensure_price_change_alert_schema(connection)
        await ensure_pricing_execution_schema(connection)
        await ensure_calendar_price_watch_schema(connection)
        await ensure_ebk_snapshot_schema(connection)
        await ensure_meituan_ebooking_watch_schema(connection)
        await ensure_fliggy_ebooking_watch_schema(connection)
        await ensure_ebk_global_push_schema(connection)
        await ensure_rag_schema(connection)
        await ensure_page_change_alert_schema(connection)
        await ensure_distill_schema(connection)
        await ensure_review_management_schema(connection)
        await ensure_ebk_period_read_schema(connection)
        await ensure_refund_orders_schema(connection)
        await ensure_reservation_orders_schema(connection)
        await ensure_shops_schema(connection)
        await ensure_bi_daily_schema(connection)
        await ensure_group_archive_schema(connection)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
