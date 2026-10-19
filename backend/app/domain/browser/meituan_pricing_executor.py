"""美团 EBK 价格页执行器（V9 Step 1）。

fill_meituan_room_price / submit_meituan_price_changes，模式对齐携程
ctrip_pricing_executor：填价 → 回读校验 → 弹窗内保存按钮 → 等待平台
保存响应/成功 toast → 逐目标回读确认。验证码/扫码/风控 → human_required；
页面结构不确定 → page_structure_changed / needs_review。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Page

from app.domain.browser.ctrip_room_type_selector import EPSILON, parse_price

PRICING_EXECUTOR_VERSION = "meituan_ebooking_pricing_executor_v1"

_BLOCKER_MARKERS = (
    "captcha",
    "验证码",
    "滑动",
    "扫码",
    "二维码",
    "风险",
    "risk control",
)
_LOGIN_MARKERS = ("login", "登录", "密码")


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
    expected_price = parse_price(str(target.get("proposed_base_price_yuan") or ""))
    if expected_price is None:
        return False
    expected_price_text = f"{expected_price:.2f}".rstrip("0").rstrip(".")
    price_pattern = (
        rf"(?<!\d){re.escape(expected_price_text)}(?:\.0+)?(?!\d)"
        if expected_price.is_integer()
        else rf"(?<!\d){re.escape(expected_price_text)}(?!\d)"
    )
    if re.search(price_pattern, normalized_payload) is None:
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
    """只接受美团域名下、携带本次目标价 payload 的写接口响应。"""
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
    if hostname != "meituan.com" and not hostname.endswith(".meituan.com"):
        return False
    endpoint = parsed.path.lower()
    has_price_scope = any(
        token in endpoint for token in ("price", "rate", "inventory", "calendar")
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


async def fill_meituan_room_price(
    page: Page,
    *,
    target: dict[str, Any],
    base_price: str,
) -> dict[str, Any]:
    """把底价填入美团 EBK 价格输入框并回读校验。"""
    room_type = str(target.get("room_type") or "")
    checkin_date = str(target.get("checkin_date") or "")
    selector_hint = target.get("selector_hint")
    input_candidates = target.get("input_candidates") or []

    if not selector_hint and input_candidates:
        first = input_candidates[0] if isinstance(input_candidates[0], dict) else {}
        selector_hint = first.get("selector_hint")

    if not selector_hint:
        return {
            "status": "not_located",
            "filled_value": None,
            "verified_value": None,
            "room_type": room_type,
            "checkin_date": checkin_date,
        }

    try:
        handle = page.locator(selector_hint).first
        await handle.wait_for(state="visible", timeout=10000)
        await handle.click(click_count=3)
        await page.wait_for_timeout(250)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(base_price, delay=80)
        await page.wait_for_timeout(500)

        readback = None
        try:
            readback = await handle.input_value()
        except Exception:
            pass

        expected = parse_price(base_price)
        actual = parse_price(readback)
        if expected is not None and actual is not None and abs(expected - actual) < EPSILON:
            return {
                "status": "filled",
                "filled_value": base_price,
                "verified_value": readback,
                "room_type": room_type,
                "checkin_date": checkin_date,
            }
        return {
            "status": "mismatch",
            "filled_value": base_price,
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


async def _read_success_toast(page: Page) -> str:
    try:
        result = await page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const nodes = Array.from(
                    document.querySelectorAll(
                        '[class*="toast"],[class*="Toast"],[class*="message"],[class*="Message"]'
                    )
                ).filter(visible);
                for (const node of nodes) {
                    const text = (node.innerText || '').trim();
                    if (text && (text.includes('成功') || text.includes('保存'))) {
                        return text.slice(0, 120);
                    }
                }
                return '';
            }
            """
        )
    except Exception:
        return ""
    return result if isinstance(result, str) else ""


async def _locate_submit_button(page: Page) -> dict[str, Any] | None:
    """B6 — 枚举可见按钮后按完整文案白名单选择唯一提交按钮。

    词表与目标按钮（提交并继续修改，提交后停留在当前页便于回读）来自
    page_health_config.json 的 meituan:pricing:calendar。无法唯一定位返回
    None，调用方按 page_structure_changed 处理，不点击任何按钮。
    """
    from app.domain.browser.pricing_submit_buttons import (
        choose_submit_button,
        resolve_pricing_button_config,
    )

    try:
        buttons = await page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
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
                return Array.from(
                    document.querySelectorAll('button, [role="button"], a[class*="btn"]')
                )
                    .filter(visible)
                    .map((button) => ({
                        selector: cssPath(button),
                        text: (button.innerText || '').trim().slice(0, 40),
                    }))
                    .filter((item) => item.text && item.selector);
            }
            """
        )
    except Exception:
        return None
    if not isinstance(buttons, list):
        return None
    config = resolve_pricing_button_config("meituan")
    choice = choose_submit_button(
        [button for button in buttons if isinstance(button, dict)],
        submit_labels=config.submit_labels,
        non_submit_labels=config.non_submit_labels,
        preferred=config.preferred_label,
    )
    if choice is None:
        return None
    return {
        "selector": choice.button.get("selector"),
        "button_text": choice.button.get("text"),
        "match_reason": choice.reason,
    }


async def _run_price_preview_and_verify(
    page: Page,
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """点击「预览修改结果」并校验预览内容含每个目标的房型与目标价。

    页面不会因未预览而禁用提交按钮（B5.1 实测），因此这一步是执行器强加的
    前置门禁：预览缺失目标房型或目标价 → needs_review，不进入提交。
    """
    from app.domain.browser.pricing_submit_buttons import resolve_pricing_button_config

    config = resolve_pricing_button_config("meituan")
    preview_label = config.preview_label
    if not preview_label:
        return {
            "status": "not_configured",
            "message": "page_health_config 缺少 meituan:pricing:calendar 的 preview_button_label。",
        }
    try:
        control = page.get_by_text(preview_label, exact=True).first
        if await control.count() == 0:
            return {
                "status": "not_found",
                "message": f"页面上没有找到「{preview_label}」按钮。",
            }
        await control.click(timeout=5_000)
        await page.wait_for_timeout(2_000)
    except Exception as exc:
        return {
            "status": "click_failed",
            "message": f"点击「{preview_label}」失败：{type(exc).__name__}: {exc}",
        }

    try:
        body_text = await page.locator("body").inner_text(timeout=6_000)
    except Exception:
        body_text = ""
    compact = _compact_preview_text(body_text)
    missing: list[str] = []
    for target in targets:
        room_type = str(target.get("room_type") or "").strip()
        price_value = parse_price(str(target.get("proposed_base_price_yuan") or ""))
        if room_type and room_type not in compact:
            missing.append(f"房型 {room_type}")
        if price_value is not None and f"{price_value:g}" not in compact:
            missing.append(f"目标价 {price_value:g}")
    if missing:
        return {
            "status": "mismatch",
            "message": "预览内容缺少目标：" + "、".join(missing[:8]),
            "preview_text_head": compact[:300],
        }
    return {
        "status": "verified",
        "message": "预览内容包含全部目标房型与目标价。",
        "preview_text_head": compact[:300],
    }


def _compact_preview_text(text: str) -> str:
    import re as _re

    return _re.sub(r"\s+", "", str(text or ""))


async def _wait_for_submit_confirmation(
    page: Page,
    responses: list[Any],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    for _ in range(40):
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
                }
            return {
                "kind": "response",
                "status": "confirmed",
                "message": "Price save response returned a successful status.",
            }

        success_toast = await _read_success_toast(page)
        if success_toast:
            return {"kind": "toast", "status": "confirmed", "message": success_toast}
        await page.wait_for_timeout(250)
    return {
        "kind": "uncertain",
        "status": "needs_review",
        "message": "Save clicked, but no submit response or explicit success toast was detected.",
    }


async def submit_meituan_price_changes(
    page: Page,
    *,
    targets: list[dict[str, Any]] | None = None,
    artifact_dir: Path | None = None,
    platform_account_id: str | None = None,
) -> dict[str, Any]:
    """点击美团 EBK 保存按钮并等待结果；仅当逐目标回读匹配才返回 executed。"""
    evidence: dict[str, Any] = {
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

    if artifact_dir is not None and platform_account_id is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        before_path = artifact_dir / f"meituan-price-before-submit-{platform_account_id}-{ts}.png"
        try:
            await page.screenshot(path=str(before_path), full_page=False)
        except Exception:
            before_path = None
        evidence["before_screenshot"] = str(before_path) if before_path else None

    initial_body_text = await _read_body_text(page)
    blocker = _blocker_status_from_text(initial_body_text)
    if blocker is not None:
        evidence["status"], evidence["message"] = blocker
        return evidence

    # B6 — 美团提交前必须先经过「预览修改结果」并校验预览内容包含目标与目标价。
    preview_check = await _run_price_preview_and_verify(page, submitted_targets)
    evidence["preview"] = preview_check
    if preview_check.get("status") != "verified":
        evidence["status"] = "needs_review"
        evidence["message"] = str(
            preview_check.get("message") or "预览修改结果未通过目标校验，已停止提交。"
        )
        return evidence

    button_info = await _locate_submit_button(page)
    if button_info is None:
        evidence["status"] = "page_structure_changed"
        evidence["message"] = "Could not locate save/confirm/submit button on Meituan EBK page."
        return evidence

    responses: list[Any] = []
    response_listener = None
    remove_listener = getattr(page, "remove_listener", None)
    try:
        on = getattr(page, "on", None)
        if callable(on):
            response_listener = responses.append
            on("response", response_listener)
        await page.locator(str(button_info["selector"])).first.click(timeout=5000)
    except Exception:
        if response_listener is not None and callable(remove_listener):
            remove_listener("response", response_listener)
        evidence["status"] = "page_structure_changed"
        evidence["message"] = "Submit button was located but could not be clicked."
        evidence["button_text"] = button_info.get("button_text")
        return evidence
    evidence["button_text"] = button_info.get("button_text")

    confirmation = await _wait_for_submit_confirmation(page, responses, submitted_targets)
    if response_listener is not None and callable(remove_listener):
        remove_listener("response", response_listener)

    if artifact_dir is not None and platform_account_id is not None:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        after_path = artifact_dir / f"meituan-price-after-submit-{platform_account_id}-{ts}.png"
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
    if confirmation.get("status") != "confirmed":
        evidence["status"] = "needs_review"
        evidence["message"] = confirmation.get("message") or "Submit result could not be confirmed."
        return evidence

    # 逐目标回读：通过各自 selector_hint 的输入框现值确认
    readbacks: list[dict[str, Any]] = []
    for target in submitted_targets:
        selector_hint = target.get("selector_hint")
        readback = await _read_input_value(page, selector_hint)
        readbacks.append(readback)
    evidence["readbacks"] = readbacks
    evidence["readback_count"] = len(readbacks)
    for target, value in zip(submitted_targets, readbacks, strict=True):
        if not _prices_match(target.get("proposed_base_price_yuan"), value):
            evidence["status"] = "needs_review"
            evidence["message"] = (
                f"Post-submit price readback did not confirm "
                f"{target.get('room_type')} on {target.get('checkin_date')}."
            )
            return evidence

    evidence["status"] = "executed"
    evidence["message"] = "Price change submitted and every target price was read back successfully."
    return evidence


async def _read_input_value(page: Page, selector_hint: Any) -> str | None:
    if not selector_hint:
        return None
    try:
        return await page.locator(str(selector_hint)).first.input_value()
    except Exception:
        return None
