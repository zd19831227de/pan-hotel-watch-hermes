from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order
from app.domain.browser.ctrip_room_rate_rules import (
    is_no_breakfast_text as _is_no_breakfast_text,
)
from app.domain.browser.ctrip_room_rate_rules import (
    normalize_breakfast_text as _normalize_public_breakfast_clue,
)


# Pure public Ctrip room-rate text parsing. It never drives the browser and only
# trusts structured DOM rows when their source is room_rate_dom_display_price.
@dataclass(frozen=True)
class _PublicPriceCandidate:
    amount: int
    display: str
    line: str
    line_index: int
    score: int
    reasons: tuple[str, ...]


def _clean_public_hotel_lines(body_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in body_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) > 240:
            continue
        if line in _PUBLIC_NOISE_LINES:
            continue
        lines.append(line)
    return _dedupe_keep_order(_combine_split_currency_lines(lines))


def _combine_split_currency_lines(lines: list[str]) -> list[str]:
    combined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if line in {"¥", "￥"} and re.fullmatch(r"\d{2,5}", next_line):
            combined.append(f"{line}{next_line}")
            index += 2
            continue
        combined.append(line)
        index += 1
    return combined


def _is_public_room_clue_line(line: str) -> bool:
    if not line or len(line) > 96:
        return False
    if any(keyword in line for keyword in _PUBLIC_REVIEW_TEXT_KEYWORDS):
        return False
    if any(keyword in line for keyword in _PUBLIC_NON_ROOM_TYPE_KEYWORDS):
        return False
    punctuation_count = sum(line.count(token) for token in ("，", "。", "！", "；", ",", ";"))
    if punctuation_count >= 2:
        return False
    if any(keyword in line for keyword in _PUBLIC_ROOM_TYPE_STRONG_KEYWORDS):
        return True
    return False


def _is_public_breakfast_clue_line(line: str) -> bool:
    if not line or len(line) > 40:
        return False
    if any(keyword in line for keyword in _PUBLIC_REVIEW_TEXT_KEYWORDS):
        return False
    punctuation_count = sum(line.count(token) for token in ("，", "。", "！", "；", ",", ";"))
    if punctuation_count:
        return False
    if re.search(r"\d+\s*份\s*(?:早餐|早)", line):
        return True
    return any(keyword in line for keyword in _PUBLIC_BREAKFAST_KEYWORDS)


def _extract_public_price_candidates(lines: list[str]) -> list[_PublicPriceCandidate]:
    candidates: list[_PublicPriceCandidate] = []
    for index, line in enumerate(lines):
        for match in _PUBLIC_PRICE_RE.finditer(line):
            amount_text = re.sub(
                r"[^\d]",
                "",
                match.group("amount") or match.group("amount_alt") or "",
            )
            if not amount_text:
                continue
            amount = int(amount_text)
            if amount < 50 or amount > 20000:
                continue
            window = _public_price_window(lines, index)
            if _is_public_price_context_blacklisted(line):
                continue
            if _is_public_price_expansion_line(line):
                continue
            score, reasons = _score_public_price_candidate(line, window)
            candidates.append(
                _PublicPriceCandidate(
                    amount=amount,
                    display=f"¥{amount}",
                    line=line,
                    line_index=index,
                    score=score,
                    reasons=tuple(reasons),
                )
            )
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.amount))


def _select_public_price_candidate(
    *,
    candidates: list[_PublicPriceCandidate],
    room_rates: list[dict[str, Any]],
) -> _PublicPriceCandidate | None:
    structured_rates = [
        rate
        for rate in room_rates
        if rate.get("source") == "room_rate_dom_display_price"
        and isinstance(rate.get("price"), dict)
        and isinstance(rate["price"].get("amount"), int)
    ]
    if structured_rates:
        best_rate = sorted(
            structured_rates,
            key=lambda rate: (
                not _is_no_breakfast_text(str(rate.get("breakfast") or "")),
                rate["price"]["amount"],
            ),
        )[0]
        amount = best_rate["price"]["amount"]
        display = str(best_rate["price"].get("display") or f"¥{amount}")
        return _PublicPriceCandidate(
            amount=amount,
            display=display,
            line=str(best_rate.get("source_price_line") or display),
            line_index=0,
            score=9,
            reasons=("room_rate_dom_display_price",),
        )
    rate_candidates = [
        candidate
        for candidate in candidates
        if any(
            rate.get("price", {}).get("amount") == candidate.amount
            and rate.get("source_price_line") == candidate.line
            for rate in room_rates
        )
    ]
    if rate_candidates:
        best = sorted(rate_candidates, key=lambda candidate: candidate.amount)[0]
        return _PublicPriceCandidate(
            amount=best.amount,
            display=best.display,
            line=best.line,
            line_index=best.line_index,
            score=max(best.score, 6),
            reasons=(*best.reasons, "structured_room_rate"),
        )
    return None


def _public_price_window(lines: list[str], index: int) -> str:
    start = max(0, index - 3)
    end = min(len(lines), index + 4)
    return " ".join(lines[start:end])


def _is_public_price_context_blacklisted(text: str) -> bool:
    return any(keyword in text for keyword in _PUBLIC_PRICE_CONTEXT_BLACKLIST)


def _is_public_price_expansion_line(text: str) -> bool:
    return any(keyword in text for keyword in _PUBLIC_PRICE_EXPANSION_KEYWORDS)


def _score_public_price_candidate(line: str, window: str) -> tuple[int, list[str]]:
    score = 1
    reasons = ["visible_currency_amount"]
    if any(keyword in line for keyword in _PUBLIC_STRONG_PRICE_KEYWORDS):
        score += 3
        reasons.append("same_line_opening_price_keyword")
    if any(keyword in window for keyword in _PUBLIC_STRONG_PRICE_KEYWORDS):
        score += 2
        reasons.append("nearby_opening_price_keyword")
    if any(keyword in window for keyword in _PUBLIC_ROOM_KEYWORDS):
        score += 1
        reasons.append("nearby_room_keyword")
    if any(keyword in window for keyword in _PUBLIC_BOOKING_KEYWORDS):
        score += 1
        reasons.append("nearby_booking_keyword")
    if any(keyword in line for keyword in ("税", "积分", "优惠券", "红包")):
        score -= 2
        reasons.append("discount_or_tax_context")
    return score, reasons


def _extract_first_line_matching(
    lines: list[str],
    keywords: tuple[str, ...],
    exclude: tuple[str, ...] = (),
    predicate: Callable[[str], bool] | None = None,
) -> tuple[str | None, str | None]:
    for line in lines:
        if exclude and any(keyword in line for keyword in exclude):
            continue
        if any(keyword in line for keyword in keywords):
            if predicate and not predicate(line):
                continue
            return line[:120], line
    return None, None


def _normalize_public_structured_room_rates(
    value: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        amount = None
        if isinstance(price, dict) and isinstance(price.get("amount"), int):
            amount = price["amount"]
        if amount is None:
            amount = _public_display_price_amount(item.get("source_price_line"))
        if amount is None or amount < 50 or amount > 20000:
            continue
        row_lines = _clean_public_hotel_lines(str(item.get("source_text") or ""))
        row_lines.extend(_clean_public_hotel_lines(str(item.get("source_breakfast_line") or "")))
        card_text = str(item.get("source_card_text") or "")
        card_context_lines = _clean_public_hotel_lines(card_text)
        room_source = item.get("room_type") or item.get("source_room_line") or ""
        card_lines = _clean_public_hotel_lines(str(room_source))
        room_type = (
            _trusted_public_structured_room_title(room_source)
            or _first_public_room_clue(card_lines)
            or _first_public_room_clue(row_lines)
        )
        breakfast = (
            _normalize_public_breakfast_clue(str(item.get("breakfast") or ""))
            if item.get("breakfast")
            else _first_public_breakfast_clue(row_lines)
        )
        if not room_type or not breakfast:
            continue
        source_price_line = str(item.get("source_price_line") or f"¥{amount}")
        explicit_source_amount = _public_display_price_amount(source_price_line)
        corrected_price = _structured_rate_price_for_breakfast(
            row_lines=row_lines,
            breakfast=breakfast,
        )
        if (
            explicit_source_amount is not None
            and (
                corrected_price is None
                or corrected_price[1] == explicit_source_amount
                or not _structured_row_contains_multiple_breakfast_plans(row_lines)
            )
        ):
            corrected_price = None
        if corrected_price is not None:
            source_price_line, amount = corrected_price
        amount, source_price_line = _correct_public_included_breakfast_price_from_card(
            amount=amount,
            source_price_line=source_price_line,
            breakfast=breakfast,
            card_lines=card_context_lines,
        )
        availability, availability_line = _extract_public_inventory_clue_for_rate(row_lines)
        if availability is None:
            availability, availability_line = _extract_public_inventory_clue_for_rate(card_lines)
        discount_amount, discount_display, source_discount_line = _public_rate_discount(
            item=item,
            row_lines=row_lines,
            card_lines=card_context_lines,
        )
        rate_payload = {
            "room_type": room_type,
            "breakfast": breakfast,
            "price": {
                "amount": amount,
                "currency": "CNY",
                "display": f"¥{amount}",
            },
            "original_price": item.get("original_price"),
            "availability": availability or "正常",
            "source_room_line": room_type,
            "source_breakfast_line": breakfast,
            "source_price_line": source_price_line,
            "source_text": str(item.get("source_text") or "")[:600],
            "source_card_text": card_text[:1800],
            "source": "room_rate_dom_display_price",
            "availability_line": availability_line,
        }
        if discount_amount is not None:
            rate_payload.update(
                {
                    "discount_amount_yuan": discount_amount,
                    "discount_display": discount_display,
                    "source_discount_line": source_discount_line,
                }
            )
        rates.append(rate_payload)
    return _dedupe_public_room_rates_by_plan(rates)[:40]


def _trusted_public_structured_room_title(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) > 120:
        return None
    if any(keyword in text for keyword in _PUBLIC_NON_ROOM_TYPE_KEYWORDS):
        return None
    if any(keyword in text for keyword in _PUBLIC_STRUCTURED_ROOM_TITLE_NOISE_KEYWORDS):
        return None
    if _public_display_price_amount(text) is not None or _public_discount_amount(text) is not None:
        return None
    if re.fullmatch(r"[\d.]+", text) or re.fullmatch(r"[\d.]+\s*分", text):
        return None
    if re.fullmatch(r"\d+\s*张.*床", text) or re.fullmatch(r"\d+(?:\.\d+)?\s*米.*床", text):
        return None
    return text[:120]


def _public_rate_discount(
    *,
    item: dict[str, Any],
    row_lines: list[str],
    card_lines: list[str],
) -> tuple[int | None, str | None, str | None]:
    discount = item.get("discount")
    if isinstance(discount, dict):
        amount = _public_discount_amount(discount.get("amount"))
        display = _public_discount_display(discount.get("display"), amount)
        if amount is not None:
            return amount, display, str(item.get("source_discount_line") or display)
    source_discount_line = str(item.get("source_discount_line") or "").strip()
    amount = _public_discount_amount(source_discount_line)
    if amount is not None:
        return amount, f"优惠{amount}", source_discount_line
    for line in [*row_lines, *card_lines]:
        amount = _public_discount_amount(line)
        if amount is not None:
            return amount, f"优惠{amount}", line[:120]
    return None, None, None


def _public_discount_amount(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 < value <= 20000 else None
    match = re.search(r"优惠\s*(\d{1,5})", str(value or ""))
    if not match:
        return None
    amount = int(match.group(1))
    return amount if 0 < amount <= 20000 else None


def _public_discount_display(value: Any, amount: int | None) -> str | None:
    text = str(value or "").strip()
    if text:
        return text[:80]
    if amount is not None:
        return f"优惠{amount}"
    return None


def _first_public_room_clue(lines: list[str]) -> str | None:
    for line in lines:
        if _is_public_room_clue_line(line):
            return line[:120]
    return None


def _first_public_breakfast_clue(lines: list[str]) -> str | None:
    for line in lines:
        if _is_public_breakfast_clue_line(line):
            return _normalize_public_breakfast_clue(line)
    return None


def _structured_rate_price_for_breakfast(
    *,
    row_lines: list[str],
    breakfast: str,
) -> tuple[str, int] | None:
    for index, line in enumerate(row_lines):
        if not _is_public_breakfast_clue_line(line):
            continue
        normalized = _normalize_public_breakfast_clue(line)
        if normalized != breakfast:
            continue
        price_line, amount = _nearest_public_rate_price_around(row_lines, index)
        if amount is not None and price_line is not None:
            return price_line, amount
    return None


def _structured_row_contains_multiple_breakfast_plans(row_lines: list[str]) -> bool:
    return len(
        {
            _normalize_public_breakfast_clue(line)
            for line in row_lines
            if _is_public_breakfast_clue_line(line)
        }
    ) > 1


def _correct_public_included_breakfast_price_from_card(
    *,
    amount: int,
    source_price_line: str,
    breakfast: str,
    card_lines: list[str],
) -> tuple[int, str]:
    if _is_no_breakfast_text(breakfast) or not card_lines:
        return amount, source_price_line
    price_candidates = _public_card_booking_prices(card_lines)
    if len(price_candidates) < 2:
        return amount, source_price_line
    amounts = [candidate[2] for candidate in price_candidates]
    if amount != min(amounts) and amount not in amounts:
        return amount, source_price_line
    lowest_amount = min(amounts)
    higher = [candidate for candidate in price_candidates if candidate[2] > lowest_amount]
    if not higher:
        return amount, source_price_line
    _index, line, corrected_amount = sorted(higher, key=lambda item: (item[2], item[0]))[0]
    return corrected_amount, line


def _public_card_booking_prices(lines: list[str]) -> list[tuple[int, str, int]]:
    prices: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines):
        amount = _public_room_rate_price_amount(line)
        if amount is None:
            continue
        window_lines = lines[max(0, index - 3) : min(len(lines), index + 4)]
        window = " ".join(window_lines)
        if not any(keyword in window for keyword in _PUBLIC_BOOKING_KEYWORDS):
            continue
        previous_lines = lines[max(0, index - 3) : index]
        if any("优惠" in candidate for candidate in previous_lines):
            continue
        prices.append((index, line, amount))
    return prices


def _public_display_price_amount(value: Any) -> int | None:
    text = str(value or "")
    for match in re.finditer(r"[¥￥]?\s*(\d{2,5})", text):
        amount = int(match.group(1))
        if 50 <= amount <= 20000:
            return amount
    return None


def _extract_public_inventory_clue_for_rate(
    lines: list[str],
) -> tuple[str | None, str | None]:
    tight_clue, tight_line = _extract_first_line_matching(
        lines,
        _PUBLIC_INVENTORY_TIGHT_KEYWORDS,
        predicate=_is_public_inventory_clue_line,
    )
    if tight_clue:
        return _normalize_public_inventory_clue(tight_clue), tight_line
    unavailable_clue, unavailable_line = _extract_first_line_matching(
        lines,
        _PUBLIC_UNAVAILABLE_KEYWORDS,
        predicate=_is_public_inventory_clue_line,
    )
    if unavailable_clue:
        return unavailable_clue, unavailable_line
    return None, None


def _extract_public_room_rates(lines: list[str]) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    current_room: str | None = None
    current_room_line: str | None = None
    current_room_index: int | None = None
    pending_breakfast: str | None = None
    pending_breakfast_line: str | None = None
    pending_breakfast_index: int | None = None
    seen: set[tuple[str | None, str | None, int]] = set()
    for index, line in enumerate(lines):
        if _is_public_room_clue_line(line):
            current_room = line[:120]
            current_room_line = line
            current_room_index = index
            pending_breakfast = None
            pending_breakfast_line = None
            pending_breakfast_index = None
            continue
        if _is_public_breakfast_clue_line(line):
            pending_breakfast = _normalize_public_breakfast_clue(line)
            pending_breakfast_line = line
            pending_breakfast_index = index
            continue
        price_amount = _public_room_rate_price_amount(line)
        if price_amount is None:
            continue
        if current_room is None:
            current_room, current_room_line, current_room_index = _nearest_public_room_before(
                lines,
                index,
            )
        if current_room is None:
            current_room, current_room_line, current_room_index = _nearest_public_room_around(
                lines,
                index,
            )
        if pending_breakfast is None:
            (
                pending_breakfast,
                pending_breakfast_line,
                pending_breakfast_index,
            ) = _nearest_public_breakfast_before(lines, index)
        if pending_breakfast is None:
            (
                pending_breakfast,
                pending_breakfast_line,
                pending_breakfast_index,
            ) = _nearest_public_breakfast_around(lines, index)
        if current_room is None or pending_breakfast is None:
            continue
        if current_room_index is None or index - current_room_index > 18:
            current_room, current_room_line, current_room_index = _nearest_public_room_around(
                lines,
                index,
            )
        if current_room_index is None or abs(index - current_room_index) > 18:
            continue
        if pending_breakfast_index is None or index - pending_breakfast_index > 14:
            nearby_breakfast, nearby_breakfast_line, nearby_breakfast_index = (
                _nearest_public_breakfast_around(lines, index)
            )
            if nearby_breakfast:
                pending_breakfast = nearby_breakfast
                pending_breakfast_line = nearby_breakfast_line
                pending_breakfast_index = nearby_breakfast_index
        if pending_breakfast_index is None or abs(index - pending_breakfast_index) > 14:
            continue
        rate_key = (current_room, pending_breakfast, price_amount)
        if rate_key in seen:
            continue
        seen.add(rate_key)
        rates.append(
            {
                "room_type": current_room,
                "breakfast": pending_breakfast,
                "price": {
                    "amount": price_amount,
                    "currency": "CNY",
                    "display": f"¥{price_amount}",
                },
                "source_room_line": current_room_line,
                "source_breakfast_line": pending_breakfast_line,
                "source_price_line": line,
            }
        )
        pending_breakfast = None
        pending_breakfast_line = None
        pending_breakfast_index = None
    rates.extend(_extract_public_room_rates_from_breakfast_windows(lines))
    return _dedupe_public_room_rates_by_plan(
        [rate for rate in rates if rate["room_type"] or rate["breakfast"]]
    )[:12]


def _extract_public_room_rates_from_breakfast_windows(
    lines: list[str],
) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not _is_public_breakfast_clue_line(line):
            continue
        breakfast = _normalize_public_breakfast_clue(line)
        room, room_line, _room_index = _nearest_public_room_around(lines, index)
        price_line, amount = _nearest_public_rate_price_around(lines, index)
        if amount is None or price_line is None:
            continue
        rates.append(
            {
                "room_type": room,
                "breakfast": breakfast,
                "price": {
                    "amount": amount,
                    "currency": "CNY",
                    "display": f"¥{amount}",
                },
                "source_room_line": room_line,
                "source_breakfast_line": line,
                "source_price_line": price_line,
            }
        )
    return rates


def _nearest_public_rate_price_around(
    lines: list[str],
    index: int,
) -> tuple[str | None, int | None]:
    next_breakfast_index = next(
        (
            candidate_index
            for candidate_index in range(index + 1, min(len(lines), index + 8))
            if _is_public_breakfast_clue_line(lines[candidate_index])
        ),
        None,
    )
    end = next_breakfast_index if next_breakfast_index is not None else min(len(lines), index + 8)
    after_candidates: list[tuple[int, str, int]] = []
    for candidate_index in range(index + 1, end):
        amount = _public_room_rate_price_amount(lines[candidate_index])
        if amount is not None:
            after_candidates.append((candidate_index, lines[candidate_index], amount))
    if after_candidates:
        booking_index = next(
            (
                candidate_index
                for candidate_index in range(index + 1, end)
                if any(keyword in lines[candidate_index] for keyword in _PUBLIC_BOOKING_KEYWORDS)
            ),
            None,
        )
        if booking_index is not None:
            before_booking = [
                candidate for candidate in after_candidates if candidate[0] < booking_index
            ]
            if before_booking:
                candidate_index, line, amount = sorted(
                    before_booking,
                    key=lambda item: (-item[0], item[2]),
                )[0]
                return line, amount
        candidate_index, line, amount = sorted(
            after_candidates,
            key=lambda item: (item[0], item[2]),
        )[0]
        return line, amount

    start = max(0, index - 7)
    end = min(len(lines), index + 8)
    candidates: list[tuple[int, str, int]] = []
    for candidate_index in range(start, end):
        amount = _public_room_rate_price_amount(lines[candidate_index])
        if amount is not None:
            candidates.append((candidate_index, lines[candidate_index], amount))
    if not candidates:
        return None, None
    after_candidates = [candidate for candidate in candidates if candidate[0] > index]
    before_candidates = [candidate for candidate in candidates if candidate[0] < index]
    directional_candidates = after_candidates or before_candidates or candidates
    candidate_index, line, amount = sorted(
        directional_candidates,
        key=lambda item: (abs(item[0] - index), item[0], item[2]),
    )[0]
    return line, amount


def _dedupe_public_room_rates_by_plan(rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_plan: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    plan_order: dict[tuple[str | None, str | None], int] = {}
    room_order: dict[str | None, int] = {}
    for rate in rates:
        price = rate.get("price")
        if not isinstance(price, dict) or not isinstance(price.get("amount"), int):
            continue
        key = (rate.get("room_type"), rate.get("breakfast"))
        room_order.setdefault(rate.get("room_type"), len(room_order))
        plan_order.setdefault(key, len(plan_order))
        current = best_by_plan.get(key)
        if current is None or price["amount"] < current["price"]["amount"]:
            best_by_plan[key] = rate
    return sorted(
        best_by_plan.values(),
        key=lambda rate: (
            room_order.get(rate.get("room_type"), 9999),
            not _is_no_breakfast_text(str(rate.get("breakfast") or "")),
            rate["price"]["amount"],
            plan_order.get((rate.get("room_type"), rate.get("breakfast")), 9999),
        ),
    )


def _select_primary_public_room_rate(room_rates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not room_rates:
        return None
    no_breakfast_rates = [
        rate for rate in room_rates if _is_no_breakfast_text(str(rate.get("breakfast") or ""))
    ]
    if no_breakfast_rates:
        return no_breakfast_rates[0]
    return room_rates[0]


def _public_room_rate_price_amount(line: str) -> int | None:
    if _is_public_price_expansion_line(line):
        return None
    amounts: list[int] = []
    if re.fullmatch(r"\d{2,5}", line):
        amount = int(line)
        if 50 <= amount <= 20000:
            amounts.append(amount)
    for match in _PUBLIC_PRICE_RE.finditer(line):
        amount_text = re.sub(
            r"[^\d]",
            "",
            match.group("amount") or match.group("amount_alt") or "",
        )
        if not amount_text:
            continue
        amount = int(amount_text)
        if 50 <= amount <= 20000:
            amounts.append(amount)
    if not amounts:
        return None
    return min(amounts)


def _nearest_public_room_before(
    lines: list[str],
    index: int,
) -> tuple[str | None, str | None, int | None]:
    for line in reversed(lines[max(0, index - 8) : index]):
        if _is_public_room_clue_line(line):
            return line[:120], line, lines.index(line)
    return None, None, None


def _nearest_public_room_around(
    lines: list[str],
    index: int,
) -> tuple[str | None, str | None, int | None]:
    start = max(0, index - 8)
    end = min(len(lines), index + 10)
    candidates: list[tuple[int, str]] = []
    for candidate_index in range(start, end):
        line = lines[candidate_index]
        if _is_public_room_clue_line(line):
            candidates.append((candidate_index, line))
    if not candidates:
        return None, None, None
    candidate_index, line = sorted(
        candidates,
        key=lambda item: (abs(item[0] - index), item[0]),
    )[0]
    return line[:120], line, candidate_index


def _nearest_public_breakfast_before(
    lines: list[str],
    index: int,
) -> tuple[str | None, str | None, int | None]:
    for line in reversed(lines[max(0, index - 5) : index]):
        if _is_public_breakfast_clue_line(line):
            return _normalize_public_breakfast_clue(line), line, lines.index(line)
    return None, None, None


def _nearest_public_breakfast_around(
    lines: list[str],
    index: int,
) -> tuple[str | None, str | None, int | None]:
    start = max(0, index - 8)
    end = min(len(lines), index + 8)
    candidates: list[tuple[int, str]] = []
    for candidate_index in range(start, end):
        line = lines[candidate_index]
        if _is_public_breakfast_clue_line(line):
            candidates.append((candidate_index, line))
    if not candidates:
        return None, None, None
    candidate_index, line = sorted(
        candidates,
        key=lambda item: (abs(item[0] - index), item[0]),
    )[0]
    return _normalize_public_breakfast_clue(line), line, candidate_index


def _extract_public_inventory_clue(
    *,
    lines: list[str],
    selected: _PublicPriceCandidate | None,
) -> tuple[str | None, str | None]:
    tight_clue, tight_line = _extract_first_line_matching(
        lines,
        _PUBLIC_INVENTORY_TIGHT_KEYWORDS,
        predicate=_is_public_inventory_clue_line,
    )
    if tight_clue:
        return _normalize_public_inventory_clue(tight_clue), tight_line
    unavailable_clue, unavailable_line = _extract_first_line_matching(
        lines,
        _PUBLIC_UNAVAILABLE_KEYWORDS,
        predicate=_is_public_inventory_clue_line,
    )
    if unavailable_clue:
        return unavailable_clue, unavailable_line
    if selected is not None:
        return "正常", None
    return None, None


def _is_public_inventory_clue_line(line: str) -> bool:
    if not line or len(line) > 80:
        return False
    return not any(keyword in line for keyword in _PUBLIC_REVIEW_TEXT_KEYWORDS)


def _normalize_public_inventory_clue(line: str) -> str:
    if "房量紧张" in line:
        return "房量紧张"
    match = re.search(r"(?:仅剩|剩余)\s*\d+\s*间", line)
    if match:
        return match.group(0)
    if "紧张" in line:
        return "房量紧张"
    return line[:120]


def _select_public_price_snippets(
    lines: list[str],
    candidates: list[_PublicPriceCandidate],
) -> list[str]:
    snippets: list[str] = []
    for candidate in candidates[:5]:
        start = max(0, candidate.line_index - 1)
        end = min(len(lines), candidate.line_index + 2)
        snippets.extend(lines[start:end])
    if not snippets:
        snippets = [
            line
            for line in lines
            if any(keyword in line for keyword in _PUBLIC_PRICE_SNIPPET_KEYWORDS)
        ][:12]
    return _dedupe_keep_order(snippets)[:18]


_PUBLIC_PRICE_RE = re.compile(
    r"(?:(?:[¥￥]|CNY)\s*(?P<amount>\d{2,5})|(?P<amount_alt>\d{2,5})\s*(?:元|CNY)?\s*起)"
)

_PUBLIC_HOTEL_BUSINESS_TERMS = (
    "酒店",
    "房型",
    "房间",
    "大床房",
    "双床房",
    "早餐",
    "低价",
    "起价",
    "可订",
    "立即预订",
)

_PUBLIC_STRONG_PRICE_KEYWORDS = (
    "起",
    "起价",
    "低价",
    "最低价",
    "开盘价",
    "每晚",
    "均价",
)

_PUBLIC_ROOM_KEYWORDS = (
    "大床房",
    "双床房",
    "家庭房",
    "套房",
    "房型",
    "客房",
    "高级",
    "商务",
    "豪华",
    "记忆枕",
    "智能客控",
)

_PUBLIC_ROOM_TYPE_STRONG_KEYWORDS = (
    "大床房",
    "大床",
    "双床房",
    "双床",
    "家庭房",
    "亲子房",
    "套房",
    "标间",
    "标准间",
    "标准房",
    "高级房",
    "高级大床",
    "高级双床",
    "商务房",
    "商务大床",
    "商务双床",
    "豪华房",
    "豪华大床",
    "豪华双床",
    "景观",
    "城景",
    "江景",
    "零压",
    "行政",
    "悦享",
    "静享",
    "尊享",
    "精英",
    "单人间",
    "双人间",
    "榻榻米",
)

_PUBLIC_NON_ROOM_TYPE_KEYWORDS = (
    "展示其余",
    "展示额外",
    "房型价格",
    "房型格局",
    "所有房型",
    "不可加床",
    "婴儿床",
)

_PUBLIC_STRUCTURED_ROOM_TITLE_NOISE_KEYWORDS = (
    "早餐",
    "无早",
    "含早",
    "预订",
    "在线付",
    "到店付",
    "立即确认",
    "免费取消",
    "房型摘要",
    "可住人数",
    "今日价格",
    "房间详情",
    "仅剩",
    "房量紧张",
    "满房",
    "订完",
    "不可订",
    "Wi-Fi",
    "平方米",
    "有窗",
    "禁烟",
)

_PUBLIC_BREAKFAST_KEYWORDS = (
    "早餐",
    "份早",
    "含早",
    "无早",
    "自助早餐",
    "单早",
    "双早",
    "三早",
)

_PUBLIC_AVAILABILITY_KEYWORDS = (
    "充足",
    "仅剩",
    "剩余",
    "可订",
    "订完",
    "满房",
    "不可订",
    "无房",
    "紧张",
)

_PUBLIC_INVENTORY_TIGHT_KEYWORDS = (
    "房量紧张",
    "仅剩",
    "剩余",
    "紧张",
)

_PUBLIC_UNAVAILABLE_KEYWORDS = (
    "订完",
    "满房",
    "不可订",
    "无房",
)

_PUBLIC_BOOKING_KEYWORDS = ("预订", "在线付", "到店付", "可订", "房型", "间")

_PUBLIC_PRICE_SNIPPET_KEYWORDS = (
    "¥",
    "￥",
    "元",
    "起",
    "低价",
    "房型",
    "早餐",
    "仅剩",
    "可订",
)

_PUBLIC_PRICE_EXPANSION_KEYWORDS = (
    "展示额外",
    "展示其余",
    "展开",
    "收起",
    "房型价格",
)

_PUBLIC_PRICE_CONTEXT_BLACKLIST = (
    "返现",
    "红包",
    "优惠券",
    "积分",
    "税费",
    "点评",
    "评分",
    "公里",
    "分钟",
    "收藏",
)

_PUBLIC_REVIEW_TEXT_KEYWORDS = (
    "住客点评",
    "用户点评",
    "点评",
    "评分",
    "前台",
    "服务",
    "位置",
    "地铁",
    "楼下",
    "下次",
    "再来",
    "味道",
    "口味",
    "小姐姐",
    "小哥哥",
    "阿姨",
    "带女儿",
    "升级了房型",
    "房间很干净",
    "酒店给我",
    "看江景",
    "夜景",
)

_PUBLIC_NOISE_LINES = {
    "携程旅行",
    "登录",
    "注册",
    "首页",
    "酒店",
    "帮助中心",
    "隐私政策",
}
