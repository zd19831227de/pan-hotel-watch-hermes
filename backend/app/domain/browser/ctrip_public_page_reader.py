from __future__ import annotations

from contextlib import suppress
import re
import random
from datetime import datetime, timedelta
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.domain.browser.ctrip_public_competitor import (
    _extract_public_calendar_tomorrow_snapshot,
    _normalize_public_structured_room_rates,
)


PUBLIC_CALENDAR_CELL_KEYS = {
    "text",
    "class_name",
    "data_d",
    "aria_label",
    "aria_disabled",
    "aria_selected",
    "price_text",
    "x",
    "y",
    "width",
    "height",
}


def _public_calendar_cell_has_day_and_price(cell: dict[str, Any]) -> bool:
    text = _clean_public_calendar_cell_text(cell.get("text"))
    if not text or len(text) > 80:
        return False
    aria_label = _clean_public_calendar_cell_text(cell.get("aria_label"))
    price_text = _clean_public_calendar_cell_text(cell.get("price_text"))
    data_d = _clean_public_calendar_cell_text(cell.get("data_d"))
    return _public_calendar_cell_has_day(
        text=text,
        aria_label=aria_label,
        data_d=data_d,
    ) and _public_calendar_cell_has_price(
        text=text,
        aria_label=aria_label,
        price_text=price_text,
    )


def _clean_public_calendar_cell_text(value: Any) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()


def _public_calendar_cell_has_day(
    *,
    text: str,
    aria_label: str,
    data_d: str,
) -> bool:
    return bool(
        re.search(r"\b\d{1,2}\b", text)
        or re.search(r"20\d{2}-\d{1,2}-\d{1,2}", data_d)
        or re.search(r"20\d{2}.*?\d{1,2}.*?\d{1,2}", aria_label)
    )


def _public_calendar_cell_has_price(
    *,
    text: str,
    aria_label: str,
    price_text: str,
) -> bool:
    if _public_calendar_cell_has_plain_price(price_text):
        return True
    if _public_calendar_cell_has_labeled_price(aria_label):
        return True
    if _public_calendar_cell_has_labeled_price(text):
        return True
    numbers = [
        int(match.group(1))
        for match in re.finditer(r"(?<!\d)(\d{2,5})(?!\d)", text)
    ]
    day_numbers = {
        int(match.group(1))
        for match in re.finditer(r"(?<!\d)(\d{1,2})(?!\d)", text)
    }
    return any(
        50 <= amount <= 20000 and amount not in day_numbers
        for amount in numbers
    )


def _public_calendar_cell_has_plain_price(value: str) -> bool:
    return any(
        50 <= int(match.group(1)) <= 20000
        for match in re.finditer(r"(?<!\d)(\d{2,5})(?!\d)", value)
    )


def _public_calendar_cell_has_labeled_price(value: str) -> bool:
    return bool(
        re.search(
            r"(?:CNY|price|价格|价|￥|¥|楼)\D{0,16}(\d{2,5})",
            value,
            flags=re.IGNORECASE,
        )
    )


# Browser-side readers for public Ctrip hotel pages. They only read visible DOM
# state and return structured evidence for the pure parser module.
async def _ensure_public_room_rates_loaded(page: Page) -> list[dict[str, Any]]:
    selectors = [
        '[class*="saleRoomItemBox"] [class*="displayPrice"]',
        '[class*="commonRoomCard"] [class*="displayPrice"]',
        "text=预订",
    ]
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(timeout=6_000, state="visible")
            await _reset_public_room_rate_scroll_position(page)
            collected_rates = await _safe_public_room_rates(page)
            clicked_texts: set[str] = set()
            for _attempt in range(2):
                await _expand_public_room_rate_cards(page, clicked_texts=clicked_texts)
                collected_rates = _merge_public_room_rate_samples(
                    collected_rates,
                    await _safe_public_room_rates(page),
                )
                collected_rates = await _scroll_public_room_rate_cards(
                    page,
                    collected_rates=collected_rates,
                )
            return collected_rates
        except PlaywrightError:
            continue
    return []


async def _reset_public_room_rate_scroll_position(page: Page) -> None:
    try:
        await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const hasRoomRateNodes = (el) => Boolean(
                    el && el.querySelector('[class*="commonRoomCard__"], [class*="saleRoomItemBox__"]')
                );
                const containers = Array.from(document.querySelectorAll('main, section, div'))
                    .filter((el) => {
                        if (!visible(el) || !hasRoomRateNodes(el)) {
                            return false;
                        }
                        const style = window.getComputedStyle(el);
                        const overflow = `${style.overflowY || ''} ${style.overflow || ''}`;
                        return /(auto|scroll|overlay)/.test(overflow)
                            && el.scrollHeight > el.clientHeight + 80;
                    })
                    .slice(0, 4);
                for (const el of containers) {
                    el.scrollTop = 0;
                }
            }"""
        )
        await page.wait_for_timeout(_public_room_rate_wait_ms(180, 360))
    except (AttributeError, TypeError, PlaywrightError):
        return


def _merge_public_room_rate_samples(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    best_by_plan: dict[tuple[Any, Any], dict[str, Any]] = {}
    plan_order: dict[tuple[Any, Any], int] = {}
    for rate in [*existing, *incoming]:
        if not isinstance(rate, dict):
            continue
        price = rate.get("price")
        amount = price.get("amount") if isinstance(price, dict) else None
        if not isinstance(amount, int):
            continue
        key = (rate.get("room_type"), rate.get("breakfast"))
        plan_order.setdefault(key, len(plan_order))
        current = best_by_plan.get(key)
        current_price = current.get("price") if isinstance(current, dict) else None
        current_amount = (
            current_price.get("amount") if isinstance(current_price, dict) else None
        )
        if current is None or not isinstance(current_amount, int) or amount < current_amount:
            best_by_plan[key] = rate
    return sorted(
        best_by_plan.values(),
        key=lambda rate: plan_order.get((rate.get("room_type"), rate.get("breakfast")), 9999),
    )[:40]


async def _expand_public_room_rate_cards(
    page: Page,
    *,
    clicked_texts: set[str] | None = None,
) -> None:
    expansion_selectors = [
        "text=/展示其余\\s*\\d*\\s*个?房型/",
        "text=/展示其余/",
        "text=/展开\\s*(?:全部)?房型/",
    ]
    if clicked_texts is None:
        clicked_texts = set()
    for attempt in range(3):
        clicked_any = False
        for selector in expansion_selectors:
            try:
                locator = page.locator(selector)
                count = min(await locator.count(), 12)
                for index in range(count):
                    button = locator.nth(index)
                    text = (await button.inner_text(timeout=1_000)).strip()
                    if not text or "收起" in text or text in clicked_texts:
                        continue
                    clicked_texts.add(text)
                    try:
                        await button.scroll_into_view_if_needed(timeout=1_000)
                        await page.wait_for_timeout(_public_room_rate_wait_ms(180, 360))
                    except (AttributeError, PlaywrightError):
                        pass
                    await button.click(timeout=1_500)
                    await page.wait_for_timeout(_public_room_rate_wait_ms(280, 520))
                    clicked_any = True
                if clicked_any:
                    break
            except PlaywrightError:
                continue
        if not clicked_any:
            if clicked_texts:
                break
            if attempt < 2:
                await page.wait_for_timeout(_public_room_rate_wait_ms(320, 620))
            continue
        await page.wait_for_timeout(_public_room_rate_wait_ms(360, 680))


async def _scroll_public_room_rate_cards(
    page: Page,
    *,
    collected_rates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    previous_card_count = -1
    stable_rounds = 0
    rates = collected_rates or []
    try:
        for _attempt in range(5):
            scroll_jitter = _public_room_rate_scroll_jitter()
            state = await page.evaluate(
                """(scrollJitter) => {
                    const numberOr = (value, fallback) => {
                        const number = Number(value);
                        return Number.isFinite(number) ? number : fallback;
                    };
                    const windowFactor = Math.min(
                        Math.max(numberOr(scrollJitter && scrollJitter.windowFactor, 0.5), 0.25),
                        0.75
                    );
                    const windowMin = Math.min(
                        Math.max(numberOr(scrollJitter && scrollJitter.windowMin, 420), 260),
                        620
                    );
                    const containerFactor = Math.min(
                        Math.max(numberOr(scrollJitter && scrollJitter.containerFactor, 0.55), 0.3),
                        0.8
                    );
                    const containerMin = Math.min(
                        Math.max(numberOr(scrollJitter && scrollJitter.containerMin, 360), 220),
                        580
                    );
                    const visible = (el) => {
                        if (!el) {
                            return false;
                        }
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const hasClassTokenPrefix = (el, prefix) => (
                        String(el && el.className || '')
                            .split(/\\s+/)
                            .some((token) => token.startsWith(prefix))
                    );
                    const cardCount = Array.from(
                        document.querySelectorAll('[class*="commonRoomCard__"]')
                    ).filter((card) => (
                        visible(card) && hasClassTokenPrefix(card, 'commonRoomCard__')
                    )).length;
                    const beforeY = window.scrollY || document.documentElement.scrollTop || 0;
                    let movedContainers = 0;
                    const containers = Array.from(document.querySelectorAll('main, section, div'))
                        .filter((el) => {
                            if (!visible(el)) {
                                return false;
                            }
                            const style = window.getComputedStyle(el);
                            const overflow = `${style.overflowY || ''} ${style.overflow || ''}`;
                            if (!/(auto|scroll|overlay)/.test(overflow)) {
                                return false;
                            }
                            if (el.scrollHeight <= el.clientHeight + 80) {
                                return false;
                            }
                            return Boolean(
                                el.querySelector('[class*="commonRoomCard__"], [class*="saleRoomItemBox__"]')
                            );
                        })
                        .slice(0, 2);
                    for (const el of containers) {
                        const style = window.getComputedStyle(el);
                        const overflow = `${style.overflowY || ''} ${style.overflow || ''}`;
                        if (!/(auto|scroll|overlay)/.test(overflow)) {
                            continue;
                        }
                        if (el.scrollHeight <= el.clientHeight + 80) {
                            continue;
                        }
                        const beforeTop = el.scrollTop;
                        const containerStep = Math.min(
                            Math.max(Math.floor(el.clientHeight * containerFactor), containerMin),
                            680
                        );
                        el.scrollTop = Math.min(
                            el.scrollTop + containerStep,
                            Math.max(el.scrollHeight - el.clientHeight, 0)
                        );
                        if (el.scrollTop !== beforeTop) {
                            movedContainers += 1;
                        }
                    }
                    if (movedContainers === 0) {
                        const scrollStep = Math.min(
                            Math.max(Math.floor(window.innerHeight * windowFactor), windowMin),
                            680
                        );
                        window.scrollBy(0, scrollStep);
                    }
                    const afterY = window.scrollY || document.documentElement.scrollTop || 0;
                    return {
                        cardCount,
                        moved: afterY !== beforeY || movedContainers > 0,
                    };
                }""",
                scroll_jitter,
            )
            if not isinstance(state, dict):
                return rates
            card_count = int(state.get("cardCount") or 0)
            if card_count > previous_card_count:
                previous_card_count = card_count
                stable_rounds = 0
            else:
                stable_rounds += 1
            await page.wait_for_timeout(_public_room_rate_wait_ms(300, 650))
            if collected_rates is not None:
                rates = _merge_public_room_rate_samples(
                    rates,
                    await _safe_public_room_rates(page),
                )
            if card_count > 0 and stable_rounds >= 2:
                return rates
    except (AttributeError, TypeError, PlaywrightError):
        return rates
    return rates


def _public_room_rate_wait_ms(min_ms: int, max_ms: int) -> int:
    return random.randint(min_ms, max_ms)


def _public_room_rate_scroll_jitter() -> dict[str, float | int]:
    return {
        "windowFactor": random.uniform(0.38, 0.62),
        "windowMin": random.randint(320, 520),
        "containerFactor": random.uniform(0.45, 0.7),
        "containerMin": random.randint(260, 460),
    }


async def _safe_public_room_rates(page: Page) -> list[dict[str, Any]]:
    try:
        rates = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const clean = (value) => String(value || '')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n\\s*/g, '\\n')
                    .trim();
                const firstVisibleText = (root, selectors) => {
                    for (const selector of selectors) {
                        for (const el of Array.from(root.querySelectorAll(selector))) {
                            const text = clean(el.innerText || el.textContent || '');
                            if (text && visible(el)) {
                                return text;
                            }
                        }
                    }
                    return '';
                };
                const hasClassTokenPrefix = (el, prefix) => (
                    String(el && el.className || '')
                        .split(/\\s+/)
                        .some((token) => token.startsWith(prefix))
                );
                const priceAmount = (value) => {
                    const match = String(value || '').match(/[¥￥]\\s*(\\d{2,5})/);
                    if (!match) {
                        return null;
                    }
                    const amount = Number.parseInt(match[1], 10);
                    if (!Number.isFinite(amount) || amount < 50 || amount > 20000) {
                        return null;
                    }
                    return amount;
                };
                const discountAmount = (value) => {
                    const match = String(value || '').match(/优惠\\s*(\\d{1,5})/);
                    if (!match) {
                        return null;
                    }
                    const amount = Number.parseInt(match[1], 10);
                    if (!Number.isFinite(amount) || amount <= 0 || amount > 20000) {
                        return null;
                    }
                    return amount;
                };
                const isRoomTitleCandidate = (value) => {
                    const text = clean(value);
                    if (!text || text.length > 120) {
                        return false;
                    }
                    if (/^[\\d.]+$/.test(text) || /^[\\d.]+\\s*分$/.test(text)) {
                        return false;
                    }
                    if (/[¥￥]\\s*\\d{2,5}|优惠\\s*\\d{1,5}/.test(text)) {
                        return false;
                    }
                    if (/早餐|无早|含早|预订|在线付|到店付|立即确认|免费取消|房型摘要|可住人数|今日价格|房间详情|展示其余|展示额外|房型价格|仅剩|房量紧张|满房|订完|不可订|Wi-Fi|平方米|有窗|禁烟/.test(text)) {
                        return false;
                    }
                    if (/^\\d+\\s*张.*床$|^\\d+\\.\\d+\\s*米.*床$/.test(text)) {
                        return false;
                    }
                    return true;
                };
                const roomPattern = (
                    /(?:大床房|大床|双床房|双床|家庭房|亲子房|套房|标间|标准间|标准房|商务|高级|豪华|景观|城景|江景|零压|行政|悦享|静享|尊享|精英|客房|单人间|双人间|三人间)/
                );
                const breakfastPattern = (
                    /无早餐|无早|\\d+\\s*份\\s*(?:自助)?(?:早餐|早)|[一二两三]\\s*份\\s*(?:自助)?(?:早餐|早)|自助早餐|含早|含早餐|单早|双早|三早|早餐/
                );
                const availabilityPattern = (
                    /房量紧张|仅剩\\s*\\d+\\s*间|剩余\\s*\\d+\\s*间|满房|订完|不可订|无房/
                );
                const roomCards = Array.from(
                    document.querySelectorAll('[class*="commonRoomCard__"]')
                ).filter((card) => (
                    visible(card) && hasClassTokenPrefix(card, 'commonRoomCard__')
                ));
                const rowsFromCards = [];
                for (const card of roomCards) {
                    const cardText = clean(card.innerText || '');
                    const titleText = firstVisibleText(card, [
                        '[class*="commonRoomCard-title"]',
                        '[class*="roomName"]',
                        '[class*="RoomName"]',
                        '[class*="room-title"]',
                        '[class*="RoomTitle"]',
                        '[class*="title"]',
                        'h1',
                        'h2',
                        'h3',
                        'strong',
                    ]);
                    const fallbackRoomName = cardText.split('\\n').find((line) => (
                        isRoomTitleCandidate(line)
                    )) || '';
                    const roomName = isRoomTitleCandidate(titleText)
                        ? titleText
                        : (fallbackRoomName || cardText.split('\\n').find((line) => (
                            roomPattern.test(line)
                        )) || '');
                    const saleRows = Array.from(
                        card.querySelectorAll('[class*="saleRoomItemBox__"]')
                    ).filter((row) => (
                        visible(row) && hasClassTokenPrefix(row, 'saleRoomItemBox__')
                    ));
                    for (const row of saleRows) {
                        rowsFromCards.push({ card, row, roomName });
                    }
                }
                const rowElements = rowsFromCards.length
                    ? rowsFromCards
                    : Array.from(document.querySelectorAll('[class*="saleRoomItemBox__"]'))
                        .filter((row) => (
                            visible(row) && hasClassTokenPrefix(row, 'saleRoomItemBox__')
                        ))
                        .map((row) => ({
                            card: row.closest('[class*="commonRoomCard__"]') || row,
                            row,
                            roomName: '',
                        }));
                const rates = [];
                for (const item of rowElements) {
                    const rowText = clean(item.row.innerText || '');
                    if (!rowText || rowText.length > 1800) {
                        continue;
                    }
                    if (!/早餐|无早|含早|单早|双早|三早|预订|在线付|到店付/.test(rowText)) {
                        continue;
                    }
                    const displayPriceText = firstVisibleText(item.row, [
                        '[class*="displayPrice"]',
                        '[class*="realPrice"]',
                    ]);
                    const amount = priceAmount(displayPriceText);
                    if (amount === null) {
                        continue;
                    }
                    const deletePriceText = firstVisibleText(item.row, [
                        '[class*="deletePrice"]',
                        '[class*="originPrice"]',
                        '[class*="marketPrice"]',
                    ]);
                    const discountText = firstVisibleText(item.row, [
                        '[class*="discount"]',
                        '[class*="Discount"]',
                        '[class*="promotion"]',
                        '[class*="Promotion"]',
                        '[class*="coupon"]',
                        '[class*="Coupon"]',
                    ]) || (rowText.match(/优惠\\s*\\d{1,5}/) || [''])[0];
                    const discount = discountAmount(discountText);
                    const breakfastMatch = rowText.match(breakfastPattern);
                    const roomName = clean(item.roomName)
                        || clean((item.card && item.card.innerText || '')
                            .split('\\n')
                            .find((line) => isRoomTitleCandidate(line)) || '')
                        || clean((item.card && item.card.innerText || '')
                            .split('\\n')
                            .find((line) => roomPattern.test(line)) || '');
                    const availabilityMatch = rowText.match(availabilityPattern);
                    const availability = availabilityMatch
                        ? availabilityMatch[0].replace(/\\s+/g, '')
                        : '';
                    const breakfast = breakfastMatch
                        ? breakfastMatch[0].replace(/\\s+/g, '')
                        : '';
                    rates.push({
                        room_type: roomName.slice(0, 120),
                        breakfast,
                        price: {
                            amount,
                            currency: 'CNY',
                            display: `¥${amount}`,
                        },
                        original_price: deletePriceText ? {
                            display: deletePriceText,
                            amount: priceAmount(deletePriceText),
                        } : null,
                        discount: discount !== null ? {
                            display: `优惠${discount}`,
                            amount: discount,
                        } : null,
                        availability,
                        source_room_line: roomName.slice(0, 160),
                        source_breakfast_line: breakfast,
                        source_price_line: displayPriceText,
                        source_discount_line: discountText || '',
                        source_text: rowText.slice(0, 600),
                        source_card_text: clean(
                            item.card && item.card.innerText || ''
                        ).slice(0, 1800),
                        source: 'room_rate_dom_display_price',
                    });
                }
                return rates.slice(0, 40);
            }""",
        )
    except (AttributeError, TypeError, PlaywrightError):
        return []
    if not isinstance(rates, list):
        return []
    return _normalize_public_structured_room_rates(rates)


async def _safe_public_date_evidence_text(page: Page) -> str:
    try:
        lines = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const clean = (value) => String(value || '')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n\\s*/g, '\\n')
                    .trim();
                const datePattern = (
                    /入住|离店|今天|明天|今日|明日|周[一二三四五六日天]|20\\d{2}-\\d{1,2}-\\d{1,2}|\\d{1,2}月\\d{1,2}日?/
                );
                const selectors = [
                    '[class*="date"]',
                    '[class*="Date"]',
                    '[class*="check"]',
                    '[class*="Check"]',
                    '[class*="night"]',
                    '[class*="Night"]',
                    'input',
                    '[aria-label]',
                    '[title]',
                ].join(', ');
                const values = [];
                const addValue = (value) => {
                    const text = clean(value);
                    if (!text || text.length > 180 || !datePattern.test(text)) {
                        return;
                    }
                    values.push(text);
                };
                for (const el of Array.from(document.querySelectorAll(selectors))) {
                    if (!visible(el)) {
                        continue;
                    }
                    addValue(el.innerText || el.textContent || '');
                    addValue(el.getAttribute('aria-label') || '');
                    addValue(el.getAttribute('title') || '');
                    addValue(el.value || '');
                }
                return Array.from(new Set(values)).slice(0, 24);
            }""",
        )
    except PlaywrightError:
        return ""
    if not isinstance(lines, list):
        return ""
    return "\n".join(str(line) for line in lines if str(line).strip())

async def _safe_public_calendar_tomorrow_snapshot(
    *,
    page: Page,
    current_date_context: dict[str, Any],
) -> dict[str, Any] | None:
    check_in = current_date_context.get("check_in")
    if not isinstance(check_in, str):
        return None
    try:
        current_date = datetime.fromisoformat(check_in).date()
    except ValueError:
        return None
    tomorrow = current_date + timedelta(days=1)
    try:
        if not await _open_public_calendar_popup(page):
            return None
        await page.wait_for_timeout(500)
        calendar_cells = await _safe_public_calendar_cells(page)
    except PlaywrightError:
        return None
    return _extract_public_calendar_tomorrow_snapshot(
        cells=calendar_cells,
        tomorrow=tomorrow,
    )

async def _open_public_calendar_popup(page: Page) -> bool:
    if await _wait_for_public_calendar_popup(page, timeout_ms=300):
        return True
    if await _click_public_calendar_date_range_opener(page):
        if await _wait_for_public_calendar_popup(page):
            return True

    selectors = [
        "text=入住",
        "text=离店",
        '[class*="date"]',
        '[class*="Date"]',
        '[class*="time"]',
        '[class*="Time"]',
    ]
    for selector in selectors:
        try:
            locators = await page.locator(selector).all()
        except PlaywrightError:
            continue
        for locator in locators[:16]:
            try:
                text = await locator.inner_text(timeout=800)
            except PlaywrightError:
                continue
            compact = re.sub(r"\s+", "", text)
            if not compact or not any(token in compact for token in ("入住", "离店", "晚")):
                continue
            try:
                await locator.click(timeout=2_000)
            except PlaywrightError:
                continue
            if await _wait_for_public_calendar_popup(page):
                return True
    return False


async def _click_public_calendar_date_range_opener(page: Page) -> bool:
    try:
        handle = await page.evaluate_handle(
            """() => {
                const clean = (value) => String(value || '')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n\\s*/g, '\\n')
                    .trim();
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const dateRangePattern = /\\d{1,2}月\\d{1,2}日[\\s\\S]{0,40}-[\\s\\S]{0,40}\\d{1,2}月\\d{1,2}日/;
                const excludedClassPattern = /review|comment|facility|policy|swiper/i;
                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('body *'))) {
                    if (!visible(el)) {
                        continue;
                    }
                    const text = clean(el.innerText || el.textContent || '');
                    if (!text || text.length > 220) {
                        continue;
                    }
                    if (!dateRangePattern.test(text) || !text.includes('晚')) {
                        continue;
                    }
                    const className = String(el.className || '');
                    if (excludedClassPattern.test(className)) {
                        continue;
                    }
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 80 || rect.width > 760 || rect.height < 20 || rect.height > 140) {
                        continue;
                    }
                    candidates.push({
                        el,
                        area: rect.width * rect.height,
                        textLength: text.length,
                    });
                }
                candidates.sort((left, right) => {
                    if (left.textLength !== right.textLength) {
                        return left.textLength - right.textLength;
                    }
                    return left.area - right.area;
                });
                return candidates.length ? candidates[0].el : null;
            }"""
        )
    except PlaywrightError:
        return False
    try:
        element = handle.as_element()
        if element is None:
            return False
        try:
            await element.click(timeout=2_000)
            return True
        except PlaywrightError:
            return False
    finally:
        with suppress(Exception):
            await handle.dispose()


async def _wait_for_public_calendar_popup(page: Page, *, timeout_ms: int = 1_200) -> bool:
    try:
        await page.wait_for_function(
            """() => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                return Array.from(
                    document.querySelectorAll('[role="gridcell"][data-d], td[data-d], [data-d][aria-label*="年"]')
                ).some(visible);
            }""",
            timeout=timeout_ms,
        )
        return True
    except PlaywrightError:
        return False


async def _safe_public_calendar_cells(page: Page) -> list[dict[str, Any]]:
    try:
        cells = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const clean = (value) => String(value || '')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n\\s*/g, '\\n')
                    .trim();
                const calendarSelector = [
                    '[class*="calendar"]',
                    '[class*="Calendar"]',
                    '[class*="datePicker"]',
                    '[class*="DatePicker"]',
                    '[role="grid"]',
                    '[class*="pop"]',
                    '[class*="Pop"]',
                ].join(', ');
                const calendarTextPattern = (
                    /202\\d年\\d{1,2}月|周日|周一|周二|周三|周四|周五|周六|入住/
                );
                const calendarRoots = Array.from(
                    document.querySelectorAll(calendarSelector)
                ).filter((el) => {
                    const text = clean(el.innerText || '');
                    const role = String(el.getAttribute('role') || '').toLowerCase();
                    const className = String(el.className || '');
                    const looksLikeCalendarContainer = role === 'grid'
                        || /calendar|datepicker/i.test(className);
                    const hasCalendarCells = !!el.querySelector(
                        '[role="gridcell"][data-d], td[data-d], [data-d][aria-label*="年"]'
                    );
                    const hasDatedCalendarCells = !!el.querySelector(
                        '[role="gridcell"][data-d][aria-label*="年"], '
                        + 'td[data-d][aria-label*="年"], '
                        + '[role="gridcell"][data-d] [aria-label*="年"], '
                        + 'td[data-d] [aria-label*="年"], '
                        + '[data-d][aria-label*="年"]'
                    );
                    const hasCalendarText = calendarTextPattern.test(text);
                    return visible(el)
                        && hasCalendarCells
                        && (looksLikeCalendarContainer || hasCalendarText || hasDatedCalendarCells);
                });
                if (!calendarRoots.length) {
                    return [];
                }
                const roots = calendarRoots;
                const cellSelector = ['td', 'li', 'button', 'div', 'span'].join(', ');
                const candidates = [];
                for (const root of roots) {
                    for (const el of Array.from(root.querySelectorAll(cellSelector))) {
                        if (!visible(el)) {
                            continue;
                        }
                        const text = clean(el.innerText || el.textContent || '');
                        if (!text || text.length > 80) {
                            continue;
                        }
                        const className = String(el.className || '');
                        const dataD = el.getAttribute('data-d') || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        const ariaDisabled = el.getAttribute('aria-disabled') || '';
                        const ariaSelected = el.getAttribute('aria-selected') || '';
                        const priceText = clean(
                            (el.querySelector('[class*="price"]') || {}).innerText ||
                            (el.querySelector('[aria-label] span[aria-label]') || {}).getAttribute?.('aria-label') ||
                            ''
                        );
                        const hasPlainPrice = (value) => (
                            Array.from(String(value || '').matchAll(/(?<!\\d)(\\d{2,5})(?!\\d)/g))
                                .some((match) => {
                                    const amount = Number.parseInt(match[1], 10);
                                    return Number.isFinite(amount) && amount >= 50 && amount <= 20000;
                                })
                        );
                        const hasLabeledPrice = (value) => (
                            /(?:CNY|price|价格|价|￥|¥|楼)\\D{0,16}\\d{2,5}/i.test(String(value || ''))
                        );
                        const textNumbers = Array.from(text.matchAll(/(?<!\\d)(\\d{2,5})(?!\\d)/g))
                            .map((match) => Number.parseInt(match[1], 10))
                            .filter((amount) => Number.isFinite(amount));
                        const dayNumbers = new Set(
                            Array.from(text.matchAll(/(?<!\\d)(\\d{1,2})(?!\\d)/g))
                                .map((match) => Number.parseInt(match[1], 10))
                                .filter((amount) => Number.isFinite(amount))
                        );
                        const hasTextPrice = textNumbers.some((amount) => (
                            amount >= 50 && amount <= 20000 && !dayNumbers.has(amount)
                        ));
                        const hasDay = /\\b\\d{1,2}\\b/.test(text)
                            || /20\\d{2}-\\d{1,2}-\\d{1,2}/.test(dataD)
                            || /20\\d{2}.*?\\d{1,2}.*?\\d{1,2}/.test(ariaLabel);
                        const hasPrice = hasPlainPrice(priceText)
                            || hasLabeledPrice(ariaLabel)
                            || hasLabeledPrice(text)
                            || hasTextPrice;
                        if (!hasDay || !hasPrice) {
                            continue;
                        }
                        const rect = el.getBoundingClientRect();
                        candidates.push({
                            text,
                            class_name: className,
                            data_d: dataD,
                            aria_label: ariaLabel,
                            aria_disabled: ariaDisabled,
                            aria_selected: ariaSelected,
                            price_text: priceText,
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                        });
                    }
                }
                return candidates.slice(0, 120);
            }""",
        )
    except PlaywrightError:
        return []
    if not isinstance(cells, list):
        return []
    return [
        cell
        for cell in cells
        if isinstance(cell, dict) and _public_calendar_cell_has_day_and_price(cell)
    ]
