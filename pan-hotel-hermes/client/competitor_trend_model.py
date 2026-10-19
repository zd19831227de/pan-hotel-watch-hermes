from __future__ import annotations

import math
from datetime import datetime
from math import floor, log10
from typing import Any


def format_trend_bucket(value: str, *, include_seconds: bool = False) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:19].replace("T", " ")
    if include_seconds:
        return parsed.strftime("%m-%d %H:%M:%S")
    return parsed.strftime("%m-%d %H:%M")


def should_show_seconds(buckets: list[str]) -> bool:
    minute_labels = [format_trend_bucket(bucket) for bucket in buckets]
    return len(set(minute_labels)) < len(minute_labels)


def collect_trend_buckets(series_list: list[dict[str, Any]]) -> list[str]:
    buckets: set[str] = set()
    for series in series_list:
        if not isinstance(series, dict):
            continue
        points = series.get("points") if isinstance(series.get("points"), list) else []
        for point in points:
            if not isinstance(point, dict):
                continue
            price = point.get("price_amount_yuan")
            if not isinstance(price, (int, float)):
                continue
            bucket = str(point.get("bucket_at") or point.get("captured_at") or "").strip()
            if bucket:
                buckets.add(bucket)
    return sorted(buckets)


def build_price_axis(min_price: float, max_price: float) -> dict[str, float]:
    if not min_price <= max_price or max_price <= 0:
        return {"min": 0.0, "max": 100.0, "interval": 20.0}
    low = min(min_price, max_price)
    high = max(min_price, max_price)
    span = high - low
    padding = max(10.0, span * 0.1) if span > 0 else max(20.0, high * 0.08)
    padded_min = max(0.0, low - padding)
    padded_max = high + padding
    interval = _choose_price_axis_interval(padded_max - padded_min)

    axis_min = floor(padded_min / interval) * interval
    axis_max = math.ceil(padded_max / interval) * interval
    if axis_max <= axis_min:
        axis_max = axis_min + interval * 4
    if axis_min == 0 and low > interval:
        axis_min = max(0.0, floor((low - padding) / interval) * interval)
    return {"min": axis_min, "max": axis_max, "interval": interval}


def _choose_price_axis_interval(range_value: float) -> float:
    safe_range = range_value if range_value > 0 else 100.0
    target = safe_range / 7.0
    exponent = floor(log10(target))
    magnitude = 10 ** exponent
    normalized = target / magnitude
    if normalized <= 1:
        step = 1
    elif normalized <= 2:
        step = 2
    elif normalized <= 5:
        step = 5
    else:
        step = 10
    return step * magnitude


def extract_series_timeline(series: dict[str, Any]) -> list[tuple[str, int, str]]:
    """按 bucket 时间排序的 (bucket_at, price, display_time)。"""
    points = series.get("points") if isinstance(series.get("points"), list) else []
    timeline: list[tuple[str, int, str]] = []
    include_seconds = False
    for point in points:
        if not isinstance(point, dict):
            continue
        price = point.get("price_amount_yuan")
        if not isinstance(price, (int, float)):
            continue
        bucket = str(point.get("bucket_at") or point.get("captured_at") or "").strip()
        if not bucket:
            continue
        captured = str(point.get("captured_at") or bucket)
        timeline.append((bucket, int(price), captured))
    if not timeline:
        return []
    buckets = [bucket for bucket, _, _ in timeline]
    include_seconds = should_show_seconds(buckets)
    deduped: dict[str, tuple[str, int, str]] = {}
    for bucket, price, captured in timeline:
        deduped[bucket] = (
            bucket,
            price,
            format_trend_bucket(captured or bucket, include_seconds=include_seconds),
        )
    return [deduped[bucket] for bucket in sorted(deduped)]


def build_panel_chart_data(
    series_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """对齐前端可信趋势图：共享时间轴 + 各酒店折线点位。"""
    axis_buckets = collect_trend_buckets(series_list)
    if not axis_buckets:
        return {
            "axis_buckets": [],
            "x_labels": [],
            "axis_min": 0.0,
            "axis_max": 100.0,
            "axis_interval": 20.0,
            "lines": [],
        }
    include_seconds = should_show_seconds(axis_buckets)
    x_labels = [
        format_trend_bucket(bucket, include_seconds=include_seconds) for bucket in axis_buckets
    ]
    bucket_index = {bucket: index for index, bucket in enumerate(axis_buckets)}
    all_prices: list[int] = []
    lines: list[dict[str, Any]] = []
    for series in series_list:
        if not isinstance(series, dict):
            continue
        coords: list[tuple[int, int]] = []
        for point in series.get("points") or []:
            if not isinstance(point, dict):
                continue
            price = point.get("price_amount_yuan")
            if not isinstance(price, (int, float)):
                continue
            bucket = str(point.get("bucket_at") or point.get("captured_at") or "").strip()
            if bucket not in bucket_index:
                continue
            price_int = int(price)
            all_prices.append(price_int)
            coords.append((bucket_index[bucket], price_int))
        if not coords:
            continue
        deduped = {x: price for x, price in coords}
        sorted_coords = sorted((x, deduped[x]) for x in deduped)
        lines.append(
            {
                "series": series,
                "coords": sorted_coords,
            }
        )
    if not all_prices:
        axis = build_price_axis(0, 100)
    else:
        axis = build_price_axis(min(all_prices), max(all_prices))
    return {
        "axis_buckets": axis_buckets,
        "x_labels": x_labels,
        "axis_min": axis["min"],
        "axis_max": axis["max"],
        "axis_interval": axis["interval"],
        "lines": lines,
    }


def summarize_price_process(timeline: list[tuple[str, int, str]], *, max_trail: int = 6) -> str:
    """描述变化过程：连续观测轨迹，而非区间首尾对比。"""
    if not timeline:
        return "暂无观测"
    prices = [price for _, price, _ in timeline]
    trail = [f"¥{price}" for _, price, _ in timeline[-max_trail:]]
    trail_text = " → ".join(trail)
    lo, hi = min(prices), max(prices)
    current = prices[-1]
    if len(prices) == 1:
        return f"1次观测 {trail_text}"
    return f"{len(prices)}次读价 {trail_text}（区间¥{lo}-¥{hi}，当前¥{current}）"


# 「各时段最低价」对比模式（2026-10-08 生产实证立瑞 21:12 三连问）：本店公开页
# 只有含早序列、水云岸只有无早序列，按 含早/无早 拆面板时两家永不同框——
# 运营说「最低价对比」指每家店各时段全房型最低价一条线，本标记驱动图表
# 渲染器切「单店一线」对比视图（本店置顶加粗），数字精确到元。
MIN_COMPARE_BREAKFAST = "min_compare"
_OWN_HOTEL_TOKENS = ("本店", "own", "自家", "我们自己")


def filter_trend_series_by_hotels(
    series_list: list[dict[str, Any]], hotels: list[str] | tuple[str, ...] | None
) -> list[dict[str, Any]]:
    """按店名关键词过滤序列；「本店」匹配 role=own。空过滤原样返回。"""
    tokens = [str(token or "").strip().lower() for token in (hotels or []) if str(token or "").strip()]
    if not tokens:
        return list(series_list)
    kept: list[dict[str, Any]] = []
    for series in series_list:
        if not isinstance(series, dict):
            continue
        alias = str(series.get("hotel_alias") or "").lower()
        name = str(series.get("hotel_name") or "").lower()
        role = str(series.get("hotel_role") or "")
        matched = any(
            (token in _OWN_HOTEL_TOKENS and role == "own")
            or (token not in _OWN_HOTEL_TOKENS and (token in alias or token in name))
            for token in tokens
        )
        if matched:
            kept.append(series)
    return kept


def build_min_comparison_series(series_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每店×入住日一条「各时段最低价」序列（跨早次/房型同桶取最低）。

    输入是后端原始序列（hotel × date_label × breakfast × room_type），输出
    按 (店, date_label) 归并：同一时间桶内取该店全部序列的最低价。这样
    本店（可能只有含早房在售）与无早竞品可以同图对比——按早次拆面板的
    标准视图做不到这一点。
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for series in series_list:
        if not isinstance(series, dict):
            continue
        hotel_key = str(
            series.get("hotel_config_id") or series.get("hotel_alias") or series.get("hotel_name") or ""
        )
        date_label = str(series.get("date_label") or "")
        if not hotel_key:
            continue
        key = (hotel_key, date_label)
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "hotel_config_id": series.get("hotel_config_id"),
                "hotel_alias": series.get("hotel_alias"),
                "hotel_name": series.get("hotel_name"),
                "hotel_role": series.get("hotel_role"),
                "sort_order": series.get("sort_order", 0),
                "date_label": date_label,
                "buckets": {},
            }
            grouped[key] = entry
            order.append(key)
        for point in series.get("points") or []:
            if not isinstance(point, dict):
                continue
            price = point.get("price_amount_yuan")
            if not isinstance(price, (int, float)):
                continue
            bucket = str(point.get("bucket_at") or point.get("captured_at") or "").strip()
            if not bucket:
                continue
            captured = str(point.get("captured_at") or bucket)
            price_int = int(price)
            current = entry["buckets"].get(bucket)
            if current is None or price_int < current[0]:
                entry["buckets"][bucket] = (price_int, captured)
    result: list[dict[str, Any]] = []
    for key in order:
        entry = grouped[key]
        buckets = entry.get("buckets") or {}
        if not buckets:
            continue
        result.append(
            {
                "hotel_config_id": entry.get("hotel_config_id"),
                "hotel_alias": entry.get("hotel_alias"),
                "hotel_name": entry.get("hotel_name"),
                "hotel_role": entry.get("hotel_role"),
                "sort_order": entry.get("sort_order", 0),
                "date_label": entry.get("date_label"),
                "breakfast_kind": MIN_COMPARE_BREAKFAST,
                "room_type": None,
                "points": [
                    {"price_amount_yuan": price, "bucket_at": bucket, "captured_at": captured}
                    for bucket, (price, captured) in sorted(buckets.items())
                ],
            }
        )
    return result
