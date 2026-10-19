from __future__ import annotations

import os
from typing import Any, Literal

WatchKind = Literal["ebk", "competitor"]

WATCH_KIND_EBK: WatchKind = "ebk"
WATCH_KIND_COMPETITOR: WatchKind = "competitor"

DEFAULT_EBK_GROUP = "ctrip_ebooking_data_center"
DEFAULT_COMPETITOR_GROUP = "default_competitor_watch"

LEGACY_EBK_GROUP_ALIASES = {
    "default_data_center_watch": DEFAULT_EBK_GROUP,
}

EBK_EXCLUSIVE_KEYS = frozenset({DEFAULT_EBK_GROUP, *LEGACY_EBK_GROUP_ALIASES.keys()})
COMPETITOR_EXCLUSIVE_KEYS = frozenset({DEFAULT_COMPETITOR_GROUP})


def normalize_ebk_watch_group_key(watch_group_key: str | None) -> str:
    raw = (watch_group_key or os.environ.get("WATCH_DEFAULT_EBK_GROUP") or DEFAULT_EBK_GROUP).strip()
    return LEGACY_EBK_GROUP_ALIASES.get(raw, raw)


def normalize_competitor_watch_group_key(watch_group_key: str | None) -> str:
    raw = (
        watch_group_key
        or os.environ.get("WATCH_DEFAULT_COMPETITOR_GROUP")
        or DEFAULT_COMPETITOR_GROUP
    ).strip()
    return raw


def watch_kind_label(watch_kind: WatchKind) -> str:
    return "EBK 数据中心" if watch_kind == WATCH_KIND_EBK else "竞品价格"


def prepare_watch_group_key(
    watch_group_key: str | None,
    *,
    watch_kind: WatchKind,
) -> tuple[str | None, dict[str, Any] | None]:
    if watch_kind == WATCH_KIND_EBK:
        key = normalize_ebk_watch_group_key(watch_group_key)
    else:
        key = normalize_competitor_watch_group_key(watch_group_key)
    mismatch = guard_watch_group_kind(key, expected_kind=watch_kind)
    if mismatch is not None:
        return None, mismatch
    return key, None


def guard_watch_group_kind(watch_group_key: str, *, expected_kind: WatchKind) -> dict[str, Any] | None:
    if expected_kind == WATCH_KIND_EBK and watch_group_key in COMPETITOR_EXCLUSIVE_KEYS:
        return _kind_mismatch_error(
            watch_group_key=watch_group_key,
            expected_kind=expected_kind,
            actual_kind=WATCH_KIND_COMPETITOR,
        )
    if expected_kind == WATCH_KIND_COMPETITOR and watch_group_key in EBK_EXCLUSIVE_KEYS:
        return _kind_mismatch_error(
            watch_group_key=watch_group_key,
            expected_kind=expected_kind,
            actual_kind=WATCH_KIND_EBK,
        )
    return None


def _kind_mismatch_error(
    *,
    watch_group_key: str,
    expected_kind: WatchKind,
    actual_kind: WatchKind,
) -> dict[str, Any]:
    expected_default = (
        normalize_ebk_watch_group_key(None)
        if expected_kind == WATCH_KIND_EBK
        else normalize_competitor_watch_group_key(None)
    )
    actual_default = (
        normalize_ebk_watch_group_key(None)
        if actual_kind == WATCH_KIND_EBK
        else normalize_competitor_watch_group_key(None)
    )
    return {
        "status": "invalid_arguments",
        "summary": (
            f"{watch_group_key} 属于{watch_kind_label(actual_kind)}监控组，"
            f"不能用于{watch_kind_label(expected_kind)}工具。"
        ),
        "watch_kind_expected": expected_kind,
        "watch_kind_actual": actual_kind,
        "watch_group_key": watch_group_key,
        "correct_default_watch_group_key": expected_default,
        "suggested_watch_group_key_for_actual_kind": actual_default,
        "usage_note": (
            "EBK 经营/好评/出租率只用 EBK 组与 get_ebk_latest_result / read_ebk_datasets；"
            "竞品价格/价格变化只用竞品组与 get_competitor_latest_prices / analyze_competitor_price_change。"
            "不确定时先调用 list_watch_groups。"
        ),
    }


def summarize_watch_group(group: dict[str, Any], *, watch_kind: WatchKind) -> dict[str, Any]:
    latest_run = group.get("latest_run") if isinstance(group.get("latest_run"), dict) else {}
    return {
        "watch_kind": watch_kind,
        "watch_group_key": group.get("group_key") or group.get("watch_group_key"),
        "name": group.get("name"),
        "enabled": group.get("enabled"),
        "auto_poll_enabled": group.get("auto_poll_enabled"),
        "latest_run_status": latest_run.get("status"),
        "latest_captured_at": latest_run.get("captured_at"),
    }


def build_watch_group_catalog(
    *,
    ebk_groups: list[dict[str, Any]],
    competitor_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "defaults": {
            "ebk": normalize_ebk_watch_group_key(None),
            "competitor": normalize_competitor_watch_group_key(None),
        },
        "ebk_groups": [summarize_watch_group(item, watch_kind=WATCH_KIND_EBK) for item in ebk_groups],
        "competitor_groups": [
            summarize_watch_group(item, watch_kind=WATCH_KIND_COMPETITOR) for item in competitor_groups
        ],
        "usage_note": (
            "EBK 与竞品是两套独立监控组，key 不可混用。"
            "EBK 默认 ctrip_ebooking_data_center；竞品默认 default_competitor_watch。"
        ),
    }
