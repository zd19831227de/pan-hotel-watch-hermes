# ruff: noqa: E402,E501
"""Ctrip EBK room-type selector for pricing page writes.

Extracted from scripts/ctrip_p0_room_type_selector.py and adapted to use
the system Playwright Page model (no CDP dependency).  Every function
receives a Playwright Page object managed by CtripEbookingBrowserService.
"""

from __future__ import annotations

import re
from time import monotonic
from typing import Any

from playwright.async_api import Page

from app.domain.browser.ctrip_common import (
    _is_ctrip_url,
    _looks_like_login_required,
    _looks_like_verification_required,
)
from app.domain.browser.js_loader import load_js

# 携程页面态分类的单一权威词表（B3 收紧）：
# - PRICING_PAGE_URL_PATHS：执行面白名单，仅日历页（方案 A，批量页不接入自动化）。
# - 主内容区特异信号：编辑动作词，或（卖价/底价列 + 房型列）组合。
#   左侧导航"房价房态/日历"在任何 EBK 页都会出现，不构成 pricing_page 证据。
PRICING_PAGE_URL_PATHS = ("/ebkovsroom/inventory/calendar",)
PRICING_PAGE_ACTION_TERMS = ("快速选择房型", "修改价格", "批量修改", "按周设置")
PRICING_PAGE_PRICE_COLUMN_TERMS = ("卖价", "底价")
PRICING_PAGE_ROOM_COLUMN_TERMS = ("房型",)
# 兼容旧 evidence 字段的词表视图（导航词不再参与判定，仅作信号展示）。
PRICING_PAGE_TERMS = (
    *PRICING_PAGE_ACTION_TERMS,
    *PRICING_PAGE_PRICE_COLUMN_TERMS,
    *PRICING_PAGE_ROOM_COLUMN_TERMS,
)

EPSILON = 0.02






# ---------------------------------------------------------------------------
# Page-state classification
# ---------------------------------------------------------------------------

def _on_pricing_url_path(normalized_url: str) -> bool:
    return any(path in normalized_url for path in PRICING_PAGE_URL_PATHS)


def _body_has_pricing_workspace_signal(body_text: str) -> bool:
    if any(term in body_text for term in PRICING_PAGE_ACTION_TERMS):
        return True
    has_price_column = any(term in body_text for term in PRICING_PAGE_PRICE_COLUMN_TERMS)
    has_room_column = any(term in body_text for term in PRICING_PAGE_ROOM_COLUMN_TERMS)
    return has_price_column and has_room_column


def classify_ctrip_pricing_page_state(url: str, body_text: str) -> str:
    """Classify the current Ctrip EBK page state.

    pricing_page requires BOTH the calendar URL whitelist (the only write
    surface) and a main-content pricing signal, so navigation-side terms
    on business-report or batch pages classify as
    authenticated_non_pricing_page instead.
    """
    normalized_url = url.lower()
    if any(token in normalized_url for token in ("captcha", "verify", "security")):
        return "verification_required"
    if _looks_like_login_required(normalized_url, body_text):
        return "login_required"
    if _looks_like_verification_required(normalized_url, body_text):
        return "verification_required"
    if not _is_ctrip_url(url):
        return "unknown"
    if not body_text:
        return "unknown"
    if _on_pricing_url_path(normalized_url) and _body_has_pricing_workspace_signal(body_text):
        return "pricing_page"
    return "authenticated_non_pricing_page"


# ---------------------------------------------------------------------------
# Async page helpers
# ---------------------------------------------------------------------------

async def wait_for_ctrip_pricing_page_state(
    page: Page,
    *,
    timeout_s: float = 15.0,
    poll_s: float = 1.0,
) -> str:
    """有界等待页面态收敛，返回最终 classify 结果。

    2026-09-14 实测：日历页主内容（快速选择房型/卖价底价列）异步渲染，
    叠加消息通知弹层与节假日提醒横幅时，goto + 固定短等待后正文里还没
    出现房价信号，会被误判 not_pricing_page 报「页面结构变化」。这里轮询
    到信号出现即返回；login/verification 是需人工的终态，出现即返回；
    超时返回最后一次分类结果（由调用方按结构变化兜底）。等待期间每轮
    顺手关闭轻量弹窗，避免弹层遮挡推迟信号出现。
    """
    deadline = monotonic() + max(timeout_s, 0.0)
    state = "unknown"
    while True:
        body_text = await read_visible_body(page)
        state = classify_ctrip_pricing_page_state(page.url, body_text)
        if state in ("pricing_page", "login_required", "verification_required"):
            return state
        remaining = deadline - monotonic()
        if remaining <= 0:
            return state
        await dismiss_lightweight_popups(page)
        try:
            await page.wait_for_timeout(min(int(poll_s * 1000), int(remaining * 1000)))
        except Exception:
            return state


async def read_visible_body(page: Page) -> str:
    """Read visible body text from the page."""
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""
    return re.sub(r"\n{3,}", "\n\n", body_text).strip()


async def dismiss_lightweight_popups(page: Page) -> None:
    """Dismiss all visible lightweight popups."""
    for _round in range(5):
        found = False
        for dismiss_text in ("知道了", "我知道了", "确定", "关闭"):
            try:
                dismiss_button = page.locator(
                    f"[class*='dialog'] button:has-text('{dismiss_text}'), "
                    f"[class*='modal'] button:has-text('{dismiss_text}'), "
                    f"[class*='popup'] button:has-text('{dismiss_text}'), "
                    f"[role='dialog'] button:has-text('{dismiss_text}'), "
                    f"button:has-text('{dismiss_text}')"
                ).first
                if await dismiss_button.count() > 0 and await dismiss_button.is_visible():
                    await dismiss_button.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                    found = True
                    break
            except Exception:
                pass
        if not found:
            break


# ---------------------------------------------------------------------------
# Room-type trigger detection
# ---------------------------------------------------------------------------

async def detect_room_type_trigger(page: Page) -> list[dict[str, Any]]:
    """Scan the DOM for room-type quick-select trigger elements."""
    try:
        results = await page.evaluate(load_js("detect_trigger"))
    except Exception:
        return []
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Room-type popup detection
# ---------------------------------------------------------------------------

async def detect_room_type_popup(page: Page) -> dict[str, Any]:
    """Scan the room-type selection popup."""
    try:
        result = await page.evaluate(load_js("detect_popup"))
    except Exception:
        return {"search_inputs": [], "checkboxes": [], "confirm_buttons": [], "room_items": []}
    if not isinstance(result, dict):
        return {"search_inputs": [], "checkboxes": [], "confirm_buttons": [], "room_items": []}
    return result


async def wait_for_room_type_popup(
    page: Page,
    *,
    timeout_s: float = 10.0,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    """有界等待房型选择弹窗渲染收敛，返回最终 detect 结果。

    2026-09-14：弹窗搜索框/房型列表异步渲染，触发点击后固定 1500ms 单次
    detect 会漏掉未渲染完的搜索框，误报「No search input」打断调价链路。
    这里轮询到搜索框/复选项/房型行任一信号出现即返回；login 类终态不在此
    层处理（弹窗内无登录态）。超时返回最后一次结果，由调用方按结构变化兜底。
    """
    deadline = monotonic() + max(timeout_s, 0.0)
    popup: dict[str, Any] = {
        "search_inputs": [],
        "checkboxes": [],
        "confirm_buttons": [],
        "room_items": [],
    }
    while True:
        popup = await detect_room_type_popup(page)
        if (
            popup.get("search_inputs")
            or popup.get("checkboxes")
            or popup.get("room_items")
        ):
            return popup
        remaining = deadline - monotonic()
        if remaining <= 0:
            return popup
        await dismiss_lightweight_popups(page)
        try:
            await page.wait_for_timeout(
                min(int(poll_s * 1000), int(remaining * 1000))
            )
        except Exception:
            return popup


# ---------------------------------------------------------------------------
# Safe click / fill
# ---------------------------------------------------------------------------

async def safe_click(page: Page, selector_hint: str, label: str) -> bool:
    """Click a visible element by its CSS selector hint."""
    try:
        element = page.locator(selector_hint).first
        await element.wait_for(state="visible", timeout=5000)
        await element.click()
        await page.wait_for_timeout(300)
        return True
    except Exception:
        return False


async def safe_fill_search(page: Page, selector: str, text: str) -> bool:
    """Fill and submit a search input."""
    try:
        element = page.locator(selector).first
        await element.wait_for(state="visible", timeout=5000)
        await element.click()
        await page.wait_for_timeout(200)
        await element.fill("")
        await page.wait_for_timeout(100)
        await element.type(text, delay=80)
        await page.wait_for_timeout(300)
        await element.press("Enter")
        await page.wait_for_timeout(2000)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Room selection in popup
# ---------------------------------------------------------------------------

async def select_matching_room_results(
    page: Page,
    search_selector: str,
    room_type: str,
    *,
    allow_filter: bool = True,
) -> bool:
    """In the room-type popup, select matching room checkboxes."""
    container_selector = await page.evaluate(load_js("popup_container"), search_selector)
    payload = {
        "containerSelector": container_selector,
        "roomType": room_type,
        "allowFilter": allow_filter,
    }
    for _attempt in range(3):
        result = await page.evaluate(load_js("check_matching_rooms"), payload)
        strategy = result.get("strategy", "")
        checked = bool(result.get("checked"))
        # 筛选切换（「全部房型」或退而取消「非直连」）会触发房型列表重渲染，
        # 需等一拍再重试，让目标房型行可见后再匹配。
        if strategy in ("all_room_types_filter_clicked", "non_direct_filter_cleared"):
            await page.wait_for_timeout(2000)
            continue
        await page.wait_for_timeout(800)
        return checked
    return False


async def click_room_selection_confirm(page: Page) -> bool:
    """Click the confirm button in the room-type selection popup."""
    # 2026-09-14 根因：playwright 1.49 的 Locator.filter 没有 visible 参数，
    # .filter(visible=True) 抛 TypeError 被下方 except 吞掉，确认按钮从未点
    # 成功过（execution 记录 8/11/14 均「Could not confirm room selection」）。
    # 改用 :visible 伪类；精确 id 优先——live CDP 实测弹窗确认按钮即
    # #j-chooseRatePlan-submit，所在 popover z 序在 modal-backdrop 之上可直点。
    selectors = (
        "#j-chooseRatePlan-submit:visible",
        "[class*='dialog'] button:has-text('确认'):visible",
        "[class*='modal'] button:has-text('确认'):visible",
        "[class*='popup'] button:has-text('确认'):visible",
        "[role='dialog'] button:has-text('确认'):visible",
        "[class*='dialog'] button:has-text('确定'):visible",
        "[class*='modal'] button:has-text('确定'):visible",
        "[class*='popup'] button:has-text('确定'):visible",
        "[role='dialog'] button:has-text('确定'):visible",
        "button:has-text('OK'):visible",
        "button:has-text('Confirm'):visible",
    )
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.count() == 0 or not await button.is_visible():
                continue
            await button.click(timeout=3000)
            await page.wait_for_timeout(2500)
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Price block detection and click
# ---------------------------------------------------------------------------

async def detect_price_block_candidates(
    page: Page, *, room_type: str, checkin_date: str
) -> dict[str, Any]:
    """Scan the calendar for price-block candidates."""
    payload = {"roomType": room_type, "checkinDate": checkin_date}
    try:
        result = await page.evaluate(load_js("price_block_candidates"), payload)
    except Exception:
        return {"status": "evaluate_failed", "day_column": None, "candidates": [], "debug": {}}
    if not isinstance(result, dict):
        return {"status": "unexpected_result", "day_column": None, "candidates": [], "debug": {}}
    return result


# 2026-09-14 实测：日历房型表在内部容器虚拟滚动，目标价格块常在视口外，
# 直接用检测时坐标 mouse.click 会落空或点到别的块。先把候选块滚进视口，
# 再重检刷新坐标；重检定位不到就不点（宁停不点错）。
_SCROLL_CANDIDATE_INTO_VIEW_JS = """/*lazy_page_collector:scroll_candidate_into_view*/
(selectorHint) => {
    if (!selectorHint) return false;
    let el = null;
    try { el = document.querySelector(selectorHint); } catch (err) { el = null; }
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const vw = window.innerWidth || document.documentElement.clientWidth;
    if (rect.top >= 0 && rect.bottom <= vh && rect.left >= 0 && rect.right <= vw) {
        return false;
    }
    let moved = false;
    let node = el.parentElement;
    while (node && node !== document.body) {
        const style = getComputedStyle(node);
        if (/(auto|scroll|overlay)/.test(style.overflowY)
            && node.scrollHeight > node.clientHeight) {
            const nodeRect = node.getBoundingClientRect();
            const delta = rect.top - nodeRect.top
                + rect.height / 2 - node.clientHeight / 2;
            if (delta !== 0) {
                node.scrollTop = Math.max(
                    0,
                    Math.min(node.scrollTop + delta, node.scrollHeight)
                );
                moved = true;
            }
        }
        node = node.parentElement;
    }
    const after = el.getBoundingClientRect();
    if (after.top < 0 || after.bottom > vh) {
        window.scrollBy(0, after.top - vh / 2 + after.height / 2);
        moved = true;
    }
    return moved;
}
"""


def resolve_price_block_click_center(
    candidate: dict[str, Any],
    *,
    scrolled: bool,
    fresh_candidates: list[dict[str, Any]] | None = None,
) -> tuple[float, float] | None:
    """决定价格块点击坐标：滚动会令旧视口坐标失效，安全取舍在此收敛。

    - 未滚动：沿用检测时坐标（原有行为）。
    - 已滚动：虚拟列表重渲染后必须在重检候选里按 (room_text, text) 重新
      定位同一价格块；定位不到返回 None，宁可不点也不按旧坐标误点其它房型。
    """

    if not scrolled:
        center_x = candidate.get("center_x")
        center_y = candidate.get("center_y")
        if center_x is None or center_y is None:
            return None
        return float(center_x), float(center_y)
    key = (str(candidate.get("room_text") or ""), str(candidate.get("text") or ""))
    for fresh in fresh_candidates or []:
        if not isinstance(fresh, dict):
            continue
        if (str(fresh.get("room_text") or ""), str(fresh.get("text") or "")) != key:
            continue
        center_x = fresh.get("center_x")
        center_y = fresh.get("center_y")
        if center_x is None or center_y is None:
            return None
        return float(center_x), float(center_y)
    return None


def order_candidates_by_canonical_breakfast(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """统一无早口径：候选按 无早（标签/干净优先于预售/钟点）→ 其余 排序，
    同组内保持原有 score 顺序（排序稳定）。"""

    from app.domain.browser.pricing_product_selection import canonical_product_tier

    def _rank(candidate: dict[str, Any]) -> tuple[int, int]:
        tier = canonical_product_tier(candidate.get("room_text"))
        if tier is None:
            return (1, 9)
        return (0, tier)

    return sorted(candidates, key=_rank)


async def click_first_price_block(
    page: Page, *, room_type: str, checkin_date: str
) -> bool:
    """Try up to 3 price block candidates (breakfast-none first, scrolled into view)."""
    result = await detect_price_block_candidates(
        page, room_type=room_type, checkin_date=checkin_date
    )
    candidates = order_candidates_by_canonical_breakfast(
        [item for item in (result.get("candidates") or []) if isinstance(item, dict)]
    )
    if not candidates:
        return False
    for candidate in candidates[:3]:
        center_x = candidate.get("center_x")
        center_y = candidate.get("center_y")
        if center_x is None or center_y is None:
            continue
        try:
            scrolled = bool(
                await page.evaluate(
                    _SCROLL_CANDIDATE_INTO_VIEW_JS, candidate.get("selector_hint")
                )
            )
        except Exception:
            scrolled = False
        if scrolled:
            await page.wait_for_timeout(600)
            fresh_result = await detect_price_block_candidates(
                page, room_type=room_type, checkin_date=checkin_date
            )
            center = resolve_price_block_click_center(
                candidate,
                scrolled=True,
                fresh_candidates=list(fresh_result.get("candidates") or []),
            )
            if center is None:
                continue
            center_x, center_y = center
        try:
            await page.mouse.click(float(center_x), float(center_y))
            await page.wait_for_timeout(2000)
            if await _check_price_modal_open(page):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Price input detection and matching
# ---------------------------------------------------------------------------

async def find_price_input_candidates(page: Page) -> list[dict[str, Any]]:
    """Find visible price input candidates on the pricing page."""
    try:
        candidates = await page.evaluate(load_js("find_price_inputs"))
    except Exception:
        return []
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


# 价格输入候选语义词：nearby/placeholder 含价格词加分，日期类输入直接排除。
# 2026-09-15 实测事故：find_price_inputs.js 词表乱码致全部 match_score=0，
# 全 0 平分时按原序取第一个可编辑输入框=日期框 #s_dateYearMode，点击弹
# 出日期选择器拦截后续操作 -> input_failed（execution 记录 17）。
_PRICE_INPUT_TERMS = ("卖价", "底价", "价格", "元", "¥", "price")
# 'date' 用词元匹配防误伤（update 类名不含独立 date 词元）。
_DATE_INPUT_WORD_RE = re.compile(r"(?:^|[^a-z])date(?:[^a-z]|$)", re.IGNORECASE)
_DATE_INPUT_MARKERS = ("日期", "YYYY", "年")


def _is_date_like_input_candidate(candidate: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(candidate.get(key) or "")
        for key in ("nearby_text", "placeholder", "aria_label", "selector_hint", "value")
    )
    if not haystack:
        return False
    if any(marker in haystack.upper() for marker in _DATE_INPUT_MARKERS):
        return True
    return bool(_DATE_INPUT_WORD_RE.search(haystack))


def match_candidates_to_room(
    candidates: list[dict[str, Any]], room_type: str
) -> list[dict[str, Any]]:
    """Score and sort price input candidates by relevance to the target room type.

    填价入口绝不允许落在日期输入框上（弹出日期选择器会拦截后续点击），
    日期类候选一律排到末尾；同分时价格语义词（卖价/底价/元）优先，
    再叠加房型名命中加分。
    """
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for candidate in candidates:
        haystack = " ".join(
            str(candidate.get(key) or "")
            for key in ("nearby_text", "placeholder", "aria_label", "value")
        )
        score = candidate.get("match_score", 0) or 0
        if room_type and room_type in haystack:
            score += 2
        if any(term in haystack for term in _PRICE_INPUT_TERMS):
            score += 1
        date_penalty = 1 if _is_date_like_input_candidate(candidate) else 0
        scored.append((score, date_penalty, candidate))
    # 日期类候选排到末尾（不管分数多高），再按分数降序。
    scored.sort(key=lambda triple: (triple[1], -triple[0]))
    return [candidate for _, _, candidate in scored]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _check_price_modal_open(page: Page) -> bool:
    """Check if the price edit modal/dialog is visible on the page."""
    indicators = [
        page.get_by_text("修改价格", exact=True).first,
        page.get_by_text("底价", exact=True).first,
        page.locator("[role='dialog']").filter(has_text="价格").first,
        page.locator("[class*='dialog']").filter(has_text="价格").first,
        page.locator("[class*='modal']").first,
    ]
    for indicator in indicators:
        try:
            if await indicator.count() > 0 and await indicator.is_visible():
                return True
        except Exception:
            pass
    return False


def parse_price(value: str | None) -> float | None:
    """Parse a price string to float, returning None on failure."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d+\.?\d*)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None
