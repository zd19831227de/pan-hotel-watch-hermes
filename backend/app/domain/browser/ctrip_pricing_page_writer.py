from __future__ import annotations

from typing import Any

PRICING_PAGE_WRITER_VERSION = "ctrip_ebooking_pricing_page_writer_dry_run_v1"


def build_ctrip_pricing_page_write_preview(
    *,
    execute_request: dict[str, Any],
    page_read: dict[str, Any],
) -> dict[str, Any]:
    if page_read.get("status") != "success":
        return {
            "status": page_read.get("status") or "page_structure_changed",
            "summary": page_read.get("summary") or "Ctrip pricing page could not be read.",
            "steps": [],
            "targets": page_read.get("targets") or [],
            "evidence": _base_evidence(page_read, dry_run_ready=False),
            "next_action": page_read.get("next_action"),
        }

    targets = [
        _target_write_preview(target)
        for target in page_read.get("targets") or []
        if isinstance(target, dict)
    ]
    missing = [target for target in targets if target.get("status") != "located"]
    if missing:
        return {
            "status": "page_structure_changed",
            "summary": "Some approved price-write targets were not located on the EBK page.",
            "steps": _dry_run_steps(targets, dry_run_ready=False),
            "targets": targets,
            "data": {"missing_targets": missing},
            "evidence": _base_evidence(page_read, dry_run_ready=False),
            "next_action": "Review Ctrip EBK page structure and selector mapping before retrying.",
        }

    return {
        "status": "page_write_preview_ready",
        "summary": "Ctrip EBK price-write dry-run preview is ready; no OTA write was submitted.",
        "steps": _dry_run_steps(targets, dry_run_ready=True),
        "targets": targets,
        "data": {
            "request_id": execute_request.get("request_id"),
            "target_count": len(targets),
        },
        "evidence": _base_evidence(page_read, dry_run_ready=True),
        "next_action": (
            "P8 may use a typed page writer to fill fields and pause before submit; "
            "this dry-run did not change the page."
        ),
    }


def _target_write_preview(target: dict[str, Any]) -> dict[str, Any]:
    candidate = _primary_input_candidate(target)
    located = target.get("status") == "located"
    return {
        "status": "located" if located else "not_found",
        "operation": target.get("operation"),
        "platform": target.get("platform"),
        "room_type": target.get("room_type"),
        "checkin_date": target.get("checkin_date"),
        "checkout_date": target.get("checkout_date"),
        "breakfast_kind": target.get("breakfast_kind"),
        "current_price_yuan": target.get("current_price_yuan"),
        "current_base_price_yuan": target.get("current_base_price_yuan"),
        "proposed_price_yuan": target.get("proposed_price_yuan"),
        "proposed_base_price_yuan": target.get("proposed_base_price_yuan"),
        "commission_rate": target.get("commission_rate"),
        "locator_strategy": target.get("locator_strategy"),
        "selector_hint": candidate.get("selector_hint") if candidate else None,
        "readback_line": _primary_readback_line(target),
        "planned_value": target.get("price_text_expected"),
        "actions_blocked": {
            "fill": True,
            "click_save": True,
            "submit": True,
        },
    }


def _dry_run_steps(
    targets: list[dict[str, Any]],
    *,
    dry_run_ready: bool,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "kind": "read_page",
            "description": "Open Ctrip EBK pricing page and read visible page state.",
            "write": False,
        },
        {
            "step": 2,
            "kind": "locate_targets",
            "description": "Locate approved room/date price fields from visible DOM evidence.",
            "write": False,
        },
    ]
    for index, target in enumerate(targets, start=3):
        steps.append(
            {
                "step": index,
                "kind": "plan_fill",
                "description": _format_dry_run_description(target),
                "target": {
                    "room_type": target.get("room_type"),
                    "checkin_date": target.get("checkin_date"),
                    "selector_hint": target.get("selector_hint"),
                    "planned_value": (
                        target.get("proposed_base_price_yuan")
                        or target.get("planned_value")
                    ),
                },
                "write": False,
            }
        )
    steps.append(
        {
            "step": len(steps) + 1,
            "kind": "stop_before_write",
            "description": (
                "Dry-run stops before filling, saving, or submitting OTA changes."
                if dry_run_ready
                else "Dry-run stops because one or more targets were not located."
            ),
            "write": False,
        }
    )
    return steps


def _format_dry_run_description(target: dict[str, Any]) -> str:
    """Describe the planned write step in operator-readable Chinese.

    When a base price is available, it shows the EBK input-box value
    together with the sell-price intent.
    """
    room = target.get("room_type") or "room"
    date = target.get("checkin_date") or ""
    base = target.get("proposed_base_price_yuan")
    sell = target.get("proposed_price_yuan")
    commission = target.get("commission_rate")
    if base is not None and sell is not None and commission is not None:
        return (
            f"Prepare to set {room} {date} base price to {base} yuan "
            f"(commission rate {commission*100:.0f}%, target sell price {sell} yuan)."
        ).strip()
    if base is not None:
        return (
            f"Prepare to set {room} {date} base price to {base} yuan."
        ).strip()
    return (
        f"Prepare to set {room} {date} price to {sell or '?'} yuan."
    ).strip()


def _primary_input_candidate(target: dict[str, Any]) -> dict[str, Any] | None:
    candidates = target.get("input_candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    return first if isinstance(first, dict) else None


def _primary_readback_line(target: dict[str, Any]) -> str | None:
    lines = target.get("matching_lines")
    if not isinstance(lines, list) or not lines:
        return None
    return str(lines[0])


def _base_evidence(page_read: dict[str, Any], *, dry_run_ready: bool) -> dict[str, Any]:
    read_evidence = (
        page_read.get("evidence") if isinstance(page_read.get("evidence"), dict) else {}
    )
    return {
        **read_evidence,
        "writer_version": PRICING_PAGE_WRITER_VERSION,
        "dry_run_ready": dry_run_ready,
        "browser_write_invoked": False,
        "input_fill_invoked": False,
        "save_click_invoked": False,
        "ota_submit_invoked": False,
        "screenshot_path_persisted": False,
    }
