import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.calendar_price_watch import router as calendar_price_watch_router
from app.api.v1.ebk_global_push import router as ebk_global_push_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.skills import router as skills_router
from app.api.v1.rag import router as rag_router
from app.api.v1.doubao_chat import router as doubao_chat_router
from app.api.v1.qianwen_chat import router as qianwen_chat_router
from app.api.v1.distill import router as distill_router
from app.api.v1.bi_daily import router as bi_daily_router
from app.api.v1.browser_sessions import router as browser_sessions_router
from app.api.v1.competitor_watch import router as competitor_watch_router
from app.api.v1.config import router as config_router
from app.api.v1.ctrip_ebooking import router as ctrip_ebooking_router
from app.api.v1.ctrip_ebooking_watch import router as ctrip_ebooking_watch_router
from app.api.v1.feishu import router as feishu_router
from app.api.v1.group_archive import router as group_archive_router
from app.api.v1.meituan_ebooking import router as meituan_ebooking_router
from app.api.v1.meituan_ebooking_watch import router as meituan_ebooking_watch_router
from app.api.v1.fliggy_ebooking_watch import router as fliggy_ebooking_watch_router
from app.api.v1.fliggy_ebooking import router as fliggy_ebooking_router
from app.api.v1.ebk_snapshots import router as ebk_snapshots_router
from app.api.v1.exports import router as exports_router
from app.api.v1.health import router as health_router
from app.api.v1.ops import router as ops_router
from app.api.v1.page_health import router as page_health_router
from app.api.v1.price_change_alerts import router as price_change_alerts_router
from app.api.v1.pricing_audit import router as pricing_audit_router
from app.api.v1.pricing_strategy import router as pricing_strategy_router
from app.api.v1.ebk_period_reads import router as ebk_period_reads_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.refunds import router as refunds_router
from app.api.v1.reservations import router as reservations_router
from app.api.v1.shops import router as shops_router
from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.db.session import ensure_db_ready


async def _recover_stuck_approval_pipelines() -> None:
    """启动时恢复重启前卡在 queued/running 的审批执行链（标记失败并补推卡片）。"""
    from app.domain.pricing_approvals.service import (
        recover_stuck_pricing_approval_pipelines,
    )
    from app.domain.promotion_adjust.approvals import (
        recover_stuck_promotion_approval_pipelines,
    )

    logger = logging.getLogger("app.main")
    for name, recover in (
        ("pricing", recover_stuck_pricing_approval_pipelines),
        ("promotion", recover_stuck_promotion_approval_pipelines),
    ):
        try:
            stuck = await recover()
            if stuck:
                logger.warning(
                    "recovered %s stuck %s approval pipeline(s) at startup",
                    len(stuck),
                    name,
                )
        except Exception:  # noqa: BLE001 - 恢复失败不阻断启动
            logger.exception("%s approval pipeline recovery failed at startup", name)


def create_app() -> FastAPI:
    configure_windows_subprocess_event_loop_policy()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Wait for create_all and every ensure_* migration before serving
        # requests that use SQLAlchemy models.
        await ensure_db_ready()
        health_task = None
        recovery_task = asyncio.create_task(_recover_stuck_approval_pipelines())
        if settings.llm_startup_health_check_enabled:
            health_task = asyncio.create_task(_run_llm_health_check())
        try:
            yield
        finally:
            recovery_task.cancel()
            try:
                await recovery_task
            except asyncio.CancelledError:
                pass
            if health_task is not None:
                health_task.cancel()
                try:
                    await health_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Read-only hotel monitoring backend.",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                key: value
                for key, value in error.items()
                if key not in {"input", "ctx"}
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(ops_router, prefix="/api/v1")
    app.include_router(page_health_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(analytics_router, prefix="/api/v1")
    app.include_router(skills_router, prefix="/api/v1")
    app.include_router(rag_router, prefix="/api/v1")
    app.include_router(doubao_chat_router, prefix="/api/v1")
    app.include_router(qianwen_chat_router, prefix="/api/v1")
    app.include_router(distill_router, prefix="/api/v1")
    app.include_router(bi_daily_router, prefix="/api/v1")
    app.include_router(browser_sessions_router, prefix="/api/v1")
    app.include_router(ctrip_ebooking_router, prefix="/api/v1")
    app.include_router(ctrip_ebooking_watch_router, prefix="/api/v1")
    app.include_router(meituan_ebooking_router, prefix="/api/v1")
    app.include_router(meituan_ebooking_watch_router, prefix="/api/v1")
    app.include_router(fliggy_ebooking_watch_router, prefix="/api/v1")
    app.include_router(fliggy_ebooking_router, prefix="/api/v1")
    app.include_router(ebk_snapshots_router, prefix="/api/v1")
    app.include_router(ebk_global_push_router, prefix="/api/v1")
    app.include_router(exports_router, prefix="/api/v1")
    app.include_router(competitor_watch_router, prefix="/api/v1")
    app.include_router(calendar_price_watch_router, prefix="/api/v1")
    app.include_router(pricing_strategy_router, prefix="/api/v1")
    app.include_router(price_change_alerts_router, prefix="/api/v1")
    app.include_router(pricing_audit_router, prefix="/api/v1")
    app.include_router(reviews_router, prefix="/api/v1")
    app.include_router(refunds_router, prefix="/api/v1")
    app.include_router(reservations_router, prefix="/api/v1")
    app.include_router(shops_router, prefix="/api/v1")
    app.include_router(ebk_period_reads_router, prefix="/api/v1")
    app.include_router(feishu_router, prefix="/api/v1")
    app.include_router(group_archive_router, prefix="/api/v1")
    return app


async def _run_llm_health_check() -> None:
    """Run the best-effort V5 model probe without blocking startup."""

    logger = logging.getLogger("app.main")
    try:
        from app.domain.config.service import get_runtime_config

        await get_runtime_config().health_check_model_configs()
    except Exception:  # noqa: BLE001 - startup must not depend on it
        logger.exception("LLM startup health check failed")


app = create_app()
