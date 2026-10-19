from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.domain.browser.sessions import get_browser_session_store

MEITUAN_EBK_LOGIN_URL = "https://eb.meituan.com/ebk/login/login.html"
MEITUAN_EBK_WORKBENCH_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F"
)
MEITUAN_EBK_DATA_CENTER_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fdata-center%2Findex.html"
)
MEITUAN_EBK_PROMOTION_COUPON_URL = (
    "https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?"
    "biz=universal&page=marketplatform&from=EBOOKING"
)
MEITUAN_EBK_COMMENT_MANAGE_URL = (
    "https://me.meituan.com/ebooking/merchant/comment-manage-react#/home"
)
# 推广通（CPC 广告）总览页：EBK 壳内嵌 ebmidas.dianping.com 内容帧，
# 财务概览 + 推广概览两组指标即页面全部广告数据（2026-09-11 实测）。
MEITUAN_EBK_AD_CPC_URL = (
    "https://me.meituan.com/ebooking/merchant/fullPathIframe?"
    "iUrl=https%3A%2F%2Febmidas.dianping.com%2Fshopdiy%2Faccount%2FpcCpcEntry%3F"
    "continueUrl%3D%2Fapp%2Fpeon-merchant-product-menu%2Fhtml%2Findex.html"
)
_MEITUAN_DATA_CENTER_PARSER = "meituan_data_center_dom_v1"
_MEITUAN_WORKBENCH_PARSER = "meituan_workbench_dom_v1"
_MEITUAN_DATA_CENTER_TAB_PARSER = "meituan_data_center_tab_dom_v1"
_MEITUAN_PROMOTION_COUPON_PARSER = "meituan_promotion_coupon_dom_v1"
_MEITUAN_COMMENT_MANAGE_PARSER = "meituan_comment_manage_dom_v1"
_MEITUAN_AD_CPC_PARSER = "meituan_ad_cpc_dom_v1"

# T1.1（2026-08-28 实测）：数据中心「首页/交易分析/流量分析」的指标区自带
# 「今日实时/昨日/近7天/近30天」页签（默认昨日）；「服务分析」无今日实时页签。
_MEITUAN_DATA_CENTER_REALTIME_TAB_DATASETS = (
    "data_center_home",
    "data_center_trade",
    "data_center_traffic",
)
_MEITUAN_REALTIME_TAB_LABEL = "今日实时"
_MEITUAN_REALTIME_TAB_METRIC_SIGNALS = (
    "引流价",
    "销售额",
    "曝光量",
    "支付订单数",
    "浏览人数",
    "访问人数",
    "销售间夜",
)

_MEITUAN_DATASET_CONFIGS: dict[str, dict[str, str | None]] = {
    "workbench_home": {
        "url": MEITUAN_EBK_WORKBENCH_URL,
        "url_marker": "new-workbench",
        "tab_label": None,
    },
    "data_center_home": {
        "url": MEITUAN_EBK_DATA_CENTER_URL,
        "url_marker": "data-center",
        "tab_label": "\u9996\u9875",
    },
    "data_center_trade": {
        "url": MEITUAN_EBK_DATA_CENTER_URL,
        "url_marker": "data-center",
        "tab_label": "\u4ea4\u6613\u5206\u6790",
    },
    "data_center_traffic": {
        "url": MEITUAN_EBK_DATA_CENTER_URL,
        "url_marker": "data-center",
        "tab_label": "\u6d41\u91cf\u5206\u6790",
    },
    "data_center_service": {
        "url": MEITUAN_EBK_DATA_CENTER_URL,
        "url_marker": "data-center",
        "tab_label": "\u670d\u52a1\u5206\u6790",
    },
    "data_center_peer_ranking": {
        "url": MEITUAN_EBK_DATA_CENTER_URL,
        "url_marker": "data-center",
        "tab_label": "\u540c\u884c\u5206\u6790",
    },
    "promotion_coupon": {
        "url": MEITUAN_EBK_PROMOTION_COUPON_URL,
        "url_marker": "marketplatform",
        "tab_label": None,
    },
    "comment_manage_home": {
        "url": MEITUAN_EBK_COMMENT_MANAGE_URL,
        "url_marker": "comment-manage",
        "tab_label": None,
    },
    "ad_cpc": {
        "url": MEITUAN_EBK_AD_CPC_URL,
        "url_marker": "pcCpcEntry",
        "tab_label": None,
    },
}


class MeituanBrowserProfileLockedError(RuntimeError):
    """Raised when another process owns the persistent Meituan browser profile."""


class MeituanEbookingBrowserService:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._cdp_browsers: dict[str, Browser] = {}
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._page_locks: dict[str, asyncio.Lock] = {}
        self._os_chrome_processes: dict[str, subprocess.Popen[Any]] = {}

    async def open_login(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        if not _is_meituan_url(page.url):
            await page.goto(MEITUAN_EBK_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
            await _settle_page(page)

        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=True,
        )

    async def check_login_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        # Always re-attach and re-select the current Meituan tab. After manual
        # login the operator may have navigated away from the original page.
        cache_key = _meituan_cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        self._pages.pop(cache_key, None)
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=False,
        )

    async def read_data_center(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        if "data-center" not in page.url:
            await page.goto(MEITUAN_EBK_DATA_CENTER_URL, wait_until="domcontentloaded", timeout=45_000)
            await _settle_page(page)

        text = await _safe_meituan_data_center_text(page)
        page_state = _classify_meituan_page_state(page.url, text)
        if page_state != "authenticated":
            return {
                "status": "human_required",
                "summary": "美团 EBK 数据中心需要人工登录或验证后才能读取。",
                "platform": "meituan",
                "dataset": "data_center",
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "manual_login_required": True,
                "data": {
                    "hotel_name": None,
                    "basic_operation_metrics": {},
                },
                "evidence": {
                    "session_port": get_browser_session_store().get(browser_session_id).session_port,
                    "profile_path_returned": False,
                    "password_returned": False,
                    "password_stored": False,
                    "automation_bypass": False,
                    "current_url": page.url,
                    "page_state": page_state,
                    "parser": _MEITUAN_DATA_CENTER_PARSER,
                },
                "next_action": "请先在美团 EBK 浏览器窗口完成人工登录或安全验证。",
            }

        parsed = parse_meituan_data_center_text(text, current_url=page.url)
        parsed["platform_account_id"] = platform_account_id
        parsed["browser_session_id"] = browser_session_id
        parsed["manual_login_required"] = False
        parsed["evidence"]["session_port"] = get_browser_session_store().get(browser_session_id).session_port
        parsed["evidence"]["page_state"] = page_state
        parsed["next_action"] = (
            "可将本次美团数据中心读取结果与携程 EBK 数据一起交给 Hermes 汇总。"
            if parsed["status"] == "success"
            else "请人工复核美团数据中心页面结构后重试。"
        )
        get_browser_session_store().mark_authenticated(
            browser_session_id,
            actor_id,
            action="Meituan EBK data center read completed.",
        )
        return parsed

    async def read_dataset(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        dataset: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        normalized_dataset = _normalize_meituan_dataset(dataset)
        config = _MEITUAN_DATASET_CONFIGS.get(normalized_dataset)
        if config is None:
            return _meituan_unknown_dataset_payload(
                dataset=dataset,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )

        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=headless,
        )
        url_marker = str(config["url_marker"] or "")
        # 推广通是三层 iframe 链（fullPathIframe → ebmidas 入口 → shopdiy 内容帧）：
        # 复用页面上即使 URL 已带标记，内容帧也可能未挂载（实测停留壳帧），
        # 必须强制重新导航并等内容帧信号出现。
        needs_navigation = bool(url_marker) and (
            url_marker not in page.url or normalized_dataset == "ad_cpc"
        )
        if needs_navigation:
            try:
                await page.goto(str(config["url"]), wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:
                if not _is_meituan_navigation_error(exc):
                    raise
                return _meituan_human_required_dataset_payload(
                    dataset=normalized_dataset,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    current_url=page.url,
                    page_state="navigation_failed",
                )
            await _settle_page(page)

        tab_label = config.get("tab_label")
        if tab_label:
            await _select_meituan_data_center_tab(page, str(tab_label))

        if normalized_dataset == "ad_cpc":
            # 推广通内容在 ebmidas iframe（shopdiy-node/main），通用打分器
            # 的数据集信号词覆盖不到该帧，走专用内容帧选择器。
            text = await _safe_meituan_ad_cpc_text(page)
        else:
            text = await _safe_meituan_dataset_text(page, url_marker=url_marker)
        page_state = _classify_meituan_page_state(page.url, text)
        if page_state != "authenticated":
            return _meituan_human_required_dataset_payload(
                dataset=normalized_dataset,
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                current_url=page.url,
                page_state=page_state,
            )

        parsed = parse_meituan_dataset_text(
            text,
            current_url=page.url,
            dataset=normalized_dataset,
        )
        if normalized_dataset in _MEITUAN_DATA_CENTER_REALTIME_TAB_DATASETS:
            realtime_block = await _read_meituan_today_realtime_block(
                page, url_marker=url_marker, dataset=normalized_dataset
            )
            if realtime_block is not None:
                parsed = _attach_meituan_today_realtime(
                    parsed,
                    realtime_block["parsed"],
                    selected_page_tab=realtime_block["selected_page_tab"],
                )
        if normalized_dataset == "data_center_peer_ranking":
            peer_rankings = await _read_meituan_peer_rankings(page)
            if peer_rankings:
                parsed.setdefault("data", {})["peer_rankings_by_tab"] = peer_rankings
        if normalized_dataset == "workbench_home":
            tab_metrics = await _read_meituan_workbench_tab_metrics(page, url_marker=url_marker)
            if tab_metrics:
                data = parsed.setdefault("data", {})
                for key, value in tab_metrics.items():
                    if value:
                        data[key] = value
        if normalized_dataset == "promotion_coupon":
            activity_data = await _read_meituan_promotion_activity_data(page, url_marker=url_marker)
            if activity_data:
                data = parsed.setdefault("data", {})
                activities = data.setdefault("coupon_activities", [])
                if isinstance(activities, list) and activities:
                    activities[0].update(activity_data)
                else:
                    data["coupon_activities"] = [activity_data]
            # T2（2026-08-28 实测）：「活动报名」页提供活动名+报名状态+优惠适用时间
            # 的确定性 current_state 证据（神券页内容常为空）。主页面解析完成后追加导航。
            signup_activities = await _read_meituan_activity_signup(page)
            if signup_activities:
                parsed.setdefault("data", {})["signup_activities"] = signup_activities
        parsed["platform_account_id"] = platform_account_id
        parsed["browser_session_id"] = browser_session_id
        parsed["manual_login_required"] = False
        parsed["evidence"]["session_port"] = get_browser_session_store().get(browser_session_id).session_port
        parsed["evidence"]["page_state"] = page_state
        parsed["next_action"] = (
            "可将本次美团 EBK 读取结果与携程 EBK 数据一起交给 Hermes 汇总。"
            if parsed["status"] == "success"
            else "请人工复核美团 EBK 页面结构后重试。"
        )
        get_browser_session_store().mark_authenticated(
            browser_session_id,
            actor_id,
            action=f"Meituan EBK dataset read completed: {normalized_dataset}.",
        )
        return parsed

    async def _sync_login_state_from_page(
        self,
        *,
        page: Page,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        opened_login_window: bool,
    ) -> dict[str, Any]:
        body_text = await _safe_visible_text(page)
        current_url = page.url
        page_state = _classify_meituan_page_state(current_url, body_text)
        login_complete = page_state == "authenticated"
        session = get_browser_session_store().get(browser_session_id)

        if login_complete:
            get_browser_session_store().mark_authenticated(
                browser_session_id,
                actor_id,
                action="Meituan EBK login confirmed from opened browser session.",
            )
            summary = "Meituan EBK login state is authenticated."
            next_action = "Meituan login is ready for later read-only capture."
        else:
            if opened_login_window:
                with suppress(KeyError):
                    get_browser_session_store().mark_login_window_opened(
                        browser_session_id,
                        actor_id,
                        action="Meituan EBK login window opened; finish login manually if required.",
                    )
                summary = "Meituan EBK login page is open in system Chrome. Complete login manually."
                next_action = "Complete Meituan login and slider verification, then click 确认登录态."
            else:
                summary = "Meituan EBK is not authenticated yet. Keep the Chrome window open and finish login."
                next_action = "Finish Meituan login in Chrome, then click 确认登录态 again."

        return {
            "status": "success" if login_complete else "human_required",
            "summary": summary,
            "platform": "meituan",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "login_url": MEITUAN_EBK_LOGIN_URL,
            "manual_login_required": not login_complete,
            "evidence": {
                "session_port": session.session_port,
                "profile_path_returned": False,
                "password_returned": False,
                "password_stored": False,
                "automation_bypass": False,
                "browser_mode": "os_chrome_cdp",
                "current_url": current_url,
                "page_state": page_state,
            },
            "next_action": next_action,
        }

    async def prepare_pricing_page_write_dry_run(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """V9 Step 3 — 美团价格页写入空跑（dry-run，不填价不提交）。"""
        from app.domain.browser.meituan_pricing_page_reader import (
            read_meituan_pricing_write_page,
        )
        from app.domain.browser.meituan_pricing_page_writer import (
            build_meituan_pricing_page_write_preview,
        )
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _sync_ebk_browser_session_state,
        )

        requested_url = _resolve_meituan_pricing_target_url(target_url)
        if requested_url is None:
            return _meituan_pricing_invalid_url_payload(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                execute_request=execute_request,
                target_url=target_url,
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # B5.3 — 写价前先把页面收敛到「只选审批目标产品 + 指定日期」。
        from app.domain.browser.pricing_page_product_selection import (
            build_selection_failure_payload,
            ensure_product_selection,
        )

        selection = await ensure_product_selection(
            page=page,
            platform="meituan",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            query_type="meituan_ebooking_pricing_page_write_dry_run",
        )
        if selection.get("status") != "success":
            return build_selection_failure_payload(
                selection,
                platform="meituan",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                query_type="meituan_ebooking_pricing_page_write_dry_run",
            )

        page_read = await read_meituan_pricing_write_page(
            page, execute_request=execute_request
        )
        preview = build_meituan_pricing_page_write_preview(
            execute_request=execute_request,
            page_read=page_read,
        )
        status = str(preview.get("status") or page_read.get("status") or "failed")
        summary = str(
            preview.get("summary") or page_read.get("summary") or "美团 EBK 价格页空跑完成。"
        )
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=status,
            page_state=str(page_read.get("page_state") or ""),
        )
        return {
            "status": status,
            "summary": summary,
            "platform": "meituan",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "query_type": "meituan_ebooking_pricing_page_write_dry_run",
                "actor_id": actor_id,
                "requested_url": requested_url,
                "current_url": page.url,
                "execute_request_id": execute_request.get("request_id"),
                "page_read": page_read,
                "page_write_preview": preview,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            "evidence": {
                "platform": "meituan",
                "read_only": True,
                "dry_run": True,
                "product_selection": {
                    "matched_rows": selection.get("matched_rows"),
                    "counter": selection.get("counter"),
                    "steps": selection.get("steps"),
                },
                "browser_write_invoked": False,
                "input_fill_invoked": False,
                "save_click_invoked": False,
                "ota_submit_invoked": False,
                "automation_bypass": False,
                "reader_evidence": page_read.get("evidence"),
                "writer_evidence": preview.get("evidence"),
            },
            "next_action": preview.get("next_action"),
        }

    async def read_pricing_product_room_rates(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        target_url: str | None = None,
        checkin_date: str | None = None,
    ) -> dict[str, Any]:
        """只读：房价房量日历页读取房型现价/房态（查房价交互）。

        2026-09-23 实测：批量改房价页产品树改版后不再展示任何价格；
        现价/房态迁到「房价房量日历」页的产品子行，读取也随之换页。
        写价链路（dry-run/execute）仍走批量改房价页，不受影响。
        """

        from app.domain.browser.pricing_room_rates import (
            login_redirected_after_read,
            platform_read_error_payload,
            platform_read_payload,
            read_meituan_calendar_room_rates,
            wait_for_definitive_page_state,
        )
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _settle_page,
            _sync_ebk_browser_session_state,
        )
        from app.domain.browser.meituan_pricing_page_reader import (
            classify_meituan_pricing_page_state,
        )

        requested_url = _resolve_meituan_room_calendar_url(target_url)
        if requested_url is None:
            return platform_read_error_payload(
                platform="meituan",
                status="page_structure_changed",
                summary="美团房价查询只接受 meituan.com 页面链接。",
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

        page_state = await wait_for_definitive_page_state(
            page, classify_meituan_pricing_page_state
        )
        if page_state in ("login_required", "verification_required"):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state=page_state,
            )
            return platform_read_error_payload(
                platform="meituan",
                status="human_required",
                summary="美团 EBK 需要先人工完成登录/验证，再查询房价。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason=page_state,
            )
        if page_state != "pricing_page":
            return platform_read_error_payload(
                platform="meituan",
                status="page_structure_changed",
                summary="美团房价房量日历页结构无法识别。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason=f"page_state={page_state}",
            )

        read = await read_meituan_calendar_room_rates(
            page, checkin_date=str(checkin_date or "")
        )
        # 采集过程中登录态可能失效（页面被重定向到 login），结果不可信。
        if login_redirected_after_read(page.url):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state="login_redirect_after_read",
            )
            return platform_read_error_payload(
                platform="meituan",
                status="human_required",
                summary="美团 EBK 登录态在读取过程中失效，需要人工重新登录后再查询房价。",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                reason="login_redirect_after_read",
            )
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=str(read.get("status") or "failed"),
            page_state=page_state,
        )
        return platform_read_payload(
            platform="meituan",
            status=str(read.get("status") or "failed"),
            summary=str(read.get("summary") or ""),
            room_rates=list(read.get("room_rates") or []),
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )

    async def execute_pricing_page_write(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        execute_request: dict[str, Any],
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """V9 Step 3 — 美团价格页正式写入（填价 + 保存 + 逐目标回读）。"""
        from app.domain.browser.ctrip_ebooking_session_helpers import (
            _sync_ebk_browser_session_state,
        )
        from app.domain.browser.meituan_pricing_executor import (
            fill_meituan_room_price,
            submit_meituan_price_changes,
        )
        from app.domain.browser.meituan_pricing_page_reader import (
            classify_meituan_pricing_page_state,
        )

        settings = get_settings()
        goto_url = _resolve_meituan_pricing_target_url(target_url)
        if goto_url is None:
            return _meituan_pricing_invalid_url_payload(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                execute_request=execute_request,
                target_url=target_url,
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(goto_url, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)

        # B5.3 — execute 同样先收敛页面到审批目标，杜绝波及默认勾选产品。
        from app.domain.browser.pricing_page_product_selection import (
            build_selection_failure_payload,
            ensure_product_selection,
        )

        selection = await ensure_product_selection(
            page=page,
            platform="meituan",
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            execute_request=execute_request,
            query_type="meituan_ebooking_pricing_page_write",
        )
        if selection.get("status") != "success":
            return build_selection_failure_payload(
                selection,
                platform="meituan",
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                query_type="meituan_ebooking_pricing_page_write",
            )

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text(timeout=5_000)
        except Exception:
            body_text = ""
        page_state = classify_meituan_pricing_page_state(page.url, body_text)
        if page_state in ("login_required", "verification_required"):
            _sync_ebk_browser_session_state(
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                status="human_required",
                page_state=page_state,
            )
            return {
                "status": "human_required",
                "summary": "美团 EBK 需要先完成登录或验证，才能执行写价。",
                "platform": "meituan",
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "data": {"page_state": page_state, "current_url": page.url},
                "evidence": {"blocked_reason": page_state, "ota_submit_invoked": False},
                "next_action": "请人工完成登录/验证后重试。",
            }
        if page_state != "pricing_page":
            return {
                "status": "page_structure_changed",
                "summary": "美团 EBK 价格页结构未能识别，写入已中止。",
                "platform": "meituan",
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "data": {"page_state": page_state, "current_url": page.url},
                "evidence": {"blocked_reason": "not_pricing_page", "ota_submit_invoked": False},
                "next_action": "请人工打开美团 EBK 价格页确认结构后重试。",
            }

        actions = execute_request.get("actions") or []
        action_results: list[dict[str, Any]] = []
        submitted_targets: list[dict[str, Any]] = []
        overall_status = "executed"
        for action in actions:
            if not isinstance(action, dict):
                continue
            room_type = str(action.get("room_type") or "")
            checkin_date = str(action.get("checkin_date") or "")
            proposed_base = str(action.get("proposed_base_price_yuan") or "")
            if not room_type or not checkin_date or not proposed_base:
                action_results.append(
                    {
                        "room_type": room_type,
                        "checkin_date": checkin_date,
                        "status": "data_required",
                        "reason": "Missing room_type, checkin_date, or proposed_base_price_yuan.",
                    }
                )
                overall_status = "data_required"
                continue
            fill_result = await fill_meituan_room_price(
                page,
                target={
                    "room_type": room_type,
                    "checkin_date": checkin_date,
                    "selector_hint": action.get("selector_hint"),
                    "input_candidates": action.get("input_candidates") or [],
                },
                base_price=proposed_base,
            )
            if fill_result.get("status") != "filled":
                action_results.append(
                    {
                        "room_type": room_type,
                        "checkin_date": checkin_date,
                        "status": fill_result.get("status", "input_failed"),
                        "fill_result": fill_result,
                    }
                )
                if overall_status == "executed":
                    overall_status = "failed"
                continue
            action_results.append(
                {"room_type": room_type, "checkin_date": checkin_date, "status": "filled"}
            )
            submitted_targets.append(
                {
                    "room_type": room_type,
                    "checkin_date": checkin_date,
                    "proposed_base_price_yuan": proposed_base,
                    "selector_hint": action.get("selector_hint"),
                }
            )

        submit_result = None
        if overall_status == "executed":
            submit_result = await submit_meituan_price_changes(
                page,
                targets=submitted_targets,
                artifact_dir=Path(settings.playwright_artifact_dir),
                platform_account_id=platform_account_id,
            )
            if submit_result.get("status") != "executed":
                overall_status = submit_result.get("status", "failed")

        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status=overall_status,
            page_state=page_state,
        )
        summary_parts = [f"Executed {len(action_results)} pricing action(s)"]
        if submit_result:
            summary_parts.append(f"Submit: {submit_result.get('status')}")
        return {
            "status": overall_status,
            "summary": ". ".join(summary_parts),
            "platform": "meituan",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "query_type": "meituan_ebooking_pricing_page_write_execute",
                "execute_request_id": execute_request.get("request_id"),
                "current_url": page.url,
                "action_results": action_results,
                "submit_result": submit_result,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            "evidence": {
                "pricing_executor_version": "meituan_ebooking_pricing_executor_v1",
                "platform": "meituan",
                "read_only": False,
                "browser_write_invoked": True,
                "ota_submit_invoked": overall_status == "executed",
                "page_state": page_state,
            },
            "next_action": (
                "Verify changed prices on Meituan EBK."
                if overall_status == "executed"
                else "Inspect the browser page and resubmit after resolving the issue."
            ),
        }

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        headless: bool,
    ) -> Page:
        cache_key = _meituan_cache_key(
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
        del headless  # Meituan login always uses a visible OS Chrome window.
        cache_key = _meituan_cache_key(
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
                    raise MeituanBrowserProfileLockedError(
                        "美团浏览器账号正在被另一个后台任务或登录窗口占用，请稍后重试，或关闭占用该账号的 Chrome 后再打开。"
                    )

                chrome_path = _resolve_installed_chrome_path()
                if chrome_path is None:
                    launch_lock.release()
                    raise RuntimeError(
                        "未找到本机 Google Chrome。请安装 Chrome 后重试，或先用带调试端口的 Chrome 打开美团登录页。"
                    )

                args = _meituan_os_chrome_launch_args(
                    chrome_path=chrome_path,
                    profile_dir=profile_dir,
                    session_port=session.session_port,
                    login_url=MEITUAN_EBK_LOGIN_URL,
                )
                process = subprocess.Popen(  # noqa: S603 - operator-owned local Chrome launch
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._os_chrome_processes[cache_key] = process
                ready = await _wait_for_cdp_ready(session.session_port, timeout_seconds=20)
                launch_lock.release()
                launch_lock = None
                if not ready:
                    raise RuntimeError(
                        "本机 Chrome 已启动，但调试端口尚未就绪。请确认账号调试端口未被占用后重试。"
                    )

                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                    timeout_ms=5_000,
                )
                if cdp_page:
                    return cdp_page
                raise RuntimeError("本机 Chrome 已启动，但无法通过 CDP 连接。请关闭占用窗口后重试。")
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
                    raise MeituanBrowserProfileLockedError(
                        "美团浏览器账号正在被其他 Chrome 进程占用，无法同时启动第二个持久化浏览器。请稍后重试或关闭占用窗口。"
                    ) from exc
                if attempt == 0 and _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                    continue
                raise
        raise RuntimeError("Meituan EBK browser page could not be created.")

    def _lock_for(self, cache_key: str) -> asyncio.Lock:
        lock = self._page_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._page_locks[cache_key] = lock
        return lock

    async def _reset_playwright_driver(self) -> None:
        close_candidates: list[Any] = [
            *self._pages.values(),
            *self._contexts.values(),
            *self._cdp_browsers.values(),
        ]
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
        page = _select_meituan_page(context.pages)
        if page is None:
            page = await context.new_page()
        cache_key = _meituan_cache_key(
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
        return (Path(settings.browser_state_dir) / "meituan-ebooking" / platform_account_id).resolve()


def parse_meituan_data_center_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    metrics = _parse_basic_operation_metrics(lines)
    hotel_name = _line_after(lines, "门店")
    if not hotel_name or not metrics:
        return {
            "status": "needs_review",
            "summary": "美团数据中心页面结构未识别，请人工复核页面是否已登录或是否改版。",
            "platform": "meituan",
            "dataset": "data_center",
            "data": {
                "hotel_name": hotel_name,
                "scope": _find_first_line(lines, prefix="仅看"),
                "update_note": _find_first_line(lines, contains="数据仅作经营参考"),
                "period_tabs": _extract_period_tabs(lines),
                "basic_operation_metrics": metrics,
                "project_performance": {},
                "marketing_hotspots": [],
                "peer_rankings": [],
            },
            "evidence": _meituan_data_center_evidence(
                current_url=current_url,
                line_count=len(lines),
            ),
        }

    return {
        "status": "success",
        "summary": "已读取美团 EBK 数据中心关键经营指标。",
        "platform": "meituan",
        "dataset": "data_center",
        "data": {
            "hotel_name": hotel_name,
            "scope": _find_first_line(lines, prefix="仅看"),
            "update_note": _find_first_line(lines, contains="数据仅作经营参考"),
            "period_tabs": _extract_period_tabs(lines),
            "basic_operation_metrics": metrics,
            "project_performance": _parse_project_performance(lines),
            "marketing_hotspots": _parse_marketing_hotspots(lines),
            "peer_rankings": _parse_peer_rankings(lines),
        },
        "evidence": _meituan_data_center_evidence(
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def parse_meituan_dataset_text(
    text: str,
    *,
    current_url: str,
    dataset: str,
) -> dict[str, Any]:
    normalized_dataset = _normalize_meituan_dataset(dataset)
    if normalized_dataset == "workbench_home":
        return parse_meituan_workbench_text(text, current_url=current_url)
    if normalized_dataset == "promotion_coupon":
        return parse_meituan_promotion_coupon_text(text, current_url=current_url)
    if normalized_dataset == "comment_manage_home":
        return parse_meituan_comment_manage_text(text, current_url=current_url)
    if normalized_dataset == "ad_cpc":
        return parse_meituan_ad_cpc_text(text, current_url=current_url)
    if normalized_dataset == "data_center_home":
        parsed = parse_meituan_data_center_text(text, current_url=current_url)
        if parsed["status"] == "success":
            parsed["dataset"] = "data_center_home"
            return parsed
        fallback = parse_meituan_data_center_tab_text(
            text,
            current_url=current_url,
            dataset="data_center_home",
            tab_label="\u9996\u9875",
        )
        return fallback if fallback["status"] == "success" else parsed
    if normalized_dataset.startswith("data_center_"):
        tab_label = str(_MEITUAN_DATASET_CONFIGS.get(normalized_dataset, {}).get("tab_label") or "")
        return parse_meituan_data_center_tab_text(
            text,
            current_url=current_url,
            dataset=normalized_dataset,
            tab_label=tab_label,
        )
    return {
        "status": "needs_review",
        "summary": "\u672a\u652f\u6301\u7684\u7f8e\u56e2 EBK \u6570\u636e\u96c6\uff0c\u8bf7\u5148\u5728\u540e\u7aef\u9002\u914d\u3002",
        "platform": "meituan",
        "dataset": dataset,
        "data": {},
        "evidence": _meituan_dataset_evidence(
            parser="unknown",
            current_url=current_url,
            line_count=len(_clean_meituan_text_lines(text)),
        ),
    }


def parse_meituan_data_center_tab_text(
    text: str,
    *,
    current_url: str,
    dataset: str,
    tab_label: str,
) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    tab_fields = _parse_meituan_data_center_tab_fields(lines, dataset=dataset)
    metrics = tab_fields.get("metrics", {})
    hotel_name = _line_after_any(lines, ["\u95e8\u5e97", "闂ㄥ簵"])
    recognized = bool(metrics) or (bool(tab_label) and tab_label in lines)
    data = {
        "hotel_name": hotel_name,
        "tab_label": tab_label,
        "metrics": metrics,
        "visible_lines": _bounded_visible_lines(lines),
    }
    data.update(tab_fields)
    if not recognized:
        return {
            "status": "needs_review",
            "summary": "\u7f8e\u56e2\u6570\u636e\u4e2d\u5fc3\u5206\u680f\u9875\u7ed3\u6784\u672a\u8bc6\u522b\uff0c\u8bf7\u4eba\u5de5\u590d\u6838\u3002",
            "platform": "meituan",
            "dataset": dataset,
            "data": data,
            "evidence": _meituan_dataset_evidence(
                parser=_MEITUAN_DATA_CENTER_TAB_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }

    return {
        "status": "success",
        "summary": "\u5df2\u8bfb\u53d6\u7f8e\u56e2\u6570\u636e\u4e2d\u5fc3\u5206\u680f\u53ef\u89c1\u6307\u6807\u3002",
        "platform": "meituan",
        "dataset": dataset,
        "data": data,
        "evidence": _meituan_dataset_evidence(
            parser=_MEITUAN_DATA_CENTER_TAB_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _parse_meituan_data_center_tab_fields(
    lines: list[str],
    *,
    dataset: str,
) -> dict[str, Any]:
    normalized_dataset = _normalize_meituan_dataset(dataset)
    if normalized_dataset == "data_center_trade":
        business_performance = _extract_known_metric_values(
            lines,
            [
                "\u9500\u552e\u989d",
                "\u652f\u4ed8\u8ba2\u5355\u6570",
                "\u5165\u4f4f\u95f4\u591c",
                "\u9500\u552e\u95f4\u591c",
                "\u5165\u4f4f\u91d1\u989d",
                "\u5e73\u5747\u623f\u4ef7",
                "\u6210\u4ea4\u7387",
                "\u652f\u4ed8\u8f6c\u5316\u7387",
                "\u9500\u552e\u5747\u4ef7",
                "\u6ee1\u623f\u7387",
                "\u5f15\u6d41\u4ef7",
                "\u66dd\u5149\u91cf",
                "\u6d4f\u89c8\u4eba\u6570",
                "\u8bbf\u95ee\u4eba\u6570",
            ],
        )
        return {
            "business_performance": business_performance,
            "metrics": business_performance,
        }
    if normalized_dataset == "data_center_traffic":
        # Funnel cards use hm-title-txt labels; chart toggles like 占比/数值 must be skipped.
        traffic_trend = _extract_known_metric_values(
            lines,
            [
                "\u66dd\u5149\u4eba\u6570",
                "\u66dd\u5149\u91cf",
                "\u6d4f\u89c8\u4eba\u6570",
                "\u8bbf\u95ee\u4eba\u6570",
                "\u652f\u4ed8\u8ba2\u5355\u6570",
                "\u652f\u4ed8\u8f6c\u5316\u7387",
                "\u66dd\u5149-\u6d4f\u89c8\u8f6c\u5316\u7387",
                "\u6d4f\u89c8-\u652f\u4ed8\u8f6c\u5316\u7387",
                "\u5f15\u6d41\u4ef7",
                "\u6d41\u91cf",
            ],
        )
        loss_analysis = _extract_known_metric_values(
            lines,
            [
                "\u6d41\u5931\u8ba2\u5355\u91cf",
                "\u6d41\u5931\u95f4\u591c\u91cf",
                "\u6d41\u5931\u8ba2\u5355\u91d1\u989d",
                "\u6d41\u5931\u7387",
            ],
        )
        # 2026-09-01 实测：流量趋势数据块每项自带 较上期/较上周+同行排名 N/M 上下文，
        # 与基础经营同构；作为 basic_operation_metrics 输出供「今日实时」块挂载复用。
        trend_section = _slice_between(lines, "流量趋势数据", "流失分析")
        trend_metrics = _parse_metric_chunks(
            trend_section,
            [
                "曝光人数",
                "曝光量",
                "浏览人数",
                "访问人数",
                "支付订单数",
                "支付转化率",
                "曝光-浏览转化率",
                "浏览-支付转化率",
                "引流价",
            ],
        )
        metrics = {**traffic_trend, **loss_analysis}
        return {
            "basic_operation_metrics": trend_metrics,
            "traffic_trend": traffic_trend,
            "loss_analysis": loss_analysis,
            "metrics": metrics,
        }
    if normalized_dataset == "data_center_service":
        service_metrics = _extract_known_label_values(
            lines,
            [
                "\u670d\u52a1\u5206",
                "\u8bc4\u4ef7\u5206",
                "\u4fe1\u606f\u5206",
                "\u65b0\u589e\u5dee\u8bc4\u6570",
                "\u5f85\u56de\u590d\u5dee\u8bc4\u6570",
                "\u65b0\u589e\u597d\u8bc4\u6570",
                "5\u5206\u949f\u786e\u8ba4\u7387",
                "\u62d2\u5355\u95ee\u9898\u91cf",
                "\u9152\u5e97\u6743\u76ca\u62a5\u540d\u91cf",
                "\u6743\u76ca\u8ba2\u5355\u91cf",
                "\u8fdd\u89c4\u8fdd\u7ea6\u6b21\u6570",
                "\u5904\u7f5a\u5b8c\u6210\u6b21\u6570",
                "\u63a8\u7ffb\u95ee\u9898\u91cf",
                "\u9003\u5355\u95ee\u9898\u91cf",
            ],
        )
        return {
            "service_metrics": service_metrics,
            "metrics": service_metrics,
        }

    generic_metrics = _extract_known_label_values(
        lines,
        [
            "\u5f15\u6d41\u4ef7",
            "\u66dd\u5149\u91cf",
            "\u6d4f\u89c8\u4eba\u6570",
            "\u652f\u4ed8\u8f6c\u5316\u7387",
            "\u652f\u4ed8\u8ba2\u5355\u6570",
            "\u9500\u552e\u95f4\u591c",
            "\u9500\u552e\u5747\u4ef7",
            "\u9500\u552e\u989d",
            "\u5165\u4f4f\u95f4\u591c",
            "\u6ee1\u623f\u7387",
            "\u540c\u884c\u6392\u540d",
            "\u8bbf\u95ee\u4eba\u6570",
            "\u8ba2\u5355\u91cf",
            "\u6d41\u91cf",
            "\u8bc4\u4ef7\u5206",
            "\u670d\u52a1\u5206",
        ],
    )
    return {"metrics": generic_metrics}


def parse_meituan_promotion_coupon_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    label_to_field = {
        "\u6d3b\u52a8\u540d\u79f0": "activity_name",
        "\u6d3b\u52a8\u6807\u9898": "activity_name",
        "\u72b6\u6001": "status",
        "\u62a5\u540d\u65f6\u95f4": "registration_time",
        "\u6d3b\u52a8\u65f6\u95f4": "activity_time",
        "\u5238\u7c7b\u578b": "coupon_type",
        "\u9884\u7b97": "budget",
        "\u6d88\u8017": "spend",
        "\u66dd\u5149": "impressions",
        "\u8ba2\u5355": "orders",
        "\u652f\u4ed8\u8ba2\u5355": "orders",
        "\u6838\u9500": "redeemed",
        "\u53d1\u653e": "issued",
    }
    activities: list[dict[str, str]] = []
    current: dict[str, str] = {}
    labels = set(label_to_field)
    for index, line in enumerate(lines[:-1]):
        field = label_to_field.get(line)
        if not field:
            continue
        value = _next_non_label_value(lines, index + 1, labels, max_hops=3)
        if value is None:
            continue
        if field == "activity_name" and current:
            activities.append(current)
            current = {}
        current[field] = value
    if current:
        activities.append(current)
    if not activities:
        real_activity = _parse_real_meituan_coupon_activity(lines)
        if real_activity:
            activities.append(real_activity)

    recognized = bool(activities) or "\u795e\u5238\u6d3b\u52a8" in lines
    if not recognized:
        return {
            "status": "needs_review",
            "summary": "\u7f8e\u56e2\u4fc3\u9500\u63a8\u5e7f\u795e\u5238\u9875\u7ed3\u6784\u672a\u8bc6\u522b\uff0c\u8bf7\u4eba\u5de5\u590d\u6838\u3002",
            "platform": "meituan",
            "dataset": "promotion_coupon",
            "data": {
                "coupon_activities": activities,
                "visible_lines": _bounded_visible_lines(lines),
            },
            "evidence": _meituan_dataset_evidence(
                parser=_MEITUAN_PROMOTION_COUPON_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }

    return {
        "status": "success",
        "summary": "\u5df2\u8bfb\u53d6\u7f8e\u56e2\u4fc3\u9500\u63a8\u5e7f\u795e\u5238\u6d3b\u52a8\u5173\u952e\u4fe1\u606f\u3002",
        "platform": "meituan",
        "dataset": "promotion_coupon",
        "data": {
            "coupon_activities": activities,
            "visible_lines": _bounded_visible_lines(lines),
        },
        "evidence": _meituan_dataset_evidence(
            parser=_MEITUAN_PROMOTION_COUPON_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


# 推广通页面标签 -> 输出键（财务概览三条去掉「推广通」前缀，卡片组标题已含平台/产品名）。
_MEITUAN_AD_CPC_FINANCE_LABELS = {
    "推广通昨日总花费(元)": "昨日总花费(元)",
    "推广通今日总花费(元)": "今日总花费(元)",
    "推广通账户余额(元)": "账户余额(元)",
}
_MEITUAN_AD_CPC_OVERVIEW_LABELS = (
    "花费(元)",
    "曝光(次)",
    "点击(次)",
    "今日预算(元)",
    "推广中(个)",
    "待优化(个)",
)
_MEITUAN_AD_CPC_VALUE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_meituan_ad_cpc_text(text: str, *, current_url: str) -> dict[str, Any]:
    """解析推广通总览页内容帧（2026-09-11 实测结构）。

    财务概览（昨日/今日总花费、账户余额）与推广概览（花费/曝光/点击/
    今日预算/推广中/待优化）的标签行下一行即数值。「推广中(个)」>0 视为
    已开通投放（未开通店铺该值为 0，页面结构相同，读取仍算 success）。
    """

    lines = _clean_meituan_text_lines(text)

    def _numeric_value_after(index: int) -> str | None:
        if index + 1 >= len(lines):
            return None
        candidate = lines[index + 1].strip()
        if candidate and _MEITUAN_AD_CPC_VALUE_RE.fullmatch(candidate):
            return candidate
        return None

    finance: dict[str, str] = {}
    overview: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line in _MEITUAN_AD_CPC_FINANCE_LABELS:
            value = _numeric_value_after(index)
            if value is not None:
                finance[_MEITUAN_AD_CPC_FINANCE_LABELS[line]] = value
        elif line in _MEITUAN_AD_CPC_OVERVIEW_LABELS:
            value = _numeric_value_after(index)
            if value is not None:
                overview[line] = value

    activated_count = overview.get("推广中(个)")
    try:
        running_count = int(str(activated_count).replace(",", ""))
    except (TypeError, ValueError):
        running_count = 0
    data = {
        "finance_overview": finance,
        "promotion_overview": overview,
        "activated": running_count > 0,
        "visible_lines": _bounded_visible_lines(lines),
    }
    if not finance and not overview:
        return {
            "status": "needs_review",
            "summary": "美团推广通页面结构未识别，请人工复核是否已开通或页面已改版。",
            "platform": "meituan",
            "dataset": "ad_cpc",
            "data": data,
            "evidence": _meituan_dataset_evidence(
                parser=_MEITUAN_AD_CPC_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "success",
        "summary": (
            "已读取美团推广通广告数据。"
            if data["activated"]
            else "美团推广通未开通投放（推广中 0 个）。"
        ),
        "platform": "meituan",
        "dataset": "ad_cpc",
        "data": data,
        "evidence": _meituan_dataset_evidence(
            parser=_MEITUAN_AD_CPC_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _normalize_meituan_dataset(dataset: str) -> str:
    normalized = (dataset or "").strip()
    aliases = {
        "data_center": "data_center_home",
        "data_center_index": "data_center_home",
        "promotion_manage": "promotion_coupon",
        "marketplatform": "promotion_coupon",
        "comment_manage": "comment_manage_home",
        "review_manage": "comment_manage_home",
    }
    return aliases.get(normalized, normalized)


# 同行分析-同行榜单四榜：榜名 -> 第一张榜表指标列名（文本流「排名/酒店名称/<指标>/营销动作」）。
_MEITUAN_PEER_RANKING_TABS = (
    ("入住榜", "入住间夜"),
    ("销售榜", "销售间夜"),
    ("流量榜", "曝光量"),
    ("转化榜", "浏览转化率"),
)


def _meituan_peer_own_keys(own_hotel: str) -> list[str]:
    """本店匹配键：完整店名优先，去括号主体兜底。

    2026-09-11 实测：立瑞店名「云州澜江路立瑞酒店」按旧逻辑截前 4 字
    「云州江汉」，同行圈多家酒店名都含该前缀（如 云州澜江路市立医院…），
    会匹配到别家酒店的名次；榜单行内是完整店名，必须整名匹配。
    """

    name = re.sub(r"\s+", "", str(own_hotel or ""))
    if not name:
        return []
    keys = [name]
    before_paren = re.split(r"[（(]", name)[0]
    if before_paren and before_paren != name:
        keys.append(before_paren)
    return keys


def _parse_meituan_peer_boards(
    tables: list[dict[str, Any]], own_hotel: str
) -> list[dict[str, str]]:
    """解析同行榜单子榜（2026-09-02 实测结构）。

    每个榜签下两张子榜：表头表（th 行，第3列为指标名）+ 数据表（td 行，
    本店行名次列有数字；前三名渲染为图标/空白，完整表最大名次=榜内总数）。
    返回按 DOM 顺序的子榜列表 [{metric, rank, total, rank_text, rank_source}]。
    """
    own_keys = _meituan_peer_own_keys(own_hotel)
    if not own_keys:
        return []
    boards: list[dict[str, str]] = []
    pending_metric = ""
    for table in tables:
        header = [str(c or "").strip() for c in (table.get("header") or [])]
        rows = [r for r in (table.get("rows") or []) if any(r)]
        if header and not rows:
            # 表头表：第3列为子榜指标名（排名/酒店名称/<指标>/营销动作）
            if len(header) >= 3 and header[0] == "排名":
                pending_metric = header[2]
            continue
        if not rows:
            continue
        board = _parse_meituan_peer_rank_rows(rows, own_keys)
        if not board:
            continue
        board["metric"] = pending_metric or board.get("metric") or ""
        pending_metric = ""
        boards.append(board)
    return boards


def _parse_meituan_peer_rank_rows(
    rows: list[list[str]], own_keys: list[str]
) -> dict[str, str]:
    """单张子榜数据表：本店名次（数字优先、行序兜底）与榜内总数（最大名次）。"""

    def _is_own(joined: str) -> bool:
        return any(key in joined for key in own_keys)

    rank = ""
    rank_source = ""
    fallback_rank = ""
    max_rank = 0
    hotel_rows: list[list[str]] = []
    for cells in rows:
        joined = " ".join(cells)
        if "酒店" in joined or _is_own(joined):
            hotel_rows.append(cells)
        for cell in cells:
            if cell.isdigit() and int(cell) > 0:
                max_rank = max(max_rank, int(cell))
        if rank or not _is_own(joined):
            continue
        for cell in cells:
            if cell.isdigit() and int(cell) > 0:
                rank = cell
                rank_source = "cell"
                break
    if not rank:
        for position, cells in enumerate(hotel_rows):
            if _is_own(" ".join(cells)):
                fallback_rank = str(position + 1)
                break
    if not rank and fallback_rank:
        rank = fallback_rank
        rank_source = "position"
    if not rank:
        return {}
    total = str(max_rank) if max_rank >= int(rank) else ""
    return {
        "rank": rank,
        "total": total,
        "rank_text": f"{rank}/{total}" if total else rank,
        "rank_source": rank_source,
    }


async def _extract_meituan_visible_tables(frame) -> list[dict[str, Any]]:
    """提取 frame 内可见表格：表头单元格（th）与数据单元格（td）分列。"""
    return await frame.evaluate(
        "() => Array.from(document.querySelectorAll('table'))"
        ".filter(t => t.getClientRects().length > 0)"
        ".map(t => ({"
        "header: Array.from(t.querySelectorAll('th'))"
        ".map(th => (th.innerText || '').trim().replace(/\s+/g, ' ')),"
        "rows: Array.from(t.querySelectorAll('tr'))"
        ".map(tr => Array.from(tr.querySelectorAll('td'))"
        ".map(td => (td.innerText || '').trim().replace(/\s+/g, ' ')))"
        ".filter(cells => cells.length > 0)"
        "}))"
    )


async def _read_meituan_peer_rankings(page: Page) -> dict[str, dict[str, Any]]:
    """在数据中心「同行分析」tab 依次点四榜，抓两张子榜的名次/总数。

    每榜先点开全部「查看完整排名」（可见表只含前几名+本店，总数以完整表
    最大名次为准）。失败返回空，不阻断主读取。
    """
    frame = None
    for candidate in page.frames:
        with suppress(Exception):
            text = await candidate.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            if "同行分析" in text and "同行榜单" in text:
                frame = candidate
                break
    if frame is None:
        return {}
    try:
        await frame.locator(
            ".data-center-tab", has_text="同行分析"
        ).first.click()
        await _settle_page(page)
        await page.wait_for_timeout(2500)
    except Exception:  # noqa: BLE001
        return {}
    own_hotel = _infer_meituan_hotel_name(
        _clean_meituan_text_lines(
            await frame.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
        )
    )
    rankings: dict[str, dict[str, Any]] = {}
    for index, (tab_label, _metric) in enumerate(_MEITUAN_PEER_RANKING_TABS):
        boards: list[dict[str, str]] = []
        try:
            if index > 0:
                with suppress(Exception):
                    await page.keyboard.press("Escape")
                await frame.locator(
                    ".mtd-tabs-item", has_text=tab_label
                ).first.click(timeout=4000)
                await page.wait_for_timeout(2000)
            # 两张子榜各有「查看完整排名」入口，全部展开
            for _ in range(2):
                with suppress(Exception):
                    links = frame.get_by_text("查看完整排名", exact=False)
                    if await links.count() == 0:
                        break
                    await links.first.click(timeout=4000)
                    await page.wait_for_timeout(1200)
            # 子榜表异步渲染：数字名次未齐时短暂重试，避免误用行序兜底
            for attempt in range(3):
                tables = await _extract_meituan_visible_tables(frame)
                boards = _parse_meituan_peer_boards(tables, own_hotel or "")
                if boards and all(
                    b.get("rank_source") == "cell" for b in boards
                ):
                    break
                await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            continue
        if boards:
            rankings.setdefault(tab_label, {"boards": boards})
    return rankings

def _parse_real_meituan_coupon_activity(lines: list[str]) -> dict[str, Any]:
    activity: dict[str, Any] = {}
    for line in lines:
        if line == "\u7f8e\u56e2\u795e\u5238":
            activity["activity_name"] = line
        elif line.startswith("\u62db\u5546\u65f6\u95f4\uff1a"):
            activity["registration_time"] = line.split("\uff1a", 1)[1].strip()
        elif line.startswith("\u6d3b\u52a8\u65f6\u95f4\uff1a"):
            activity["activity_time"] = line.split("\uff1a", 1)[1].strip()
        elif line in {"\u62a5\u540d\u6210\u529f", "\u62a5\u540d\u4e2d", "\u5df2\u7ed3\u675f"}:
            activity["status"] = line
        elif line in {"\u5e73\u53f0\u8054\u52a8", "\u5546\u5bb6\u81ea\u8425"}:
            activity["channel"] = line
    metrics = _parse_labeled_metric_section(
        lines,
        start_marker="\u4eca\u65e5\u6570\u636e",
        end_markers=["\u5386\u53f2\u6570\u636e"],
        labels=_meituan_activity_metric_labels(),
        with_units=True,
    )
    if not metrics:
        metrics = _extract_known_label_values_with_units(lines, _meituan_activity_metric_labels())
    if metrics:
        activity["activity_metrics"] = metrics
    return activity if activity.get("activity_name") or activity.get("activity_metrics") else {}


def _meituan_unknown_dataset_payload(
    *,
    dataset: str,
    platform_account_id: str,
    browser_session_id: str,
) -> dict[str, Any]:
    return {
        "status": "needs_review",
        "summary": "\u672a\u652f\u6301\u7684\u7f8e\u56e2 EBK \u6570\u636e\u96c6\uff0c\u8bf7\u5148\u5728\u540e\u7aef\u9002\u914d\u3002",
        "platform": "meituan",
        "dataset": dataset,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "manual_login_required": False,
        "data": {},
        "evidence": _meituan_dataset_evidence(
            parser="unknown",
            current_url="",
            line_count=0,
        ),
    }


def _meituan_human_required_dataset_payload(
    *,
    dataset: str,
    platform_account_id: str,
    browser_session_id: str,
    current_url: str,
    page_state: str,
) -> dict[str, Any]:
    return {
        "status": "human_required",
        "summary": "\u7f8e\u56e2 EBK \u9875\u9762\u9700\u8981\u4eba\u5de5\u767b\u5f55\u6216\u9a8c\u8bc1\u540e\u624d\u80fd\u8bfb\u53d6\u3002",
        "platform": "meituan",
        "dataset": dataset,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "manual_login_required": True,
        "data": {
            "hotel_name": None,
            "visible_lines": [],
        },
        "evidence": {
            "session_port": get_browser_session_store().get(browser_session_id).session_port,
            "profile_path_returned": False,
            "password_returned": False,
            "password_stored": False,
            "automation_bypass": False,
            "current_url": current_url,
            "page_state": page_state,
            "parser": _parser_name_for_meituan_dataset(dataset),
        },
        "next_action": "\u8bf7\u5148\u5728\u7f8e\u56e2 EBK Chrome \u7a97\u53e3\u5b8c\u6210\u4eba\u5de5\u767b\u5f55\u6216\u5b89\u5168\u9a8c\u8bc1\u3002",
    }


def _parser_name_for_meituan_dataset(dataset: str) -> str:
    normalized = _normalize_meituan_dataset(dataset)
    if normalized == "workbench_home":
        return _MEITUAN_WORKBENCH_PARSER
    if normalized == "promotion_coupon":
        return _MEITUAN_PROMOTION_COUPON_PARSER
    if normalized == "comment_manage_home":
        return _MEITUAN_COMMENT_MANAGE_PARSER
    if normalized == "data_center_home":
        return _MEITUAN_DATA_CENTER_PARSER
    if normalized.startswith("data_center_"):
        return _MEITUAN_DATA_CENTER_TAB_PARSER
    return "unknown"


def _meituan_dataset_evidence(
    *,
    parser: str,
    current_url: str,
    line_count: int,
) -> dict[str, Any]:
    return {
        "parser": parser,
        "current_url": current_url,
        "line_count": line_count,
        "profile_path_returned": False,
        "password_returned": False,
        "password_stored": False,
        "automation_bypass": False,
    }


def _meituan_data_center_evidence(*, current_url: str, line_count: int) -> dict[str, Any]:
    return {
        "parser": _MEITUAN_DATA_CENTER_PARSER,
        "current_url": current_url,
        "line_count": line_count,
        "profile_path_returned": False,
        "password_returned": False,
        "password_stored": False,
        "automation_bypass": False,
    }



# T2（2026-08-28 实测）：「活动报名」页（promotion/activity#/index）卡片结构为
# 活动名 → 描述… → 优惠适用时间：X → 动作（免费报名/重新报名/已报名/报名详情）
# → 累积N家参与；同一卡可同时出现「重新报名」按钮与「已报名」标记，取已报名。
_MEITUAN_ACTIVITY_SIGNUP_URL = (
    "https://me.meituan.com/ebooking/merchant/promotion/activity#/index"
)
_MEITUAN_SIGNUP_STATUS_TOKENS = ("免费报名", "重新报名", "已报名", "报名详情")
_MEITUAN_SIGNUP_STATUS_PRIORITY = {
    "已报名": 0,
    "报名详情": 1,
    "重新报名": 2,
    "免费报名": 3,
}
_MEITUAN_SIGNUP_STATUS_NORMALIZE = {
    "已报名": "已报名",
    "报名详情": "已报名",
    "重新报名": "需重新报名",
    "免费报名": "未报名",
}
_MEITUAN_SIGNUP_NAME_HINTS = (
    "【",
    "特惠",
    "特价",
    "优惠",
    "折扣",
    "立减",
    "生财宝",
    "一起订",
)


def _parse_meituan_activity_signup_text(
    text: str, *, current_url: str
) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    signup_lines = lines
    for marker in ("可报名活动", "已报名活动"):
        index = next((i for i, line in enumerate(lines) if line == marker), None)
        if index is not None:
            signup_lines = lines[index:]
            break
    activities: dict[str, dict[str, str]] = {}
    order: list[str] = []
    valid_period = ""
    for index, line in enumerate(signup_lines):
        if line.startswith("优惠适用时间"):
            valid_period = line.split("：", 1)[-1].strip() or line.split(":", 1)[-1].strip()
            continue
        if line not in _MEITUAN_SIGNUP_STATUS_TOKENS:
            continue
        name, name_index = _meituan_signup_activity_name(signup_lines, index)
        if not name:
            continue
        normalized = _MEITUAN_SIGNUP_STATUS_NORMALIZE[line]
        existing = activities.get(name)
        if existing is None:
            order.append(name)
            activities[name] = {
                "name": name,
                "status": normalized,
                "raw_status": line,
                "valid_period": valid_period,
                "description": _meituan_signup_activity_description(
                    signup_lines, name_index, index
                ),
            }
        elif (
            _MEITUAN_SIGNUP_STATUS_PRIORITY[line]
            < _MEITUAN_SIGNUP_STATUS_PRIORITY[existing["raw_status"]]
        ):
            existing["status"] = normalized
            existing["raw_status"] = line
            if valid_period:
                existing["valid_period"] = valid_period
    result_activities = [activities[name] for name in order][:20]
    data: dict[str, Any] = {
        "signup_activities": result_activities,
        "signup_page": "活动报名",
    }
    if result_activities:
        return {
            "status": "success",
            "summary": "已读取美团活动报名清单（含报名状态与优惠适用时间）。",
            "platform": "meituan",
            "dataset": "promotion_coupon",
            "data": data,
            "evidence": _meituan_dataset_evidence(
                parser="meituan_activity_signup_dom_v1",
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    return {
        "status": "needs_review",
        "summary": "美团活动报名页结构未识别，请人工复核。",
        "platform": "meituan",
        "dataset": "promotion_coupon",
        "data": data,
        "evidence": _meituan_dataset_evidence(
            parser="meituan_activity_signup_dom_v1",
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _meituan_signup_activity_name(
    lines: list[str], status_index: int
) -> tuple[str, int]:
    """状态行向前找活动名：跳过 优惠适用时间/累积/状态行；名称行优先取含「【」的
    最近一行（活动名规范带【平台主推】等前缀，描述行可能含「特惠」等提示词），
    无【行时退回最近的提示词行。返回 (名称, 名称行下标)。"""
    fallback = ""
    fallback_index = -1
    for cursor in range(status_index - 1, max(status_index - 8, -1), -1):
        line = lines[cursor]
        if not line:
            continue
        if line.startswith(("优惠适用时间",)):
            continue
        # 累积…参与 / 状态行是上一张卡的边界，跨过即误取他卡名称
        if line.startswith("累积") or line in _MEITUAN_SIGNUP_STATUS_TOKENS:
            break
        if "：" in line and line.split("：", 1)[0] in {"活动类型", "针对人群"}:
            continue
        is_name = line.startswith("【") or any(
            hint in line for hint in _MEITUAN_SIGNUP_NAME_HINTS
        )
        if not is_name:
            continue
        if "【" in line:
            return line, cursor
        if not fallback:
            fallback = line
            fallback_index = cursor
    return fallback, fallback_index


def _meituan_signup_activity_description(
    lines: list[str], name_index: int, status_index: int
) -> str:
    """名称行之后、状态行之前的描述行（2026-09-02 需求：活动内容进卡）；
    跳过 促转化 等运营标签与 优惠适用时间/累积 行，取第一条。"""
    for cursor in range(name_index + 1, status_index):
        line = lines[cursor].strip()
        if not line or len(line) < 6:
            continue
        if line.startswith(("优惠适用时间", "累积", "促转化", "促流量")):
            continue
        if line in _MEITUAN_SIGNUP_STATUS_TOKENS:
            continue
        if "：" in line and line.split("：", 1)[0] in {"活动类型", "针对人群"}:
            continue
        return line
    return ""


def _clean_meituan_text_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in (text or "").replace("\r", "\n").split("\n")
        if line.strip()
    ]


def _line_after(lines: list[str], marker: str) -> str | None:
    try:
        index = lines.index(marker)
    except ValueError:
        return None
    return lines[index + 1] if index + 1 < len(lines) else None


def _line_after_any(lines: list[str], markers: list[str]) -> str | None:
    for marker in markers:
        value = _line_after(lines, marker)
        if value:
            return value
    return None


def _infer_meituan_hotel_name(lines: list[str]) -> str | None:
    for line in lines[:20]:
        if "\u9152\u5e97" in line and not line.startswith("\u5f53\u524d\u89d2\u8272"):
            return line
    return None


_MEITUAN_METRIC_UNITS = frozenset({"\u5143", "\u4e2a", "\u6b21", "\u4eba", "\u5355", "%"})


def _extract_known_label_values(lines: list[str], labels: list[str]) -> dict[str, str]:
    label_set = set(labels)
    values: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line not in label_set:
            continue
        value = _next_non_label_value(lines, index + 1, label_set)
        if value is not None:
            values[line] = value
    return values


def _extract_known_label_values_with_units(lines: list[str], labels: list[str]) -> dict[str, str]:
    """Like label/value extraction, but joins a trailing unit line (e.g. 1,582.28 + 元)."""

    label_set = set(labels)
    values: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line not in label_set:
            continue
        for hop, candidate in enumerate(lines[index + 1 : index + 1 + 3]):
            if candidate in label_set:
                break
            if not candidate.strip():
                continue
            value = candidate
            unit_index = index + 1 + hop + 1
            if unit_index < len(lines) and lines[unit_index] in _MEITUAN_METRIC_UNITS:
                value = f"{value}{lines[unit_index]}"
            values[line] = value
            break
    return values


def _extract_known_metric_values(lines: list[str], labels: list[str]) -> dict[str, str]:
    """Extract numeric metric values, skipping subtitle labels like 销售间夜占比."""

    label_set = set(labels)
    values: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line not in label_set:
            continue
        value = _next_metric_value(lines, index + 1, label_set)
        if value is not None:
            values[line] = value
    return values


_METRIC_VALUE_BOUNDARY = {
    "\u8f83\u4e0a\u5468",
    "\u8f83\u6628\u65e5",
    "\u540c\u884c\u6392\u540d",
    "\u540c\u884c\u5747\u503c",
    "\u73af\u6bd4",
    "\u540c\u6bd4",
}


def _looks_like_metric_subtitle(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return True
    if text.endswith(("\u5360\u6bd4", "\u5206\u6790", "\u8868\u73b0", "\u8d8b\u52bf", "\u63d0\u9192")):
        return True
    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z_/·\-\s]+", text) and not re.search(r"\d", text):
        return True
    return False


def _metric_value_score(line: str) -> int:
    text = str(line or "").strip()
    score = 0
    if re.search(r"\d", text):
        score += 2
    if any(token in text for token in ("\u5143", "%", "\u95f4\u591c", "\u5355", "\u4eba", "\u6b21")):
        score += 3
    if re.search(r"\d+\.\d+", text):
        score += 1
    if re.fullmatch(r"\d{1,2}", text):
        score -= 1
    return score


def _next_metric_value(
    lines: list[str],
    start_index: int,
    labels: set[str],
    *,
    max_hops: int = 6,
) -> str | None:
    best: str | None = None
    best_score = -999
    for candidate in lines[start_index : start_index + max_hops]:
        if candidate in labels or candidate in _METRIC_VALUE_BOUNDARY:
            break
        if _looks_like_metric_subtitle(candidate):
            continue
        if not re.search(r"\d", candidate):
            continue
        score = _metric_value_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score
        if best_score >= 5:
            break
    return best


def _next_non_label_value(
    lines: list[str],
    start_index: int,
    labels: set[str],
    *,
    max_hops: int = 2,
) -> str | None:
    for candidate in lines[start_index : start_index + max_hops]:
        if candidate in labels:
            return None
        if candidate.strip():
            return candidate
    return None


def _bounded_visible_lines(lines: list[str], *, limit: int = 160) -> list[str]:
    return lines[:limit]


def _find_first_line(
    lines: list[str],
    *,
    prefix: str | None = None,
    contains: str | None = None,
) -> str | None:
    for line in lines:
        if prefix is not None and line.startswith(prefix):
            return line
        if contains is not None and contains in line:
            return line
    return None


def _extract_period_tabs(lines: list[str]) -> list[str]:
    labels = ["今日实时", "昨日", "近7天", "近30天"]
    return [label for label in labels if label in lines]


def _parse_metric_chunks(
    section: list[str], metric_names: list[str]
) -> dict[str, dict[str, str | None]]:
    """指标区块通用解析：每个指标取到下一个指标前的 chunk，
    提取 值/较上期(较上周)/同行排名/同行均值（2026-09-01 实测页签结构）。"""
    metrics: dict[str, dict[str, str | None]] = {}
    for metric_name in metric_names:
        if metric_name not in section:
            continue
        start = section.index(metric_name)
        end = min(
            [
                index
                for index in (
                    section.index(other, start + 1)
                    for other in metric_names
                    if other in section[start + 1 :]
                )
                if index > start
            ]
            or [len(section)]
        )
        chunk = section[start:end]
        metrics[metric_name] = {
            "value": chunk[1] if len(chunk) > 1 else None,
            "week_over_week": _comparison_after_marker(chunk),
            "peer_rank": _value_after_marker(chunk, "同行排名"),
            "peer_average": _value_after_marker(chunk, "同行均值"),
        }
    return metrics


def _parse_basic_operation_metrics(lines: list[str]) -> dict[str, dict[str, str | None]]:
    section = _slice_between(lines, "基础经营", "项目表现")
    return _parse_metric_chunks(section, _meituan_workbench_metric_labels())


def _parse_project_performance(lines: list[str]) -> dict[str, dict[str, dict[str, str | None]]]:
    section = _slice_between(lines, "项目表现", "营销热点提醒")
    project_names = ["获客宝", "推广通", "门店会员"]
    metric_markers = [
        "获得获客币",
        "浏览量",
        "花费获客币",
        "花费",
        "获得返现",
        "获得红包",
        "节省佣金",
        "会员间夜",
        "门店会员数",
        "HOS分",
        "信息分",
        "评价分",
    ]
    result: dict[str, dict[str, dict[str, str | None]]] = {}
    for project_name in project_names:
        if project_name not in section:
            continue
        start = section.index(project_name)
        end = min(
            [
                index
                for index in (
                    section.index(other, start + 1)
                    for other in project_names
                    if other in section[start + 1 :]
                )
                if index > start
            ]
            or [len(section)]
        )
        chunk = section[start:end]
        project_metrics: dict[str, dict[str, str | None]] = {}
        for marker in metric_markers:
            if marker not in chunk:
                continue
            index = chunk.index(marker)
            value = chunk[index + 1] if index + 1 < len(chunk) else None
            benchmark = _next_line_matching(chunk[index + 1 :], prefix="同行标杆")
            if benchmark is None:
                benchmark = _next_line_matching(chunk[index + 1 :], prefix="同行排名")
            project_metrics[marker] = {"value": value, "benchmark": benchmark}
        result[project_name] = project_metrics
    return result


def _parse_marketing_hotspots(lines: list[str]) -> list[dict[str, str | None]]:
    section = _slice_between(lines, "营销热点提醒", "昨日同行排名")
    events: list[dict[str, str | None]] = []
    for index, line in enumerate(section):
        if " 至 " not in line:
            continue
        title = section[index - 1] if index > 0 else None
        countdown = None
        for candidate in reversed(section[max(0, index - 4) : index - 1]):
            if candidate.isdigit():
                countdown = candidate
                break
        if title:
            events.append(
                {
                    "title": title,
                    "date_range": line,
                    "countdown_days": countdown,
                }
            )
    return events


def _parse_peer_rankings(lines: list[str]) -> list[dict[str, str]]:
    section = _slice_between(lines, "昨日同行排名", "看懂数据")
    rankings: list[dict[str, str]] = []
    for index, line in enumerate(section[:-1]):
        if not line.isdigit():
            continue
        hotel = section[index + 1]
        if hotel.isdigit() or "\t" in hotel or hotel in {"查看同行动态"}:
            continue
        rankings.append({"rank": line, "hotel": hotel})
    return rankings


def _slice_between(lines: list[str], start_marker: str, end_marker: str) -> list[str]:
    try:
        start = lines.index(start_marker) + 1
    except ValueError:
        return []
    try:
        end = lines.index(end_marker, start)
    except ValueError:
        end = len(lines)
    return lines[start:end]


def _value_after_marker(lines: list[str], marker: str) -> str | None:
    try:
        index = lines.index(marker)
    except ValueError:
        return None
    value = lines[index + 1] if index + 1 < len(lines) else None
    # 页面缺失指标用「-」占位（如实时页签的曝光量/入住间夜），不是数值。
    if value is not None and value.strip() in {"-", "–", "—"}:
        return None
    return value


def _comparison_after_marker(lines: list[str]) -> str | None:
    """「今日实时」页签对比标记为「较上期」，默认（昨日）页签为「较上周」，
    两者都可能是幅度值（2026-09-01 实测）。"""
    for marker in ("较上期", "较上周"):
        value = _value_after_marker(lines, marker)
        if value:
            return value
    return None


def _next_line_matching(lines: list[str], *, prefix: str) -> str | None:
    for line in lines:
        if line.startswith(prefix):
            return line
    return None


def parse_meituan_workbench_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    # 新版工作台首次进入会要求选择角色（前台/店长/老板），选完才渲染经营数据；
    # 选择持久化前每次读取都停在这个页面。给出可操作提示而不是笼统的结构未识别。
    if "\u8bf7\u9009\u62e9\u4f60\u7684\u89d2\u8272" in text or (
        "\u89d2\u8272\u7528\u4e8e\u751f\u6210\u5de5\u4f5c\u53f0" in text
        and "\u6211\u9009\u597d\u4e86" in text
    ):
        return {
            "status": "needs_review",
            "summary": (
                "\u7f8e\u56e2\u65b0\u7248\u5de5\u4f5c\u53f0\u9700\u8981\u9996\u6b21\u9009\u62e9\u89d2\u8272"
                "\uff08\u524d\u53f0/\u5e97\u957f/\u8001\u677f\uff09\uff0c\u8bf7\u5728\u7cfb\u7edf\u64cd\u4f5c\u53f0"
                "\u6253\u5f00\u7f8e\u56e2 EBK \u767b\u5f55\u7a97\u53e3\u5b8c\u6210\u4e00\u6b21\u9009\u62e9\u3002"
            ),
            "platform": "meituan",
            "dataset": "workbench_home",
            "data": {"visible_lines": _bounded_visible_lines(lines)},
            "evidence": _meituan_dataset_evidence(
                parser=_MEITUAN_WORKBENCH_PARSER,
                current_url=current_url,
                line_count=len(lines),
            ),
        }
    metric_labels = _meituan_workbench_metric_labels()
    pending_cards = _extract_known_label_values(
        lines,
        [
            "\u5f85\u786e\u8ba4\u8ba2\u5355",
            "\u5f85\u5165\u4f4f",
            "\u5f85\u5904\u7406\u8ba2\u5355",
            "\u5f85\u63a5\u5355",
            "\u5f85\u652f\u4ed8",
        ],
    )
    business_summary = _extract_known_label_values(lines, metric_labels)
    realtime_data = _parse_labeled_metric_section(
        lines,
        start_marker="\u5b9e\u65f6\u6570\u636e",
        end_markers=["\u6628\u65e5\u6570\u636e", "\u8fdd\u7ea6\u770b\u677f", "HOS\u5206"],
        labels=metric_labels,
    )
    yesterday_data = _parse_labeled_metric_section(
        lines,
        start_marker="\u6628\u65e5\u6570\u636e",
        end_markers=["\u8fdd\u7ea6\u770b\u677f", "HOS\u5206"],
        labels=metric_labels,
    )
    if not realtime_data:
        realtime_data = _parse_labeled_metric_section(
            lines,
            start_marker="\u8fdd\u7ea6\u770b\u677f",
            end_markers=["HOS\u5206", "HOS\u5206\u63d0HOS\u5206\uff0c\u6da8\u8ba2\u5355"],
            labels=metric_labels,
        )
    hos_score = _parse_meituan_hos_score(lines)
    hotel_name = _line_after_any(lines, ["\u95e8\u5e97"]) or _infer_meituan_hotel_name(lines)
    data = {
        "hotel_name": hotel_name,
        "pending_cards": pending_cards,
        "business_summary": business_summary,
        "realtime_data": realtime_data,
        "yesterday_data": yesterday_data,
        "hos_score": hos_score,
        "visible_lines": _bounded_visible_lines(lines),
    }
    recognized = bool(hotel_name) and bool(
        pending_cards or business_summary or realtime_data or yesterday_data or hos_score
    )
    return {
        "status": "success" if recognized else "needs_review",
        "summary": (
            "\u5df2\u8bfb\u53d6\u7f8e\u56e2 EBK \u9996\u9875\u5b9e\u65f6\u3001\u6628\u65e5\u548c HOS \u6570\u636e\u3002"
            if recognized
            else "\u7f8e\u56e2 EBK \u9996\u9875\u7ed3\u6784\u672a\u8bc6\u522b\uff0c\u8bf7\u4eba\u5de5\u590d\u6838\u3002"
        ),
        "platform": "meituan",
        "dataset": "workbench_home",
        "data": data,
        "evidence": _meituan_dataset_evidence(
            parser=_MEITUAN_WORKBENCH_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def parse_meituan_comment_manage_text(text: str, *, current_url: str) -> dict[str, Any]:
    lines = _clean_meituan_text_lines(text)
    review_status = _extract_known_label_values(
        lines,
        [
            "\u597d\u8bc4\u7387",
            "\u5f85\u56de\u590d\u8bc4\u4ef7",
            "\u5dee\u8bc4",
            "\u8fd130\u5929\u65b0\u589e\u8bc4\u4ef7",
            "\u65b0\u589e\u8bc4\u4ef7",
            "\u6709\u56fe\u8bc4\u4ef7",
        ],
    )
    hotel_name = _line_after_any(lines, ["\u95e8\u5e97"]) or _infer_meituan_hotel_name(lines)
    review_score = _numeric_value_after_marker(lines, "\u8bc4\u4ef7\u5f97\u5206")
    if review_score is None:
        review_score = _numeric_value_after_marker(lines, "\u8bc4\u4ef7\u5206")
    review_total = _parse_meituan_review_total(lines)
    review_dimensions = _parse_labeled_metric_section(
        lines,
        start_marker="\u603b\u5f97\u5206",
        end_markers=["\u6e29\u99a8\u63d0\u793a", "\u5e38\u7528\u8bc4\u4ef7\u5de5\u5177\uff1a", "\u8bc4\u4ef7\u60c5\u51b5"],
        labels=["\u670d\u52a1", "\u4f4d\u7f6e", "\u8bbe\u65bd", "\u536b\u751f"],
    )
    review_periods = _parse_meituan_review_periods(lines)
    review_filters = _parse_meituan_review_filters(lines)
    recognized = bool(
        review_score or review_status or review_total or review_dimensions or review_periods or review_filters
    )
    return {
        "status": "success" if recognized else "needs_review",
        "summary": (
            "\u5df2\u8bfb\u53d6\u7f8e\u56e2\u8bc4\u4ef7\u7ba1\u7406\u5f97\u5206\u548c\u8bc4\u4ef7\u60c5\u51b5\u3002"
            if recognized
            else "\u7f8e\u56e2\u8bc4\u4ef7\u7ba1\u7406\u9875\u7ed3\u6784\u672a\u8bc6\u522b\uff0c\u8bf7\u4eba\u5de5\u590d\u6838\u3002"
        ),
        "platform": "meituan",
        "dataset": "comment_manage_home",
        "data": {
            "hotel_name": hotel_name,
            "review_score": review_score,
            "review_total": review_total,
            "review_dimensions": review_dimensions,
            "review_status": review_status,
            "review_periods": review_periods,
            "review_filters": review_filters,
            "visible_lines": _bounded_visible_lines(lines),
        },
        "evidence": _meituan_dataset_evidence(
            parser=_MEITUAN_COMMENT_MANAGE_PARSER,
            current_url=current_url,
            line_count=len(lines),
        ),
    }


def _parse_meituan_review_total(lines: list[str]) -> str | None:
    for line in lines:
        match = re.match(r"^\u5171\u8ba1([0-9,]+)\u6761\u8bc4\u4ef7$", line)
        if match:
            return match.group(1)
    return None


def _parse_meituan_review_periods(lines: list[str]) -> dict[str, dict[str, str]]:
    period_labels = ["30\u5929", "180\u5929", "365\u5929"]
    metric_labels = [
        "\u65b0\u589e\u6709\u6548\u8bc4\u4ef7\u6570",
        "\u8bc4\u4ef7\u6570\u8868\u73b0",
        "\u5dee\u8bc4\u7387",
        "\u5dee\u8bc4\u7387\u8868\u73b0",
    ]
    periods: dict[str, dict[str, str]] = {}
    for index, line in enumerate(lines):
        if line not in period_labels:
            continue
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            if lines[next_index] in period_labels or lines[next_index] == "AI \u52a9\u7406":
                end = next_index
                break
        metrics = _extract_known_label_values(lines[index + 1 : end], metric_labels)
        if metrics:
            periods[line] = metrics
    return periods


def _parse_meituan_review_filters(lines: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^(.+)\(([0-9,]+)\)$", line)
        if not match:
            continue
        label = match.group(1).strip()
        if label in {
            "\u5168\u90e8\u8bc4\u4ef7",
            "\u6709\u56fe\u8bc4\u4ef7",
            "\u5dee\u8bc4",
            "\u672a\u56de\u590d",
            "\u5df2\u6295\u8bc9",
        }:
            filters[label] = match.group(2)
    return filters


def _meituan_workbench_metric_labels() -> list[str]:
    return [
        "\u6d4f\u89c8\u4eba\u6570",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "\u9500\u552e\u989d",
        "\u5165\u4f4f\u95f4\u591c",
        "\u9500\u552e\u95f4\u591c",
        "\u66dd\u5149\u91cf",
        "\u5f15\u6d41\u4ef7",
        "\u652f\u4ed8\u8f6c\u5316\u7387",
        "\u6ee1\u623f\u7387",
        "\u6d41\u5931\u8ba2\u5355\u91cf",
    ]


def _meituan_activity_metric_labels() -> list[str]:
    return [
        "\u66dd\u5149\u91cf",
        "\u6d4f\u89c8\u4eba\u6570",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "\u9500\u552e\u989d",
        "\u6838\u9500",
        "\u53d1\u653e",
        "\u6d88\u8017",
        "\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d",
        "\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570",
        "\u7528\u795e\u5238\u6838\u9500\u91d1\u989d",
        "\u7528\u795e\u5238\u79bb\u5e97\u8ba2\u5355\u6570",
        "\u7528\u795e\u5238\u6838\u9500\u5ba2\u5355\u4ef7",
    ]


def _parse_labeled_metric_section(
    lines: list[str],
    *,
    start_marker: str,
    end_markers: list[str],
    labels: list[str],
    with_units: bool = False,
) -> dict[str, str]:
    try:
        start = lines.index(start_marker) + 1
    except ValueError:
        return {}
    end = len(lines)
    for marker in end_markers:
        with suppress(ValueError):
            marker_index = lines.index(marker, start)
            end = min(end, marker_index)
    section = lines[start:end]
    if with_units:
        return _extract_known_label_values_with_units(section, labels)
    return _extract_known_label_values(section, labels)


def _parse_meituan_hos_score(lines: list[str]) -> dict[str, str]:
    score_index: int | None = None
    with suppress(ValueError):
        score_index = lines.index("HOS\u5206")
    if score_index is None:
        for index, line in enumerate(lines):
            if _looks_like_decimal_score(line) and _next_line_matching(
                lines[index + 1 : index + 4],
                prefix="\u518d\u63a5\u518d\u5389",
            ):
                score_index = index - 1
                break
    if score_index is None:
        return {}
    value = lines[score_index + 1] if score_index + 1 < len(lines) else None
    if value is None or not _looks_like_decimal_score(value):
        return {}
    result = {"value": value}
    if score_index + 2 < len(lines):
        result["grade_tip"] = lines[score_index + 2]
    message = _next_line_matching(lines[score_index + 2 : score_index + 8], prefix="HOS\u5206")
    if message:
        result["message"] = message
    return result


def _looks_like_decimal_score(value: str) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return 0 <= number <= 5


def _numeric_value_after_marker(lines: list[str], marker: str) -> str | None:
    try:
        start = lines.index(marker) + 1
    except ValueError:
        return None
    for candidate in lines[start : start + 6]:
        if _looks_like_decimal_score(candidate):
            return candidate
    return None


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


def _meituan_cache_key(*, platform_account_id: str, browser_session_id: str) -> str:
    return f"{platform_account_id}:{browser_session_id}:meituan-ebooking"


def _meituan_os_chrome_launch_args(
    *,
    chrome_path: Path,
    profile_dir: Path,
    session_port: int,
    login_url: str,
) -> list[str]:
    # Launch real installed Chrome as a normal OS process, then attach via CDP.
    # Do not use Playwright launch_persistent_context here: that path is more
    # likely to trip Meituan slider risk checks even for manual human drags.
    return [
        str(chrome_path),
        f"--remote-debugging-port={session_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        login_url,
    ]


def _resolve_installed_chrome_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


async def _wait_for_cdp_ready(session_port: int, *, timeout_seconds: float = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{session_port}/json/version"
    while time.monotonic() < deadline:
        ready = await asyncio.to_thread(_probe_cdp_version, url)
        if ready:
            return True
        await asyncio.sleep(0.25)
    return False


def _probe_cdp_version(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - local CDP only
            return int(getattr(response, "status", 200) or 200) == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _select_meituan_page(pages: list[Page]) -> Page | None:
    open_pages = [page for page in pages if not page.is_closed()]
    if not open_pages:
        return None

    authenticated = [
        page
        for page in open_pages
        if _classify_meituan_page_state(page.url, "") == "authenticated"
    ]
    if authenticated:
        return authenticated[0]

    meituan_pages = [page for page in open_pages if _is_meituan_url(page.url)]
    if meituan_pages:
        return meituan_pages[0]
    return open_pages[0]


def _is_meituan_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return "meituan.com" in host or "dianping.com" in host


def _classify_meituan_page_state(url: str, body_text: str) -> str:
    normalized_url = (url or "").lower()
    text = (body_text or "").lower()
    login_url = "/login" in normalized_url or normalized_url.endswith("login.html")
    human_markers = (
        "验证码",
        "短信",
        "扫码",
        "二维码",
        "人机验证",
        "风控",
        "安全验证",
        "滑块",
        "请向右拖动",
    )
    if login_url and any(marker.lower() in text for marker in human_markers):
        return "human_required"
    if login_url:
        return "login"
    # Logged-in Meituan EBK may land on eb.meituan.com or me.meituan.com workbench.
    if ("meituan.com" in normalized_url or "dianping.com" in normalized_url) and (
        "ebooking" in normalized_url
        or "ebk" in normalized_url
        or "workbench" in normalized_url
        or "merchant" in normalized_url
    ):
        return "authenticated"
    if any(marker.lower() in text for marker in human_markers):
        return "human_required"
    return "human_required"


def _resolve_meituan_pricing_target_url(target_url: str | None) -> str | None:
    """美团写价只接受 meituan.com 域名下的 http(s) 价格页链接。"""
    cleaned = str(target_url or "").strip()
    if not cleaned:
        # B5.1 实测：默认执行面为「产品管理 → 批量改房价」页。
        from app.domain.browser.pricing_page_product_selection import (
            MEITUAN_PRICING_PAGE_URL,
        )

        return MEITUAN_PRICING_PAGE_URL
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host != "meituan.com" and not host.endswith(".meituan.com"):
        return None
    return cleaned


def _resolve_meituan_room_calendar_url(target_url: str | None) -> str | None:
    """美团查房价只读面：默认「房价房量日历」页；自定义链接须是 meituan.com。"""

    cleaned = str(target_url or "").strip()
    if not cleaned:
        # 2026-09-23 实测：批量改房价页改版后无价格，现价在日历页产品子行。
        from app.domain.browser.pricing_page_product_selection import (
            MEITUAN_ROOM_CALENDAR_URL,
        )

        return MEITUAN_ROOM_CALENDAR_URL
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host != "meituan.com" and not host.endswith(".meituan.com"):
        return None
    return cleaned


def _meituan_pricing_invalid_url_payload(
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    target_url: str | None,
) -> dict[str, Any]:
    return {
        "status": "page_structure_changed",
        "summary": "美团价格页写入只接受 meituan.com 域名下的 EBK 价格页链接。",
        "platform": "meituan",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "meituan_ebooking_pricing_page_write",
            "actor_id": actor_id,
            "execute_request_id": execute_request.get("request_id"),
            "invalid_target_url": target_url,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        "evidence": {
            "invalid_target_url": target_url,
            "browser_write_invoked": False,
            "ota_submit_invoked": False,
            "automation_bypass": False,
        },
        "next_action": "请提供有效的 meituan.com EBK 价格页链接。",
    }


async def _settle_page(page: Page) -> None:
    with suppress(Exception):
        await page.wait_for_timeout(800)


async def _safe_visible_text(page: Page) -> str:
    with suppress(Exception):
        return await page.locator("body").inner_text(timeout=3_000)
    return ""


def _has_meituan_ad_cpc_signal(text: str) -> bool:
    normalized = _normalize_visible_text_for_stability(text)
    # 只认内容帧完整渲染的双标记（渲染中途只有财务概览、标签无「推广通」
    # 前缀的瞬态不作为稳定信号）；壳帧/菜单帧的「推广通」字样不算。
    return "财务概览" in normalized and "推广概览" in normalized


async def _safe_meituan_ad_cpc_text(page: Page) -> str:
    """推广通总览页文本：按广告内容标记选帧，双拍稳定即返回。"""

    best_text = ""
    last_normalized_text = ""
    stable_count = 0
    max_attempts = 10
    for attempt in range(max_attempts):
        text = await _read_meituan_ad_cpc_text_once(page)
        normalized_text = _normalize_visible_text_for_stability(text)
        if normalized_text:
            if len(normalized_text) >= len(_normalize_visible_text_for_stability(best_text)):
                best_text = text
            if normalized_text == last_normalized_text:
                stable_count += 1
            else:
                last_normalized_text = normalized_text
                stable_count = 1
            if stable_count >= 2 and _has_meituan_ad_cpc_signal(text):
                return text
        if attempt < max_attempts - 1:
            with suppress(Exception):
                await page.wait_for_timeout(800)
    return best_text or await _safe_visible_text(page)


async def _read_meituan_ad_cpc_text_once(page: Page) -> str:
    candidates: list[tuple[int, str]] = []
    for frame in page.frames:
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=5_000)
            if not text.strip():
                continue
            score = min(len(text), 10_000)
            if _has_meituan_ad_cpc_signal(text):
                score += 15_000
            if _is_meituan_shell_only_text(_normalize_visible_text_for_stability(text)):
                score -= 20_000
            candidates.append((score, text))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return await _safe_visible_text(page)


async def _safe_meituan_dataset_text(page: Page, *, url_marker: str) -> str:
    best_text = ""
    last_normalized_text = ""
    stable_count = 0
    max_attempts = 12
    for attempt in range(max_attempts):
        text = await _read_meituan_dataset_text_once(page, url_marker=url_marker)
        normalized_text = _normalize_visible_text_for_stability(text)
        if normalized_text:
            if len(normalized_text) >= len(_normalize_visible_text_for_stability(best_text)):
                best_text = text
            if normalized_text == last_normalized_text:
                stable_count += 1
            else:
                last_normalized_text = normalized_text
                stable_count = 1
            if stable_count >= 2 and _has_meituan_dataset_signal(text):
                return text
        if attempt < max_attempts - 1:
            with suppress(Exception):
                await page.wait_for_timeout(800)
    if best_text.strip():
        return best_text
    return await _safe_visible_text(page)


def _attach_meituan_today_realtime(
    parsed: dict[str, Any],
    realtime_parsed: dict[str, Any],
    *,
    selected_page_tab: str,
) -> dict[str, Any]:
    """把「今日实时」页签的解析结果挂到 parsed.data（原默认页签指标保留）。

    仅当选中态校验为「今日实时」且实时解析成功时挂载
    ``today_realtime_metrics`` 与 ``today_realtime_evidence``；默认页签
    （昨日口径）的原指标不受影响。
    """

    if str(selected_page_tab or "") != _MEITUAN_REALTIME_TAB_LABEL:
        return parsed
    if not isinstance(realtime_parsed, dict) or realtime_parsed.get("status") != "success":
        return parsed
    realtime_data = (
        realtime_parsed.get("data")
        if isinstance(realtime_parsed.get("data"), dict)
        else {}
    )
    metrics: dict[str, Any] | None = None
    for key in ("basic_operation_metrics", "business_performance", "metrics"):
        value = realtime_data.get(key)
        if isinstance(value, dict) and value:
            metrics = value
            break
    if not metrics:
        return parsed
    data = parsed.setdefault("data", {}) if isinstance(parsed, dict) else {}
    data["today_realtime_metrics"] = metrics
    data["today_realtime_evidence"] = {
        "selected_page_tab": _MEITUAN_REALTIME_TAB_LABEL,
        "page_tabs": ["今日实时", "昨日", "近7天", "近30天"],
        "default_page_tab": "昨日",
    }
    return parsed


async def _find_meituan_realtime_period_frame(page: Page):
    """定位带「今日实时」页签与指标信号的数据内容 frame。"""

    for frame in page.frames:
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=3_000)
            if (
                _MEITUAN_REALTIME_TAB_LABEL in text
                and any(signal in text for signal in _MEITUAN_REALTIME_TAB_METRIC_SIGNALS)
            ):
                return frame
    return None


async def _meituan_realtime_tab_checked(frame) -> bool:
    """DOM 选中态校验：mtd-radio-button-checked 中出现「今日实时」。"""

    with suppress(Exception):
        checked = await frame.evaluate(
            """() => Array.from(
                document.querySelectorAll('.mtd-radio-button-checked')
            ).map(node => (node.textContent || '').trim()).filter(Boolean)"""
        )
        if isinstance(checked, list):
            return _MEITUAN_REALTIME_TAB_LABEL in checked
    return False


def _meituan_realtime_core_metrics_loading(parsed: dict[str, Any] | None) -> bool:
    """核心指标（销售额/支付订单数）均为「-」占位 → 数据异步加载未完成。

    2026-09-02 运营反馈：偶发抓到「-」，人工刷新即恢复——是加载时序问题，
    非页面真实缺数。判定只看这两个核心指标且要求同时占位（单个指标时段性
    占位属正常，不触发重读）。
    """

    if not isinstance(parsed, dict) or parsed.get("status") != "success":
        return False
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    metrics: dict[str, Any] = {}
    for key in ("basic_operation_metrics", "business_performance", "metrics"):
        value = data.get(key)
        if isinstance(value, dict) and value:
            metrics = value
            break
    if not metrics:
        return False

    def _dashed(label: str) -> bool:
        metric = metrics.get(label)
        value = metric.get("value") if isinstance(metric, dict) else metric
        text = str(value or "").strip()
        return text in {"-", "–", "—"} or text.startswith("- ")

    return _dashed("销售额") and _dashed("支付订单数")


async def _read_meituan_today_realtime_block(
    page: Page, *, url_marker: str, dataset: str
) -> dict[str, Any] | None:
    """T1.1：点击「今日实时」页签 → 校验选中态 → 重读解析 → 恢复默认页签。

    页签不存在（如服务分析）、点击失败或选中态校验失败时返回 None，
    不影响默认页签（昨日口径）的原解析结果。
    """

    frame = await _find_meituan_realtime_period_frame(page)
    if frame is None:
        return None
    try:
        await frame.locator(
            "span", has_text=re.compile(f"^{_MEITUAN_REALTIME_TAB_LABEL}$")
        ).first.click(timeout=4_000, force=True)
    except Exception:
        return None
    await page.wait_for_timeout(3_500)
    if not await _meituan_realtime_tab_checked(frame):
        parsed = None
    else:
        text = await _safe_meituan_dataset_text(page, url_marker=url_marker)
        parsed = parse_meituan_dataset_text(text, current_url=page.url, dataset=dataset)
        # 核心指标占位说明异步数据未挂载完（2026-09-02 运营反馈刷新即恢复）；
        # 轮询重读，仍占位才按页面真实状态落库。
        for _ in range(6):
            if not _meituan_realtime_core_metrics_loading(parsed):
                break
            await page.wait_for_timeout(1_500)
            text = await _safe_meituan_dataset_text(page, url_marker=url_marker)
            parsed = parse_meituan_dataset_text(text, current_url=page.url, dataset=dataset)
    # 恢复默认页签（昨日），避免影响后续数据集读取的页面状态。
    with suppress(Exception):
        await frame.locator(
            "span", has_text=re.compile("^昨日$")
        ).first.click(timeout=4_000, force=True)
        await page.wait_for_timeout(1_500)
    if parsed is None:
        return None
    return {"parsed": parsed, "selected_page_tab": _MEITUAN_REALTIME_TAB_LABEL}


async def _read_meituan_dataset_text_once(page: Page, *, url_marker: str) -> str:
    candidates: list[tuple[int, str]] = []
    for frame in page.frames:
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=5_000)
            if not text.strip():
                continue
            score = min(len(text), 10_000)
            normalized_text = _normalize_visible_text_for_stability(text)
            if _is_meituan_shell_only_text(normalized_text):
                score -= 20_000
            elif _has_meituan_dataset_signal(text):
                score += 15_000
            frame_url = (frame.url or "").lower()
            if url_marker and url_marker.lower() in frame_url:
                score += 10_000
            if "ebooking" in frame_url or "meituan" in frame_url:
                score += 1_000
            candidates.append((score, text))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return await _safe_visible_text(page)


def _normalize_visible_text_for_stability(text: str) -> str:
    return "\n".join(_clean_meituan_text_lines(text))


def _has_meituan_dataset_signal(text: str) -> bool:
    normalized = _normalize_visible_text_for_stability(text)
    if _is_meituan_shell_only_text(normalized):
        return False
    markers = (
        "\u95e8\u5e97",
        "\u5de5\u4f5c\u53f0",
        "\u6570\u636e\u4e2d\u5fc3",
        "\u795e\u5238",
        "\u6d3b\u52a8\u540d\u79f0",
        "\u8bc4\u4ef7\u7ba1\u7406",
        "\u8bc4\u4ef7\u5f97\u5206",
        "\u540c\u884c\u5206\u6790",
        "\u57fa\u7840\u7ecf\u8425",
        "闂ㄥ簵",
        "鍩虹缁忚惀",
        "钀ラ攢",
    )
    return any(marker in normalized for marker in markers)


def _is_meituan_shell_only_text(normalized_text: str) -> bool:
    lines = [line.strip() for line in normalized_text.split("\n") if line.strip()]
    if len(lines) > 80:
        return False
    shell_markers = {
        "\u5546\u6237\u5730\u56fe",
        "\u5fae\u4fe1\u516c\u4f17\u53f7",
        "\u624b\u673a\u7aef",
        "\u95e8\u5e97\u6d88\u606f",
        "\u5b66\u4e60\u4e2d\u5fc3",
    }
    business_markers = {
        "\u4ec5\u770b\u672c\u8d26\u53f7\u6570\u636e",
        "\u57fa\u7840\u7ecf\u8425",
        "\u66dd\u5149\u91cf",
        "\u6d4f\u89c8\u4eba\u6570",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "\u6d3b\u52a8\u540d\u79f0",
        "\u62a5\u540d\u65f6\u95f4",
    }
    return bool(shell_markers.intersection(lines)) and not bool(
        business_markers.intersection(lines)
    )


async def _safe_meituan_data_center_text(page: Page) -> str:
    for frame in page.frames:
        if "data-center-pc" not in frame.url and "data-center" not in frame.url:
            continue
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=5_000)
            if text.strip():
                return text
    return await _safe_visible_text(page)


async def _select_meituan_data_center_tab(page: Page, tab_label: str) -> bool:
    for frame in page.frames:
        with suppress(Exception):
            locator = frame.get_by_text(tab_label, exact=True).first
            if await locator.count() > 0:
                await locator.click(timeout=3_000)
                await _settle_page(page)
                return True
    with suppress(Exception):
        locator = page.get_by_text(tab_label, exact=True).first
        if await locator.count() > 0:
            await locator.click(timeout=3_000)
            await _settle_page(page)
            return True
    return False


async def _read_meituan_workbench_tab_metrics(page: Page, *, url_marker: str) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    labels = {
        "\u5b9e\u65f6\u6570\u636e": "realtime_data",
        "\u6628\u65e5\u6570\u636e": "yesterday_data",
    }
    for label, key in labels.items():
        clicked = await _click_meituan_visible_text(page, label)
        if not clicked:
            continue
        text = await _safe_meituan_dataset_text(page, url_marker=url_marker)
        lines = _clean_meituan_text_lines(text)
        metrics = _parse_labeled_metric_section(
            lines,
            start_marker="\u8fdd\u7ea6\u770b\u677f",
            end_markers=["HOS\u5206", "HOS\u5206\u63d0HOS\u5206\uff0c\u6da8\u8ba2\u5355"],
            labels=_meituan_workbench_metric_labels(),
        )
        if not metrics:
            metrics = _parse_labeled_metric_section(
                lines,
                start_marker=label,
                end_markers=["\u8fdd\u7ea6\u770b\u677f", "HOS\u5206"],
                labels=_meituan_workbench_metric_labels(),
            )
        if metrics:
            results[key] = metrics
    return results


async def _read_meituan_promotion_activity_data(page: Page, *, url_marker: str) -> dict[str, Any]:
    clicked = await _click_meituan_visible_text(page, "\u6d3b\u52a8\u6570\u636e")
    if not clicked:
        return {}
    text = await _safe_meituan_dataset_text(page, url_marker=url_marker)
    parsed = parse_meituan_promotion_coupon_text(text, current_url=page.url)
    activities = parsed.get("data", {}).get("coupon_activities", [])
    if not isinstance(activities, list) or not activities:
        return {}
    first_activity = activities[0]
    if not isinstance(first_activity, dict):
        return {}
    if not first_activity.get("activity_metrics"):
        return {}
    return first_activity


async def _read_meituan_activity_signup(page: Page) -> list[dict[str, str]]:
    """导航到「活动报名」页解析报名状态清单；导航/解析失败返回空，不阻断主读取。

    读取完后强制回到数据中心，避免美团活动页运营弹窗/推广 redirect 把浏览器
    停在超团套餐等推广页（2026-09-03 运营反馈：读取完跳 spu/gift-list）。
    """
    try:
        await page.goto(
            _MEITUAN_ACTIVITY_SIGNUP_URL,
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        await _settle_page(page)
        text = await _safe_meituan_dataset_text(page, url_marker="promotion")
        parsed = _parse_meituan_activity_signup_text(text, current_url=page.url)
    except Exception:  # noqa: BLE001 - 活动报名为增强证据源，失败不影响神券主读取
        await _restore_meituan_data_center(page)
        return []
    activities = parsed.get("data", {}).get("signup_activities", [])
    if not isinstance(activities, list):
        await _restore_meituan_data_center(page)
        return []
    await _restore_meituan_data_center(page)
    return [item for item in activities if isinstance(item, dict)]


async def _restore_meituan_data_center(page: Page) -> None:
    """读取后把浏览器恢复回数据中心首页，避免停在美团推广页。"""
    with suppress(Exception):
        await page.goto(
            MEITUAN_EBK_DATA_CENTER_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )


async def _click_meituan_visible_text(page: Page, text: str) -> bool:
    for frame in page.frames:
        with suppress(Exception):
            locator = frame.get_by_text(text, exact=True).first
            if await locator.count() > 0:
                await locator.click(timeout=3_000)
                await _settle_page(page)
                return True
        with suppress(Exception):
            clicked = await _click_meituan_dom_text(frame, text)
            if clicked:
                await _settle_page(page)
                return True
    with suppress(Exception):
        locator = page.get_by_text(text, exact=True).first
        if await locator.count() > 0:
            await locator.click(timeout=3_000)
            await _settle_page(page)
            return True
    with suppress(Exception):
        clicked = await _click_meituan_dom_text(page, text)
        if clicked:
            await _settle_page(page)
            return True
    return False


async def _click_meituan_dom_text(scope: Any, text: str) -> bool:
    return bool(
        await scope.evaluate(
            """
            (label) => {
              const selector = [
                '.mtd-tabs-nav-item',
                '[role="tab"]',
                'button',
                'a'
              ].join(',');
              const candidates = Array.from(document.querySelectorAll(selector));
              const target = candidates.find((element) => {
                const visibleText = (element.innerText || element.textContent || '')
                  .replace(/\\s+/g, ' ')
                  .trim();
                return visibleText === label;
              });
              if (!target) return false;
              target.click();
              return true;
            }
            """,
            text,
        )
    )


def _is_stale_playwright_page_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "target closed" in message
        or "browser has been closed" in message
        or "context or browser has been closed" in message
        or "connection closed" in message
    )


def _is_meituan_navigation_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    navigation_markers = (
        "page.goto",
        "net::err_connection_closed",
        "net::err_",
        "navigation timeout",
        "timeout",
        "target closed",
        "connection closed",
    )
    return any(marker in message for marker in navigation_markers)


def _is_profile_singleton_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "processsingleton" in message
        or "lock file can not be created" in message
        or "profile directory" in message
        or "profile 已被" in message
    )


def get_meituan_ebooking_service() -> MeituanEbookingBrowserService:
    return _MEITUAN_EBOOKING_SERVICE


_MEITUAN_EBOOKING_SERVICE = MeituanEbookingBrowserService()
