from __future__ import annotations

from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order
from app.domain.browser.ctrip_public_competitor_types import (
    CompetitorPriceComparison,
    CompetitorPriceSnapshot,
)
from app.domain.browser.ctrip_room_rate_rules import (
    TRUSTED_DOM_DISPLAY_PRICE_SOURCE,
    breakfast_price_amount_for_primary_room,
)


def _build_public_competitor_price_comparison(
    snapshots: list[CompetitorPriceSnapshot],
) -> CompetitorPriceComparison:
    own = next((snapshot for snapshot in snapshots if snapshot.get("role") == "own"), None)
    own_amount = _snapshot_price_amount(own)
    priced = [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot.get("price"), dict)
        and isinstance(snapshot["price"].get("amount"), int)
    ]
    if not priced:
        return {
            "own_price": None,
            "lowest_price": None,
            "highest_price": None,
            "spread": None,
            "own_rank": None,
            "breakfast_comparison": _build_public_competitor_breakfast_comparison(
                snapshots
            ),
            "summary_lines": [],
        }
    sorted_by_price = sorted(priced, key=lambda snapshot: snapshot["price"]["amount"])
    lowest = sorted_by_price[0]
    highest = sorted_by_price[-1]
    own_rank = None
    if own_amount is not None:
        for index, snapshot in enumerate(sorted_by_price, start=1):
            if snapshot.get("role") == "own":
                own_rank = index
                break
    summary_lines = _build_public_competitor_summary_lines(
        own=own,
        own_amount=own_amount,
        lowest=lowest,
        highest=highest,
        priced_count=len(priced),
    )
    breakfast_comparison = _build_public_competitor_breakfast_comparison(snapshots)
    return {
        "own_price": own_amount,
        "lowest_price": {
            "role": lowest.get("role"),
            "hotel_name": lowest.get("hotel_name"),
            "amount": lowest["price"]["amount"],
            "display": lowest["price"]["display"],
        },
        "highest_price": {
            "role": highest.get("role"),
            "hotel_name": highest.get("hotel_name"),
            "amount": highest["price"]["amount"],
            "display": highest["price"]["display"],
        },
        "spread": highest["price"]["amount"] - lowest["price"]["amount"],
        "own_rank": own_rank,
        "priced_count": len(priced),
        "breakfast_comparison": breakfast_comparison,
        "summary_lines": summary_lines,
    }


def _build_public_competitor_breakfast_comparison(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "no_breakfast": _build_public_competitor_rate_comparison(
            snapshots,
            breakfast_kind="no_breakfast",
        ),
        "with_breakfast": _build_public_competitor_rate_comparison(
            snapshots,
            breakfast_kind="with_breakfast",
        ),
    }


def _build_public_competitor_rate_comparison(
    snapshots: list[dict[str, Any]],
    *,
    breakfast_kind: str,
) -> dict[str, Any]:
    priced = [
        item
        for item in (
            _snapshot_breakfast_price(
                snapshot,
                breakfast_kind=breakfast_kind,
            )
            for snapshot in snapshots
        )
        if item is not None
    ]
    own = next((item for item in priced if item.get("role") == "own"), None)
    if not priced:
        return {
            "own_price": None,
            "lowest_price": None,
            "highest_price": None,
            "spread": None,
            "own_rank": None,
            "priced_count": 0,
        }
    sorted_by_price = sorted(priced, key=lambda item: item["amount"])
    own_rank = None
    if own is not None:
        for index, item in enumerate(sorted_by_price, start=1):
            if item.get("role") == "own":
                own_rank = index
                break
    lowest = sorted_by_price[0]
    highest = sorted_by_price[-1]
    spread = highest["amount"] - lowest["amount"]
    if lowest.get("role") == "own":
        competitor_amounts = [
            item["amount"] for item in sorted_by_price if item.get("role") != "own"
        ]
        if competitor_amounts:
            spread = min(competitor_amounts) - lowest["amount"]
    return {
        "own_price": own.get("amount") if own else None,
        "lowest_price": lowest,
        "highest_price": highest,
        "spread": spread,
        "own_rank": own_rank,
        "priced_count": len(priced),
    }


def _snapshot_breakfast_price(
    snapshot: dict[str, Any],
    *,
    breakfast_kind: str,
) -> dict[str, Any] | None:
    if snapshot.get("status") != "success":
        return None
    rates = snapshot.get("room_rates")
    kind = "none" if breakfast_kind == "no_breakfast" else "included"
    amount = breakfast_price_amount_for_primary_room(
        rates,
        kind=kind,
        trusted_source=TRUSTED_DOM_DISPLAY_PRICE_SOURCE,
    )
    if amount is None:
        return None
    return {
        "role": snapshot.get("role"),
        "hotel_name": snapshot.get("hotel_name"),
        "amount": amount,
        "display": f"¥{amount}",
    }


def _snapshot_price_amount(snapshot: dict[str, Any] | None) -> int | None:
    if not snapshot:
        return None
    price = snapshot.get("price")
    if not isinstance(price, dict):
        return None
    amount = price.get("amount")
    return amount if isinstance(amount, int) else None


def _build_public_competitor_summary_lines(
    *,
    own: dict[str, Any] | None,
    own_amount: int | None,
    lowest: dict[str, Any],
    highest: dict[str, Any],
    priced_count: int,
) -> list[str]:
    lines: list[str] = []
    if own_amount is None:
        lines.append("我们店可见起价未稳定识别，需要人工复核截图。")
    elif lowest.get("role") == "own":
        spread = highest["price"]["amount"] - own_amount
        lines.append(f"我们店起价最低（¥{own_amount}），与最高差价¥{spread}。")
    else:
        lowest_amount = lowest["price"]["amount"]
        lines.append(
            f"我们店起价¥{own_amount}，比最低竞品高¥{own_amount - lowest_amount}。"
        )
    if own and own.get("availability"):
        lines.append(f"我们店库存线索：{own['availability']}。")
    lines.append(f"本次稳定识别 {priced_count} 家酒店可见起价。")
    return lines


def _public_competitor_price_status(snapshots: list[CompetitorPriceSnapshot]) -> str:
    statuses = [snapshot.get("status") for snapshot in snapshots]
    tomorrow_statuses: list[str] = []
    for snapshot in snapshots:
        context = snapshot.get("date_context")
        if not isinstance(context, dict) or not context.get("check_in"):
            continue
        tomorrow = snapshot.get("tomorrow")
        tomorrow_statuses.append(
            str(tomorrow.get("status"))
            if isinstance(tomorrow, dict)
            else "needs_review"
        )
    all_statuses = [*statuses, *tomorrow_statuses]
    if any(status == "human_required" for status in all_statuses):
        return "human_required"
    if all_statuses and all(status == "success" for status in all_statuses):
        return "success"
    if any(status == "success" for status in all_statuses):
        return "partial"
    return "needs_review"


def _public_competitor_price_summary(
    status: str,
    snapshots: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> str:
    if status == "human_required":
        return "Ctrip public hotel page requires login or verification before reading prices."
    priced_count = comparison.get("priced_count") or 0
    if status == "success":
        return f"Ctrip competitor prices were read for {priced_count} hotels."
    snapshot_statuses = _snapshot_and_tomorrow_statuses(snapshots)
    needs_review = sum(1 for item in snapshot_statuses if item != "success")
    success_count = sum(1 for item in snapshot_statuses if item == "success")
    if status == "partial":
        return f"竞品价格读取部分成功：成功 {success_count} 个日期页，需复核 {needs_review} 个日期页。"
    return (
        f"Ctrip competitor price page opened, but {needs_review} hotel price snapshot(s) "
        "need review."
    )


def _public_competitor_price_next_action(status: str) -> str:
    if status == "human_required":
        return "Complete login or verification in the persistent browser, then retry."
    if status in {"partial", "needs_review"}:
        return "Review screenshot/raw_snippets and refine visible price selectors."
    return "Use this read-only snapshot for Feishu monitoring and trend storage."


def _snapshot_and_tomorrow_statuses(snapshots: list[dict[str, Any]]) -> list[str]:
    statuses: list[str] = []
    for snapshot in snapshots:
        statuses.append(str(snapshot.get("status") or "needs_review"))
        context = snapshot.get("date_context")
        if not isinstance(context, dict) or not context.get("check_in"):
            continue
        tomorrow = snapshot.get("tomorrow")
        statuses.append(
            str(tomorrow.get("status"))
            if isinstance(tomorrow, dict)
            else "needs_review"
        )
    return statuses


def _extract_date_context_from_snapshots(snapshots: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for snapshot in snapshots:
        context = snapshot.get("date_context", {})
        if not isinstance(context, dict):
            continue
        check_in = context.get("check_in")
        if isinstance(check_in, str) and check_in:
            dates.append(check_in)
    return _dedupe_keep_order(dates)
