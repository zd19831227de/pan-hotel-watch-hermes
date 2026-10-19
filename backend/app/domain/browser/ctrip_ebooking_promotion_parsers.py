"""Ctrip EBK promotion parsers ? extracted from ctrip_ebooking_parsers.py.

Pure functions for parsing promotion effect and promotion registered datasets.
No browser actions, no side effects.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order
from app.domain.browser.ctrip_ebooking_dataset_parsers import (
    _line_contains_metric_label,
    _normalize_metric_value,
)
from app.domain.browser.ctrip_ebooking_parsers import (
    _BUSINESS_VALUE_RE,
    _is_action_or_navigation_line,
)


def _extract_specialized_dataset_payload(
    *,
    dataset_id: str,
    lines: list[str],
    metrics: dict[str, dict[str, Any]],
    tables: list[dict[str, Any]],
    raw_snippets: list[str],
) -> dict[str, Any]:
    if dataset_id == "promotion_registered":
        return _extract_promotion_registered_payload(lines, raw_snippets=raw_snippets)
    if dataset_id == "promotion_effect":
        return _extract_promotion_effect_payload(lines, metrics=metrics, tables=tables)
    return {}


def _extract_promotion_effect_payload(
    lines: list[str],
    *,
    metrics: dict[str, dict[str, Any]],
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    suggestions = _extract_promotion_effect_suggestions(lines, tables=tables)
    promotion_metrics = _extract_promotion_effect_promotion_metrics(lines)
    page_prompts = _extract_promotion_page_prompts(lines)
    notes: list[str] = []
    core_missing = [
        label
        for key, label in (
            ("participated_promotions", "参加促销"),
            ("promotion_room_nights", "含促销间夜"),
            ("promotion_room_night_share", "含促销间夜占比"),
            ("average_daily_app_visitors", "APP 访客量"),
        )
        if key not in metrics
    ]
    if core_missing and metrics:
        notes.append(f"促销效果核心指标未完整识别：{', '.join(core_missing)}。")
    status_hint = None
    if not metrics and (promotion_metrics or suggestions):
        status_hint = "needs_review"
    return {
        "structure_ok": bool(metrics or suggestions or promotion_metrics),
        "status_hint": status_hint,
        "summary_metrics": metrics,
        "promotion_metrics": promotion_metrics,
        "suggestions": suggestions,
        "page_prompts": page_prompts,
        "data_quality_notes": notes,
    }


def _extract_promotion_effect_suggestions(
    lines: list[str],
    *,
    tables: list[dict[str, Any]],
) -> list[str]:
    suggestions: list[str] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows", []) or []:
            if isinstance(row, dict) and row.get("text"):
                suggestions.append(str(row["text"]))
    for line in lines:
        if line.startswith("建议") or any(
            term in line for term in ("机会", "异常", "低于", "提升")
        ):
            suggestions.append(line)
    return _dedupe_keep_order(suggestions)[:20]


def _extract_promotion_effect_promotion_metrics(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(_extract_promotion_effect_table_rows(lines))
    for index, line in enumerate(lines):
        if not _looks_like_promotion_effect_row(line):
            continue
        values = _extract_promotion_metric_values_from_text(line)
        context = " ".join(lines[index : index + 4])
        values.update(_extract_promotion_metric_values_from_text(context))
        if not values:
            continue
        name = _promotion_metric_row_name(line)
        if name == "未识别促销":
            continue
        rows.append(
            {
                "name": name,
                "metrics": values,
                "raw_text": context[:800],
            }
        )
    rows.extend(_extract_promotion_effect_metric_table_rows(lines))
    return _dedupe_promotion_metric_rows(rows)[:20]


def _extract_promotion_effect_table_rows(lines: list[str]) -> list[dict[str, Any]]:
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if all(
                term in line
                for term in (
                    "促销名称",
                    "含促销间夜数",
                    "含促销间夜占比",
                    "含促销收益",
                    "含促销收益占比",
                    "优惠金额",
                )
            )
        ),
        None,
    )
    if header_index is None:
        return []
    rows: list[dict[str, Any]] = []
    cursor = header_index + 1
    while cursor < len(lines):
        line = lines[cursor]
        if _is_promotion_effect_table_stop_line(line) or _is_action_or_navigation_line(line):
            break
        if _is_promotion_registered_navigation_line(line):
            break
        if not line or _BUSINESS_VALUE_RE.fullmatch(line):
            cursor += 1
            continue
        name, values = _promotion_effect_table_row_name_and_values(line)
        if not name:
            cursor += 1
            continue
        value_cursor = cursor + 1
        while value_cursor < len(lines) and len(values) < 5:
            candidate = lines[value_cursor]
            if _is_promotion_effect_table_stop_line(candidate):
                break
            if _BUSINESS_VALUE_RE.fullmatch(candidate):
                normalized = _normalize_metric_value(candidate)
                if normalized:
                    values.append(normalized)
                value_cursor += 1
                continue
            if values:
                break
            if not _is_action_or_navigation_line(candidate):
                break
            value_cursor += 1
        if len(values) >= 5:
            rows.append(
                {
                    "name": name[:80],
                    "metrics": {
                        "room_nights": values[0],
                        "room_night_share": values[1],
                        "revenue": values[2],
                        "revenue_share": values[3],
                        "discount_amount": values[4],
                    },
                    "raw_text": "\n".join(lines[cursor:value_cursor])[:800],
                }
            )
            cursor = value_cursor
            continue
        cursor += 1
    return rows


def _promotion_effect_table_row_name_and_values(line: str) -> tuple[str, list[str]]:
    match = re.match(
        rf"^(?P<name>.+?)\s+(?P<value>{_BUSINESS_VALUE_RE.pattern})$",
        line,
    )
    if not match:
        return line[:80], []
    name = match.group("name").strip(" ：:-")
    value = _normalize_metric_value(match.group("value"))
    if not name or _is_promotion_registered_navigation_line(name):
        return "", []
    return name[:80], [value] if value else []


def _looks_like_promotion_effect_row(line: str) -> bool:
    if any(
        _line_contains_metric_label(line, label)
        for label in (
            "参加促销",
            "总促销数",
            "含促销间夜",
            "含促销间夜占比",
            "访客量（APP）",
        )
    ):
        return False
    metric_hits = sum(
        1
        for term in ("曝光", "点击", "转化", "订单", "销售额", "ROI", "间夜")
        if term.lower() in line.lower()
    )
    return ("促销" in line or "活动" in line) and metric_hits >= 1


def _promotion_metric_row_name(line: str) -> str:
    name = re.split(r"曝光|点击|转化|订单|销售额|ROI|间夜", line, maxsplit=1)[0]
    name = re.sub(r"^(?:活动名称|促销名称)[:：]?", "", name).strip(" ：:-")
    return name[:80] or "未识别促销"


def _extract_promotion_metric_values_from_text(text: str) -> dict[str, Any]:
    specs = {
        "exposure": ("曝光", "曝光量"),
        "clicks": ("点击", "点击量"),
        "conversion_rate": ("转化率", "转化"),
        "orders": ("订单", "订单数"),
        "sales_amount": ("销售额", "销售"),
        "roi": ("ROI", "roi"),
        "room_nights": ("间夜", "间夜量"),
    }
    metrics: dict[str, Any] = {}
    for key, labels in specs.items():
        for label in labels:
            value = _extract_labeled_value_from_text(text, label)
            if value:
                metrics[key] = value
                break
    return metrics


def _extract_promotion_effect_metric_table_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        columns = _promotion_effect_metric_columns(line)
        if len(columns) < 2:
            continue
        for candidate in lines[index + 1 : index + 20]:
            if _is_action_or_navigation_line(candidate):
                continue
            if _is_promotion_effect_table_stop_line(candidate):
                break
            row = _parse_promotion_effect_metric_table_row(candidate, columns=columns)
            if row:
                rows.append(row)
    return rows


def _promotion_effect_metric_columns(line: str) -> list[tuple[str, str]]:
    specs = (
        ("曝光", "exposure"),
        ("点击", "clicks"),
        ("转化率", "conversion_rate"),
        ("转化", "conversion_rate"),
        ("订单数", "orders"),
        ("订单", "orders"),
        ("销售额", "sales_amount"),
        ("销售", "sales_amount"),
        ("ROI", "roi"),
        ("间夜量", "room_nights"),
        ("间夜", "room_nights"),
    )
    located: list[tuple[int, str, str]] = []
    for label, key in specs:
        position = line.lower().find(label.lower())
        if position >= 0 and key not in {item[2] for item in located}:
            located.append((position, label, key))
    located.sort(key=lambda item: item[0])
    if len(located) < 2:
        return []
    if not any(term in line for term in ("活动", "促销", "曝光", "点击", "ROI")):
        return []
    return [(label, key) for _, label, key in located]


def _parse_promotion_effect_metric_table_row(
    line: str,
    *,
    columns: list[tuple[str, str]],
) -> dict[str, Any] | None:
    matches = list(_BUSINESS_VALUE_RE.finditer(line))
    if len(matches) < 2:
        return None
    metric_matches = matches[-len(columns) :] if len(matches) >= len(columns) else matches
    numbers = [_normalize_metric_value(match.group(0)) for match in metric_matches]
    first_metric = metric_matches[0]
    name = line[: first_metric.start()].strip(" ：:-")
    name = re.sub(r"^(?:活动名称|促销名称|促销|活动)[:：]?", "", name).strip(" ：:-")
    if not name or _is_promotion_registered_navigation_line(name):
        return None
    metric_values: dict[str, Any] = {}
    for (_, key), value in zip(columns, numbers, strict=False):
        if value:
            metric_values[key] = value
    if not metric_values:
        return None
    return {
        "name": name[:80],
        "metrics": metric_values,
        "raw_text": line[:800],
    }


def _is_promotion_effect_table_stop_line(line: str) -> bool:
    if line in {"促销中心", "促销效果", "已参加促销", "促销报名", "数据来源"}:
        return True
    if _line_contains_metric_label(line, "参加促销"):
        return True
    if _line_contains_metric_label(line, "含促销间夜"):
        return True
    if line.startswith("建议") or line.startswith("竞争圈平均"):
        return True
    return False


def _extract_labeled_value_from_text(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}\s*[:：]?\s*(?P<value>{_BUSINESS_VALUE_RE.pattern})"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        return _normalize_metric_value(match.group("value"))
    return None


def _dedupe_promotion_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("name") or ""), str(row.get("raw_text") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _extract_promotion_registered_payload(
    lines: list[str],
    *,
    raw_snippets: list[str],
) -> dict[str, Any]:
    promotions = _extract_registered_promotions(lines)
    warnings = _extract_promotion_registered_warnings(lines, promotions=promotions)
    page_prompts = _extract_promotion_page_prompts(lines)
    summary = _extract_promotion_registered_summary(lines, promotions=promotions)
    notes: list[str] = []
    empty_state = _promotion_registered_empty_state(lines)
    if not promotions and not empty_state:
        notes.append("已参加促销页面未能稳定识别单个促销卡片，请复核 raw_snippets。")
    elif promotions:
        missing_dates = sum(
            1
            for promotion in promotions
            if not promotion.get("start_date") and not promotion.get("end_date")
        )
        if missing_dates:
            notes.append(f"{missing_dates} 个已参加促销未识别到明确起止日期。")
    status_hint = None
    if promotions and notes:
        status_hint = "partial"
    elif not promotions and not empty_state:
        status_hint = "needs_review"
    return {
        "structure_ok": bool(promotions or summary or empty_state),
        "status_hint": status_hint,
        "summary": summary,
        "promotions": promotions,
        "warnings": warnings,
        "page_prompts": page_prompts,
        "raw_snippets": _select_promotion_registered_snippets(
            lines,
            raw_snippets=raw_snippets,
            promotions=promotions,
        ),
        "data_quality_notes": notes,
    }


def _extract_promotion_registered_summary(
    lines: list[str],
    *,
    promotions: list[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "visible_promotion_count": len(promotions),
    }
    declared_count = _extract_declared_registered_promotion_count(lines)
    if declared_count is not None:
        summary["declared_promotion_count"] = declared_count
    status_counts: dict[str, int] = {}
    for promotion in promotions:
        status = str(promotion.get("status") or promotion.get("signup_status") or "").strip()
        if not status:
            continue
        status_counts[status] = status_counts.get(status, 0) + 1
    if status_counts:
        summary["status_counts"] = status_counts
    type_counts: dict[str, int] = {}
    for promotion in promotions:
        promotion_type = str(promotion.get("promotion_type") or "").strip()
        if not promotion_type:
            continue
        type_counts[promotion_type] = type_counts.get(promotion_type, 0) + 1
    if type_counts:
        summary["type_counts"] = type_counts
    auto_extend_enabled_count = sum(1 for item in promotions if item.get("auto_extend") is True)
    auto_extend_disabled_count = sum(1 for item in promotions if item.get("auto_extend") is False)
    if auto_extend_enabled_count or auto_extend_disabled_count:
        summary["auto_extend_enabled_count"] = auto_extend_enabled_count
        summary["auto_extend_disabled_count"] = auto_extend_disabled_count
    warning_count = sum(len(item.get("warnings") or []) for item in promotions)
    if warning_count:
        summary["warning_count"] = warning_count
    if _promotion_registered_empty_state(lines):
        summary["empty_state"] = True
    return summary


def _extract_declared_registered_promotion_count(lines: list[str]) -> int | None:
    for line in lines:
        if not any(
            term in line for term in ("已参加促销", "已报名活动", "促销数量", "活动数量", "共")
        ):
            continue
        match = re.search(r"(?P<count>\d+)\s*(?:个|项|条)?", line)
        if match:
            return int(match.group("count"))
    return None


def _extract_registered_promotions(lines: list[str]) -> list[dict[str, Any]]:
    table_promotions = _extract_registered_promotions_from_visible_table(lines)
    if table_promotions:
        return _dedupe_registered_promotions(table_promotions)[:50]

    starts = _registered_promotion_start_indexes(lines)
    promotions: list[dict[str, Any]] = []
    for position, start in enumerate(starts):
        stop = starts[position + 1] if position + 1 < len(starts) else len(lines)
        chunk = _trim_registered_promotion_chunk(lines[start:stop])
        if not chunk:
            continue
        promotion = _parse_registered_promotion_chunk(chunk)
        if promotion is not None:
            promotions.append(promotion)
    if not promotions:
        promotions = _extract_registered_promotions_from_date_rows(lines)
    return _dedupe_registered_promotions(promotions)[:50]


def _extract_registered_promotions_from_visible_table(lines: list[str]) -> list[dict[str, Any]]:
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if all(
                term in line
                for term in (
                    "促销名称",
                    "参与房型",
                    "促销时间",
                    "促销房量",
                    "促销优惠",
                    "订单储备金",
                )
            )
        ),
        None,
    )
    if header_index is None:
        return []
    status_lines = {"已生效", "已暂停", "已退出", "已过期"}
    promotions: list[dict[str, Any]] = []
    cursor = header_index + 1
    while cursor < len(lines):
        if lines[cursor] in {"隐私政策规则中心", "分享反馈"}:
            break
        if lines[cursor] in status_lines or re.fullmatch(r"\d+", lines[cursor]):
            cursor += 1
            continue
        if _is_registered_table_group_line(lines[cursor]):
            group = lines[cursor]
            name_index = _next_non_action_line_index(lines, cursor + 1)
            if name_index is None:
                break
            name = _clean_registered_promotion_name(lines[name_index])
            if not name or _is_promotion_registered_navigation_line(name):
                cursor = name_index + 1
                continue
            chunk_end = _next_registered_table_boundary(lines, name_index + 1)
            promotion = _parse_registered_table_row(lines[name_index:chunk_end], group=group)
            if promotion is not None:
                promotions.append(promotion)
            cursor = chunk_end
            continue
        cursor += 1
    return promotions


def _is_registered_table_group_line(line: str) -> bool:
    return line.endswith("促销组") or line in {"基础促销组", "场景促销组"}


def _next_non_action_line_index(lines: list[str], start: int) -> int | None:
    for index in range(start, min(len(lines), start + 8)):
        line = lines[index]
        if not line or line in {"查看所有", "更多", "修改", "暂停"}:
            continue
        if _is_action_or_navigation_line(line):
            continue
        return index
    return None


def _next_registered_table_boundary(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        line = lines[index]
        if line in {"隐私政策规则中心", "分享反馈"}:
            return index
        if _is_registered_table_group_line(line):
            return index
    return min(len(lines), start + 32)


def _parse_registered_table_row(chunk: list[str], *, group: str) -> dict[str, Any] | None:
    if not chunk:
        return None
    name = _clean_registered_promotion_name(chunk[0])
    if not name:
        return None
    raw_text = "\n".join(chunk)
    date_range = _extract_date_range(raw_text)
    room_types = _extract_registered_table_room_types(chunk)
    promotion_offers = _extract_registered_table_offer(chunk)
    reserve_fund = _extract_registered_table_reserve_fund(chunk)
    room_quota = _extract_registered_table_room_quota(chunk)
    promotion_time = ""
    if date_range:
        promotion_time = f"{date_range[0]}-{date_range[1]}"
    return {
        "name": name,
        "promotion_group": group,
        "promotion_type": _extract_registered_promotion_type([name, *chunk]),
        "status": _extract_registered_promotion_status(chunk),
        "start_date": date_range[0] if date_range else "",
        "end_date": date_range[1] if date_range else "",
        "promotion_time": promotion_time,
        "rules": promotion_offers,
        "room_scope": room_types,
        "room_types": room_types,
        "date_scope": promotion_time,
        "room_quota": room_quota,
        "promotion_offer": "；".join(promotion_offers),
        "order_reserve_fund": reserve_fund,
        "auto_extend": _extract_registered_auto_extend(chunk),
        "signup_status": _extract_registered_signup_status(chunk),
        "metrics": _extract_promotion_metric_values_from_text(raw_text),
        "warnings": _extract_registered_promotion_warnings(chunk),
        "raw_text": raw_text[:1200],
    }


def _extract_registered_table_room_types(chunk: list[str]) -> str:
    for line in chunk[1:8]:
        if line in {"查看所有", "不限"}:
            continue
        if _extract_date_range(line):
            continue
        if any(term in line for term in ("房", "床", "预付", "连住", "间")):
            return line[:240]
    return ""


def _extract_registered_table_offer(chunk: list[str]) -> list[str]:
    date_indexes = [index for index, line in enumerate(chunk) if _extract_date_range(line)]
    start = max(date_indexes) + 1 if date_indexes else 1
    offers: list[str] = []
    for line in chunk[start:]:
        if any(term in line for term in ("立减", "优惠", "折", "满", "减")):
            offers.append(line[:120])
    return _dedupe_keep_order(offers)[:12]


def _extract_registered_table_reserve_fund(chunk: list[str]) -> str:
    for line in chunk:
        if "余额" in line or "储备金" in line:
            return line[:160]
        if line in {"未使用", "已使用"} or line.startswith("使用，"):
            return line[:160]
    return ""


def _extract_registered_table_room_quota(chunk: list[str]) -> str:
    for line in chunk:
        if line == "不限":
            return line
        if re.fullmatch(r"\d+\s*间?", line):
            return line
    return ""


def _registered_promotion_start_indexes(lines: list[str]) -> list[int]:
    starts: list[int] = []
    for index, line in enumerate(lines):
        previous = lines[index - 1] if index > 0 else ""
        next_lines = lines[index + 1 : index + 5]
        if _is_registered_promotion_name_candidate(line, previous=previous, next_lines=next_lines):
            starts.append(index)
    return starts


def _is_registered_promotion_name_candidate(
    line: str,
    *,
    previous: str,
    next_lines: list[str],
) -> bool:
    if not line or len(line) > 90:
        return False
    if _is_promotion_registered_navigation_line(line):
        return False
    if _is_promotion_registered_detail_line(line):
        return False
    if line.startswith(("提示", "提醒", "建议")) or any(
        term in line for term in ("请复核", "需复核", "待处理")
    ):
        return False
    has_name_keyword = any(
        term in line
        for term in (
            "促销",
            "活动",
            "折扣",
            "满减",
            "优惠",
            "连住",
            "早鸟",
            "会员",
            "套餐",
            "立减",
            "特价",
            "闪促",
        )
    )
    context = " ".join(next_lines)
    has_card_context = any(
        term in context
        for term in (
            "活动时间",
            "有效期",
            "适用房型",
            "活动规则",
            "报名状态",
            "已报名",
            "生效中",
            "进行中",
            "待生效",
            "已结束",
        )
    ) or bool(_extract_date_range(" ".join([line, context])))
    if not has_name_keyword and not has_card_context:
        return False
    if previous in {"促销名称", "活动名称"}:
        return True
    return has_name_keyword or has_card_context


def _is_promotion_registered_navigation_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if _is_promotion_status_count_line(line):
        return True
    if any(
        compact.startswith(prefix)
        for prefix in ("已参加促销", "可参加促销", "全部促销", "促销中心", "促销报名")
    ):
        return True
    navigation_terms = {
        "促销中心",
        "已参加促销",
        "可参加促销",
        "全部促销",
        "促销效果",
        "促销报名",
        "搜索",
        "筛选",
        "重置",
        "全部",
        "生效中",
        "进行中",
        "已生效",
        "待生效",
        "未开始",
        "已结束",
        "已过期",
        "已暂停",
        "审核中",
        "报名成功",
        "已报名",
        "查看更多",
        "查看所有",
        "查看详情",
        "编辑",
        "取消报名",
        "去报名",
        "报名",
    }
    return line in navigation_terms or line.startswith("tab=")


def _is_promotion_status_count_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return bool(
        re.fullmatch(
            r"(全部|生效中|进行中|已生效|待生效|未开始|已结束|已过期|已暂停|已退出|审核中|报名成功|已报名)\d+",
            compact,
        )
    )


def _is_promotion_registered_detail_line(line: str) -> bool:
    detail_terms = (
        "活动类型",
        "促销类型",
        "活动状态",
        "状态",
        "报名状态",
        "活动时间",
        "有效期",
        "开始时间",
        "结束时间",
        "适用房型",
        "房型范围",
        "房间范围",
        "日期范围",
        "适用日期",
        "活动规则",
        "规则",
        "自动延期",
        "自动续期",
        "曝光",
        "点击",
        "转化",
        "订单",
        "销售额",
        "ROI",
    )
    compact = re.sub(r"\s+", "", line)
    return any(compact.startswith(term) for term in detail_terms)


def _trim_registered_promotion_chunk(chunk: list[str]) -> list[str]:
    result: list[str] = []
    for line in chunk[:24]:
        if line in {"隐私政策规则中心", "分享反馈"}:
            break
        if line in {"查看详情", "编辑", "取消报名", "去报名", "报名"}:
            continue
        result.append(line)
    return result


def _parse_registered_promotion_chunk(chunk: list[str]) -> dict[str, Any] | None:
    if not chunk:
        return None
    name = _clean_registered_promotion_name(chunk[0])
    if not name:
        return None
    raw_text = "\n".join(chunk)
    date_range = _extract_date_range(raw_text)
    promotion_type = _extract_registered_promotion_type(chunk)
    status = _extract_registered_promotion_status(chunk)
    signup_status = _extract_registered_signup_status(chunk)
    return {
        "name": name,
        "promotion_type": promotion_type,
        "status": status,
        "start_date": date_range[0] if date_range else "",
        "end_date": date_range[1] if date_range else "",
        "rules": _extract_registered_promotion_rules(chunk),
        "room_scope": _extract_labeled_text(
            chunk,
            labels=("适用房型", "房型范围", "房间范围", "适用房间", "售卖房型"),
        ),
        "date_scope": _extract_labeled_text(
            chunk,
            labels=("活动时间", "有效期", "日期范围", "适用日期", "入住日期", "售卖日期"),
        ),
        "auto_extend": _extract_registered_auto_extend(chunk),
        "signup_status": signup_status,
        "metrics": _extract_promotion_metric_values_from_text(raw_text),
        "warnings": _extract_registered_promotion_warnings(chunk),
        "raw_text": raw_text[:1200],
    }


def _clean_registered_promotion_name(line: str) -> str:
    name = re.sub(r"^(?:活动名称|促销名称)[:：]?", "", line).strip(" ：:-")
    return name[:120]


def _extract_date_range(text: str) -> tuple[str, str] | None:
    date_pattern = r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?"
    match = re.search(
        rf"(?P<start>{date_pattern})\s*(?:至|到|~|～|-|—|－)\s*(?P<end>{date_pattern})",
        text,
    )
    if not match:
        return None
    return _normalize_date_text(match.group("start")), _normalize_date_text(match.group("end"))


def _normalize_date_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _extract_registered_promotion_type(chunk: list[str]) -> str:
    labeled = _extract_labeled_text(chunk, labels=("活动类型", "促销类型", "类型"))
    if labeled:
        return labeled
    type_terms = (
        "满减",
        "折扣",
        "连住",
        "早鸟",
        "会员",
        "套餐",
        "立减",
        "返现",
        "优惠券",
        "特价",
        "闪促",
        "新客",
    )
    for line in chunk:
        for term in type_terms:
            if term in line:
                return term
    return ""


def _extract_registered_promotion_status(chunk: list[str]) -> str:
    status_terms = (
        "生效中",
        "进行中",
        "已生效",
        "待生效",
        "未开始",
        "已结束",
        "已过期",
        "已暂停",
        "审核中",
        "报名成功",
        "已报名",
    )
    labeled = _extract_labeled_text(chunk, labels=("活动状态", "状态"))
    if labeled:
        return labeled
    for line in chunk:
        for term in status_terms:
            if term in line:
                return term
    return ""


def _extract_registered_signup_status(chunk: list[str]) -> str:
    labeled = _extract_labeled_text(chunk, labels=("报名状态", "报名结果"))
    if labeled:
        return labeled
    for line in chunk:
        if "已报名" in line or "报名成功" in line:
            return "已报名"
        if "待审核" in line:
            return "待审核"
    return ""


def _extract_labeled_text(chunk: list[str], *, labels: tuple[str, ...]) -> str:
    for index, line in enumerate(chunk):
        compact = re.sub(r"\s+", "", line)
        for label in labels:
            if not compact.startswith(label):
                continue
            suffix = re.sub(r"^\s*" + re.escape(label) + r"\s*[:：]?", "", line).strip()
            if suffix and suffix != label:
                return suffix[:240]
            if index + 1 < len(chunk):
                candidate = chunk[index + 1].strip()
                if candidate and not _is_promotion_registered_detail_line(candidate):
                    return candidate[:240]
    return ""


def _extract_registered_promotion_rules(chunk: list[str]) -> list[str]:
    rules: list[str] = []
    rule_terms = ("活动规则", "规则", "优惠", "折扣", "满", "减", "连住", "提前", "券", "赠")
    for line in chunk:
        if any(term in line for term in rule_terms) and not _is_registered_metric_line(line):
            rules.append(line)
    return _dedupe_keep_order(rules)[:12]


def _is_registered_metric_line(line: str) -> bool:
    return any(term in line for term in ("曝光", "点击", "转化", "订单", "销售额", "ROI"))


def _extract_registered_auto_extend(chunk: list[str]) -> bool | None:
    for line in chunk:
        if not any(term in line for term in ("自动延期", "自动续期", "自动延长", "到期自动")):
            continue
        if any(term in line for term in ("是", "开启", "打开", "自动")) and not any(
            term in line for term in ("否", "关闭", "不")
        ):
            return True
        if any(term in line for term in ("否", "关闭", "不")):
            return False
    return None


def _extract_registered_promotion_warnings(chunk: list[str]) -> list[str]:
    return [
        line
        for line in chunk
        if any(term in line for term in ("异常", "风险", "即将结束", "已暂停", "失败", "待审核"))
        and not _is_promotion_status_count_line(line)
    ][:8]


def _extract_promotion_registered_warnings(
    lines: list[str],
    *,
    promotions: list[dict[str, Any]],
) -> list[str]:
    warnings = [
        line
        for line in lines
        if any(term in line for term in ("异常", "风险", "即将结束", "已暂停", "失败", "待审核"))
        and not _is_promotion_status_count_line(line)
    ]
    for promotion in promotions:
        warnings.extend(str(item) for item in promotion.get("warnings", []) if item)
    return _dedupe_keep_order(warnings)[:20]


def _extract_registered_promotions_from_date_rows(lines: list[str]) -> list[dict[str, Any]]:
    promotions: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        date_range = _extract_date_range(line)
        if date_range is None:
            continue
        name = ""
        for candidate in reversed(lines[max(0, index - 3) : index]):
            if not _is_promotion_registered_navigation_line(
                candidate
            ) and not _is_promotion_registered_detail_line(candidate):
                name = _clean_registered_promotion_name(candidate)
                break
        if not name:
            continue
        chunk = lines[max(0, index - 3) : min(len(lines), index + 6)]
        parsed = _parse_registered_promotion_chunk([name, *chunk])
        if parsed:
            parsed["start_date"], parsed["end_date"] = date_range
            promotions.append(parsed)
    return promotions


def _dedupe_registered_promotions(promotions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for promotion in promotions:
        key = (
            str(promotion.get("name") or ""),
            str(promotion.get("start_date") or ""),
            str(promotion.get("end_date") or ""),
            str(promotion.get("status") or promotion.get("signup_status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(promotion)
    return result


def _promotion_registered_empty_state(lines: list[str]) -> bool:
    return any(
        any(term in line for term in ("暂无", "没有", "未参加", "无已参加"))
        and any(term in line for term in ("促销", "活动"))
        for line in lines
    )


def _select_promotion_registered_snippets(
    lines: list[str],
    *,
    raw_snippets: list[str],
    promotions: list[dict[str, Any]],
) -> list[str]:
    snippets = list(raw_snippets)
    for promotion in promotions[:8]:
        raw_text = promotion.get("raw_text")
        if isinstance(raw_text, str) and raw_text:
            snippets.extend(raw_text.splitlines()[:8])
    if not snippets:
        snippets = [
            line
            for line in lines
            if any(term in line for term in ("促销", "活动", "报名", "生效", "有效期", "房型"))
        ]
    return _dedupe_keep_order(snippets)[:40]


def _extract_promotion_page_prompts(lines: list[str]) -> list[str]:
    prompt_terms = (
        "提示",
        "提醒",
        "建议",
        "机会",
        "异常",
        "风险",
        "待处理",
        "待审核",
        "审核",
        "即将",
        "过期",
        "余额",
        "不足",
        "失败",
        "暂停",
        "下线",
        "到期",
        "需",
    )
    prompts = [
        line
        for line in lines
        if any(term in line for term in prompt_terms)
        and not _is_promotion_registered_navigation_line(line)
        and not _is_action_or_navigation_line(line)
        and not _is_promotion_status_count_line(line)
    ]
    return _dedupe_keep_order(prompts)[:20]
