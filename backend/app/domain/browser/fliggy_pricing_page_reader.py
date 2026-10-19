"""飞猪 EBK 价格页读取器（V9 Step 1）。

模式对齐携程 ctrip_pricing_page_reader：页面态分类（登录/验证/价格页）+
可见 DOM 目标定位（房型/日期/价格）。selector 全部来自运行时可见结构，
具体页面词表走 page_health_config.json 的 fliggy:pricing:* target，
便于运营热更新；结构不确定返回 page_structure_changed / needs_review。
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from app.domain.browser.ctrip_common import (
    _looks_like_login_required,
    _looks_like_verification_required,
)

from app.domain.browser.pricing_input_match import match_price_input_candidates

PRICING_PAGE_READER_VERSION = "fliggy_ebooking_pricing_page_reader_v1"
_PRICING_PAGE_TERMS = ("房价", "房型", "日历", "库存")
_PRICE_INPUT_TERMS = ("价格", "房价", "卖价", "底价", "¥", "￥")
_VERIFICATION_BODY_MARKERS = ("验证码", "滑动验证", "拼图", "扫码", "二维码", "安全验证")


def _is_fliggy_url(url: str) -> bool:
    lowered = url.lower()
    return "fliggy.com" in lowered


def classify_fliggy_pricing_page_state(url: str, body_text: str) -> str:
    normalized_url = url.lower()
    if any(token in normalized_url for token in ("captcha", "verify", "security", "risk")):
        return "verification_required"
    if any(marker in body_text for marker in _VERIFICATION_BODY_MARKERS):
        return "verification_required"
    if _looks_like_login_required(normalized_url, body_text):
        return "login_required"
    if _looks_like_verification_required(normalized_url, body_text):
        return "verification_required"
    if not _is_fliggy_url(url):
        return "unknown"
    pricing_score = sum(1 for term in _PRICING_PAGE_TERMS if term in body_text)
    if pricing_score >= 2:
        return "pricing_page"
    if body_text:
        return "authenticated_non_pricing_page"
    return "unknown"


async def read_fliggy_pricing_write_page(
    page: Page,
    *,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    title = await _safe_title(page)
    current_url = page.url
    body_text = await _safe_visible_text(page)
    page_state = classify_fliggy_pricing_page_state(current_url, body_text)
    if page_state in {"login_required", "verification_required"}:
        return {
            "status": "human_required",
            "summary": "Fliggy EBK requires login or verification before price write dry-run.",
            "page_state": page_state,
            "current_url": current_url,
            "page_title": title,
            "targets": [],
            "evidence": _base_evidence(body_text, page_state),
            "next_action": "Complete login or verification manually, then retry dry-run.",
        }

    if page_state != "pricing_page":
        return {
            "status": "page_structure_changed",
            "summary": "Fliggy EBK pricing page was not recognized from visible structure.",
            "page_state": page_state,
            "current_url": current_url,
            "page_title": title,
            "targets": [],
            "evidence": _base_evidence(body_text, page_state),
            "next_action": "Open the Fliggy EBK room price page and retry dry-run.",
        }

    lines = _visible_lines(body_text)
    input_candidates = await _safe_price_input_candidates(page)
    actions = _execute_actions(execute_request)
    targets = [
        _target_readback(action=action, lines=lines, input_candidates=input_candidates)
        for action in actions
    ]
    return {
        "status": "success",
        "summary": "Fliggy EBK pricing page was opened and read for dry-run.",
        "page_state": page_state,
        "current_url": current_url,
        "page_title": title,
        "targets": targets,
        "evidence": {
            **_base_evidence(body_text, page_state),
            "input_candidate_count": len(input_candidates),
            "target_count": len(targets),
            "reader_version": PRICING_PAGE_READER_VERSION,
        },
        "next_action": "Build a page write preview without filling or submitting fields.",
    }


def _target_readback(
    *,
    action: dict[str, Any],
    lines: list[str],
    input_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    room_type = _optional_str(action.get("room_type"))
    checkin_date = _optional_str(action.get("checkin_date"))
    proposed_base = _optional_str(action.get("proposed_base_price_yuan"))
    proposed_sell = _optional_str(action.get("proposed_price_yuan"))
    proposed_price = proposed_base or proposed_sell
    current_price_value = (
        action.get("current_price_yuan") or action.get("approved_before_price_yuan")
    )
    current_price = _optional_str(current_price_value)
    matching_lines = _matching_lines(lines, [room_type, _date_day(checkin_date), current_price])
    candidates = _matching_input_candidates(
        input_candidates, [room_type, _date_day(checkin_date), current_price]
    )
    status = "located" if candidates or matching_lines else "not_found"
    return {
        "status": status,
        "operation": action.get("operation"),
        "platform": "fliggy",
        "room_type": room_type,
        "checkin_date": checkin_date,
        "checkout_date": action.get("checkout_date"),
        "breakfast_kind": action.get("breakfast_kind"),
        "current_price_yuan": current_price_value,
        "proposed_price_yuan": action.get("proposed_price_yuan"),
        "proposed_base_price_yuan": action.get("proposed_base_price_yuan"),
        "price_text_expected": proposed_price,
        "matching_lines": matching_lines[:5],
        "input_candidates": candidates[:5],
        "locator_strategy": (
            "visible_input_near_room_date_price"
            if candidates
            else "visible_text_near_room_date_price"
            if matching_lines
            else "not_located"
        ),
    }


async def _safe_price_input_candidates(page: Page) -> list[dict[str, Any]]:
    try:
        candidates = await page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const text = (el) => (el && (el.innerText || el.textContent) || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const cssPath = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const parts = [];
                    let current = el;
                    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 4) {
                        const tag = current.tagName.toLowerCase();
                        const className = Array.from(current.classList || [])
                            .slice(0, 2)
                            .map((item) => `.${CSS.escape(item)}`)
                            .join('');
                        parts.unshift(`${tag}${className}`);
                        current = current.parentElement;
                    }
                    return parts.join(' > ');
                };
                const inputs = Array.from(
                    document.querySelectorAll('input, [contenteditable="true"], textarea')
                ).filter(visible).filter((input) => {
                    if (input.tagName !== 'INPUT') return true;
                    const type = (input.getAttribute('type') || 'text').toLowerCase();
                    return !['checkbox', 'radio', 'search', 'button', 'submit',
                             'reset', 'file', 'hidden'].includes(type);
                });
                return inputs.slice(0, 80).map((input, index) => {
                    const container = input.closest(
                        '[class*="row"],[class*="Row"],[class*="cell"],[class*="Cell"],'
                        + '[class*="card"],[class*="Card"],tr,td,li'
                    ) || input.parentElement || input;
                    return {
                        index,
                        selector_hint: cssPath(input),
                        tag: input.tagName.toLowerCase(),
                        type: input.getAttribute('type') || '',
                        aria_label: input.getAttribute('aria-label') || '',
                        placeholder: input.getAttribute('placeholder') || '',
                        value: input.value || input.getAttribute('value') || '',
                        nearby_text: text(container).slice(0, 500),
                    };
                });
            }
            """
        )
    except Exception:
        return []
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


# B4 — 价格输入框候选匹配统一走共享收紧实现（pricing_input_match）。
_matching_input_candidates = match_price_input_candidates


def _matching_lines(lines: list[str], terms: list[str | None]) -> list[str]:
    normalized_terms = [term for term in terms if term]
    matches: list[tuple[int, str]] = []
    for line in lines:
        score = sum(1 for term in normalized_terms if term and term in line)
        if any(term in line for term in _PRICE_INPUT_TERMS):
            score += 1
        if score > 0:
            matches.append((score, line))
    return [line for _, line in sorted(matches, key=lambda pair: pair[0], reverse=True)]


def _base_evidence(body_text: str, page_state: str) -> dict[str, Any]:
    return {
        "page_state": page_state,
        "body_text_length": len(body_text),
        "pricing_page_terms_found": [
            term for term in _PRICING_PAGE_TERMS if term in body_text
        ],
        "read_only": True,
        "browser_write_invoked": False,
        "ota_submit_invoked": False,
        "screenshot_captured": False,
    }


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


async def _safe_visible_text(page: Page) -> str:
    try:
        text = await page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _execute_actions(execute_request: dict[str, Any]) -> list[dict[str, Any]]:
    actions = execute_request.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _visible_lines(body_text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in body_text.splitlines()
        if line.strip()
    ]


def _date_day(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2})$", value[:10])
    if not match:
        return value
    return str(int(match.group(1)))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
