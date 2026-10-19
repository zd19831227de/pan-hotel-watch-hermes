"""携程公共酒店竞品价格读取模块，从 ``ctrip_ebooking.py`` 拆分而来。

本模块只负责携程公共酒店页面的只读链路，主要输出
``CtripPublicCompetitorPriceResult``，其中包含目标信息、逐酒店快照、
按日期区分的房价、竞品比较结果和审计证据。

关键变量边界：

* ``platform_account_id`` 表示 OTA 账号及其浏览器配置。
* ``browser_session_id`` 表示持久化浏览器会话和登录状态，不是酒店编号。
* ``target`` 表示一个酒店和日期对应的 URL；``targets`` 是有序目标列表，
  顺序为本店在前、竞品在后。
* ``page`` 是一次读取中由全部目标共用的 Playwright ``Page``。本模块不管理
  浏览器生命周期；页面、上下文、CDP 连接、锁和截图由父服务管理。
* ``parsed`` 和 ``tomorrow_parsed`` 是临时页面快照，完成状态分类后才会写入
  ``snapshots``。

本模块不包含 EBK 写入、调价执行、评论或飞书业务逻辑，只读取公共页面中
可见且可核验的证据，再把结构化结果交给上层持久化或展示。
"""

from __future__ import annotations

import asyncio
import random
import re as _re
from contextlib import suppress
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Playwright

    from app.domain.browser.ctrip_ebooking import (
        CtripEbookingBrowserService,
        CtripPublicCompetitorPriceResult,
    )

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.domain.browser.ctrip_public_competitor import (
    _build_competitor_price_targets,
    _build_public_competitor_price_comparison,
    _build_public_tomorrow_snapshot_from_detail,
    _classify_public_hotel_page_state,
    _extract_date_context_from_snapshots,
    _extract_public_hotel_price_snapshot,
    _invalid_public_ctrip_hotel_urls,
    _missing_public_tomorrow_snapshot,
    _public_competitor_price_next_action,
    _public_competitor_price_status,
    _public_competitor_price_summary,
    _public_page_state_with_room_rates,
    _shift_public_hotel_url_dates,
)
from app.domain.browser.ctrip_public_hotel_dates import (
    _extract_public_calendar_price_points,
)
from app.domain.browser.ctrip_public_page_reader import (
    _ensure_public_room_rates_loaded,
    _open_public_calendar_popup,
    _safe_public_calendar_cells,
    _safe_public_date_evidence_text,
    _safe_public_room_rates,
)
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _is_stale_playwright_page_error,
)
from app.domain.browser.sessions import get_browser_session_store
from app.domain.competitor_prices.browser_pool import public_competitor_cdp_decision


def _public_competitor_read_delay_ms() -> int:
    """返回公共页面导航之间使用的小范围随机等待时间。"""
    return random.randint(2_000, 3_000)


async def _wait_public_competitor_retry_delay(page: Page | None) -> None:
    """重试前等待，并在可行时继续使用当前 ``page``。

    导航失败后 Playwright 页面可能已关闭或失效，因此 ``page`` 允许为空。
    页面仍可用时调用 Playwright 的非阻塞 ``wait_for_timeout``；否则退回
    ``asyncio.sleep``，避免继续访问失效页面。
    """
    delay_ms = _public_competitor_read_delay_ms()
    if page is not None and _is_public_price_page_usable(page):
        await page.wait_for_timeout(delay_ms)
        return
    await asyncio.sleep(delay_ms / 1000)


def _is_public_navigation_retriable_error(exc: Exception) -> bool:
    """判断导航异常 ``exc`` 是否允许安全重试一次。

    这里只识别已知的临时 Playwright 或网络错误标记。解析错误和配置错误不会
    触发额外导航，而是继续向上传递并生成当前目标的失败快照。
    """
    if _is_stale_playwright_page_error(exc):
        return True
    message = str(exc).lower()
    retriable_markers = (
        "page.goto",
        "timeout",
        "navigation timeout",
        "net::err_timed_out",
        "net::err_connection_reset",
        "net::err_connection_closed",
        "net::err_connection_aborted",
    )
    return isinstance(exc, (PlaywrightError, TimeoutError, RuntimeError)) and any(
        marker in message for marker in retriable_markers
    )


def _is_public_login_redirect_snapshot(snapshot: dict[str, Any]) -> bool:
    """识别公共酒店 URL 是否被重定向到携程登录页。

    ``snapshot['page_state']`` 是解析器给出的语义状态，
    ``snapshot['current_url']`` 是导航结束后的实际地址。必须同时满足两个
    条件，避免其他 ``human_required`` 结果误触发第二次导航。
    """
    if str(snapshot.get("page_state") or "") != "login_required":
        return False
    current_url = str(snapshot.get("current_url") or "")
    parsed = urlparse(current_url)
    return parsed.netloc.lower().endswith("passport.ctrip.com")


def _public_price_read_error_payload(
    *,
    target: dict[str, str],
    date_label: str,
    exc: Exception,
) -> dict[str, str]:
    """把异常转换为限定长度、限定目标范围的审计信息。

    ``target`` 提供角色、名称和请求 URL；``date_label`` 区分今天和明天；
    ``exc`` 提供异常类型和消息。消息会先规范空白再截断，防止原始浏览器输出
    占满日志或 API 响应。
    """
    message = _re.sub(r"\s+", " ", str(exc)).strip()
    return {
        "role": str(target.get("role") or ""),
        "label": str(target.get("label") or ""),
        "date_label": date_label,
        "requested_url": str(target.get("url") or ""),
        "error_type": type(exc).__name__,
        "error_message": (message or type(exc).__name__)[:240],
    }


def _public_price_read_failure_snapshot(
    *,
    target: dict[str, str],
    review_reason: str,
    error: dict[str, str],
) -> dict[str, Any]:
    """为读取失败生成可持久化快照，但绝不填入虚假价格。

    返回快照保留目标身份和异常证据，同时明确清空 ``price`` 和
    ``room_rates``。这样既能入库供后续复核，也不会把失败页面当成业务数据。
    """
    requested_url = str(target.get("url") or "")
    snapshot = _extract_public_hotel_price_snapshot(
        role=str(target.get("role") or ""),
        label=str(target.get("label") or ""),
        requested_url=requested_url,
        current_url=requested_url,
        page_title="",
        page_state="unknown",
        body_text="",
        screenshot_path="",
        structured_room_rates=[],
    )
    snapshot.update(
        {
            "status": "needs_review",
            "price": None,
            "price_candidate": None,
            "room_rates": [],
            "page_state": "read_failed",
            "parser_confidence": "low",
            "review_reason": review_reason,
            "raw_snippets": [],
            "clue_lines": {},
            "read_error": error,
            "captured_at": datetime.now(UTC).isoformat(),
        }
    )
    return snapshot


def _calendar_page_needs_review_result(
    *,
    target: dict[str, str],
    review_reason: str,
    summary: str,
    current_url: str = "",
    page_state: str = "unknown",
) -> dict[str, Any]:
    return {
        "status": "needs_review",
        "summary": summary,
        "points": [],
        "page_state": page_state,
        "review_reason": review_reason,
        "hotel_name": str(target.get("label") or ""),
        "current_url": current_url,
        "requested_url": str(target.get("url") or ""),
    }


class _PublicCompetitorCdpUnavailableError(RuntimeError):
    pass


def _public_competitor_cache_key(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> str:
    return f"{platform_account_id}:{browser_session_id}:ctrip-public-competitor"


def _public_competitor_cdp_browser_key(browser_session_id: str) -> str:
    return f"{browser_session_id}:ctrip-public-competitor-cdp"


def _normalize_cdp_url(value: str) -> str:
    """把配置的 CDP 端口或主机规范为 HTTP endpoint 字符串。"""
    normalized = str(value or "").strip()
    if not normalized:
        return "http://127.0.0.1:9222"
    if normalized.isdigit():
        return f"http://127.0.0.1:{normalized}"
    if "://" not in normalized:
        return f"http://{normalized}"
    return normalized


def _public_competitor_session_port(browser_session_id: str) -> int | None:
    """Return the configured CDP debug port for this browser session.

    Returns None when the session is not found or when the port cannot be
    resolved.  Callers must gracefully fall back when this returns None.
    """
    from contextlib import suppress

    from app.domain.browser.sessions import get_browser_session_store

    with suppress(KeyError, ValueError):
        session = get_browser_session_store().get(browser_session_id)
        port = int(session.session_port)
        if 1024 <= port <= 65535:
            return port
    return None


def _public_competitor_cdp_unavailable_result(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    targets: list[dict[str, str]],
    check_dates: list[str] | None,
    snapshots: list[dict[str, Any]],
    screenshot_paths: list[str],
    page_states: dict[str, str],
    error: str,
) -> CtripPublicCompetitorPriceResult:
    """配置的 CDP endpoint 不可用时返回需要人工处理的结果。

    ``targets``、``snapshots``、``page_states`` 和 ``screenshot_paths`` 会
    作为审计上下文保留。结果始终只读，并且只暴露安全的 CDP 决策信息；
    密码、Cookie 和浏览器 profile 路径不会进入响应。
    """
    cdp_decision_result = public_competitor_cdp_decision(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
    )
    cdp_url = _normalize_cdp_url(cdp_decision_result.cdp_url)
    # Late import to avoid circular dependency
    from app.domain.browser.ctrip_ebooking import CtripPublicCompetitorPriceResult
    return CtripPublicCompetitorPriceResult(
        status="human_required",
        summary="Ctrip public competitor CDP endpoint is not healthy.",
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        data={
            "query_type": "ctrip_public_competitor_price_snapshot",
            "actor_id": actor_id,
            "targets": targets,
            "check_dates": check_dates or [],
            "snapshots": snapshots,
            "comparison": {},
            "captured_at": datetime.now(UTC).isoformat(),
        },
        evidence={
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "target_count": len(targets),
            "screenshot_paths": screenshot_paths,
            "password_stored": False,
            "password_returned": False,
            "automation_bypass": False,
            "read_only": True,
            "no_booking_attempted": True,
            "parser_version": "ctrip_public_visible_price_v1",
            "page_states": page_states,
            "cdp_enabled": cdp_decision_result.enabled,
            "cdp_url": cdp_url,
            "cdp_fallback_enabled": cdp_decision_result.fallback_enabled,
            "browser_mode": cdp_decision_result.browser_mode,
            "browser_mode_reason": cdp_decision_result.reason,
            "browser_pool_enabled": cdp_decision_result.pool_enabled,
            "browser_pool_size": cdp_decision_result.pool_size,
            "human_required_reason": "ctrip_public_competitor_cdp_unavailable",
            "error": error,
        },
        next_action=(
            "Restart the dedicated Chrome CDP browser and verify "
            f"{cdp_url.rstrip('/')}/json/version before retrying."
        ),
    )


def _public_competitor_browser_mode_evidence(
    *,
    platform_account_id: str,
    browser_session_id: str,
) -> dict[str, object]:
    return public_competitor_cdp_decision(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
    ).evidence()


def _competitor_price_check_in_date(check_dates: list[str] | None) -> date:
    """选择第一个有效入住日期；没有有效值时使用上海时区当天日期。"""
    from datetime import datetime as _dt  # noqa: F811
    for value in check_dates or []:
        try:
            return _dt.fromisoformat(str(value)).date()
        except ValueError:
            continue
    return _dt.now(ZoneInfo("Asia/Shanghai")).date()



class CtripPublicCompetitorPriceReader:
    """通过共享浏览器状态读取携程公共酒店竞品价格。

    ``_service`` 引用父级 ``CtripEbookingBrowserService``。代理属性直接使用
    父服务的 ``_pages``、``_contexts`` 和 ``_cdp_browsers`` 缓存，
    ``_lock_for`` 则按 profile/cache key 串行化访问。

    页面恢复过程可能创建或复用另一个 ``Page``，因此调用方必须把方法返回的
    页面重新赋值给本地 ``page``。本类不持有浏览器生命周期、登录凭据或调价
    写入能力，这些资源和策略均由父服务管理。
    """

    def __init__(self, service: CtripEbookingBrowserService) -> None:
        self._service = service

    # ---- Delegated helpers on the parent service ----

    @property
    def _pages(self) -> dict[str, Page]:
        return self._service._pages

    @property
    def _contexts(self) -> dict[str, BrowserContext]:
        return self._service._contexts

    @property
    def _cdp_browsers(self) -> dict[str, Browser]:
        return self._service._cdp_browsers

    def _lock_for(self, platform_account_id: str) -> asyncio.Lock:
        return self._service._lock_for(platform_account_id)

    async def _capture_screenshot(self, page: Page, platform_account_id: str) -> str:
        return await self._service._capture_screenshot(page, platform_account_id)

    async def _get_playwright(self) -> Playwright:
        return await self._service._get_playwright()

    async def _reset_playwright_driver(self) -> None:
        return await self._service._reset_playwright_driver()

    async def _discard_cached_browser_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> None:
        return await self._service._discard_cached_browser_state(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        return await self._service._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )

    # ---- CDP / page management ----

    async def _connect_public_competitor_cdp_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        cdp_url: str,
    ) -> Page | None:
        cache_key = _public_competitor_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            return await self._connect_public_competitor_cdp_page_unlocked(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                cdp_url=cdp_url,
            )

    async def _connect_public_competitor_cdp_page_unlocked(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        cdp_url: str,
    ) -> Page | None:
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _is_stale_playwright_page_error,
            _select_ctrip_page,
        )
        # ``cache_key`` 将公共竞品页面与 EBK 页面隔离；即使两个流程使用同一
        # 账号和 browser session，也不会共用同一个页面缓存键。
        cache_key = _public_competitor_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        existing = self._pages.get(cache_key)
        if existing and _is_public_price_page_usable(existing):
            if await _is_public_price_page_alive(existing):
                await _bring_public_price_page_to_front(existing)
                return existing
            # 缓存页通道探测失败：清掉失效缓存条目，走后续重连/重建路径。
            # 只做同步 pop，避免在 ``_lock_for`` 临界区内嵌套 service 的
            # discard（它内部另拿 EBK key 锁）。
            self._pages.pop(cache_key, None)

        # ``browser`` 会写入 ``_cdp_browsers``，让后续调用复用现有 Chrome/CDP
        # 连接，避免再次打开浏览器窗口。
        browser = None
        for attempt in range(2):
            playwright = await self._get_playwright()
            try:
                browser = await playwright.chromium.connect_over_cdp(
                    _normalize_cdp_url(cdp_url),
                    timeout=4_000,
                )
                break
            except Exception as exc:
                if not _is_stale_playwright_page_error(exc):
                    if isinstance(exc, PlaywrightError):
                        return None
                    raise
                await self._reset_playwright_driver()
                if attempt > 0:
                    raise
        if browser is None:
            return None

        # CDP browser 可能暴露多个 context；公共读取固定选择第一个 context，
        # 再从中选择并复用一个携程页面。
        contexts = browser.contexts
        if not contexts:
            await browser.close()
            return None
        context = contexts[0]
        page = _select_ctrip_page(context.pages)
        if page is None:
            page = await context.new_page()
        # 关闭残留的多余页面并把工作页拉回前台：多余页面会把工作页挤到
        # 后台，浏览器对后台页面的渲染节流会让读取等待永久超时。
        await _close_extra_public_competitor_pages(context, keep=page)
        await _bring_public_price_page_to_front(page)
        self._cdp_browsers[_public_competitor_cdp_browser_key(browser_session_id)] = browser
        self._contexts[cache_key] = context
        self._pages[cache_key] = page
        return page

    async def _get_or_create_public_persistent_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Page:
        """Launch or reuse a persistent Chrome profile for public competitor reads.

        Uses the public-competitor cache key and profile launch lock so EBK
        data-center pages stay isolated while still avoiding duplicate profile
        launches across processes.
        """
        from app.domain.browser.ctrip_ebooking import (
            CtripBrowserProfileLockedError,
            _BrowserProfileLaunchLock,
            _is_profile_singleton_error,
        )

        cache_key = _public_competitor_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            existing = self._pages.get(cache_key)
            if existing and _is_public_price_page_usable(existing):
                if await _is_public_price_page_alive(existing):
                    await _bring_public_price_page_to_front(existing)
                    return existing
                # 同步 pop，避免临界区内嵌套 service 的 discard 锁。
                self._pages.pop(cache_key, None)

            session = get_browser_session_store().get(browser_session_id)
            # ``profile_dir`` 归属账号；``session`` 保存配置的远程调试端口。
            # 启动锁用于防止两个进程同时打开同一个持久化 profile。
            profile_dir = self._service._profile_dir(platform_account_id)
            profile_dir.mkdir(parents=True, exist_ok=True)
            for attempt in range(2):
                playwright = await self._get_playwright()
                launch_lock: _BrowserProfileLaunchLock | None = None
                try:
                    page = await self._connect_public_competitor_cdp_page_unlocked(
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        cdp_url=f"http://127.0.0.1:{session.session_port}",
                    )
                    if page is not None and _is_public_price_page_usable(page):
                        return page

                    launch_lock = _BrowserProfileLaunchLock(profile_dir)
                    if not launch_lock.acquire():
                        page = await self._connect_public_competitor_cdp_page_unlocked(
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                            cdp_url=f"http://127.0.0.1:{session.session_port}",
                        )
                        if page is not None and _is_public_price_page_usable(page):
                            return page
                        raise CtripBrowserProfileLockedError(
                            "浏览器账号正在被另一个后台任务或登录窗口占用，请稍后重试，或关闭占用该账号的 Chrome 后再打开。"
                        )
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=False,
                        args=[f"--remote-debugging-port={session.session_port}"],
                        viewport={"width": 1440, "height": 1000},
                    )
                    launch_lock.release()
                    page = context.pages[0] if context.pages else await context.new_page()
                    await _bring_public_price_page_to_front(page)
                    self._contexts[cache_key] = context
                    self._pages[cache_key] = page
                    return page
                except Exception as exc:
                    if launch_lock is not None:
                        launch_lock.release()
                    if _is_profile_singleton_error(exc):
                        page = await self._connect_public_competitor_cdp_page_unlocked(
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                            cdp_url=f"http://127.0.0.1:{session.session_port}",
                        )
                        if page is not None and _is_public_price_page_usable(page):
                            return page
                        raise CtripBrowserProfileLockedError(
                            "浏览器账号 profile 已被其他 Chrome 进程占用，无法同时启动第二个持久化浏览器。请稍后重试或关闭占用窗口。"
                        ) from exc
                    if attempt == 0 and _is_stale_playwright_page_error(exc):
                        await self._reset_playwright_driver()
                        continue
                    raise
            raise RuntimeError("Ctrip public competitor browser page could not be created.")

    async def _get_or_create_public_price_page_with_recovery(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Page:
        """按照既定优先级解析一个可用的公共页面。

        查找顺序为：缓存页面、session 专用 CDP、全局 CDP 配置、带锁的
        persistent-context fallback。每个分支都向调用方返回同一个逻辑
        ``Page`` 资源；恢复过程只清理失效的公共浏览器缓存再重新创建。
        """

        # ``cache_key`` 是 ``_pages`` 和 ``_contexts`` 的稳定查找键，并且有意
        # 与 EBK cache key 区分。
        cache_key = _public_competitor_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        existing = self._pages.get(cache_key)
        if existing and _is_public_price_page_usable(existing):
            if await _is_public_price_page_alive(existing):
                await _bring_public_price_page_to_front(existing)
                return existing
            # 缓存页通道探测失败：清掉失效缓存条目，走后续重连/重建路径。
            # 只做同步 pop，避免在 ``_lock_for`` 临界区内嵌套 service 的
            # discard（它内部另拿 EBK key 锁）。
            self._pages.pop(cache_key, None)

        # 优先级 1：连接 session port 对应的 CDP，复用已经通过
        # --remote-debugging-port 启动的 Chrome。手工登录窗口或 Worker 已在
        # 运行时，不再重复启动 persistent context。
        session_port = _public_competitor_session_port(browser_session_id)
        if session_port is not None:
            page = await self._connect_public_competitor_cdp_page(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                cdp_url=f"http://127.0.0.1:{session_port}",
            )
            if page is not None and _is_public_price_page_usable(page):
                return page

        # 优先级 2：使用全局 CDP URL 作为配置级 fallback。
        cdp_decision_result = public_competitor_cdp_decision(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        if cdp_decision_result.enabled and cdp_decision_result.browser_mode == "global_cdp":
            page = await self._connect_public_competitor_cdp_page(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                cdp_url=cdp_decision_result.cdp_url,
            )
            if page is not None and _is_public_price_page_usable(page):
                return page

            if not cdp_decision_result.fallback_enabled:
                cdp_url = _normalize_cdp_url(cdp_decision_result.cdp_url)
                raise _PublicCompetitorCdpUnavailableError(
                    "Ctrip public competitor CDP mode is enabled, but Chrome DevTools "
                    f"CDP is not healthy at {cdp_url}. Verify {cdp_url.rstrip('/')}"
                    "/json/version returns webSocketDebuggerUrl, then retry."
                )

        # 优先级 3：通过 launch_persistent_context 创建公共读取专用页面。
        # 该分支受 profile 启动锁保护，手工登录窗口和 Worker 不会为同一
        # profile 创建两个互相竞争的 Chrome 实例。
        page = await self._get_or_create_public_persistent_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        if _is_public_price_page_usable(page):
            return page
        await self._discard_cached_browser_state(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        return await self._get_or_create_public_persistent_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def _read_public_price_target(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
        target: dict[str, str],
        require_visible_date_match: bool = False,
    ) -> tuple[Page, dict[str, Any], str]:
        """把 ``page`` 导航到一个目标并返回解析快照。

        ``target`` 包含 ``role``、``label`` 和请求 ``url``。方法先对临时
        ``goto`` 失败重试一次；如果被重定向到登录页，则在随机等待后额外导航
        一次。返回值依次为可能经过恢复的 ``Page``、语义快照 ``parsed`` 和
        ``screenshot_path``。是否因 ``human_required`` 停止后续目标，由批次
        调用方决定。
        """
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _settle_page,
        )

        # ``attempt`` 只覆盖传输或页面异常。登录重定向在首次导航成功后单独
        # 处理，正常酒店页面不会被无意义地导航两次。
        for attempt in range(2):
            try:
                await page.goto(target["url"], wait_until="commit", timeout=45_000)
                break
            except Exception as exc:
                if attempt > 0 or not _is_public_navigation_retriable_error(exc):
                    raise
                await self._discard_cached_browser_state(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                )
                page = await self._get_or_create_public_price_page_with_recovery(
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                )
                await _wait_public_competitor_retry_delay(page)
        else:
            # The loop always either breaks after a successful navigation or raises.
            # This keeps type-checkers and future edits honest.
            raise RuntimeError("Ctrip public hotel page navigation did not complete.")
        parsed, screenshot_path = await self._parse_public_price_target_page(
            page=page,
            platform_account_id=platform_account_id,
            target=target,
            require_visible_date_match=require_visible_date_match,
        )
        if _is_public_login_redirect_snapshot(parsed):
            # 携程可能短暂把公共详情 URL 重定向到 passport。这里固定只重试
            # 一次；第二次仍为 ``login_required`` 时，由批次调用方安全停止。
            await _wait_public_competitor_retry_delay(page)
            await page.goto(target["url"], wait_until="commit", timeout=45_000)
            await _settle_page(page)
            parsed, screenshot_path = await self._parse_public_price_target_page(
                page=page,
                platform_account_id=platform_account_id,
                target=target,
                require_visible_date_match=require_visible_date_match,
            )
        return page, parsed, screenshot_path

    async def _parse_public_price_target_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        target: dict[str, str],
        require_visible_date_match: bool,
    ) -> tuple[dict[str, Any], str]:
        """先分类当前页面，再执行耗时的房价解析。

        ``current_url`` 和 ``body_text`` 首先确定 ``page_state``。登录、验证、
        风控页面的 ``structured_room_rates`` 保持空列表，也不会等待房型展开
        selector。酒店页面可用时，``loaded_room_rates`` 和
        ``structured_room_rates`` 会把可信的可见 DOM 房价传入
        ``_extract_public_hotel_price_snapshot``。
        """
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _safe_title,
            _safe_visible_text,
            _settle_page,
        )

        # 只等待当前共享页面稳定，不创建第二个 tab，因此不会与目标循环抢页面。
        await _settle_page(page)
        title = await _safe_title(page)
        body_text = await _safe_visible_text(page)
        current_url = page.url
        page_state = _classify_public_hotel_page_state(current_url, body_text)
        structured_room_rates: list[dict[str, Any]] = []
        if page_state not in {
            "login_required",
            "risk_control",
            "verification_required",
        }:
            # DOM 展开是耗时步骤。需要人工处理的页面没有可信房价，因此直接
            # 跳过该步骤。
            loaded_room_rates = await _ensure_public_room_rates_loaded(page)
            date_evidence_text = await _safe_public_date_evidence_text(page)
            if date_evidence_text:
                body_text = f"{body_text}\n{date_evidence_text}"
            structured_room_rates = loaded_room_rates or await _safe_public_room_rates(page)
            if page_state == "unknown":
                page_state = _classify_public_hotel_page_state(current_url, body_text)
        # 截图只是当前快照的审计证据，不参与解析，也不是额外的用户浏览器 tab。
        screenshot_path = await self._capture_screenshot(page, platform_account_id)
        page_state = _public_page_state_with_room_rates(
            page_state=page_state,
            room_rates=structured_room_rates,
        )
        parsed = _extract_public_hotel_price_snapshot(
            role=target["role"],
            label=target["label"],
            requested_url=target["url"],
            current_url=current_url,
            page_title=title,
            page_state=page_state,
            body_text=body_text,
            screenshot_path=screenshot_path,
            structured_room_rates=structured_room_rates,
            require_visible_date_match=require_visible_date_match,
        )
        return parsed, screenshot_path

    async def _parse_public_calendar_target_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        target: dict[str, str],
    ) -> tuple[dict[str, Any], str]:
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _safe_title,
            _safe_visible_text,
            _settle_page,
        )

        async def parse_current_page() -> tuple[dict[str, Any], str]:
            await _settle_page(page)
            title = await _safe_title(page)
            body_text = await _safe_visible_text(page)
            date_evidence_text = await _safe_public_date_evidence_text(page)
            if date_evidence_text:
                body_text = f"{body_text}\n{date_evidence_text}"
            current_url = page.url
            screenshot_path = await self._capture_screenshot(page, platform_account_id)
            page_state = _classify_public_hotel_page_state(current_url, body_text)
            parsed = _extract_public_hotel_price_snapshot(
                role=target["role"],
                label=target["label"],
                requested_url=target["url"],
                current_url=current_url,
                page_title=title,
                page_state=page_state,
                body_text=body_text,
                screenshot_path=screenshot_path,
                structured_room_rates=[],
            )
            return parsed, screenshot_path

        for attempt in range(2):
            try:
                await page.goto(target["url"], wait_until="commit", timeout=45_000)
                break
            except Exception as exc:
                if _is_stale_playwright_page_error(exc):
                    raise
                if attempt > 0 or not _is_public_navigation_retriable_error(exc):
                    raise
                await _wait_public_competitor_retry_delay(page)
        else:
            raise RuntimeError("Ctrip public calendar page navigation did not complete.")

        parsed, screenshot_path = await parse_current_page()
        if _is_public_login_redirect_snapshot(parsed):
            await _wait_public_competitor_retry_delay(page)
            await page.goto(target["url"], wait_until="commit", timeout=45_000)
            parsed, screenshot_path = await parse_current_page()
        return parsed, screenshot_path

    async def _recover_public_price_page_if_closed(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Page:
        try:
            if _is_public_price_page_usable(page):
                return page
        except Exception:
            pass
        return await self._get_or_create_public_price_page_with_recovery(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def _new_public_price_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Page:
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _is_stale_playwright_page_error,
        )

        try:
            return await page.context.new_page()
        except Exception as exc:
            if not _is_stale_playwright_page_error(exc):
                raise
            await self._discard_cached_browser_state(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )
            return await self._get_or_create_public_price_page_with_recovery(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )

    async def _new_public_calendar_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Page | None:
        try:
            return await page.context.new_page()
        except Exception as exc:
            if not _is_stale_playwright_page_error(exc):
                raise
            await self._discard_cached_browser_state(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )
            return None

    # ---- Main entry point ----

    async def read_public_calendar_prices(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        target: dict[str, str],
        start_date: date,
        days: int,
    ) -> dict[str, Any]:
        """从共享公共页面读取一个酒店的日历价格。

        ``target`` 标识酒店，``start_date`` 和 ``days`` 限定日历范围，
        ``calendar_page`` 是复用页面，``last_result`` 在两次受限尝试之间保留
        待复核结果。该链路独立于竞品批次，但共用登录状态、页面状态和截图规则。
        """
        shared_page = await self._get_or_create_public_price_page_with_recovery(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        calendar_page = shared_page
        last_result: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                parsed, screenshot_path = await self._parse_public_calendar_target_page(
                    page=calendar_page,
                    platform_account_id=platform_account_id,
                    target=target,
                )
            except Exception as exc:
                if not _is_stale_playwright_page_error(exc):
                    raise
                return _calendar_page_needs_review_result(
                    target=target,
                    review_reason="calendar_page_stale",
                    summary="日历读取页已关闭，需稍后重试。",
                    current_url=str(getattr(calendar_page, "url", "") or ""),
                )
            if str(parsed.get("page_state") or "") in {
                "login_required",
                "risk_control",
                "verification_required",
            }:
                return {
                    "status": "human_required",
                    "summary": "携程公共酒店页需要人工登录或验证后才能读取日历价。",
                    "points": [],
                    "page_state": parsed.get("page_state"),
                    "review_reason": parsed.get("review_reason"),
                }
            bounded_days = max(1, min(int(days), 30))
            if not await _open_public_calendar_popup(calendar_page):
                last_result = {
                    "status": "needs_review",
                    "summary": "未打开日历浮层，需人工复核页面结构。",
                    "points": [],
                    "page_state": parsed.get("page_state"),
                    "review_reason": "calendar_popup_not_opened",
                    "hotel_name": parsed.get("hotel_name"),
                    "current_url": parsed.get("current_url"),
                    "requested_url": parsed.get("requested_url"),
                    "screenshot_path": screenshot_path,
                }
                if attempt == 0:
                    await _wait_public_competitor_retry_delay(calendar_page)
                    continue
                return last_result
            await _wait_public_competitor_retry_delay(calendar_page)
            cells = await _safe_public_calendar_cells(calendar_page)
            points = _extract_public_calendar_price_points(
                cells=cells,
                start_date=start_date,
                days=bounded_days,
            )
            if not points:
                last_result = {
                    "status": "needs_review",
                    "summary": "未读取到可信日历价，需人工复核页面结构。",
                    "points": [],
                    "page_state": parsed.get("page_state"),
                    "review_reason": "calendar_price_not_found",
                    "hotel_name": parsed.get("hotel_name"),
                    "current_url": parsed.get("current_url"),
                    "requested_url": parsed.get("requested_url"),
                    "screenshot_path": screenshot_path,
                }
                if attempt == 0:
                    await _wait_public_competitor_retry_delay(calendar_page)
                    continue
                return last_result
            status = "partial" if len(points) < bounded_days else "success"
            return {
                "status": status,
                "summary": f"读取到 {len(points)} 个可信日历价。",
                "points": points,
                "page_state": parsed.get("page_state"),
                "review_reason": None,
                "hotel_name": parsed.get("hotel_name"),
                "current_url": parsed.get("current_url"),
                "requested_url": parsed.get("requested_url"),
                "screenshot_path": screenshot_path,
            }
        return last_result or _calendar_page_needs_review_result(
            target=target,
            review_reason="calendar_price_not_found",
            summary="未读取到可信日历价，需人工复核页面结构。",
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
        """按顺序读取本店和全部已配置竞品。

        输入变量引用：

        * ``own_hotel_url`` 是第一个目标，也是竞品比较基准。
        * ``competitor_urls`` 由 ``_build_competitor_price_targets`` 展开为有序
          ``targets``；每个目标读取今天，URL 支持日期切换时再读取明天。
        * ``check_dates`` 提供请求入住日期；缺失或无效时使用上海时区当天日期。
        * ``actor_id`` 写入审计数据和浏览器 session 状态。
        * ``push_to_feishu`` 只控制末尾的旧版推送结果，不改变浏览器导航和价格
          解析过程。

        输出变量引用：

        * ``snapshots`` 每个目标对应一个元素，可包含嵌套 ``tomorrow``。
        * ``page_states`` 按角色和日期记录页面状态。
        * ``target_errors`` 记录限定长度的失败信息，不补造价格。
        * ``screenshot_paths`` 指向各解析页面对应的审计证据。
        """
        from app.domain.browser.ctrip_ebooking import CtripPublicCompetitorPriceResult
        from app.domain.notifications.feishu import FeishuPushResult

        # 打开浏览器前先验证完整输入列表，避免异常 URL 进入目标列表或长期酒店配置。
        invalid_urls = _invalid_public_ctrip_hotel_urls([own_hotel_url, *competitor_urls])
        if invalid_urls:
            return CtripPublicCompetitorPriceResult(
                status="failed",
                summary="Competitor price read only accepts public Ctrip hotel detail URLs.",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                data={
                    "query_type": "ctrip_public_competitor_price_snapshot",
                    "actor_id": actor_id,
                    "targets": [],
                    "snapshots": [],
                    "comparison": {},
                    "captured_at": datetime.now(UTC).isoformat(),
                },
                evidence={
                    "invalid_urls": invalid_urls,
                    "read_only": True,
                    "automation_bypass": False,
                    "parser_version": "ctrip_public_visible_price_v1",
                },
                next_action="Use hotels.ctrip.com hotel detail URLs only.",
            )
        # ``targets`` 是标准有序工作列表：本店在前，竞品在后。下方循环不会为
        # 每个目标创建页面；只有恢复过程返回替代共享页面时才重新赋值 ``page``。
        targets = _build_competitor_price_targets(
            own_hotel_url=own_hotel_url,
            competitor_urls=competitor_urls,
            check_in_date=_competitor_price_check_in_date(check_dates),
        )
        if len(targets) < 2:
            return CtripPublicCompetitorPriceResult(
                status="failed",
                summary="Own hotel URL and at least one competitor URL are required.",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                data={
                    "query_type": "ctrip_public_competitor_price_snapshot",
                    "actor_id": actor_id,
                    "targets": [],
                    "snapshots": [],
                    "comparison": {},
                    "captured_at": datetime.now(UTC).isoformat(),
                },
                evidence={
                    "read_only": True,
                    "automation_bypass": False,
                    "parser_version": "ctrip_public_visible_price_v1",
                },
                next_action="Provide own_hotel_url and at least one competitor_url.",
            )

        # 以下四个集合是同一批次的并行审计视图：``snapshots`` 保存业务证据，
        # ``page_states`` 保存角色/日期状态，``target_errors`` 保存失败详情，
        # ``screenshot_paths`` 关联页面截图证据。
        snapshots: list[dict[str, Any]] = []
        screenshot_paths: list[str] = []
        page_states: dict[str, str] = {}
        target_errors: list[dict[str, str]] = []
        try:
            page = await self._get_or_create_public_price_page_with_recovery(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )

            for index, target in enumerate(targets):
                try:
                    page, parsed, screenshot_path = await self._read_public_price_target(
                        page=page,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        target=target,
                    )
                    screenshot_paths.append(screenshot_path)
                    page_states[f"{target['role']}:today"] = str(
                        parsed.get("page_state") or ""
                    )
                except _PublicCompetitorCdpUnavailableError:
                    raise
                except Exception as exc:
                    error = _public_price_read_error_payload(
                        target=target,
                        date_label="today",
                        exc=exc,
                    )
                    target_errors.append(error)
                    parsed = _public_price_read_failure_snapshot(
                        target=target,
                        review_reason="public_price_read_failed",
                        error=error,
                    )
                    page_states[f"{target['role']}:today"] = "read_failed"
                    page = await self._recover_public_price_page_if_closed(
                        page=page,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                    )
                if str(parsed.get("status") or "") == "human_required":
                    # 停止前先把当前目标的人工处理证据加入最终结果；不会为剩余
                    # 日期生成第二次导航，但不影响批次中其他目标的读取。
                    snapshots.append(parsed)
                    continue
                tomorrow_url = _shift_public_hotel_url_dates(target["url"], days=1)
                if tomorrow_url:
                    try:
                        await page.wait_for_timeout(_public_competitor_read_delay_ms())
                    except _PublicCompetitorCdpUnavailableError:
                        raise
                    except Exception:
                        page = await self._get_or_create_public_price_page_with_recovery(
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                        )
                    # ``tomorrow_target`` 保留 role/label，只替换带日期的 URL；
                    # 仍通过同一个 ``page`` 读取，并合并到 ``parsed['tomorrow']``。
                    tomorrow_target = {**target, "url": tomorrow_url}
                    try:
                        (
                            page,
                            tomorrow_parsed,
                            tomorrow_screenshot_path,
                        ) = await self._read_public_price_target(
                            page=page,
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                            target=tomorrow_target,
                            require_visible_date_match=True,
                        )
                        screenshot_paths.append(tomorrow_screenshot_path)
                        page_states[f"{target['role']}:tomorrow"] = str(
                            tomorrow_parsed.get("page_state") or ""
                        )
                    except _PublicCompetitorCdpUnavailableError:
                        raise
                    except Exception as exc:
                        error = _public_price_read_error_payload(
                            target=tomorrow_target,
                            date_label="tomorrow",
                            exc=exc,
                        )
                        target_errors.append(error)
                        tomorrow_parsed = _public_price_read_failure_snapshot(
                            target=tomorrow_target,
                            review_reason="public_tomorrow_price_read_failed",
                            error=error,
                        )
                        page_states[f"{target['role']}:tomorrow"] = "read_failed"
                    page = await self._recover_public_price_page_if_closed(
                        page=page,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                    )
                    parsed["tomorrow"] = _build_public_tomorrow_snapshot_from_detail(
                        tomorrow_parsed
                    )
                else:
                    parsed["tomorrow"] = _missing_public_tomorrow_snapshot(parsed)
                snapshots.append(parsed)
                if index < len(targets) - 1:
                    try:
                        await page.wait_for_timeout(_public_competitor_read_delay_ms())
                    except _PublicCompetitorCdpUnavailableError:
                        raise
                    except Exception:
                        page = await self._get_or_create_public_price_page_with_recovery(
                            platform_account_id=platform_account_id,
                            browser_session_id=browser_session_id,
                        )
        except _PublicCompetitorCdpUnavailableError as exc:
            return _public_competitor_cdp_unavailable_result(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                targets=targets,
                check_dates=check_dates,
                snapshots=snapshots,
                screenshot_paths=screenshot_paths,
                page_states=page_states,
                error=str(exc),
            )

        # 所有导航结束后再计算分类和比较，确保解析输出、状态、下一步动作和审计
        # 证据都来自同一份 ``snapshots``。
        comparison = _build_public_competitor_price_comparison(snapshots)
        status = _public_competitor_price_status(snapshots)
        summary = _public_competitor_price_summary(status, snapshots, comparison)
        next_action = _public_competitor_price_next_action(status)
        trusted_dom_rate_count = _trusted_public_dom_rate_count(snapshots)
        _sync_public_competitor_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            trusted_dom_rate_count=trusted_dom_rate_count,
            status=status,
            page_states=page_states,
        )
        data = {
            "query_type": "ctrip_public_competitor_price_snapshot",
            "actor_id": actor_id,
            "targets": targets,
            "check_dates": check_dates or _extract_date_context_from_snapshots(snapshots),
            "snapshots": snapshots,
            "comparison": comparison,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        evidence = {
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "target_count": len(targets),
            "screenshot_paths": screenshot_paths,
            "password_stored": False,
            "password_returned": False,
            "automation_bypass": False,
            "read_only": True,
            "no_booking_attempted": True,
            "parser_version": "ctrip_public_visible_price_v1",
            "page_states": page_states,
            "target_errors": target_errors,
            "trusted_dom_rate_count": trusted_dom_rate_count,
            **_public_competitor_browser_mode_evidence(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            ),
        }
        feishu_push = None
        if push_to_feishu:
            skip_data = {
                "target": "deprecated_legacy_read_competitor_prices_push",
                "replacement_endpoint": (
                    "/api/v1/ctrip-ebooking/competitor-watch-groups/"
                    "{watch_group_key}/run-now"
                ),
            }
            skip_evidence = {
                "global_feishu_webhook_used": False,
                "competitor_watch_webhook_used": False,
                "price_change_alert_webhook_used": False,
                "pricing_strategy_webhook_used": False,
                "daily_brief_webhook_used": False,
            }
            feishu_push = FeishuPushResult(
                status="skipped",
                summary=(
                    "Skipped legacy read-competitor-prices Feishu push. Use the "
                    "competitor watch-group run-now endpoint so pushes use "
                    "module-owned webhooks and alert side effects stay explicit."
                ),
                data=skip_data,
                evidence=skip_evidence,
            )
            data["legacy_push_policy"] = skip_data
            evidence["legacy_push_policy"] = skip_evidence
        return CtripPublicCompetitorPriceResult(
            status=status,
            summary=summary,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            data=data,
            evidence=evidence,
            feishu_push=feishu_push,
            next_action=next_action,
        )


def _trusted_public_dom_rate_count(snapshots: list[dict[str, Any]]) -> int:
    """统计今天及嵌套明天快照中的可信房价行数。"""
    total = 0
    for snapshot in snapshots:
        total += _trusted_public_dom_rate_count_for_snapshot(snapshot)
        tomorrow = snapshot.get("tomorrow")
        if isinstance(tomorrow, dict):
            total += _trusted_public_dom_rate_count_for_snapshot(tomorrow)
    return total


def _trusted_public_dom_rate_count_for_snapshot(snapshot: dict[str, Any]) -> int:
    """统计来源和整数金额都能证明来自可见 DOM 的房价行。"""
    rates = snapshot.get("room_rates")
    if not isinstance(rates, list):
        return 0
    return sum(
        1
        for rate in rates
        if isinstance(rate, dict)
        and rate.get("source") == "room_rate_dom_display_price"
        and isinstance(rate.get("price"), dict)
        and isinstance(rate["price"].get("amount"), int)
    )


def _sync_public_competitor_browser_session_state(
    *,
    browser_session_id: str,
    actor_id: str,
    trusted_dom_rate_count: int,
    status: str,
    page_states: dict[str, str],
) -> None:
    """根据可信读取证据同步浏览器登录状态。

    ``trusted_dom_rate_count`` 优先级最高：只要存在可信可见房价，就把 session
    标记为已认证。没有可信房价时，再依据 ``page_states`` 和汇总 ``status``
    判断是否需要人工登录或验证。session store 查找失败会被忽略，避免仅因并发
    删除了旧 session 记录而导致结果持久化失败。
    """
    states = set(page_states.values())
    if trusted_dom_rate_count > 0:
        with suppress(KeyError):
            get_browser_session_store().mark_authenticated(
                browser_session_id,
                actor_id,
                action=(
                    "Ctrip public competitor read found trusted DOM room-rate prices."
                ),
            )
        return
    if states & {"login_required", "verification_required"}:
        reason = _public_competitor_human_action_reason(
            status=status,
            page_states=page_states,
        )
        _mark_public_competitor_requires_human_action(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            reason=reason,
            action="Ctrip public competitor read requires manual login or verification.",
        )
        return
    if status == "human_required":
        reason = _public_competitor_human_action_reason(
            status=status,
            page_states=page_states,
        )
        if reason in {
            "login_required",
            "verification_required",
            "ctrip_public_competitor_human_required",
        }:
            _mark_public_competitor_requires_human_action(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                reason=reason,
                action="Ctrip public competitor read requires manual login or verification.",
            )
        return
    if "authenticated" in states:
        with suppress(KeyError):
            get_browser_session_store().mark_authenticated(
                browser_session_id,
                actor_id,
                action=(
                    "Ctrip public competitor read confirmed an authenticated hotel page."
                ),
            )


def _public_competitor_human_action_reason(
    *,
    status: str,
    page_states: dict[str, str],
) -> str:
    """把汇总状态和逐日期页面状态转换为一个操作员处理原因。"""
    states = set(page_states.values())
    if "login_required" in states:
        return "login_required"
    if "verification_required" in states:
        return "verification_required"
    if status == "human_required":
        return "ctrip_public_competitor_human_required"
    return "ctrip_public_competitor_no_trusted_dom_prices"


def _mark_public_competitor_requires_human_action(
    *,
    browser_session_id: str,
    actor_id: str,
    reason: str,
    action: str,
) -> None:
    """把浏览器 session 标记为需人工处理，不暴露 profile 或 Cookie 数据。"""
    with suppress(KeyError):
        get_browser_session_store().mark_requires_human_action(
            browser_session_id,
            actor_id,
            reason=reason,
            risk_flags=[reason, "ctrip_public_competitor_read_blocked"],
            action=action,
        )


def _is_public_price_page_usable(page: Page) -> bool:
    """判断 Playwright ``page`` 是否仍可安全复用。"""
    try:
        return not page.is_closed()
    except Exception:
        return False


# ``is_closed`` 探测不到"页面还在但 CDP 通道挂死"的半死状态；复用前用一次
# 带超时的 evaluate 做通道活性探测（2026-09-22 竞品读死循环事故的纵深防御）。
_PUBLIC_PAGE_LIVENESS_TIMEOUT_SECONDS = 2.0


async def _is_public_price_page_alive(page: Page) -> bool:
    # 用 ``asyncio.wait`` 而非 ``wait_for``：``wait_for`` 超时后要先等
    # 任务真正取消才返回，通道挂死时取消可能永远完不成，探测自身会
    # 变成新的挂点；``wait`` 超时立即返回，残留任务尽力取消即可。
    task = asyncio.ensure_future(page.evaluate("1"))
    try:
        done, _pending = await asyncio.wait(
            {task}, timeout=_PUBLIC_PAGE_LIVENESS_TIMEOUT_SECONDS
        )
    except Exception:
        task.cancel()
        return False
    if not done:
        task.cancel()
        return False
    try:
        task.result()
        return True
    except Exception:
        return False


async def _bring_public_price_page_to_front(page: Page) -> None:
    """把工作页拉回前台；多余页面抢走前台后，浏览器对后台页面的渲染
    节流会让元素等待永远超时。前台化失败不应阻断读取。"""
    with suppress(Exception):
        await page.bring_to_front()


async def _close_extra_public_competitor_pages(context: Any, *, keep: Page) -> None:
    """关闭公共竞品工作浏览器里多余的非登录页面。

    9222 竞品实例是竞品/日历读取的专用工作浏览器；残留的第二个页面会把
    缓存的工作页挤到后台导致读取挂死（2026-09-22 事故根因）。登录页保留，
    避免打断人工恢复登录；关闭单页失败不阻断其余清理。
    """
    from app.domain.browser.ctrip_common import _is_login_url

    for extra in list(context.pages):
        if extra is keep or extra.is_closed():
            continue
        if _is_login_url(extra.url):
            continue
        with suppress(Exception):
            await extra.close()
