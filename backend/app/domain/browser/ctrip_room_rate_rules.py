from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

TRUSTED_DOM_DISPLAY_PRICE_SOURCE = "room_rate_dom_display_price"

BreakfastKind = Literal["none", "included", "unknown"]

_BREAKFAST_COUNT_LABELS = {
    1: "含单早",
    2: "含双早",
    3: "含三早",
}
_BREAKFAST_COUNT_WORDS = {
    "一": 1,
    "单": 1,
    "二": 2,
    "两": 2,
    "双": 2,
    "三": 3,
}

_NO_BREAKFAST_TERMS = ("无早餐", "无早", "鏃犳棭")
_NO_BREAKFAST_PATTERNS = (
    r"\broom\s*only\b",
    r"\bno\s+breakfast\b",
    r"\bwithout\s+breakfast\b",
    r"\bbreakfast\s+not\s+included\b",
    r"\bnot\s+include(?:d|s)?\s+breakfast\b",
)
_WITH_BREAKFAST_TERMS = (
    "含早",
    "含早餐",
    "自助早餐",
    "早餐",
    "单早",
    "双早",
    "三早",
    # Legacy mojibake snippets from earlier local data; keep them classified
    # without letting broad terms like "早鸟优惠" become breakfast.
    "鏃╅",
    "浠芥棭",
)
_WITH_BREAKFAST_PATTERNS = (
    r"\bwith\s+breakfast\b",
    r"\bbreakfast\s+included\b",
    r"\binclude(?:d|s)?\s+breakfast\b",
    r"\b\d+\s+breakfasts?\b",
)


def normalize_breakfast_text(value: str) -> str:
    text = str(value or "").strip()
    if is_no_breakfast_text(text):
        return "无早餐"
    count = _breakfast_count_from_text(text)
    if count is not None:
        return _breakfast_count_label(count)
    if "自助早餐" in text:
        return "自助早餐"
    if "含早" in text:
        return "含早"
    if "早餐" in text:
        return "含早餐"
    return text[:40]


def _breakfast_count_from_text(value: str) -> int | None:
    compact = re.sub(r"\s+", "", str(value or ""))
    match = re.search(r"(?:(\d+)|([一二两三]))份(?:自助)?(?:早餐|早)", compact)
    if match:
        if match.group(1):
            return int(match.group(1))
        return _BREAKFAST_COUNT_WORDS.get(match.group(2))
    for marker, count in _BREAKFAST_COUNT_WORDS.items():
        if f"含{marker}早" in compact or f"{marker}早" in compact:
            return count
    return None


def _breakfast_count_label(count: int) -> str:
    return _BREAKFAST_COUNT_LABELS.get(count, f"含{count}份早")


def is_no_breakfast_text(value: str) -> bool:
    text = str(value or "")
    compact = re.sub(r"\s+", "", text)
    latin = text.lower()
    return any(term in compact for term in _NO_BREAKFAST_TERMS) or any(
        re.search(pattern, latin) for pattern in _NO_BREAKFAST_PATTERNS
    )


def is_with_breakfast_text(value: str) -> bool:
    text = str(value or "")
    compact = re.sub(r"\s+", "", text)
    if not compact or is_no_breakfast_text(text):
        return False
    if _breakfast_count_from_text(compact) is not None:
        return True
    latin = text.lower()
    return any(term in compact for term in _WITH_BREAKFAST_TERMS) or any(
        re.search(pattern, latin) for pattern in _WITH_BREAKFAST_PATTERNS
    )


def classify_breakfast_kind(value: str | None) -> BreakfastKind:
    if not value:
        return "unknown"
    if is_no_breakfast_text(value):
        return "none"
    if is_with_breakfast_text(value):
        return "included"
    return "unknown"


def rate_price_amount(rate: dict[str, Any]) -> int | None:
    price = rate.get("price")
    if not isinstance(price, dict):
        return None
    amount = price.get("amount")
    return amount if isinstance(amount, int) else None


def rate_matches_breakfast_kind(rate: dict[str, Any], *, kind: BreakfastKind) -> bool:
    classified = classify_breakfast_kind(str(rate.get("breakfast") or ""))
    return classified == kind


def room_type_match_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", normalized).strip().lower()


def primary_room_type_key_from_rates(rates: Any, *, trusted_source: str | None = None) -> str:
    if not isinstance(rates, list):
        return ""
    for rate in rates:
        if not isinstance(rate, dict):
            continue
        if trusted_source and rate.get("source") != trusted_source:
            continue
        key = room_type_comparison_key(rate.get("room_type"))
        if key:
            return key
    return ""


_BREAKFAST_SUFFIX_PATTERN = (
    r"(?:无早(?:餐)?|含早(?:餐)?|含?单早|含?双早|含?三早|自助早餐|(?:\d+|[一二两三])份(?:自助)?(?:早餐|早))"
)


def room_type_comparison_key(value: Any) -> str:
    compact = room_type_match_key(value)
    if not compact:
        return ""
    base = compact
    base = re.sub(rf"[（(]{_BREAKFAST_SUFFIX_PATTERN}[）)]$", "", base).strip()
    base = re.sub(rf"[·丨|/\-_.：:]?{_BREAKFAST_SUFFIX_PATTERN}$", "", base).strip()
    base = re.sub(rf"{_BREAKFAST_SUFFIX_PATTERN}$", "", base).strip()
    return base or compact


def breakfast_price_amount_for_room_type_key(
    rates: Any,
    *,
    kind: BreakfastKind,
    room_type_key: str,
    trusted_source: str | None = TRUSTED_DOM_DISPLAY_PRICE_SOURCE,
) -> int | None:
    if not isinstance(rates, list) or not room_type_key:
        return None
    amounts = [
        amount
        for rate in rates
        if isinstance(rate, dict)
        and (not trusted_source or rate.get("source") == trusted_source)
        and room_type_comparison_key(rate.get("room_type")) == room_type_key
        and rate_matches_breakfast_kind(rate, kind=kind)
        for amount in [rate_price_amount(rate)]
        if amount is not None
    ]
    return min(amounts) if amounts else None


def breakfast_price_amount_for_primary_room(
    rates: Any,
    *,
    kind: BreakfastKind,
    trusted_source: str | None = TRUSTED_DOM_DISPLAY_PRICE_SOURCE,
) -> int | None:
    if not isinstance(rates, list):
        return None
    primary_key = primary_room_type_key_from_rates(rates, trusted_source=trusted_source)
    amounts = [
        amount
        for rate in rates
        if isinstance(rate, dict)
        and (not trusted_source or rate.get("source") == trusted_source)
        and (not primary_key or room_type_comparison_key(rate.get("room_type")) == primary_key)
        and rate_matches_breakfast_kind(rate, kind=kind)
        for amount in [rate_price_amount(rate)]
        if amount is not None
    ]
    return min(amounts) if amounts else None
