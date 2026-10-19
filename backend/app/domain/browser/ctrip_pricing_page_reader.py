from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from app.domain.browser.ctrip_room_type_selector import (
    PRICING_PAGE_TERMS,
    classify_ctrip_pricing_page_state,
    detect_price_block_candidates,
)
from app.domain.browser.pricing_input_match import match_price_input_candidates

PRICING_PAGE_READER_VERSION = "ctrip_ebooking_pricing_page_reader_v1"
_PRICE_INPUT_TERMS = ("价格", "房价", "卖价", "底价", "¥", "￥")


async def read_ctrip_pricing_write_page(
    page: Page,
    *,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    title = await _safe_title(page)
    current_url = page.url
    body_text = await _safe_visible_text(page)
    page_state = classify_ctrip_pricing_page_state(current_url, body_text)
    if page_state in {"login_required", "verification_required"}:
        return {
            "status": "human_required",
            "summary": "Ctrip EBK requires login or verification before price write dry-run.",
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
            "summary": "Ctrip EBK pricing page was not recognized from visible structure.",
            "page_state": page_state,
            "current_url": current_url,
            "page_title": title,
            "targets": [],
            "evidence": _base_evidence(body_text, page_state),
            "next_action": "Open the Ctrip EBK room price/status page and retry dry-run.",
        }

    lines = _visible_lines(body_text)
    input_candidates = await _safe_price_input_candidates(page)
    actions = _execute_actions(execute_request)
    current_rate_readbacks = await _safe_current_rate_readbacks(page, actions)
    targets = [
        _target_readback(
            action=action,
            lines=lines,
            input_candidates=input_candidates,
            current_rate_readback=(
                current_rate_readbacks[index]
                if index < len(current_rate_readbacks)
                else {}
            ),
        )
        for index, action in enumerate(actions)
    ]
    return {
        "status": "success",
        "summary": "Ctrip EBK pricing page was opened and read for dry-run.",
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
                    const aria = input.getAttribute('aria-label') || '';
                    const placeholder = input.getAttribute('placeholder') || '';
                    const value = input.value || input.getAttribute('value') || '';
                    return {
                        index,
                        selector_hint: cssPath(input),
                        tag: input.tagName.toLowerCase(),
                        type: input.getAttribute('type') || '',
                        aria_label: aria,
                        placeholder,
                        value,
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


def _target_readback(
    *,
    action: dict[str, Any],
    lines: list[str],
    input_candidates: list[dict[str, Any]],
    current_rate_readback: dict[str, Any],
) -> dict[str, Any]:
    room_type = _optional_str(action.get("room_type"))
    checkin_date = _optional_str(action.get("checkin_date"))
    proposed_base = _optional_str(action.get("proposed_base_price_yuan"))
    proposed_sell = _optional_str(action.get("proposed_price_yuan"))
    # EBK input box expects base price (底价), not sell price (划线价).
    # When proposed_base_price_yuan is available, use it as the planned input value.
    proposed_price = proposed_base or proposed_sell
    current_price_value = (
        action.get("current_price_yuan")
        or action.get("approved_before_price_yuan")
        or current_rate_readback.get("current_price_yuan")
    )
    current_price = _optional_str(current_price_value)
    matching_lines = _matching_lines(
        lines,
        [room_type, _date_day(checkin_date), current_price],
    )
    candidates = _matching_input_candidates(
        input_candidates,
        [room_type, _date_day(checkin_date), current_price],
    )
    current_rate_located = (
        action.get("operation") == "read_current_room_price"
        and current_rate_readback.get("status") == "located"
    )
    status = "located" if candidates or matching_lines or current_rate_located else "not_found"
    return {
        "status": status,
        "operation": action.get("operation"),
        "platform": action.get("platform"),
        "room_type": room_type,
        "checkin_date": checkin_date,
        "checkout_date": action.get("checkout_date"),
        "breakfast_kind": action.get("breakfast_kind"),
        "current_price_yuan": current_price_value,
        "current_base_price_yuan": (
            action.get("current_base_price_yuan")
            or current_rate_readback.get("current_base_price_yuan")
        ),
        "room_status_text": (
            action.get("room_status_text") or current_rate_readback.get("room_status_text")
        ),
        "room_inventory_text": (
            action.get("room_inventory_text")
            or current_rate_readback.get("room_inventory_text")
        ),
        "current_rate_source": current_rate_readback.get("source"),
        "current_rate_confidence": current_rate_readback.get("confidence"),
        "proposed_price_yuan": action.get("proposed_price_yuan"),
        "proposed_base_price_yuan": action.get("proposed_base_price_yuan"),
        "commission_rate": action.get("commission_rate"),
        "price_text_expected": proposed_price,
        "matching_lines": matching_lines[:5],
        "input_candidates": candidates[:5],
        "locator_strategy": (
            "visible_input_near_room_date_price"
            if candidates
            else "visible_calendar_cell_current_rate"
            if current_rate_located
            else "visible_text_near_room_date_price"
            if matching_lines
            else "not_located"
        ),
    }


async def _safe_current_rate_readbacks(
    page: Page,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    readbacks: list[dict[str, Any]] = []
    for action in actions:
        if action.get("operation") != "read_current_room_price":
            readbacks.append({})
            continue
        readbacks.append(await _safe_current_rate_readback(page, action))
    return readbacks


async def _safe_current_rate_readback(
    page: Page,
    action: dict[str, Any],
) -> dict[str, Any]:
    room_type = _optional_str(action.get("room_type"))
    checkin_date = _optional_str(action.get("checkin_date"))
    if not room_type or not _looks_like_iso_date(checkin_date):
        return {
            "status": "data_required",
            "source": "ctrip_ebooking_pricing_calendar_dom",
            "confidence": "none",
            "reason": "room_type_or_checkin_date_missing",
        }
    result = await detect_price_block_candidates(
        page,
        room_type=room_type,
        checkin_date=checkin_date,
    )
    candidates = result.get("candidates") if isinstance(result, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    parsed_candidates: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        if not isinstance(candidate, dict):
            continue
        parsed = _parse_current_rate_from_pricing_text(
            candidate.get("text"),
            candidate.get("nearby_text"),
            candidate.get("room_text"),
        )
        if parsed.get("status") != "located":
            continue
        parsed_candidates.append({**candidate, **parsed})
    if not parsed_candidates:
        return {
            "status": "not_found",
            "source": "ctrip_ebooking_pricing_calendar_dom",
            "confidence": "none",
            "candidate_count": len(candidates),
            "price_block_status": result.get("status") if isinstance(result, dict) else None,
        }
    best = sorted(
        parsed_candidates,
        key=lambda item: (
            int(item.get("score") or 0),
            1 if item.get("current_price_yuan") is not None else 0,
            1 if item.get("current_base_price_yuan") is not None else 0,
        ),
        reverse=True,
    )[0]
    return {
        "status": "located",
        "source": "ctrip_ebooking_pricing_calendar_dom",
        "confidence": "high" if best.get("day_column") else "medium",
        "current_price_yuan": best.get("current_price_yuan"),
        "current_base_price_yuan": best.get("current_base_price_yuan"),
        "room_status_text": best.get("room_status_text"),
        "room_inventory_text": best.get("room_inventory_text"),
        "selector_hint": best.get("selector_hint"),
        "source_text": best.get("source_text"),
        "candidate_count": len(candidates),
        "price_block_status": result.get("status") if isinstance(result, dict) else None,
    }


def _parse_current_rate_from_pricing_text(*parts: Any) -> dict[str, Any]:
    source_text = _compact_text(" ".join(str(part or "") for part in parts))
    if not source_text:
        return {"status": "not_found"}
    current_price = _labelled_price(source_text, ("卖价", "售价", "房价"))
    current_base = _labelled_price(source_text, ("底价", "成本价"))
    if current_price is None or current_base is None:
        money_values = _money_like_numbers(source_text)
        if current_price is None and money_values:
            current_price = money_values[0]
        if current_base is None and len(money_values) >= 2:
            current_base = money_values[1]
    room_status = _room_status_text(source_text)
    inventory = _room_inventory_text(source_text)
    if current_price is None and current_base is None and not room_status and not inventory:
        return {"status": "not_found", "source_text": source_text[:240]}
    return {
        "status": "located",
        "current_price_yuan": current_price,
        "current_base_price_yuan": current_base,
        "room_status_text": room_status,
        "room_inventory_text": inventory,
        "source_text": source_text[:240],
    }


def _looks_like_iso_date(value: str | None) -> bool:
    return bool(value and re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", value))


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _labelled_price(text: str, labels: tuple[str, ...]) -> float | int | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})\s*(?:[:：]|\(元\)|（元）|元)?\s*([0-9]{{2,5}}(?:\.[0-9]{{1,2}})?)",
        text,
    )
    if not match:
        return None
    return _number_value(match.group(1))


def _money_like_numbers(text: str) -> list[float | int]:
    filtered = re.sub(r"\d{1,2}[-月]\d{1,2}(?:日)?", " ", text)
    values: list[float | int] = []
    for raw in re.findall(r"(?<!\d)([1-9]\d{1,4}(?:\.\d{1,2})?)(?!\d)", filtered):
        value = _number_value(raw)
        if value is not None and value >= 30:
            values.append(value)
    return values


def _number_value(value: str) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def _room_status_text(text: str) -> str | None:
    for keyword in ("已开房", "已关房", "开房", "关房", "可售", "满房", "停售"):
        if keyword in text:
            return keyword
    return None


def _room_inventory_text(text: str) -> str | None:
    patterns = (
        r"(不限量)",
        r"(限量\s*剩\s*\d+)",
        r"(剩余\s*\d+)",
        r"(仅剩\s*\d+)",
        r"(房量\s*[:：]?\s*\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _compact_text(match.group(1))
    return None


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
            term for term in PRICING_PAGE_TERMS if term in body_text
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
