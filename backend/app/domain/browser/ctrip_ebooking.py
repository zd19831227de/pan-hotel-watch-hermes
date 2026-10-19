from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.domain.browser.ctrip_ebooking_read_flows import (
    goto_dataset_page_with_recovery,
    read_comment_monitor_flow,
    read_dataset_flow,
    read_monitor_flow,
    read_overview_snapshot_flow,
)
from app.domain.browser.ctrip_ebooking_results import (
    CtripEbookingCommentMonitorResult,
    CtripEbookingDatasetResult,
    CtripEbookingLoginResult,
    CtripEbookingMonitorResult,
    CtripEbookingSnapshotResult,
    CtripPricingPageWritePreviewResult,
    CtripPublicCompetitorPriceResult,
)
from app.domain.browser import ctrip_pricing_write_flow as _pricing_write_flow
from app.domain.browser.ctrip_pricing_page_reader import read_ctrip_pricing_write_page
from app.domain.browser.ctrip_pricing_page_writer import build_ctrip_pricing_page_write_preview
from app.domain.browser.ctrip_pricing_write_flow import (
    execute_pricing_page_write_flow,
    prepare_pricing_page_write_dry_run_flow,
    read_pricing_calendar_current_rates_flow,
)
from app.domain.browser.ctrip_promotion_adjust_flow import (
    execute_promotion_adjust_write_flow,
    prepare_promotion_adjust_page_write_dry_run_flow,
    read_promotion_enrollments_flow,
)
from app.domain.browser.ctrip_pyramid_bidding_flow import (
    execute_pyramid_bid_write_flow,
    prepare_pyramid_bid_page_write_dry_run_flow,
    read_pyramid_promotions_flow,
)
from app.domain.browser.ctrip_public_price_reader import (
    CtripPublicCompetitorPriceReader,
    _public_competitor_cache_key,
    _public_competitor_cdp_browser_key,
)
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _build_login_summary,
    _classify_page_state,
    _clear_ctrip_login_state,
    _credential_source,
    _ebooking_cache_key,
    _is_stale_playwright_page_error,
    _login_next_action,
    _mask_identifier,
    _mask_phone,
    _resolve_credentials,
    _resolve_login_url,
    _safe_visible_text,
    _select_ctrip_page,
    _settle_page,
    _sync_ebk_browser_session_state,
    _try_fill_login_form,
)
from app.domain.browser.sessions import get_browser_session_store


class CtripBrowserProfileLockedError(RuntimeError):
    """Raised when another process owns the persistent browser profile."""


def _is_navigation_aborted_error(exc: BaseException) -> bool:
    """goto 被并发导航打断（ERR_ABORTED）是瞬态竞争，值得重试。"""
    return "ERR_ABORTED" in str(exc)


class CtripEbookingBrowserService:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._cdp_browsers: dict[str, Browser] = {}
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._page_locks: dict[str, asyncio.Lock] = {}
        self._public_price_reader = CtripPublicCompetitorPriceReader(self)

    async def open_login(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        account_phone: str | None,
        account_username: str | None = None,
        account_password: str | None = None,
        auto_submit_login: bool | None = None,
        force_new_login: bool = False,
        headless: bool = False,
        login_context: str = "ebk",
    ) -> CtripEbookingLoginResult:
        # 操作员连点/并行读会在同一缓存页上并发 goto 互踩（被打断的一路
        # ERR_ABORTED 未捕获直达 API 变裸 500，2026-10-08 生产事故）：整段
        # 登录流程按页串行，排队方等前一轮完成后再导航。
        cache_key = _ebooking_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            return await self._open_login_flow(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                account_phone=account_phone,
                account_username=account_username,
                account_password=account_password,
                auto_submit_login=auto_submit_login,
                force_new_login=force_new_login,
                headless=headless,
                login_context=login_context,
            )

    async def _open_login_flow(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        account_phone: str | None,
        account_username: str | None = None,
        account_password: str | None = None,
        auto_submit_login: bool | None = None,
        force_new_login: bool = False,
        headless: bool = False,
        login_context: str = "ebk",
    ) -> CtripEbookingLoginResult:
        # 外层 open_login 已持有 _lock_for(cache_key)（连点串行化）；
        # asyncio.Lock 不可重入，此处必须走不加锁版本，否则登录链路自死锁。
        page = await self._get_or_create_page_unlocked(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        login_url = _resolve_login_url(login_context)
        login_state_clear = None
        if force_new_login:
            login_state_clear = await _clear_ctrip_login_state(page, login_url=login_url)

        # ERR_ABORTED 多为跨进程并发导航的瞬态打断：有界重试一次，其余异常
        # 保持原路径（真实环境问题不吞）。
        for goto_attempt in range(2):
            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
                break
            except PlaywrightError as exc:
                if goto_attempt == 0 and _is_navigation_aborted_error(exc):
                    await asyncio.sleep(0.5)
                    continue
                raise
        await _settle_page(page)

        credentials = _resolve_credentials(
            account_phone=account_phone,
            account_username=account_username,
            account_password=account_password,
            auto_submit_login=auto_submit_login,
        )
        credential_fill = await _try_fill_login_form(page, credentials)
        await _settle_page(page)

        body_text = await _safe_visible_text(page)
        current_url = page.url
        page_state = _classify_page_state(current_url, body_text)
        login_complete = page_state == "authenticated"
        session = get_browser_session_store().get(browser_session_id)
        summary = _build_login_summary(
            credentials=credentials,
            credential_fill=credential_fill,
            page_state=page_state,
        )
        if login_complete:
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id="login_flow",
                status="success",
                page_state=page_state,
                login_context=login_context,
            )
        else:
            with suppress(KeyError):
                get_browser_session_store().mark_login_window_opened(
                    browser_session_id,
                    "login_flow",
                    action="Ctrip EBK login window opened; finish login manually if required.",
                )

        return CtripEbookingLoginResult(
            status="success" if login_complete else "human_required",
            summary=summary,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            login_url=login_url,
            account_phone_masked=_mask_phone(account_phone),
            account_username_masked=_mask_identifier(credentials.username),
            persistent_profile_configured=True,
            manual_login_required=not login_complete,
            evidence={
                "session_port": session.session_port,
                "profile_path_returned": False,
                "password_stored": False,
                "password_returned": False,
                "credential_source": _credential_source(credentials),
                "credential_fill": credential_fill,
                "force_new_login": force_new_login,
                "login_context": login_context,
                "login_state_clear": login_state_clear,
                "automation_bypass": False,
                "current_url": current_url,
                "page_state": page_state,
            },
            next_action=_login_next_action(page_state, login_context=login_context),
        )

    async def read_overview_snapshot(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        hotel_id: str | None,
        actor_id: str,
        push_to_feishu: bool,
    ) -> CtripEbookingSnapshotResult:
        return await read_overview_snapshot_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            hotel_id=hotel_id,
            actor_id=actor_id,
            push_to_feishu=push_to_feishu,
        )

    async def read_dataset(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        dataset_id: str,
        filters: dict[str, Any] | None,
        actor_id: str,
        push_to_feishu: bool,
    ) -> CtripEbookingDatasetResult:
        # P8：同一浏览器的完整页面操作互斥（API 与 worker 共见租约表）。
        # 存储不可用按 fail-closed 处理：不无租约读取。
        from app.domain.browser.operation_leases import (
            LeaseConflict,
            LeaseUnavailable,
            acquire_operation_lease,
            browser_session_lease_key,
            lease_holder_id,
            release_operation_lease,
        )

        def _busy(summary: str) -> CtripEbookingDatasetResult:
            return CtripEbookingDatasetResult(
                status="browser_busy",
                summary=summary,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                dataset_id=dataset_id,
                evidence={"lease": "browser_operation_leases"},
            )

        try:
            handle = await acquire_operation_lease(
                browser_session_lease_key(browser_session_id),
                holder=lease_holder_id(actor_id),
            )
        except LeaseUnavailable:
            return _busy(
                "浏览器互斥存储暂不可用，已按 fail-closed 停止本次读取；请稍后重试。"
            )
        except LeaseConflict as exc:
            return _busy(
                f"浏览器正被其他任务占用（{exc.holder}），本次读取未执行；请稍后重试。"
            )
        try:
            return await read_dataset_flow(
                self,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                dataset_id=dataset_id,
                filters=filters,
                actor_id=actor_id,
                push_to_feishu=push_to_feishu,
            )
        finally:
            try:
                await release_operation_lease(handle)
            except LeaseUnavailable:
                # 释放失败保留租约等待 TTL 过期；本次读取已完成，不回滚结果。
                pass

    async def _goto_dataset_page_with_recovery(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        url: str,
    ) -> Page:
        return await goto_dataset_page_with_recovery(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            url=url,
        )

    async def read_monitor(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        filters: dict[str, Any] | None,
        actor_id: str,
        push_to_feishu: bool,
    ) -> CtripEbookingMonitorResult:
        return await read_monitor_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            filters=filters,
            actor_id=actor_id,
            push_to_feishu=push_to_feishu,
        )

    async def read_comment_monitor(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        filters: dict[str, Any] | None,
        actor_id: str,
        push_to_feishu: bool,
    ) -> CtripEbookingCommentMonitorResult:
        return await read_comment_monitor_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            filters=filters,
            actor_id=actor_id,
            push_to_feishu=push_to_feishu,
        )

    async def read_competitor_prices(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        own_hotel_url: str,
        competitor_urls: list[str],
        check_dates: list[str] | None,
        actor_id: str,
        push_to_feishu: bool,
    ) -> CtripPublicCompetitorPriceResult:
        return await self._public_price_reader.read_competitor_prices(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            own_hotel_url=own_hotel_url,
            competitor_urls=competitor_urls,
            check_dates=check_dates,
            actor_id=actor_id,
            push_to_feishu=push_to_feishu,
        )

    async def read_public_calendar_prices(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        target: dict[str, str],
        start_date: date,
        days: int,
    ) -> dict[str, Any]:
        return await self._public_price_reader.read_public_calendar_prices(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            target=target,
            start_date=start_date,
            days=days,
        )

    async def prepare_pricing_page_write_dry_run(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> CtripPricingPageWritePreviewResult:
        _pricing_write_flow.read_ctrip_pricing_write_page = read_ctrip_pricing_write_page
        _pricing_write_flow.build_ctrip_pricing_page_write_preview = (
            build_ctrip_pricing_page_write_preview
        )
        return await prepare_pricing_page_write_dry_run_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            target_url=target_url,
        )

    async def read_pricing_calendar_current_rates(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> CtripPricingPageWritePreviewResult:
        _pricing_write_flow.read_ctrip_pricing_write_page = read_ctrip_pricing_write_page
        return await read_pricing_calendar_current_rates_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            target_url=target_url,
        )

    async def execute_pricing_page_write(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> CtripPricingPageWritePreviewResult:
        return await execute_pricing_page_write_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            target_url=target_url,
        )

    async def read_pricing_calendar_room_rates(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        checkin_date: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """只读：EBK 房价日历读取指定日期全部房型的现价名单（查房价交互）。"""

        from app.domain.browser.pricing_room_rates import (
            platform_read_error_payload,
            platform_read_payload,
            read_ctrip_calendar_room_rates,
        )
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _resolve_ctrip_pricing_target_url,
            _settle_page,
            _sync_ebk_browser_session_state,
        )
        from app.domain.browser.ctrip_room_type_selector import (
            wait_for_ctrip_pricing_page_state,
        )

        requested_url = _resolve_ctrip_pricing_target_url(target_url)
        if requested_url is None:
            return platform_read_error_payload(
                platform="ctrip",
                status="page_structure_changed",
                summary="携程房价查询只接受携程 EBK 日历链接。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason="invalid_target_url",
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # 2026-09-14：日历主内容异步渲染（叠加消息弹层/节假日提醒横幅），
        # 固定 600ms 后正文常无房价信号，曾被误报「页面结构变化」；
        # 改为有界等待信号收敛，超时才判结构变化。
        page_state = await wait_for_ctrip_pricing_page_state(page)
        if page_state in ("login_required", "verification_required"):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state=page_state,
            )
            return platform_read_error_payload(
                platform="ctrip",
                status="human_required",
                summary="携程 EBK 需要先人工完成登录/验证，再查询房价。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason=page_state,
            )
        if page_state != "pricing_page":
            return platform_read_error_payload(
                platform="ctrip",
                status="page_structure_changed",
                summary="携程 EBK 房价日历页结构无法识别。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason="not_pricing_page",
            )

        read = await read_ctrip_calendar_room_rates(page, checkin_date=checkin_date)
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=str(read.get("status") or "failed"),
            page_state=page_state,
        )
        return platform_read_payload(
            platform="ctrip",
            status=str(read.get("status") or "failed"),
            summary=str(read.get("summary") or ""),
            room_rates=list(read.get("room_rates") or []),
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def read_pyramid_promotions(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        return await read_pyramid_promotions_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
        )

    async def prepare_pyramid_bid_page_write_dry_run(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
    ) -> dict[str, Any]:
        return await prepare_pyramid_bid_page_write_dry_run_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
        )

    async def execute_pyramid_bid_write(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
    ) -> dict[str, Any]:
        return await execute_pyramid_bid_write_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
        )

    async def read_promotion_enrollments(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        return await read_promotion_enrollments_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
        )

    async def prepare_promotion_adjust_page_write_dry_run(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
    ) -> dict[str, Any]:
        return await prepare_promotion_adjust_page_write_dry_run_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
        )

    async def execute_promotion_adjust_write(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
    ) -> dict[str, Any]:
        return await execute_promotion_adjust_write_flow(
            self,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
        )

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        cache_key = _ebooking_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            return await self._get_or_create_page_unlocked(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                headless=headless,
            )

    async def _get_or_create_page_unlocked(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        cache_key = _ebooking_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        existing = self._pages.get(cache_key)
        if existing:
            try:
                if not existing.is_closed():
                    return existing
            except Exception as exc:
                if _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                else:
                    raise

        session = get_browser_session_store().get(browser_session_id)
        profile_dir = self._profile_dir(platform_account_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            playwright = await self._get_playwright()
            launch_lock: _BrowserProfileLaunchLock | None = None
            try:
                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                )
                if cdp_page:
                    return cdp_page

                launch_lock = _BrowserProfileLaunchLock(profile_dir)
                if not launch_lock.acquire():
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page:
                        return cdp_page
                    raise CtripBrowserProfileLockedError(
                        "浏览器账号正在被另一个后台任务或登录窗口占用，请稍后重试，或关闭占用该账号的 Chrome 后再打开。"
                    )
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=headless,
                    args=[f"--remote-debugging-port={session.session_port}"],
                    viewport={"width": 1440, "height": 1000},
                )
                launch_lock.release()
                page = context.pages[0] if context.pages else await context.new_page()
                self._contexts[cache_key] = context
                self._pages[cache_key] = page
                return page
            except Exception as exc:
                if launch_lock is not None:
                    launch_lock.release()
                if _is_profile_singleton_error(exc):
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page:
                        return cdp_page
                    raise CtripBrowserProfileLockedError(
                        "浏览器账号 profile 已被其他 Chrome 进程占用，无法同时启动第二个持久化浏览器。请稍后重试或关闭占用窗口。"
                    ) from exc
                if attempt == 0 and _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                    continue
                raise
        raise RuntimeError("Ctrip EBK browser page could not be created.")

    def _lock_for(self, cache_key: str) -> asyncio.Lock:
        lock = self._page_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._page_locks[cache_key] = lock
        return lock

    async def _discard_cached_browser_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> None:
        cache_key = _ebooking_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            public_cache_key = _public_competitor_cache_key(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )
            public_browser_key = _public_competitor_cdp_browser_key(browser_session_id)
            page = self._pages.pop(cache_key, None)
            context = self._contexts.pop(cache_key, None)
            browser = self._cdp_browsers.pop(browser_session_id, None)
            public_page = self._pages.pop(public_cache_key, None)
            public_context = self._contexts.pop(public_cache_key, None)
            public_browser = self._cdp_browsers.pop(public_browser_key, None)

        close_candidates: list[Any] = []
        if browser is None:
            close_candidates.extend([page, context])
        if public_browser is None:
            close_candidates.extend([public_page, public_context])
        else:
            close_candidates.append(public_browser)

        for cached in close_candidates:
            if cached is None:
                continue
            with suppress(Exception):
                await cached.close()

    async def _reset_playwright_driver(self) -> None:
        close_candidates: list[Any] = [*self._pages.values(), *self._contexts.values(), *self._cdp_browsers.values()]
        self._pages.clear()
        self._contexts.clear()
        self._cdp_browsers.clear()
        for cached in close_candidates:
            with suppress(Exception):
                await cached.close()
        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
        self._playwright = None

    async def close(self) -> None:
        await self._reset_playwright_driver()

    async def _connect_existing_debug_page(
        self,
        *,
        playwright: Playwright,
        platform_account_id: str,
        browser_session_id: str,
        port: int,
        timeout_ms: int = 2_000,
    ) -> Page | None:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                timeout=timeout_ms,
            )
        except PlaywrightError as exc:
            if _is_stale_playwright_page_error(exc):
                raise
            return None
        contexts = browser.contexts
        if not contexts:
            await browser.close()
            return None
        context = contexts[0]
        page = _select_ctrip_page(context.pages)
        if page is None:
            page = await context.new_page()
        cache_key = _ebooking_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        self._cdp_browsers[browser_session_id] = browser
        self._contexts[cache_key] = context
        self._pages[cache_key] = page
        return page

    async def _get_playwright(self) -> Playwright:
        if self._playwright is None:
            configure_windows_subprocess_event_loop_policy()
            self._playwright = await async_playwright().start()
        return self._playwright

    def _profile_dir(self, platform_account_id: str) -> Path:
        settings = get_settings()
        account_profile_dir = settings.ctrip_ebooking_profile_dir.strip()
        if account_profile_dir:
            return Path(account_profile_dir).expanduser().resolve()
        return (Path(settings.browser_state_dir) / "ctrip-ebooking" / platform_account_id).resolve()

    async def _capture_screenshot(self, page: Page, platform_account_id: str) -> str:
        settings = get_settings()
        artifact_dir = Path(settings.playwright_artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        screenshot_path = artifact_dir / f"ctrip-ebk-{platform_account_id}-{timestamp}.png"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
        except PlaywrightError:
            return "screenshot_failed"
        return str(screenshot_path)


class _BrowserProfileLaunchLock:
    _STALE_AFTER_SECONDS = 120

    def __init__(self, profile_dir: Path) -> None:
        self.path = profile_dir / ".pan_hotel_watch_launch.lock"
        self._handle: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self._handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._handle, str(os.getpid()).encode("ascii", errors="ignore"))
                return True
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    with suppress(FileNotFoundError):
                        self.path.unlink()
                    continue
                return False
        return False

    def release(self) -> None:
        if self._handle is not None:
            with suppress(OSError):
                os.close(self._handle)
            self._handle = None
        with suppress(FileNotFoundError):
            self.path.unlink()

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self._STALE_AFTER_SECONDS
        except OSError:
            return False


def _is_profile_singleton_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "processsingleton" in message
        or "lock file can not be created" in message
        or "profile directory" in message
        or "profile 已被" in message
    )


def get_ctrip_ebooking_service() -> CtripEbookingBrowserService:
    return _CTRIP_EBOOKING_SERVICE


_CTRIP_EBOOKING_SERVICE = CtripEbookingBrowserService()
