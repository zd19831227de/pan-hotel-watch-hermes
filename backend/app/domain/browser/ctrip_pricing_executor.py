"""Ctrip EBK pricing page executor.

Implements fill_ctrip_room_price and submit_ctrip_price_changes
for P10 execute_pricing_page_write() to use.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Page

from app.domain.browser.ctrip_room_type_selector import (
    EPSILON,
    find_price_input_candidates,
    match_candidates_to_room,
    parse_price,
)
from app.domain.browser.ctrip_pricing_page_reader import _safe_current_rate_readback
from app.domain.browser.js_loader import load_js

PRICING_EXECUTOR_VERSION = "ctrip_ebooking_pricing_executor_v1"

_BLOCKER_MARKERS = (
    "captcha",
    "\u9a8c\u8bc1\u7801",
    "\u6ed1\u5757",
    "\u626b\u7801",
    "\u4e8c\u7ef4\u7801",
    "\u98ce\u9669",
    "risk control",
)
_LOGIN_MARKERS = ("login", "\u767b\u5f55", "\u5bc6\u7801")
# 2026-09-15 实测：修改价格弹窗（popover pop-amendprice-table-box）的提交
# 按钮是 #JPrice_Confirm，文案是「确定」——旧词表只有「确认」没「确定」，
# locate 一直失败（execution 20「Could not locate save/confirm/submit」）。
_SUBMIT_BUTTON_LABELS = (
    "\u4fdd\u5b58",  # 保存
    "\u786e\u5b9a",  # 确定
    "\u786e\u8ba4",  # 确认
    "\u63d0\u4ea4",  # 提交
    "\u53d1\u5e03",  # 发布
)
# 修改价格弹窗提交按钮的已知 id，命中即直用，避免通用 JS 扫描歧义。
_PRICE_MODAL_CONFIRM_IDS = ("#JPrice_Confirm",)


async def _locate_price_modal_submit_button(page: Page) -> dict[str, Any] | None:
    for button_id in _PRICE_MODAL_CONFIRM_IDS:
        try:
            locator = page.locator(f"{button_id}:visible").first
            if await locator.count() > 0 and await locator.is_visible():
                return {"selector": f"{button_id}:visible", "button_text": "确定"}
        except Exception:
            continue
    try:
        result = await page.evaluate(load_js("price_modal_button"), list(_SUBMIT_BUTTON_LABELS))
    except Exception:
        return None
    return result if isinstance(result, dict) and result.get("selector") else None


async def _read_body_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def _blocker_status_from_text(text: str) -> tuple[str, str] | None:
    body_lower = text.lower()
    for marker in _BLOCKER_MARKERS:
        if marker in body_lower:
            return "human_required", f"Submit triggered verification: {marker}"
    if any(token in body_lower for token in _LOGIN_MARKERS):
        return "human_required", "Login required after submit."
    return None


async def _read_success_toast(page: Page) -> str:
    try:
        result = await page.evaluate(load_js("success_toast"))
    except Exception:
        return ""
    return result if isinstance(result, str) else ""


def _request_payload_text(request: Any) -> str:
    for attribute in ("post_data_json", "post_data"):
        value = getattr(request, attribute, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is None or value == "":
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    return ""


def _payload_matches_price_target(payload_text: str, target: dict[str, Any]) -> bool:
    normalized_payload = re.sub(r"\s+", "", payload_text).lower()
    # 2026-09-15 live 实测保存 API（/api/inventory/batchsetroomprice）payload：
    # {"roomPriceInfoList":[{"roomName":"奢华三卧套房...","costPrice":891.44,
    #  "salePrice":1013,...}],"dateRangeInfo":[{"startDate":"2026-09-16",...}]}
    # 卖价是填入值、底价是平台按卖价×(1-佣金)派生——价格匹配必须以店长
    # 目标卖价为主，旧口径底价（卖价/(1+佣金)）不在 payload 里。
    price_candidates = [
        target.get("proposed_price_yuan"),
        target.get("proposed_base_price_yuan"),
    ]
    price_matched = False
    for candidate in price_candidates:
        expected_price = parse_price(str(candidate or ""))
        if expected_price is None:
            continue
        expected_price_text = f"{expected_price:.2f}".rstrip("0").rstrip(".")
        price_pattern = (
            rf"(?<!\d){re.escape(expected_price_text)}(?:\.0+)?(?!\d)"
            if expected_price.is_integer()
            else rf"(?<!\d){re.escape(expected_price_text)}(?!\d)"
        )
        if re.search(price_pattern, normalized_payload) is not None:
            price_matched = True
            break
    if not price_matched:
        return False

    room_type = re.sub(r"\s+", "", str(target.get("room_type") or "")).lower()
    checkin_date = str(target.get("checkin_date") or "").strip().lower()
    date_variants = {
        checkin_date,
        checkin_date.replace("-", ""),
        checkin_date.replace("-", "/"),
    } - {""}
    payload_without_separators = normalized_payload.replace("-", "").replace("/", "")
    room_matches = bool(room_type and room_type in normalized_payload)
    date_matches = any(
        variant in normalized_payload
        or variant.replace("-", "").replace("/", "") in payload_without_separators
        for variant in date_variants
    )
    return room_matches or date_matches


def _is_price_submit_response(
    response: Any,
    targets: list[dict[str, Any]] | None = None,
) -> bool:
    """Accept only Ctrip write endpoints whose payload matches this submit."""
    request = getattr(response, "request", None)
    method = getattr(request, "method", "") if request is not None else ""
    if callable(method):
        method = method()
    if str(method or "").upper() not in {"POST", "PUT", "PATCH"}:
        return False
    try:
        parsed = urlsplit(str(getattr(response, "url", "") or ""))
    except ValueError:
        return False
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if hostname != "ctrip.com" and not hostname.endswith(".ctrip.com"):
        return False
    endpoint = parsed.path.lower()
    has_price_scope = any(
        token in endpoint
        for token in ("price", "rate", "inventory", "calendar")
    )
    has_write_action = any(
        token in endpoint
        for token in ("save", "submit", "update", "modify", "change", "batch")
    )
    if not has_price_scope or not has_write_action:
        return False
    payload_text = _request_payload_text(request)
    if not payload_text:
        return False
    return any(
        _payload_matches_price_target(payload_text, target)
        for target in (targets or [])
        if isinstance(target, dict)
    )


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if callable(status):
        status = status()
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _prices_match(expected: Any, actual: Any) -> bool:
    expected_value = parse_price(str(expected)) if expected is not None else None
    actual_value = parse_price(str(actual)) if actual is not None else None
    return (
        expected_value is not None
        and actual_value is not None
        and abs(expected_value - actual_value) < EPSILON
    )


async def _wait_for_submit_confirmation(
    page: Page,
    responses: list[Any],
    targets: list[dict[str, Any]],
    *,
    page_save_clicker: Any = None,
) -> dict[str, Any]:
    """Poll for a submit-specific response or an explicit visible toast.

    2026-09-15：弹窗「确定」可能只把改价落到页面暂存区，还需点页面级
    「保存」才发保存请求。轮询过半仍无任何保存信号时，用 page_save_clicker
    兜底点一次页面级保存（只点一次），然后继续等确认信号。
    """
    save_clicked = False
    for attempt in range(40):
        body_text = await _read_body_text(page)
        blocker = _blocker_status_from_text(body_text)
        if blocker is not None:
            return {"kind": "blocker", "status": blocker[0], "message": blocker[1]}

        for response in responses:
            if not _is_price_submit_response(response, targets):
                continue
            status = _response_status(response)
            if status is not None and not 200 <= status < 300:
                return {
                    "kind": "response_error",
                    "status": "needs_review",
                    "message": f"Price save response returned HTTP {status}.",
                    "response_status": status,
                    "response_url": str(getattr(response, "url", "") or ""),
                }
            return {
                "kind": "response",
                "status": "confirmed",
                "message": "Price save response returned a successful status.",
                "response_status": status,
                "response_url": str(getattr(response, "url", "") or ""),
            }

        success_toast = await _read_success_toast(page)
        if success_toast:
            return {
                "kind": "toast",
                "status": "confirmed",
                "message": success_toast,
            }
        if (
            not save_clicked
            and page_save_clicker is not None
            and attempt >= 8
        ):
            try:
                save_clicked = bool(await page_save_clicker(page))
            except Exception:
                save_clicked = False
        await page.wait_for_timeout(250)
    return {
        "kind": "uncertain",
        "status": "needs_review",
        "message": "Save clicked, but no submit response or explicit success toast was detected."
        + (" Page-level save button was not found or not clickable." if page_save_clicker is not None and not save_clicked else ""),
    }


_PAGE_SAVE_CLICK_JS = """/*ctrip_pricing_executor:click_page_level_save*/
() => {
    const visible = (el) => {
        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
        return s.visibility !== 'hidden' && s.display !== 'none'
            && r.width > 0 && r.height > 0;
    };
    // 页面级保存：不在任何弹层（popover/modal/dialog）容器内的可见按钮，
    // 文案精确等于「保存」/「保 存」，避免误点「保存成功」提示或其他动词。
    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
        .filter(visible)
        .filter((el) => !el.closest("[class*='popover'], [class*='modal'], [class*='dialog'], [role='dialog']"));
    for (const el of buttons) {
        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
        if (text === '保存') {
            el.click();
            return true;
        }
    }
    return false;
}"""


async def _click_page_level_save_button(page: Page) -> bool:
    """兜底点击页面级「保存」按钮（弹层外、文案精确匹配）。"""
    try:
        return bool(await page.evaluate(_PAGE_SAVE_CLICK_JS))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# fill_ctrip_room_price
# ---------------------------------------------------------------------------


_SELL_INPUT_TERMS = ("卖价", "售价", "房价")
_BASE_INPUT_TERMS = ("底价", "成本价")


def _pick_fill_value(
    candidate: dict[str, Any] | None,
    *,
    base_price: str,
    target: dict[str, Any],
) -> str:
    """按输入框标签决定填什么价格。

    2026-09-15 实测（修改价格弹窗 live CDP）：价格模式=卖价+服务费，唯一
    可编辑价格框是「卖价(元)」（JPrice_CalculateSell），底价由平台按
    卖价×(1-12%) 派生（原卖价1012->原底价890.56 实证）。旧逻辑无条件把
    proposed_base（目标/1.12，与本页底价口径也不一致）填进卖价框——
    目标1013会被写成公开卖价904.46，属写错价。
    底价标注的输入框才填 base；卖价/未标注的填店长目标卖价。
    """
    haystack = " ".join(
        str((candidate or {}).get(key) or "")
        for key in ("nearby_text", "placeholder", "aria_label")
    )
    if any(term in haystack for term in _BASE_INPUT_TERMS):
        return base_price
    sell = str(target.get("proposed_price_yuan") or "").strip()
    return sell or base_price


async def fill_ctrip_room_price(
    page: Page,
    *,
    target: dict[str, Any],
    base_price: str,
) -> dict[str, Any]:
    """Fill the base price into the EBK pricing modal input.

    target comes from the page reader target_readback result, containing
    selector_hint, input_candidates, proposed_base_price_yuan, etc.

    Returns:
        {"status": "filled"|"mismatch"|"not_located"|"input_failed",
         "filled_value": str | None,
         "verified_value": str | None}
    """
    room_type = str(target.get("room_type") or "")
    checkin_date = str(target.get("checkin_date") or "")
    selector_hint = target.get("selector_hint")
    input_candidates = target.get("input_candidates") or []

    if not selector_hint and not input_candidates:
        # Try locating input candidates from the page
        candidates = await find_price_input_candidates(page)
        matched = match_candidates_to_room(candidates, room_type)
    else:
        matched = list(input_candidates)

    selected = matched[0] if matched else None
    if not selector_hint and selected:
        selector_hint = selected.get("selector_hint")

    if not selector_hint:
        return {
            "status": "not_located",
            "filled_value": None,
            "verified_value": None,
            "room_type": room_type,
            "checkin_date": checkin_date,
        }

    try:
        fill_value = _pick_fill_value(selected, base_price=base_price, target=target)
        handle = page.locator(selector_hint).first
        await handle.wait_for(state="visible", timeout=10000)
        # Select all existing content and replace
        await handle.click(click_count=3)
        await page.wait_for_timeout(250)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(fill_value, delay=80)
        await page.wait_for_timeout(500)

        readback = None
        try:
            readback = await handle.input_value()
        except Exception:
            pass

        expected = parse_price(fill_value)
        actual = parse_price(readback)
        if expected is not None and actual is not None and abs(expected - actual) < EPSILON:
            return {
                "status": "filled",
                "filled_value": fill_value,
                "verified_value": readback,
                "room_type": room_type,
                "checkin_date": checkin_date,
            }
        return {
            "status": "mismatch",
            "filled_value": fill_value,
            "verified_value": readback,
            "room_type": room_type,
            "checkin_date": checkin_date,
        }
    except Exception:
        return {
            "status": "input_failed",
            "filled_value": None,
            "verified_value": None,
            "room_type": room_type,
            "checkin_date": checkin_date,
        }


# ---------------------------------------------------------------------------
# submit_ctrip_price_changes
# ---------------------------------------------------------------------------


async def submit_ctrip_price_changes(
    page: Page,
    *,
    targets: list[dict[str, Any]] | None = None,
    artifact_dir: Path | None = None,
    platform_account_id: str | None = None,
) -> dict[str, Any]:
    """Click the save/confirm button on the EBK pricing page and wait for result.

    Takes a before screenshot (evidence), clicks the save button, waits for
    the page to settle, takes an after screenshot, and reads the body text
    to determine success/failure.

    Screenshots go to artifact_dir; they are not persisted in MySQL.

    targets contains the room/date/base-price values filled in this submit.
    The function returns ``executed`` only after each target is read back from
    the pricing calendar and matches its submitted base price.

    Returns:
        {"status": "executed"|"human_required"|"page_structure_changed"|"needs_review"|"failed",
         "message": str,
         "before_screenshot": str | None,
         "after_screenshot": str | None}
    """
    evidence = {
        "status": "failed",
        "message": "",
        "before_screenshot": None,
        "after_screenshot": None,
    }
    submitted_targets = [item for item in (targets or []) if isinstance(item, dict)]
    if not submitted_targets:
        evidence["status"] = "needs_review"
        evidence["message"] = "No submitted room/date target prices were supplied for verification."
        return evidence

    # --- Before screenshot ---
    before_path = None
    if artifact_dir is not None and platform_account_id is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        before_path = artifact_dir / f"ctrip-price-before-submit-{platform_account_id}-{ts}.png"
        try:
            await page.screenshot(path=str(before_path), full_page=False)
        except Exception:
            before_path = None
    evidence["before_screenshot"] = str(before_path) if before_path else None

    # --- Dismiss popups that might obscure the price modal button ---
    from app.domain.browser.ctrip_room_type_selector import dismiss_lightweight_popups

    await dismiss_lightweight_popups(page)

    initial_body_text = await _read_body_text(page)
    blocker = _blocker_status_from_text(initial_body_text)
    if blocker is not None:
        evidence["status"], evidence["message"] = blocker
        return evidence

    # --- Locate and click the save/confirm button inside the active price modal ---
    button_info = await _locate_price_modal_submit_button(page)
    if button_info is None:
        evidence["status"] = "page_structure_changed"
        evidence["message"] = "Could not locate save/confirm/submit button inside price modal."
        return evidence

    responses: list[Any] = []
    response_listener = None
    remove_listener = getattr(page, "remove_listener", None)
    try:
        on = getattr(page, "on", None)
        if callable(on):
            # 2026-09-15：playwright 1.49 的 remove_listener 会给 handler 挂
            # 包装属性，list.append 这类内建方法不接受 setattr，卸载时直接
            # AttributeError——必须用普通函数当监听器。
            def _capture_response(response: Any, sink: list[Any] = responses) -> None:
                sink.append(response)

            response_listener = _capture_response
            on("response", response_listener)
        await page.locator(str(button_info["selector"])).first.click(timeout=5000)
    except Exception:
        if response_listener is not None and callable(remove_listener):
            remove_listener("response", response_listener)
        evidence["status"] = "page_structure_changed"
        evidence["message"] = "Price modal submit button was located but could not be clicked."
        evidence["button_text"] = button_info.get("button_text")
        return evidence
    evidence["button_text"] = button_info.get("button_text")

    # --- Wait for an explicit submit result, never a body-wide marker ---
    confirmation = await _wait_for_submit_confirmation(
        page,
        responses,
        submitted_targets,
        page_save_clicker=_click_page_level_save_button,
    )
    if response_listener is not None and callable(remove_listener):
        remove_listener("response", response_listener)

    # --- After screenshot ---
    after_path = None
    if artifact_dir is not None and platform_account_id is not None:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        after_path = artifact_dir / f"ctrip-price-after-submit-{platform_account_id}-{ts}.png"
        try:
            await page.screenshot(path=str(after_path), full_page=False)
        except Exception:
            after_path = None
    evidence["after_screenshot"] = str(after_path) if after_path else None

    if confirmation.get("kind") == "blocker":
        evidence["status"] = confirmation["status"]
        evidence["message"] = confirmation["message"]
        return evidence
    if confirmation.get("kind") == "toast":
        evidence["success_evidence"] = confirmation.get("message")
    if confirmation.get("kind") == "response":
        evidence["response_status"] = confirmation.get("response_status")
        evidence["response_url"] = confirmation.get("response_url")
    if confirmation.get("status") != "confirmed":
        evidence["status"] = "needs_review"
        evidence["message"] = confirmation.get("message") or "Submit result could not be confirmed."
        return evidence

    # --- Read every submitted room/date back from the pricing calendar ---
    readbacks: list[dict[str, Any]] = []
    for target in submitted_targets:
        readback_target = {**target, "operation": "read_current_room_price"}
        try:
            readback = await _safe_current_rate_readback(page, readback_target)
        except Exception as exc:
            readback = {"status": "readback_failed", "error": str(exc)}
        readbacks.append(readback if isinstance(readback, dict) else {"status": "readback_failed"})

    evidence["readbacks"] = readbacks
    evidence["readback_count"] = len(readbacks)
    for target, readback in zip(submitted_targets, readbacks, strict=True):
        # 2026-09-15：校验口径改为日历「卖价」列（店长目标价）。EBK 底价由
        # 平台按卖价×(1-佣金)派生，与 sell_to_base(卖价/(1+佣金)) 口径不一致，
        # 按底价比对会把成功写入误判 needs_review；无卖价字段的旧 target
        # 保持底价比对兜底。
        proposed_sell = target.get("proposed_price_yuan")
        sell_verified = proposed_sell is not None and _prices_match(
            proposed_sell, readback.get("current_price_yuan")
        )
        base_verified = _prices_match(
            target.get("proposed_base_price_yuan"),
            readback.get("current_base_price_yuan"),
        )
        if readback.get("status") != "located" or not (sell_verified or base_verified):
            room_type = str(target.get("room_type") or "")
            checkin_date = str(target.get("checkin_date") or "")
            evidence["status"] = "needs_review"
            evidence["message"] = (
                f"Post-submit price readback did not confirm {room_type} on {checkin_date}."
            )
            evidence["failed_target"] = {
                "room_type": room_type,
                "checkin_date": checkin_date,
                "expected_price_yuan": proposed_sell,
                "actual_price_yuan": readback.get("current_price_yuan"),
                "expected_base_price_yuan": target.get("proposed_base_price_yuan"),
                "actual_base_price_yuan": readback.get("current_base_price_yuan"),
            }
            return evidence

    evidence["status"] = "executed"
    evidence["message"] = "Price change submitted and every target price was read back successfully."
    return evidence
