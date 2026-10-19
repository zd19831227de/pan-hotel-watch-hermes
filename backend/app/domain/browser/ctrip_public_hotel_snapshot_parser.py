from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.domain.browser.ctrip_common import (
    _is_ctrip_url,
    _looks_like_login_required,
    _looks_like_verification_required,
)
from app.domain.browser.ctrip_public_competitor_types import CompetitorPriceSnapshot
from app.domain.browser.ctrip_public_hotel_dates import (
    _extract_public_hotel_date_context,
    _public_hotel_date_verification,
)
from app.domain.browser.ctrip_public_room_rate_parser import (
    _PUBLIC_BREAKFAST_KEYWORDS,
    _PUBLIC_HOTEL_BUSINESS_TERMS,
    _PUBLIC_PRICE_CONTEXT_BLACKLIST,
    _PUBLIC_ROOM_KEYWORDS,
    _clean_public_hotel_lines,
    _extract_first_line_matching,
    _extract_public_inventory_clue,
    _extract_public_price_candidates,
    _is_public_breakfast_clue_line,
    _is_public_room_clue_line,
    _normalize_public_structured_room_rates,
    _PublicPriceCandidate,
    _select_primary_public_room_rate,
    _select_public_price_candidate,
    _select_public_price_snippets,
)


def _classify_public_hotel_page_state(url: str, body_text: str) -> str:
    normalized_url = url.lower()
    normalized_body = body_text.lower()
    if "/pages/404" in normalized_url or "error code: 3" in normalized_url:
        return "page_not_found"
    if "404" in normalized_body and "人在旅途" in body_text:
        return "page_not_found"
    if any(token in normalized_url for token in ("captcha", "verify", "security")):
        return "verification_required"
    if _looks_like_public_hotel_readable_price_page(body_text):
        return "authenticated"
    if _looks_like_login_required(normalized_url, body_text):
        return "login_required"
    if _looks_like_verification_required(normalized_url, body_text):
        return "verification_required"
    if _is_ctrip_url(url) and body_text:
        return "authenticated"
    return "unknown"


def _looks_like_public_hotel_readable_price_page(body_text: str) -> bool:
    business_score = sum(1 for term in _PUBLIC_HOTEL_BUSINESS_TERMS if term in body_text)
    strong_score = sum(
        1
        for term in (
            "房型",
            "大床房",
            "双床房",
            "早餐",
            "预订",
            "在线付",
            "今日价格",
            "¥",
            "￥",
        )
        if term in body_text
    )
    return business_score >= 3 and strong_score >= 2


def _public_page_state_with_room_rates(
    *,
    page_state: str,
    room_rates: list[dict[str, Any]],
) -> str:
    if page_state != "verification_required":
        return page_state
    if room_rates:
        return "authenticated"
    return page_state


def _extract_public_hotel_price_snapshot(
    *,
    role: str,
    label: str,
    requested_url: str,
    current_url: str,
    page_title: str,
    page_state: str,
    body_text: str,
    screenshot_path: str,
    structured_room_rates: list[dict[str, Any]] | None = None,
    require_visible_date_match: bool = False,
) -> CompetitorPriceSnapshot:
    lines = _clean_public_hotel_lines(body_text)
    date_context = _extract_public_hotel_date_context(lines, requested_url)
    date_verification = _public_hotel_date_verification(
        requested_url=requested_url,
        current_url=current_url,
        lines=lines,
        require_visible_date_match=require_visible_date_match,
    )
    candidates = _extract_public_price_candidates(lines)
    room_rates = _normalize_public_structured_room_rates(structured_room_rates or [])
    selected = _select_public_price_candidate(
        candidates=candidates,
        room_rates=room_rates,
    )
    status = _public_hotel_snapshot_status(page_state, selected)
    date_review_reason = date_verification.get("review_reason")
    if date_review_reason:
        status = "needs_review"
        room_rates = []
        selected = None
    hotel_name = _extract_public_hotel_name(lines, page_title)
    primary_rate = _select_primary_public_room_rate(room_rates)
    fallback_room_clue, fallback_room_line = _extract_first_line_matching(
        lines,
        _PUBLIC_ROOM_KEYWORDS,
        exclude=_PUBLIC_PRICE_CONTEXT_BLACKLIST,
        predicate=_is_public_room_clue_line,
    )
    fallback_breakfast_clue, fallback_breakfast_line = _extract_first_line_matching(
        lines,
        _PUBLIC_BREAKFAST_KEYWORDS,
        predicate=_is_public_breakfast_clue_line,
    )
    price = None
    price_candidate = None
    parser_confidence = "low"
    if selected:
        parser_confidence = "high" if selected.score >= 5 else "medium"
        price_candidate = {
            "amount": selected.amount,
            "currency": "CNY",
            "display": selected.display,
            "source_line": selected.line,
            "candidate_count": len(candidates),
            "confidence": parser_confidence,
            "reasons": list(selected.reasons),
        }
        if status == "success":
            price = price_candidate
    room_clue = (
        primary_rate.get("room_type")
        if isinstance(primary_rate, dict)
        else fallback_room_clue
    )
    breakfast_clue = (
        primary_rate.get("breakfast")
        if isinstance(primary_rate, dict)
        else fallback_breakfast_clue
    )
    room_line = (
        primary_rate.get("source_room_line")
        if isinstance(primary_rate, dict)
        else fallback_room_line
    )
    breakfast_line = (
        primary_rate.get("source_breakfast_line")
        if isinstance(primary_rate, dict)
        else fallback_breakfast_line
    )
    availability_clue, availability_line = _extract_public_inventory_clue(
        lines=lines,
        selected=selected,
    )
    if isinstance(primary_rate, dict) and primary_rate.get("availability"):
        availability_clue = str(primary_rate["availability"])
        availability_line = str(
            primary_rate.get("availability_line")
            or primary_rate.get("source_text")
            or availability_line
            or ""
        )
    return {
        "role": role,
        "label": label,
        "hotel_name": hotel_name or label,
        "status": status,
        "price": price,
        "price_candidate": price_candidate,
        "room_rates": room_rates,
        "room_type": room_clue,
        "breakfast": breakfast_clue,
        "availability": availability_clue,
        "date_context": date_context,
        "requested_url": requested_url,
        "current_url": current_url,
        "page_title": page_title,
        "page_state": page_state,
        "screenshot_path": screenshot_path,
        "captured_at": datetime.now(UTC).isoformat(),
        "parser_confidence": parser_confidence,
        "date_verification": date_verification,
        "review_reason": date_review_reason
        or _public_hotel_review_reason(page_state, selected),
        "raw_snippets": _select_public_price_snippets(lines, candidates),
        "clue_lines": {
            "room": room_line,
            "breakfast": breakfast_line,
            "availability": availability_line,
        },
    }


def _public_hotel_snapshot_status(
    page_state: str,
    selected: _PublicPriceCandidate | None,
) -> str:
    if page_state == "login_required":
        return "human_required"
    if page_state == "page_not_found":
        return "needs_review"
    if selected is None:
        if page_state == "verification_required":
            return "human_required"
        return "needs_review"
    if selected.score < 5:
        return "needs_review"
    if page_state == "verification_required":
        return "needs_review"
    return "success"


def _public_hotel_review_reason(
    page_state: str,
    selected: _PublicPriceCandidate | None,
) -> str | None:
    if page_state == "login_required":
        return "login_required"
    if page_state == "page_not_found":
        return "page_not_found"
    if selected is None:
        if page_state == "verification_required":
            return "verification_required"
        return "visible_opening_price_not_found"
    if selected.score < 5:
        return "price_candidate_low_confidence"
    if page_state == "verification_required":
        return "verification_required"
    return None


def _extract_public_hotel_name(lines: list[str], page_title: str) -> str | None:
    title_name = re.split(r"预订|价格|电话|地址", page_title)[0].strip()
    title_name = re.split(r"[-_｜|]", title_name)[0].strip()
    title_name = re.sub(r"\s+", " ", title_name)
    if _looks_like_hotel_name(title_name):
        return title_name[:80]
    for line in lines[:24]:
        if _looks_like_hotel_name(line):
            return line[:80]
    return title_name[:80] if title_name else None


def _looks_like_hotel_name(value: str) -> bool:
    if not value or len(value) < 3 or len(value) > 80:
        return False
    if any(keyword in value for keyword in ("酒店", "泉吉", "瀚庭", "娅朵", "希尔硕", "民宿")):
        return not any(noise in value for noise in ("预订", "携程", "去哪儿", "低价"))
    return False


