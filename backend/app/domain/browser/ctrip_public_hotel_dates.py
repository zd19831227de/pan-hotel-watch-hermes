from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from app.domain.browser import ctrip_public_hotel_urls as _hotel_url_rules
from app.domain.browser.ctrip_public_competitor_types import CompetitorPriceSnapshot
from app.domain.browser.ctrip_public_room_rate_parser import (
    _clean_public_hotel_lines,
    _public_room_rate_price_amount,
)

_PUBLIC_HOTEL_URL_KEEP_QUERY_KEYS = {
    "adult",
    "ages",
    "barcurr",
    "checkIn",
    "checkOut",
    "children",
    "cityEnName",
    "cityId",
    "crn",
    "curr",
    "display",
    "hotelId",
    "hotelType",
}

def _is_valid_public_ctrip_hotel_url(url: str) -> bool:
    return _hotel_url_rules.is_valid_public_ctrip_hotel_url(url)


def _public_ctrip_hotel_id_from_url(url: str) -> str | None:
    return _hotel_url_rules.public_ctrip_hotel_id_from_url(url)


def _public_ctrip_hotel_url_error(url: str) -> str | None:
    return _hotel_url_rules.public_ctrip_hotel_url_error(url)


def _invalid_public_ctrip_hotel_urls(urls: list[str]) -> list[str]:
    return _hotel_url_rules.invalid_public_ctrip_hotel_urls(urls)


def _build_competitor_price_targets(
    *,
    own_hotel_url: str,
    competitor_urls: list[str],
    check_in_date: date | None = None,
) -> list[dict[str, str]]:
    target_check_in = check_in_date or _local_public_check_in_date()
    targets: list[dict[str, str]] = []
    own_url = _public_hotel_url_for_check_in(
        _sanitize_public_hotel_url(own_hotel_url),
        check_in_date=target_check_in,
    )
    if own_url:
        targets.append({"role": "own", "label": "我们店", "url": own_url})
    for index, url in enumerate(competitor_urls, start=1):
        normalized = _public_hotel_url_for_check_in(
            _sanitize_public_hotel_url(url),
            check_in_date=target_check_in,
        )
        if not normalized:
            continue
        targets.append(
            {
                "role": f"competitor_{index}",
                "label": f"竞品{index}",
                "url": normalized,
            }
        )
    return targets


def _sanitize_public_hotel_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in _PUBLIC_HOTEL_URL_KEEP_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(query=urlencode(query_items)))


def _public_hotel_url_for_check_in(url: str, *, check_in_date: date) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query_items)
    nights = _nights_between(values.get("checkIn"), values.get("checkOut")) or 1
    check_out_date = check_in_date + timedelta(days=nights)
    seen_dates: set[str] = set()
    updated_items: list[tuple[str, str]] = []
    for key, value in query_items:
        if key == "checkIn":
            updated_items.append((key, check_in_date.isoformat()))
            seen_dates.add(key)
        elif key == "checkOut":
            updated_items.append((key, check_out_date.isoformat()))
            seen_dates.add(key)
        else:
            updated_items.append((key, value))
    if "checkIn" not in seen_dates:
        updated_items.append(("checkIn", check_in_date.isoformat()))
    if "checkOut" not in seen_dates:
        updated_items.append(("checkOut", check_out_date.isoformat()))
    return urlunparse(parsed._replace(query=urlencode(updated_items)))


def _local_public_check_in_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _extract_public_hotel_date_context(lines: list[str], url: str) -> dict[str, Any]:
    query = parse_qs(urlparse(url).query)
    context: dict[str, Any] = {}
    check_in = _first_query_value(query, "checkIn")
    check_out = _first_query_value(query, "checkOut")
    if check_in:
        context["check_in"] = check_in
    if check_out:
        context["check_out"] = check_out
    nights = _nights_between(check_in, check_out)
    if nights is not None:
        context["nights"] = nights
    date_lines = [
        line
        for line in lines
        if any(keyword in line for keyword in ("入住", "离店", "今天", "明天", "今日", "明日"))
        or re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日", line)
    ]
    if date_lines:
        context["visible_lines"] = date_lines[:6]
    return context


def _public_hotel_date_verification(
    *,
    requested_url: str,
    current_url: str,
    lines: list[str],
    require_visible_date_match: bool = False,
) -> CompetitorPriceSnapshot:
    requested_dates = _public_hotel_url_dates(requested_url)
    current_dates = _public_hotel_url_dates(current_url)
    visible_dates = _public_hotel_visible_dates(lines, requested_dates=requested_dates)
    signals = {
        "requested": requested_dates,
        "current": current_dates,
        "visible": visible_dates,
    }
    available = {key: value for key, value in signals.items() if value is not None}
    matching = {
        key: value for key, value in available.items() if value == requested_dates
    }
    external_matching = {
        key: value for key, value in matching.items() if key != "requested"
    }
    verified = requested_dates is None or bool(external_matching)
    review_reason = None
    if (
        requested_dates is not None
        and visible_dates is not None
        and visible_dates != requested_dates
    ):
        review_reason = "requested_visible_date_mismatch"
    elif (
        requested_dates is not None
        and current_dates is not None
        and current_dates != requested_dates
    ):
        review_reason = "requested_current_date_mismatch"
    elif (
        requested_dates is not None
        and require_visible_date_match
        and visible_dates != requested_dates
    ):
        review_reason = "visible_date_verification_failed"
    elif not verified:
        review_reason = "date_verification_failed"
    return {
        "verified": verified,
        "review_reason": review_reason,
        "requested": _date_signal_payload(requested_dates),
        "current": _date_signal_payload(current_dates),
        "visible": _date_signal_payload(visible_dates),
        "matching_signal_count": len(matching),
        "matching_external_signal_count": len(external_matching),
        "available_signal_count": len(available),
        "visible_required": require_visible_date_match,
    }


def _public_hotel_url_dates(url: str) -> tuple[str, str] | None:
    query = parse_qs(urlparse(url).query)
    check_in = _first_query_value(query, "checkIn")
    check_out = _first_query_value(query, "checkOut")
    if not check_in or not check_out:
        return None
    return check_in, check_out


def _public_hotel_visible_dates(
    lines: list[str],
    *,
    requested_dates: tuple[str, str] | None,
) -> tuple[str, str] | None:
    date_lines = _public_hotel_visible_date_lines(lines)
    iso_dates: list[str] = []
    for line in date_lines:
        iso_dates.extend(re.findall(r"20\d{2}-\d{1,2}-\d{1,2}", line))
    if len(iso_dates) >= 2:
        return _normalize_iso_date(iso_dates[0]), _normalize_iso_date(iso_dates[1])
    if requested_dates is None:
        return None
    requested_check_in, requested_check_out = requested_dates
    if any(requested_check_in in line for line in date_lines) and any(
        requested_check_out in line for line in date_lines
    ):
        return requested_dates
    month_day_dates = _public_hotel_month_day_visible_dates(
        date_lines,
        requested_dates=requested_dates,
    )
    if month_day_dates is not None:
        return month_day_dates
    return None


def _public_hotel_visible_date_lines(lines: list[str]) -> list[str]:
    date_lines = [
        line
        for line in lines
        if any(keyword in line for keyword in ("入住", "离店", "今天", "明天", "今日", "明日"))
        or re.search(r"20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日?", line)
    ]
    return date_lines or lines[:24]


def _public_hotel_month_day_visible_dates(
    lines: list[str],
    *,
    requested_dates: tuple[str, str],
) -> tuple[str, str] | None:
    requested_check_in = _parse_iso_date(requested_dates[0])
    requested_check_out = _parse_iso_date(requested_dates[1])
    if requested_check_in is None or requested_check_out is None:
        return None
    month_days: list[tuple[int, int]] = []
    for line in lines:
        for match in re.finditer(r"(\d{1,2})月(\d{1,2})日?", line):
            month_days.append((int(match.group(1)), int(match.group(2))))
    if len(month_days) < 2:
        return None
    check_in = _month_day_near_date(
        month_days[0][0],
        month_days[0][1],
        anchor=requested_check_in,
    )
    check_out = _month_day_near_date(
        month_days[1][0],
        month_days[1][1],
        anchor=check_in + timedelta(days=1),
    )
    if check_out <= check_in:
        check_out = _month_day_near_date(
            month_days[1][0],
            month_days[1][1],
            anchor=requested_check_out,
        )
    return check_in.isoformat(), check_out.isoformat()


def _month_day_near_date(month: int, day: int, *, anchor: date) -> date:
    candidates: list[date] = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return anchor
    return min(candidates, key=lambda value: abs((value - anchor).days))


def _parse_iso_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _normalize_iso_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value


def _date_signal_payload(value: tuple[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"check_in": value[0], "check_out": value[1]}


def _shift_public_hotel_url_dates(url: str, *, days: int) -> str | None:
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query_items)
    check_in = values.get("checkIn")
    check_out = values.get("checkOut")
    if not check_in or not check_out:
        return None
    try:
        shifted_check_in = datetime.fromisoformat(check_in).date() + timedelta(days=days)
        shifted_check_out = datetime.fromisoformat(check_out).date() + timedelta(days=days)
    except ValueError:
        return None
    shifted_items = [
        (
            key,
            shifted_check_in.isoformat()
            if key == "checkIn"
            else shifted_check_out.isoformat()
            if key == "checkOut"
            else value,
        )
        for key, value in query_items
    ]
    return urlunparse(parsed._replace(query=urlencode(shifted_items)))


def _build_public_tomorrow_snapshot_from_detail(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "date_context": snapshot.get("date_context", {}),
        "hotel_name": snapshot.get("hotel_name"),
        "price": snapshot.get("price"),
        "status": snapshot.get("status"),
        "review_reason": snapshot.get("review_reason"),
        "room_rates": snapshot.get("room_rates", []),
        "room_type": snapshot.get("room_type"),
        "breakfast": snapshot.get("breakfast"),
        "availability": snapshot.get("availability"),
        "requested_url": snapshot.get("requested_url"),
        "current_url": snapshot.get("current_url"),
        "page_title": snapshot.get("page_title"),
        "page_state": snapshot.get("page_state"),
        "screenshot_path": snapshot.get("screenshot_path"),
        "captured_at": snapshot.get("captured_at"),
        "parser_confidence": snapshot.get("parser_confidence"),
        "raw_snippets": snapshot.get("raw_snippets") or [],
        "clue_lines": snapshot.get("clue_lines") or {},
        "source": "next_day_detail_page",
    }


def _missing_public_tomorrow_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = snapshot.get("date_context", {})
    if not isinstance(context, dict):
        context = {}
    check_in = context.get("check_in")
    tomorrow_context: dict[str, Any] = {}
    if isinstance(check_in, str):
        try:
            tomorrow_context["check_in"] = (
                datetime.fromisoformat(check_in).date() + timedelta(days=1)
            ).isoformat()
        except ValueError:
            pass
    return {
        "date_context": tomorrow_context,
        "price": None,
        "status": "needs_review",
        "review_reason": "next_day_detail_url_not_available",
        "room_rates": [],
        "source": "next_day_detail_page",
    }


def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _nights_between(check_in: str | None, check_out: str | None) -> int | None:
    if not check_in or not check_out:
        return None
    try:
        start = datetime.fromisoformat(check_in).date()
        end = datetime.fromisoformat(check_out).date()
    except ValueError:
        return None
    nights = (end - start).days
    return nights if nights > 0 else None


def _extract_visible_public_calendar_tomorrow_snapshot(
    *,
    lines: list[str],
    current_date_context: dict[str, Any],
) -> dict[str, Any] | None:
    check_in = current_date_context.get("check_in")
    if not isinstance(check_in, str):
        return None
    try:
        current_date = datetime.fromisoformat(check_in).date()
    except ValueError:
        return None
    tomorrow = current_date.fromordinal(current_date.toordinal() + 1)
    day_text = str(tomorrow.day)
    candidates: list[int] = []
    for index, line in enumerate(lines):
        if line != day_text and not line.startswith(f"{day_text} "):
            continue
        window = lines[index : min(len(lines), index + 4)]
        for value in window[1:]:
            amount = _public_calendar_price_amount(value)
            if amount is not None:
                candidates.append(amount)
                break
    if not candidates:
        return None
    amount = min(candidates)
    return {
        "date_context": {"check_in": tomorrow.isoformat()},
        "price": {"amount": amount, "currency": "CNY", "display": f"¥{amount}"},
        "status": "success",
        "review_reason": None,
        "source": "same_page_visible_calendar",
    }


def _extract_public_calendar_tomorrow_snapshot(
    *,
    cells: list[dict[str, Any]],
    tomorrow: date,
) -> dict[str, Any] | None:
    amount: int | None = None
    source_text = ""
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or "")
        price = _public_calendar_cell_price_amount(text, tomorrow.day)
        if price is None:
            continue
        amount = price if amount is None else min(amount, price)
        source_text = text
    if amount is None:
        return {
            "date_context": {"check_in": tomorrow.isoformat()},
            "price": None,
            "status": "needs_review",
            "review_reason": "same_page_calendar_price_not_found",
            "source": "same_page_calendar_popup",
        }
    return {
        "date_context": {"check_in": tomorrow.isoformat()},
        "price": {"amount": amount, "currency": "CNY", "display": f"¥{amount}"},
        "status": "success",
        "review_reason": None,
        "source": "same_page_calendar_popup",
        "source_text": source_text[:120],
    }


def _extract_public_calendar_price_points(
    *,
    cells: list[dict[str, Any]],
    start_date: date,
    days: int,
) -> list[dict[str, Any]]:
    bounded_days = max(1, min(int(days), 30))
    end_date = start_date + timedelta(days=bounded_days)
    by_date: dict[str, dict[str, Any]] = {}
    for cell in cells:
        if not isinstance(cell, dict) or _public_calendar_cell_disabled(cell):
            continue
        checkin_date = _public_calendar_cell_checkin_date(cell)
        if checkin_date is None or not (start_date <= checkin_date < end_date):
            continue
        amount = _public_calendar_cell_amount(cell, checkin_date)
        if amount is None:
            continue
        key = checkin_date.isoformat()
        point = {
            "checkin_date": key,
            "price_amount_yuan": amount,
            "currency": "CNY",
            "source": "calendar_dom_cell_price",
            "is_trusted": True,
            "source_text": _calendar_source_text(cell)[:160],
        }
        previous = by_date.get(key)
        if previous is None or amount < int(previous["price_amount_yuan"]):
            by_date[key] = point
    return [by_date[key] for key in sorted(by_date)]


def _public_calendar_cell_disabled(cell: dict[str, Any]) -> bool:
    class_name = str(cell.get("class_name") or "").lower()
    aria_disabled = str(cell.get("aria_disabled") or "").lower()
    return aria_disabled == "true" or "disabled" in class_name or "is-disabled" in class_name


def _public_calendar_cell_checkin_date(cell: dict[str, Any]) -> date | None:
    data_d = str(cell.get("data_d") or "").strip()
    if data_d:
        normalized = data_d.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).astimezone(
                ZoneInfo("Asia/Shanghai")
            ).date()
        except ValueError:
            pass
    aria_label = str(cell.get("aria_label") or "")
    match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", aria_label)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def _public_calendar_cell_amount(cell: dict[str, Any], checkin_date: date) -> int | None:
    for value in (
        cell.get("price_text"),
        cell.get("aria_label"),
        cell.get("text"),
    ):
        amount = _public_calendar_price_amount(str(value or ""))
        if amount is not None and amount != checkin_date.day:
            return amount
    return _public_calendar_cell_price_amount(str(cell.get("text") or ""), checkin_date.day)


def _calendar_source_text(cell: dict[str, Any]) -> str:
    parts = [
        str(cell.get("aria_label") or ""),
        str(cell.get("text") or ""),
        str(cell.get("price_text") or ""),
    ]
    joined = " | ".join(part.strip() for part in parts if part.strip())
    return re.sub(r"\s+", " ", joined).strip()


def _public_calendar_cell_price_amount(text: str, day: int) -> int | None:
    lines = _clean_public_hotel_lines(text)
    if not lines:
        return None
    compact = re.sub(r"\s+", "", text)
    day_text = str(day)
    if not any(
        line == day_text
        or line.startswith(f"{day_text} ")
        or line.startswith(f"{day_text}¥")
        or line.startswith(f"{day_text}￥")
        or re.search(rf"\d{{1,2}}月{day_text}日", line)
        for line in lines
    ):
        if not re.match(
            rf"^(?:\d{{1,2}}月)?{day_text}(?:日|入住|CNY|¥|￥|\d)",
            compact,
        ):
            return None
    candidates: list[int] = []
    for line in lines:
        if line == day_text:
            continue
        if line.startswith(f"{day_text} "):
            line = line[len(day_text) :].strip()
        if line.startswith(day_text) and len(line) > len(day_text):
            line = line[len(day_text) :].strip()
        amount = _public_calendar_price_amount(line)
        if amount is not None and amount != day:
            candidates.append(amount)
    if not candidates:
        inline_match = re.search(rf"^{day_text}(?:入住)?(?:CNY|[¥￥])?(\d{{2,5}})", compact)
        if inline_match:
            candidates.append(int(inline_match.group(1)))
    valid_candidates = [amount for amount in candidates if 50 <= amount <= 5000]
    if not valid_candidates:
        return None
    return min(valid_candidates)


def _public_calendar_price_amount(line: str) -> int | None:
    amount = _public_room_rate_price_amount(line)
    if amount is None:
        return None
    if 50 <= amount <= 5000:
        return amount
    return None


