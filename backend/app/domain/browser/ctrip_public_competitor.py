from __future__ import annotations

# Compatibility facade for older imports. Public Ctrip parsing is split by
# responsibility into date/url helpers, snapshot parsing, and room-rate parsing.
from app.domain.browser.ctrip_public_competitor_comparison import (
    _build_public_competitor_price_comparison as _build_public_competitor_price_comparison,
)
from app.domain.browser.ctrip_public_competitor_comparison import (
    _extract_date_context_from_snapshots as _extract_date_context_from_snapshots,
)
from app.domain.browser.ctrip_public_competitor_comparison import (
    _public_competitor_price_next_action as _public_competitor_price_next_action,
)
from app.domain.browser.ctrip_public_competitor_comparison import (
    _public_competitor_price_status as _public_competitor_price_status,
)
from app.domain.browser.ctrip_public_competitor_comparison import (
    _public_competitor_price_summary as _public_competitor_price_summary,
)
from app.domain.browser.ctrip_public_hotel_dates import (  # noqa: F401
    _build_competitor_price_targets,
    _build_public_tomorrow_snapshot_from_detail,
    _date_signal_payload,
    _extract_public_calendar_tomorrow_snapshot,
    _extract_public_hotel_date_context,
    _extract_visible_public_calendar_tomorrow_snapshot,
    _first_query_value,
    _invalid_public_ctrip_hotel_urls,
    _is_valid_public_ctrip_hotel_url,
    _local_public_check_in_date,
    _missing_public_tomorrow_snapshot,
    _month_day_near_date,
    _nights_between,
    _normalize_iso_date,
    _parse_iso_date,
    _public_calendar_cell_price_amount,
    _public_calendar_price_amount,
    _public_ctrip_hotel_id_from_url,
    _public_ctrip_hotel_url_error,
    _public_hotel_date_verification,
    _public_hotel_month_day_visible_dates,
    _public_hotel_url_dates,
    _public_hotel_url_for_check_in,
    _public_hotel_visible_date_lines,
    _public_hotel_visible_dates,
    _sanitize_public_hotel_url,
    _shift_public_hotel_url_dates,
)
from app.domain.browser.ctrip_public_hotel_snapshot_parser import (  # noqa: F401
    _classify_public_hotel_page_state,
    _extract_public_hotel_name,
    _extract_public_hotel_price_snapshot,
    _looks_like_hotel_name,
    _looks_like_public_hotel_readable_price_page,
    _public_hotel_review_reason,
    _public_hotel_snapshot_status,
    _public_page_state_with_room_rates,
)
from app.domain.browser.ctrip_public_room_rate_parser import (  # noqa: F401
    _combine_split_currency_lines,
    _dedupe_public_room_rates_by_plan,
    _extract_first_line_matching,
    _extract_public_inventory_clue,
    _extract_public_inventory_clue_for_rate,
    _extract_public_price_candidates,
    _extract_public_room_rates,
    _extract_public_room_rates_from_breakfast_windows,
    _first_public_breakfast_clue,
    _first_public_room_clue,
    _is_public_breakfast_clue_line,
    _is_public_inventory_clue_line,
    _is_public_price_context_blacklisted,
    _is_public_price_expansion_line,
    _is_public_room_clue_line,
    _nearest_public_breakfast_around,
    _nearest_public_breakfast_before,
    _nearest_public_rate_price_around,
    _nearest_public_room_around,
    _nearest_public_room_before,
    _normalize_public_inventory_clue,
    _normalize_public_structured_room_rates,
    _public_display_price_amount,
    _public_price_window,
    _public_room_rate_price_amount,
    _score_public_price_candidate,
    _select_primary_public_room_rate,
    _select_public_price_candidate,
    _select_public_price_snippets,
)
