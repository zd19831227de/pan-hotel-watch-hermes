from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

PUBLIC_CTRIP_HOTEL_HOST = "hotels.ctrip.com"


def is_valid_public_ctrip_hotel_url(url: str) -> bool:
    return public_ctrip_hotel_url_error(url) is None


def public_ctrip_hotel_id_from_url(url: str) -> str | None:
    normalized = str(url or "").strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    query = parse_qs(parsed.query)
    for key in ("hotelId", "hotelid", "hotelID"):
        values = query.get(key)
        if not values:
            continue
        value = str(values[0]).strip()
        if re.fullmatch(r"\d{5,}", value):
            return value
        return None
    path_match = re.search(r"(?:hotels?/|hotel[_-]?)(\d{5,})", parsed.path, re.IGNORECASE)
    if path_match:
        return path_match.group(1)
    return None


def public_ctrip_hotel_url_error(url: str) -> str | None:
    normalized = str(url or "").strip()
    if not normalized:
        return "empty_url"
    parsed = urlparse(normalized)
    host = parsed.hostname.lower() if parsed.hostname else ""
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"}:
        return "invalid_scheme"
    if host != PUBLIC_CTRIP_HOTEL_HOST:
        return "invalid_host"
    if "/pages/404" in path:
        return "not_found_page"
    if "hotels" not in path and "detail" not in path:
        return "not_hotel_detail_page"
    if public_ctrip_hotel_id_from_url(normalized) is None:
        return "missing_numeric_hotel_id"
    return None


def invalid_public_ctrip_hotel_urls(urls: list[str]) -> list[str]:
    invalid: list[str] = []
    for url in urls:
        normalized = str(url or "").strip()
        if normalized and public_ctrip_hotel_url_error(normalized) is not None:
            invalid.append(normalized)
    return invalid
