from __future__ import annotations

import re
from contextlib import suppress
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.core.settings import get_settings
from app.domain.browser.ctrip_common import (
    _BUSINESS_TERMS,
    _is_ctrip_url,
    _looks_like_login_required,
    _looks_like_verification_required,
)
from app.domain.browser.ctrip_ebooking_results import _LoginCredentials
from app.domain.browser.sessions import get_browser_session_store

CTRIP_EBOOKING_PRICING_CALENDAR_URL = (
    "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar?microJump=true"
)


def _select_ctrip_page(pages: list[Page]) -> Page | None:
    for page in pages:
        if not page.is_closed() and _is_ctrip_url(page.url):
            return page
    for page in pages:
        if not page.is_closed():
            return page
    return None


def _is_stale_playwright_page_error(exc: Exception) -> bool:
    message = str(exc).lower()
    stale_markers = (
        "'nonetype' object has no attribute 'send'",
        "event loop is closed",
        "target page, context or browser has been closed",
        "browser has been closed",
        "context or browser has been closed",
        "connection closed",
        "connection was closed",
        "not connected",
        "page closed",
    )
    # Playwright 管道传输层崩溃时抛的是裸 Exception（rewrite_error 保留原类型），
    # 必须按消息识别，否则死 driver 永远不会触发重置重试，登录窗口只会 500。
    transport_death_markers = (
        "connection closed while reading from the driver",
        "socket.send() raised exception",
    )
    if any(marker in message for marker in transport_death_markers):
        return True
    return isinstance(exc, (PlaywrightError, AttributeError, RuntimeError)) and any(
        marker in message for marker in stale_markers
    )


def _sync_ebk_browser_session_state(
    *,
    browser_session_id: str,
    actor_id: str,
    status: str,
    page_state: str | None,
    login_context: str = "ebk",
) -> None:
    store = get_browser_session_store()
    if status == "success":
        with suppress(KeyError):
            store.mark_authenticated(
                browser_session_id,
                actor_id,
                action=_browser_session_authenticated_action(login_context),
            )
        return
    reason = _ebk_human_action_reason(status=status, page_state=page_state)
    if not reason:
        return
    with suppress(KeyError):
        store.mark_requires_human_action(
            browser_session_id,
            actor_id,
            reason=reason,
            risk_flags=[reason, _browser_session_blocked_flag(login_context)],
            action=_browser_session_blocked_action(login_context),
        )


def _ebk_human_action_reason(*, status: str, page_state: str | None) -> str | None:
    if page_state == "login_required":
        return "login_required"
    if page_state == "verification_required":
        return "verification_required"
    if status == "human_required":
        return "ctrip_ebk_human_required"
    return None


def _browser_session_authenticated_action(login_context: str) -> str:
    return "Ctrip EBK real browser read confirmed an authenticated page."


def _browser_session_blocked_flag(login_context: str) -> str:
    return "ctrip_ebk_read_blocked"


def _browser_session_blocked_action(login_context: str) -> str:
    return "Ctrip EBK real browser read could not use the current login state."


async def _clear_ctrip_login_state(page: Page, *, login_url: str | None = None) -> dict[str, bool]:
    result = {
        "cookies_cleared": False,
        "web_storage_cleared": False,
        "origin_storage_cleared": False,
    }
    settings = get_settings()
    target_url = login_url or settings.ctrip_ebooking_login_url
    try:
        await page.context.clear_cookies()
        result["cookies_cleared"] = True
    except PlaywrightError:
        pass
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        await page.evaluate("() => { window.localStorage.clear(); window.sessionStorage.clear(); }")
        result["web_storage_cleared"] = True
    except PlaywrightError:
        pass
    try:
        client = await page.context.new_cdp_session(page)
        for origin in _ctrip_storage_origins(target_url):
            await client.send(
                "Storage.clearDataForOrigin",
                {"origin": origin, "storageTypes": "all"},
            )
        await client.detach()
        result["origin_storage_cleared"] = True
    except PlaywrightError:
        pass
    return result


def _ctrip_storage_origins(target_url: str) -> list[str]:
    origins = ["https://ebooking.ctrip.com", "https://passport.ctrip.com"]
    with suppress(ValueError):
        parsed = urlparse(target_url)
        if parsed.scheme and parsed.netloc:
            origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return list(dict.fromkeys(origins))


async def _settle_page(page: Page) -> None:
    with suppress(PlaywrightError):
        await page.wait_for_timeout(600)


_EBK_READ_BLOCKING_POPUPS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "语音通知自动播放失败",
        ("知道了", "我知道了", "确定", "关闭"),
        "voice_notification_autoplay_failed",
    ),
)


async def _dismiss_ebk_read_blocking_popups(
    page: Page,
    *,
    timeout_ms: int = 5_000,
    poll_ms: int = 250,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "dismissed": False,
        "popup_type": None,
        "button_text": None,
        "method": None,
        "attempt_count": 0,
    }
    deadline = monotonic() + max(timeout_ms, 0) / 1_000
    poll_ms = max(poll_ms, 50)

    while True:
        result["attempt_count"] += 1
        for popup_text, button_texts, popup_type in _EBK_READ_BLOCKING_POPUPS:
            dismissed = await _dismiss_ebk_read_blocking_popup_once(
                page=page,
                popup_text=popup_text,
                button_texts=button_texts,
                popup_type=popup_type,
            )
            if dismissed["dismissed"]:
                result.update(dismissed)
                return result

        remaining_ms = int((deadline - monotonic()) * 1_000)
        if remaining_ms <= 0:
            return result
        with suppress(PlaywrightError):
            await page.wait_for_timeout(min(poll_ms, remaining_ms))
    return result


async def _dismiss_ebk_read_blocking_popup_once(
    *,
    page: Page,
    popup_text: str,
    button_texts: tuple[str, ...],
    popup_type: str,
) -> dict[str, Any]:
    locator_result = await _dismiss_ebk_read_blocking_popup_with_locator(
        page=page,
        popup_text=popup_text,
        button_texts=button_texts,
        popup_type=popup_type,
    )
    if locator_result["dismissed"]:
        return locator_result

    return await _dismiss_ebk_read_blocking_popup_with_dom(
        page=page,
        popup_text=popup_text,
        button_texts=button_texts,
        popup_type=popup_type,
    )


async def _dismiss_ebk_read_blocking_popup_with_locator(
    *,
    page: Page,
    popup_text: str,
    button_texts: tuple[str, ...],
    popup_type: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dismissed": False,
        "popup_type": None,
        "button_text": None,
        "method": None,
    }
    try:
        popup = page.locator(
            "[role='dialog'], "
            ".he-micro-trip-ui-modal-root, "
            ".he-micro-trip-ui-modal-confirm, "
            ".he-micro-trip-ui-modal-content, "
            "[class*='dialog'], "
            "[class*='modal'], "
            "[class*='popup']"
        ).filter(has_text=popup_text).first
        if await popup.count() <= 0 or not await popup.is_visible():
            return result
        for button_text in button_texts:
            button = popup.locator(
                f".he-micro-trip-ui-modal-confirm-btns button:has-text('{button_text}'), "
                f"button:has-text('{button_text}'), "
                f"[role='button']:has-text('{button_text}')"
            ).first
            if await button.count() <= 0 or not await button.is_visible():
                continue
            await button.click(timeout=3_000)
            await page.wait_for_timeout(800)
            result.update(
                {
                    "dismissed": True,
                    "popup_type": popup_type,
                    "button_text": button_text,
                    "method": "locator",
                }
            )
            return result
    except (AttributeError, PlaywrightError, TypeError):
        return result
    return result


async def _dismiss_ebk_read_blocking_popup_with_dom(
    *,
    page: Page,
    popup_text: str,
    button_texts: tuple[str, ...],
    popup_type: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dismissed": False,
        "popup_type": None,
        "button_text": None,
        "method": None,
    }
    try:
        js_result = await page.evaluate(
            """({ popupText, buttonTexts }) => {
                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                const isVisible = (node) => {
                    if (!node || !(node instanceof HTMLElement)) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
                        return false;
                    }
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const containerSelectors = [
                    "[role='dialog']",
                    ".he-micro-trip-ui-modal-root",
                    ".he-micro-trip-ui-modal-confirm",
                    ".he-micro-trip-ui-modal-content",
                    "[class*='dialog']",
                    "[class*='modal']",
                    "[class*='popup']",
                ];
                const containers = Array.from(document.querySelectorAll(containerSelectors.join(",")));
                const popup = containers.find((node) => {
                    if (!isVisible(node)) return false;
                    return normalize(node.innerText || node.textContent).includes(popupText);
                });
                if (!popup) return { dismissed: false };
                const buttons = Array.from(popup.querySelectorAll("button,[role='button']"));
                for (const buttonText of buttonTexts) {
                    const button = buttons.find((node) => {
                        if (!isVisible(node)) return false;
                        return normalize(node.innerText || node.textContent) === buttonText;
                    });
                    if (button) {
                        button.click();
                        return { dismissed: true, buttonText };
                    }
                }
                return { dismissed: false };
            }""",
            {"popupText": popup_text, "buttonTexts": list(button_texts)},
        )
        if isinstance(js_result, dict) and js_result.get("dismissed"):
            await page.wait_for_timeout(800)
            result.update(
                {
                    "dismissed": True,
                    "popup_type": popup_type,
                    "button_text": js_result.get("buttonText"),
                    "method": "dom",
                }
            )
    except (AttributeError, PlaywrightError, TypeError):
        return result
    return result


async def _select_comment_monitor_today(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "calendar_opened": False,
        "today_clicked": False,
        "read_only_filter": True,
    }
    result["calendar_opened"] = await _click_first_available(page, _COMMENT_DATE_PICKER_SELECTORS)
    with suppress(PlaywrightError):
        await page.wait_for_timeout(300)
    result["today_clicked"] = await _click_first_available(page, _COMMENT_TODAY_FILTER_SELECTORS)
    if result["today_clicked"]:
        with suppress(PlaywrightError):
            await page.wait_for_timeout(500)
    return result


async def _select_promotion_effect_yesterday_source(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "target": "昨日",
        "selected": False,
        "read_only_filter": True,
    }
    select_count = 0
    with suppress(AttributeError, PlaywrightError):
        select_count = await page.locator("select").count()
    for index in range(select_count):
        try:
            select = page.locator("select").nth(index)
            option_texts = [
                text.strip()
                for text in await select.locator("option").all_inner_texts()
                if text.strip()
            ]
            if not any("昨日" in text for text in option_texts):
                continue
            await select.select_option(label="昨日", timeout=3_000)
            result.update(
                {"selected": True, "method": "native_select", "options": option_texts[:8]}
            )
            await page.wait_for_timeout(800)
            return result
        except (AttributeError, PlaywrightError):
            continue

    js_result: dict[str, Any] | None = None
    with suppress(AttributeError, PlaywrightError):
        js_result = await page.evaluate(
            """() => {
                const selects = Array.from(document.querySelectorAll("select"));
                for (const select of selects) {
                    const options = Array.from(select.options || []);
                    const option = options.find((item) =>
                        (item.textContent || "").trim().includes("昨日")
                    );
                    if (!option) continue;
                    select.value = option.value;
                    select.dispatchEvent(new Event("input", { bubbles: true }));
                    select.dispatchEvent(new Event("change", { bubbles: true }));
                    return {
                        selected: true,
                        method: "native_select_js",
                        options: options
                            .map((item) => (item.textContent || "").trim())
                            .filter(Boolean)
                            .slice(0, 8),
                    };
                }
                return { selected: false };
            }"""
        )
    if isinstance(js_result, dict) and js_result.get("selected"):
        result.update(js_result)
        await page.wait_for_timeout(800)
        return result

    clicked = await _click_first_available(
        page,
        (
            "xpath=//*[normalize-space()='数据来源']/following::*"
            "[@role='combobox' or contains(@class,'select')][1]",
            ".ant-select:has-text('今日')",
            ".ant-select-selector:has-text('今日')",
            "[role='combobox']:has-text('今日')",
        ),
    )
    result["dropdown_opened"] = clicked
    if clicked:
        with suppress(PlaywrightError):
            await page.wait_for_timeout(300)
        selected = await _click_first_available(
            page,
            (
                "[role='option']:has-text('昨日')",
                ".ant-select-item-option:has-text('昨日')",
                "text=昨日",
            ),
        )
        result["selected"] = selected
        result["method"] = "custom_dropdown" if selected else "custom_dropdown_failed"
        with suppress(AttributeError, PlaywrightError):
            await page.wait_for_timeout(800)
    return result


async def _select_ad_pyramid_today_filter(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "target": "今日",
        "selected": False,
        "read_only_filter": True,
    }
    clicked = await _click_first_available(
        page,
        (
            "button:has-text('今天')",
            "[role='button']:has-text('今天')",
            ".ant-radio-button-wrapper:has-text('今天')",
            ".ant-segmented-item:has-text('今天')",
            "button:has-text('今日')",
            "[role='button']:has-text('今日')",
            ".ant-radio-button-wrapper:has-text('今日')",
            ".ant-segmented-item:has-text('今日')",
        ),
    )
    if clicked:
        result.update({"selected": True, "method": "quick_range_button"})
        with suppress(PlaywrightError):
            await page.wait_for_timeout(800)
        return result

    js_result: dict[str, Any] | None = None
    with suppress(AttributeError, PlaywrightError):
        js_result = await page.evaluate(
            """() => {
                const selectors = [
                    "button",
                    "[role='button']",
                    ".ant-radio-button-wrapper",
                    ".ant-segmented-item",
                    ".ant-tabs-tab"
                ];
                const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
                const candidate = nodes.find((node) => {
                    const text = (node.textContent || "").trim();
                    if (!["今天", "今日"].includes(text)) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === "none" || style.visibility === "hidden") return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
                if (!candidate) return { selected: false };
                candidate.click();
                return { selected: true, method: "quick_range_button_js" };
            }"""
        )
    if isinstance(js_result, dict) and js_result.get("selected"):
        result.update(js_result)
        with suppress(PlaywrightError):
            await page.wait_for_timeout(800)
    return result


async def _select_dataset_time_dimension(
    page: Page,
    *,
    dataset_id: str,
    dimension_key: str,
) -> dict[str, Any]:
    from app.domain.browser.ctrip_ebooking_time_dimensions import dataset_time_dimension_specs

    specs = dataset_time_dimension_specs(dataset_id)
    spec = next((item for item in specs if item.key == dimension_key), None)
    result: dict[str, Any] = {
        "attempted": True,
        "key": dimension_key,
        "label": spec.label if spec else dimension_key,
        "selected": False,
        "read_only_filter": True,
    }
    if spec is None:
        result["method"] = "unknown_dimension"
        return result

    if spec.embedded_section:
        result.update({"selected": True, "method": "embedded_section"})
        return result

    click_attempts = 12 if not spec.allow_label_fallback else 1
    clicked = False
    for attempt in range(click_attempts):
        clicked = await _click_first_available(page, spec.selectors)
        if clicked:
            break
        if attempt + 1 < click_attempts:
            with suppress(PlaywrightError):
                await page.wait_for_timeout(250)
    if clicked:
        result.update({"selected": True, "method": "selector"})
        with suppress(PlaywrightError):
            await page.wait_for_timeout(800)
        return result

    if not spec.allow_label_fallback:
        result["method"] = "not_found"
        return result

    js_result: dict[str, Any] | None = None
    label = spec.label
    with suppress(AttributeError, PlaywrightError):
        js_result = await page.evaluate(
            """(label) => {
                const selectors = [
                    "a.btn", "button", "[role='button']",
                    ".he-trip-ui-radio-button-wrapper",
                    ".ant-radio-button-wrapper",
                    ".ant-segmented-item"
                ];
                const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
                const candidate = nodes.find((node) => {
                    const text = (node.textContent || "").trim();
                    if (text !== label && !text.includes(label)) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === "none" || style.visibility === "hidden") return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
                if (!candidate) return { selected: false };
                candidate.click();
                return { selected: true, method: "label_js" };
            }""",
            label,
        )
    if isinstance(js_result, dict) and js_result.get("selected"):
        result.update(js_result)
        with suppress(PlaywrightError):
            await page.wait_for_timeout(800)
        return result

    result["method"] = "not_found"
    return result


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except (AttributeError, PlaywrightError):
        return ""


async def _safe_visible_text(page: Page) -> str:
    chunks: list[str] = []
    for frame in page.frames:
        try:
            text = await frame.locator("body").inner_text(timeout=4_000)
        except PlaywrightError:
            continue
        if text:
            chunks.append(text)
        table_text = await _safe_visible_table_text(frame)
        if table_text:
            chunks.append(table_text)
    return "\n".join(dict.fromkeys(chunks))


async def _safe_visible_table_text(frame) -> str:
    try:
        table_chunks = await frame.evaluate(
            """() => {
                const selectors = [
                    "table",
                    "[role='table']",
                    ".ant-table",
                    ".he-trip-ui-table",
                    ".table-condition",
                ];
                const seen = new Set();
                const chunks = [];
                for (const selector of selectors) {
                    for (const element of document.querySelectorAll(selector)) {
                        const style = window.getComputedStyle(element);
                        if (
                            style.display === "none" ||
                            style.visibility === "hidden" ||
                            element.closest("[aria-hidden='true']")
                        ) {
                            continue;
                        }
                        const rect = element.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const text = (element.innerText || element.textContent || "").trim();
                        if (!text || seen.has(text)) continue;
                        seen.add(text);
                        chunks.push(text);
                    }
                }
                return chunks.slice(0, 12);
            }"""
        )
    except (AttributeError, PlaywrightError):
        return ""
    if not isinstance(table_chunks, list):
        return ""
    return "\n".join(str(chunk) for chunk in table_chunks if str(chunk).strip())


async def _try_fill_login_form(
    page: Page,
    credentials: _LoginCredentials,
) -> dict[str, Any]:
    if not credentials.username and not credentials.password:
        return {
            "attempted": False,
            "username_filled": False,
            "password_filled": False,
            "submitted": False,
        }

    result: dict[str, Any] = {
        "attempted": True,
        "username_provided": bool(credentials.username),
        "password_provided": bool(credentials.password),
        "username_filled": False,
        "password_filled": False,
        "submitted": False,
        "auto_submit_requested": credentials.auto_submit,
    }
    if credentials.username:
        result["username_filled"] = await _fill_first_available(
            page,
            _USERNAME_SELECTORS,
            credentials.username,
        )
    if credentials.password:
        result["password_filled"] = await _fill_first_available(
            page,
            _PASSWORD_SELECTORS,
            credentials.password,
        )
    if credentials.auto_submit and result["username_filled"] and result["password_filled"]:
        result["submitted"] = await _click_first_available(page, _LOGIN_BUTTON_SELECTORS)
        await _settle_page(page)
    return result


async def _fill_first_available(page: Page, selectors: tuple[str, ...], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() == 0:
                continue
            if not await locator.is_visible(timeout=1_000):
                continue
            if not await locator.is_enabled(timeout=1_000):
                continue
            await locator.fill(value, timeout=3_000)
            return True
        except PlaywrightError:
            continue
    return False


async def _click_first_available(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if not await locator.is_visible(timeout=1_000):
                continue
            if not await locator.is_enabled(timeout=1_000):
                continue
            await locator.click(timeout=5_000)
            return True
        except (AttributeError, PlaywrightError):
            continue
    return False


def _resolve_credentials(
    *,
    account_phone: str | None,
    account_username: str | None,
    account_password: str | None,
    auto_submit_login: bool | None,
) -> _LoginCredentials:
    settings = get_settings()
    username = (
        _clean_secret(account_username)
        or _clean_secret(settings.ctrip_ebooking_username)
        or _clean_secret(account_phone)
    )
    password = _clean_secret(account_password) or _clean_secret(settings.ctrip_ebooking_password)
    auto_submit = (
        auto_submit_login
        if auto_submit_login is not None
        else settings.ctrip_ebooking_auto_submit_login
    )
    return _LoginCredentials(username=username, password=password, auto_submit=auto_submit)


def _resolve_login_url(login_context: str) -> str:
    settings = get_settings()
    if login_context in {"competitor_price", "public_competitor", "competitor"}:
        return settings.ctrip_public_competitor_login_url or settings.ctrip_ebooking_home_url
    return settings.ctrip_ebooking_home_url or settings.ctrip_ebooking_login_url


def _clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _ebooking_cache_key(*, platform_account_id: str, browser_session_id: str) -> str:
    return f"{platform_account_id}:{browser_session_id}:ctrip-ebooking"


def _credential_source(credentials: _LoginCredentials) -> str:
    if not credentials.username and not credentials.password:
        return "none"
    return "runtime_only"


def _build_login_summary(
    *,
    credentials: _LoginCredentials,
    credential_fill: dict[str, Any],
    page_state: str,
) -> str:
    if page_state == "authenticated":
        return "Ctrip EBK login state is available in the persistent browser."
    if not credential_fill.get("attempted"):
        return "Ctrip EBK login page is open. Complete login and verification manually if needed."
    if credentials.auto_submit:
        return "Ctrip EBK credentials were filled and submitted. Manual verification may still be required."
    return "Ctrip EBK credentials were filled only. Review the browser and submit manually."


def _login_next_action(page_state: str, *, login_context: str = "ebk") -> str:
    if page_state == "authenticated":
        return "Run a Ctrip EBK read-only query."
    if page_state == "verification_required":
        return "Complete CAPTCHA, SMS, QR, or platform risk-control verification manually, then retry."
    return "Complete login in the opened browser, then retry the read."


def _classify_page_state(url: str, body_text: str) -> str:
    normalized_url = url.lower()
    if any(token in normalized_url for token in ("captcha", "verify", "security", "risk")):
        return "verification_required"
    if _looks_like_login_required(normalized_url, body_text):
        return "login_required"
    business_score = sum(1 for term in _BUSINESS_TERMS if term in body_text)
    if business_score >= 2:
        return "authenticated"
    if _looks_like_verification_required(normalized_url, body_text):
        return "verification_required"
    if _is_ctrip_url(url) and body_text:
        return "authenticated"
    return "unknown"


def _is_ctrip_home_url(url: str) -> bool:
    normalized = url.lower()
    return "ebooking.ctrip.com/home" in normalized


def _is_ctrip_ebooking_url(url: str) -> bool:
    normalized = url.lower().strip()
    return normalized.startswith("https://ebooking.ctrip.com/") or normalized.startswith(
        "http://ebooking.ctrip.com/"
    )


def _is_ctrip_ebooking_pricing_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "ebooking.ctrip.com"
        and parsed.path.startswith("/ebkovsroom/inventory/calendar")
    )


def _resolve_ctrip_pricing_target_url(target_url: str | None) -> str | None:
    if not target_url:
        return CTRIP_EBOOKING_PRICING_CALENDAR_URL
    stripped = target_url.strip()
    if not _is_ctrip_ebooking_pricing_url(stripped):
        return None
    return stripped


def _pricing_actions(execute_request: dict[str, Any]) -> list[dict[str, Any]]:
    actions = execute_request.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


async def _select_pricing_room_type_read_only(
    page: Page,
    *,
    room_type: str,
) -> dict[str, Any]:
    from app.domain.browser.ctrip_room_type_selector import (
        click_room_selection_confirm,
        detect_room_type_trigger,
        dismiss_lightweight_popups,
        safe_click,
        safe_fill_search,
        select_matching_room_results,
        wait_for_room_type_popup,
    )

    if not room_type:
        return {"status": "data_required", "reason": "room_type_missing"}

    await dismiss_lightweight_popups(page)
    triggers = await detect_room_type_trigger(page)
    best_trigger = triggers[0] if triggers else None
    if not best_trigger or best_trigger.get("score", 0) <= 0:
        return {
            "status": "page_structure_changed",
            "reason": "room_type_trigger_missing",
            "trigger_count": len(triggers),
        }
    if not await safe_click(page, best_trigger["selector_hint"], best_trigger.get("text", "")):
        return {
            "status": "page_structure_changed",
            "reason": "room_type_trigger_click_failed",
            "trigger_text": best_trigger.get("text"),
        }
    # 2026-09-14：弹窗搜索框异步渲染，固定 1500ms 单次 detect 会漏掉未渲染完
    # 的搜索框（误报「No search input」打断调价链路）；改有界等待信号收敛。
    popup = await wait_for_room_type_popup(page)
    search_inputs = popup.get("search_inputs") or []
    if not search_inputs:
        return {
            "status": "page_structure_changed",
            "reason": "room_type_search_input_missing",
            "popup": popup,
        }
    search_selector = search_inputs[0]["selector_hint"]
    if await select_matching_room_results(
        page,
        search_selector,
        room_type,
        allow_filter=False,
    ):
        if not await click_room_selection_confirm(page):
            return {
                "status": "page_structure_changed",
                "reason": "room_type_confirm_failed",
            }
        await page.wait_for_timeout(1200)
        return {
            "status": "selected",
            "reason": "room_type_selected",
            "trigger_text": best_trigger.get("text"),
            "search_placeholder": search_inputs[0].get("placeholder"),
            "selection_strategy": "visible_list_before_search",
        }
    if not await safe_fill_search(page, search_selector, room_type):
        return {
            "status": "page_structure_changed",
            "reason": "room_type_search_fill_failed",
        }
    if not await select_matching_room_results(page, search_selector, room_type):
        return {
            "status": "page_structure_changed",
            "reason": "room_type_match_failed",
        }
    if not await click_room_selection_confirm(page):
        return {
            "status": "page_structure_changed",
            "reason": "room_type_confirm_failed",
        }
    await page.wait_for_timeout(1200)
    return {
        "status": "selected",
        "reason": "room_type_selected",
        "trigger_text": best_trigger.get("text"),
        "search_placeholder": search_inputs[0].get("placeholder"),
    }


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return "***"
    return f"{digits[:3]}****{digits[-4:]}"


def _mask_identifier(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        return _mask_phone(value)
    if len(value) <= 4:
        return f"{value[:1]}***"
    return f"{value[:2]}***{value[-2:]}"


_USERNAME_SELECTORS = (
    "input[name='username']",
    "input[name='account']",
    "input[name*='user' i]",
    "input[name*='account' i]",
    "input[id*='user' i]",
    "input[id*='account' i]",
    "input[placeholder*='账号']",
    "input[placeholder*='用户名']",
    "input[placeholder*='手机号']",
    "input[type='tel']",
    "input[type='text']",
    "input:not([type])",
)

_PASSWORD_SELECTORS = (
    "input[type='password']",
    "input[name*='password' i]",
    "input[id*='password' i]",
    "input[placeholder*='密码']",
)

_LOGIN_BUTTON_SELECTORS = (
    "button:has-text('登录')",
    "button:has-text('登陆')",
    "button:has-text('立即登录')",
    "button[type='submit']",
    "[role='button']:has-text('登录')",
    "text=登录",
)

_COMMENT_DATE_PICKER_SELECTORS = (
    "input[placeholder*='开始日期']",
    "input[placeholder*='结束日期']",
    "input[placeholder*='日期']",
    ".ant-picker-range",
    "[class*='date'] input",
    "[class*='calendar']",
    "[aria-label*='calendar' i]",
)

_COMMENT_TODAY_FILTER_SELECTORS = (
    "button:has-text('今日')",
    "[role='button']:has-text('今日')",
    ".ant-picker-presets li:has-text('今日')",
    ".ant-picker-ranges li:has-text('今日')",
    "text=今日",
)
