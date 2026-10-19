"""Pure parsers for the Ctrip EBK review-analysis page."""

from __future__ import annotations

import re
from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order
from app.domain.browser.ctrip_ebooking_parsers import (
    _COMMENT_COUNT_RE,
    _COMMENT_DATE_RE,
    _COMMENT_DIMENSION_LABELS,
    _COMMENT_RATING_LABEL_RE,
    _COMMENT_SCORE_PATTERNS,
    _COMMENT_SCORE_RE,
    _COMMENT_SNIPPET_TERMS,
)

_COMMENT_ANALYSIS_SECTION_TITLES = (
    "点评总体印象",
    "点评概览",
    "回复概览",
    "点评统计",
    "点评数量统计",
    "点评用户画像",
    "过去半年点评趋势",
    "近6个月点评率趋势",
    "竞争圈对比",
)

_COMMENT_ANALYSIS_NUMERIC_VALUE_RE = re.compile(r"^[0-9][0-9,]*(?:\.\d+)?%?$")

_COMMENT_COMPETITION_ROW_RE = re.compile(
    r"^(?P<hotel>.+?)\s+(?P<overall>[0-5](?:\.\d+)?)\s+"
    r"(?P<hygiene>[0-5](?:\.\d+)?)\s+(?P<service>[0-5](?:\.\d+)?)\s+"
    r"(?P<facilities>[0-5](?:\.\d+)?)\s+(?P<environment>[0-5](?:\.\d+)?)$"
)


def _extract_comment_analysis_snapshot(
    *,
    lines: list[str],
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    sections = _split_comment_analysis_sections(lines)
    review_overview = _extract_comment_review_overview(sections.get("点评概览", []))
    reply_overview = _extract_comment_reply_overview(sections.get("回复概览", []))
    overall_impression = _extract_comment_overall_impression(sections.get("点评总体印象", []))
    keyword_statistics = _extract_comment_keyword_statistics(sections.get("点评统计", []))
    user_profile = _extract_comment_user_profile(sections.get("点评用户画像", []))
    competition_benchmark = _extract_comment_competition_benchmark(
        sections.get("竞争圈对比", [])
    )
    raw_snippets = _select_comment_analysis_snippets(lines)
    data_quality_notes = _comment_analysis_data_quality_notes(
        review_overview=review_overview,
        reply_overview=reply_overview,
    )
    structure_ok = bool(review_overview or reply_overview or overall_impression)
    return {
        "structure_ok": structure_ok,
        "status_hint": "partial" if structure_ok and data_quality_notes else "success",
        "review_overview": review_overview,
        "reply_overview": reply_overview,
        "overall_impression": overall_impression,
        "keyword_statistics": keyword_statistics,
        "user_profile": user_profile,
        "competition_benchmark": competition_benchmark,
        "sections": sections,
        "raw_snippets": raw_snippets,
        "metrics": _comment_analysis_metrics(review_overview, reply_overview),
        "data_quality_notes": data_quality_notes,
        "filters": filters or {},
    }


def _split_comment_analysis_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_title: str | None = None
    for line in lines:
        if line in _COMMENT_ANALYSIS_SECTION_TITLES:
            current_title = line
            sections.setdefault(line, [])
            continue
        if current_title is not None:
            sections[current_title].append(line)
    return sections


def _extract_comment_review_overview(lines: list[str]) -> dict[str, dict[str, Any]]:
    rows = _normalize_comment_analysis_lines(lines)
    if not rows:
        return {}
    result: dict[str, dict[str, Any]] = {}
    rating = _next_numeric_value(rows, "点评分")
    if rating:
        result["overall_score"] = _metric("点评分", rating)
    rank_text = _first_matching_line(rows, "同城同星酒店")
    if rank_text and "overall_score" in result:
        result["overall_score"]["competitive_rank"] = rank_text
    total_comments = _next_numeric_value(rows, "点评数")
    if total_comments:
        result["total_comment_count"] = _metric("点评数", total_comments)
    positive_rate = _metric_from_inline_text(rows, "好评率")
    if positive_rate:
        result["positive_rate"] = positive_rate
    image_review_rate = _metric_from_inline_text(rows, "带图点评率")
    if image_review_rate:
        result["image_review_rate"] = image_review_rate
    result.update(_extract_comment_sentiment_counts(rows))
    return result


def _extract_comment_reply_overview(lines: list[str]) -> dict[str, dict[str, Any]]:
    rows = _normalize_comment_analysis_lines(lines)
    if not rows:
        return {}
    result: dict[str, dict[str, Any]] = {}
    reply_rate = _next_numeric_value(rows, "回复率")
    if reply_rate:
        result["reply_rate"] = _metric("回复率", reply_rate)
    negative_reply_rate = _metric_from_inline_text(rows, "差评回复率")
    if negative_reply_rate:
        result["negative_reply_rate"] = negative_reply_rate
    pending_negative_reviews = _next_numeric_value(rows, "待回复差评数")
    if pending_negative_reviews:
        result["pending_negative_reviews"] = _metric("待回复差评数", pending_negative_reviews)
    last_reply = _inline_text_value(rows, "上次回复")
    if last_reply:
        result["last_reply_age"] = _metric("上次回复", last_reply)
    reply_specificity = _next_numeric_value(rows, "差评回复针对性")
    if reply_specificity:
        result["negative_reply_specificity"] = _metric("差评回复针对性", reply_specificity)
    content_negative_reviews = _inline_text_value(rows, "有内容的差评")
    if content_negative_reviews:
        result["negative_reviews_with_content"] = _metric("有内容的差评", content_negative_reviews)
    return result


def _extract_comment_overall_impression(lines: list[str]) -> dict[str, Any]:
    rows = _normalize_comment_analysis_lines(lines)
    if not rows:
        return {}
    result: dict[str, Any] = {}
    positive_index = _find_line_index(rows, "认可度最高")
    if positive_index >= 0:
        result["top_positive"] = {
            "title": "认可度最高",
            "topic": _line_at(rows, positive_index + 1),
            "summary": _line_at(rows, positive_index + 2),
        }
    negatives: list[dict[str, Any]] = []
    for index, line in enumerate(rows):
        if not line.startswith("口碑最差Top"):
            continue
        suggestion = _line_at(rows, index + 2)
        if suggestion.startswith("建议"):
            suggestion = suggestion.removeprefix("建议").strip()
        negatives.append(
            {
                "title": line,
                "topic": _line_at(rows, index + 1),
                "suggestion": suggestion,
            }
        )
    if negatives:
        result["top_negative"] = negatives
    return result


def _extract_comment_keyword_statistics(lines: list[str]) -> list[dict[str, Any]]:
    rows = _normalize_comment_analysis_lines(lines)
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        if not re.fullmatch(r"\d+", rows[index]):
            index += 1
            continue
        if index + 2 >= len(rows):
            break
        rank = int(rows[index])
        category = rows[index + 1]
        count_text = rows[index + 2]
        if not re.fullmatch(r"\d+", count_text):
            index += 1
            continue
        index += 3
        keywords: list[str] = []
        while index < len(rows) and not re.fullmatch(r"\d+", rows[index]):
            if rows[index] not in {"优", "中", "差"}:
                keywords.append(rows[index])
            index += 1
        result.append(
            {
                "rank": rank,
                "category": category,
                "count": int(count_text),
                "keywords": keywords,
            }
        )
    return result


def _extract_comment_user_profile(lines: list[str]) -> dict[str, str]:
    rows = _normalize_comment_analysis_lines(lines)
    if not rows:
        return {}
    result = {"profile": rows[0]}
    if len(rows) > 1:
        result["recommendation"] = rows[1]
    return result


def _extract_comment_competition_benchmark(lines: list[str]) -> dict[str, Any]:
    rows = _normalize_comment_analysis_lines(lines)
    parsed_rows: list[dict[str, Any]] = []
    for line in rows:
        match = _COMMENT_COMPETITION_ROW_RE.match(line)
        if not match:
            continue
        parsed_rows.append(
            {
                "hotel_name": match.group("hotel"),
                "overall_score": match.group("overall"),
                "hygiene": match.group("hygiene"),
                "service": match.group("service"),
                "facilities": match.group("facilities"),
                "environment": match.group("environment"),
            }
        )
    result: dict[str, Any] = {"rows": parsed_rows[:20]}
    for row in parsed_rows:
        hotel_name = str(row.get("hotel_name") or "")
        if hotel_name == "竞争圈平均":
            result["competition_average"] = row
        elif hotel_name == "我的酒店":
            result["my_hotel"] = row
    return result


def _comment_analysis_metrics(
    review_overview: dict[str, dict[str, Any]],
    reply_overview: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for key, value in {**review_overview, **reply_overview}.items():
        if isinstance(value, dict) and value.get("value") is not None:
            metrics[key] = value
    return metrics


def _comment_analysis_data_quality_notes(
    *,
    review_overview: dict[str, dict[str, Any]],
    reply_overview: dict[str, dict[str, Any]],
) -> list[str]:
    notes: list[str] = []
    for key, label in {
        "overall_score": "点评分",
        "total_comment_count": "点评数",
        "positive_rate": "好评率",
        "image_review_rate": "带图点评率",
        "positive_reviews_30d": "30天内好评",
        "positive_reviews_yesterday": "昨日好评",
        "negative_reviews_30d": "30天内差评",
        "negative_reviews_yesterday": "昨日差评",
    }.items():
        if key not in review_overview:
            notes.append(f"点评分析缺少 {label}")
    for key, label in {
        "reply_rate": "回复率",
        "negative_reply_rate": "差评回复率",
        "pending_negative_reviews": "待回复差评数",
    }.items():
        if key not in reply_overview:
            notes.append(f"点评分析缺少 {label}")
    return notes


def _extract_comment_sentiment_counts(lines: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label, prefix in (("好评", "positive_reviews"), ("差评", "negative_reviews")):
        try:
            start_index = lines.index(label)
        except ValueError:
            continue
        values: list[str] = []
        for line in lines[start_index + 1 :]:
            normalized = line.replace(",", "").strip()
            if _COMMENT_ANALYSIS_NUMERIC_VALUE_RE.fullmatch(normalized):
                values.append(line.strip())
                if len(values) == 2:
                    break
            elif values:
                break
        if values:
            result[f"{prefix}_30d"] = _metric(f"30天内{label}", values[0])
        if len(values) >= 2:
            result[f"{prefix}_yesterday"] = _metric(f"昨日{label}", values[1])
    return result


def _metric_from_inline_text(lines: list[str], label: str) -> dict[str, Any] | None:
    value = _inline_text_value(lines, label)
    if not value:
        return None
    return _metric(label, value)


def _inline_text_value(lines: list[str], label: str) -> str | None:
    for line in lines:
        if label not in line:
            continue
        text = line.replace("：", ":")
        if ":" in text:
            _, raw_value = text.split(":", 1)
            cleaned = raw_value.strip()
            if cleaned:
                return cleaned
        match = re.search(rf"{re.escape(label)}\s*(?P<value>[^\s].+)$", line)
        if match:
            cleaned = match.group("value").strip()
            if cleaned:
                return cleaned
    return None


def _next_numeric_value(lines: list[str], label: str) -> str | None:
    index = _find_line_index(lines, label)
    if index < 0:
        return None
    for line in lines[index + 1 :]:
        normalized = line.replace(",", "").strip()
        if _COMMENT_ANALYSIS_NUMERIC_VALUE_RE.fullmatch(normalized):
            return line.strip()
        if line in _COMMENT_ANALYSIS_SECTION_TITLES or line in {"好评", "差评"}:
            break
        if normalized and not any(char.isdigit() for char in normalized):
            break
    return None


def _first_matching_line(lines: list[str], needle: str) -> str | None:
    for line in lines:
        if needle in line:
            return line
    return None


def _find_line_index(lines: list[str], target: str) -> int:
    for index, line in enumerate(lines):
        if line == target:
            return index
    return -1


def _line_at(lines: list[str], index: int) -> str:
    if 0 <= index < len(lines):
        return lines[index]
    return ""


def _normalize_comment_analysis_lines(lines: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", str(line or "")).strip() for line in lines if str(line or "").strip()]


def _metric(label: str, value: Any, **extra: Any) -> dict[str, Any]:
    payload = {"label": label, "value": value}
    payload.update(extra)
    return payload


def _select_comment_analysis_snippets(lines: list[str]) -> list[str]:
    section_titles = set(_COMMENT_ANALYSIS_SECTION_TITLES)
    snippets = [
        line
        for line in lines
        if line in section_titles
        or any(term in line for term in ("好评率", "带图点评率", "差评回复率", "待回复差评数"))
        or "同城同星酒店" in line
        or line.startswith("口碑最差Top")
        or line == "认可度最高"
    ]
    if snippets:
        return _dedupe_keep_order(snippets)[:40]
    return lines[:32]


def _extract_comment_total_count(lines: list[str]) -> int | None:
    for line in lines:
        match = _COMMENT_COUNT_RE.search(line)
        if match:
            return int(match.group("count").replace(",", ""))
    for line in lines:
        if "全部点评" not in line and "点评" not in line:
            continue
        match = re.search(r"(?P<count>\d[\d,]*)\s*条", line)
        if match:
            return int(match.group("count").replace(",", ""))
    return None


def _extract_comment_overall_rating(lines: list[str]) -> dict[str, Any]:
    for line in lines:
        if not _looks_like_comment_overall_rating_line(line):
            continue
        scores = _comment_scores_from_text(line)
        if not scores:
            continue
        return {
            "score": scores[0],
            "label": _extract_comment_rating_label(line),
            "raw_text": line,
        }
    return {"score": None, "label": None, "raw_text": None}


def _looks_like_comment_overall_rating_line(line: str) -> bool:
    if any(term in line for term in ("全部点评", "开始日期", "结束日期")):
        return False
    rating_terms = ("总评分", "点评分", "评分", "很好", "优秀", "好")
    has_score_context = any(term in line for term in rating_terms)
    has_dimensions = sum(1 for label in _COMMENT_DIMENSION_LABELS if label in line) >= 2
    return has_score_context and (has_dimensions or "总评分" in line or "点评分" in line)


def _extract_comment_rating_label(line: str) -> str | None:
    match = _COMMENT_RATING_LABEL_RE.search(line)
    return match.group(0) if match else None


def _extract_comment_dimension_scores(lines: list[str]) -> dict[str, dict[str, Any]]:
    dimensions: dict[str, dict[str, Any]] = {}
    for line in lines:
        for label, key in _COMMENT_DIMENSION_LABELS.items():
            if key in dimensions:
                continue
            match = re.search(rf"{label}\s*(?P<score>[0-5](?:\.\d+)?)", line)
            if match:
                dimensions[key] = {
                    "label": label,
                    "score": float(match.group("score")),
                    "raw_text": line,
                }
    return dimensions


def _extract_comment_score_rows(lines: list[str]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        comment_date = _extract_comment_date(line)
        if comment_date is None:
            continue
        window = lines[index : index + 8]
        score = _extract_comment_score_from_window(window)
        if score is None:
            continue
        comments.append(
            {
                "comment_date": comment_date,
                "overall_score": score,
                "raw_text": " ".join(window[:4]),
            }
        )
    return _dedupe_comment_scores(comments)


def _extract_comment_date(line: str) -> str | None:
    if any(term in line for term in ("开始日期", "结束日期", "筛选", "入住日期")):
        return None
    if _looks_like_comment_overall_rating_line(line):
        return None
    match = _COMMENT_DATE_RE.search(line)
    if not match:
        return None
    return _normalize_comment_date(match.group(0))


def _normalize_comment_date(value: str) -> str:
    normalized = value.strip()
    normalized = normalized.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    parts = normalized.split("-")
    if len(parts) == 3:
        year, month, day = parts
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if len(parts) == 2:
        month, day = parts
        return f"{int(month):02d}-{int(day):02d}"
    return value.strip()


def _extract_comment_score_from_window(lines: list[str]) -> float | None:
    for line in lines:
        if _is_comment_dimension_only_line(line):
            continue
        for pattern in _COMMENT_SCORE_PATTERNS:
            match = pattern.search(line)
            if match:
                return float(match.group("score"))
        if _COMMENT_RATING_LABEL_RE.search(line):
            scores = _comment_scores_from_text(line)
            if scores:
                return scores[0]
    return None


def _is_comment_dimension_only_line(line: str) -> bool:
    return (
        "总评分" not in line
        and "点评分" not in line
        and sum(1 for label in _COMMENT_DIMENSION_LABELS if label in line) >= 2
    )


def _comment_scores_from_text(line: str) -> list[float]:
    scores: list[float] = []
    for match in _COMMENT_SCORE_RE.finditer(line):
        score = float(match.group("score"))
        if 0 <= score <= 5:
            scores.append(score)
    return scores


def _dedupe_comment_scores(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, float, str]] = set()
    deduped: list[dict[str, Any]] = []
    for comment in comments:
        key = (
            str(comment.get("comment_date") or ""),
            float(comment.get("overall_score") or 0),
            str(comment.get("raw_text") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(comment)
    return deduped


def _build_comment_daily_scores(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for comment in comments:
        comment_date = str(comment.get("comment_date") or "")
        score = comment.get("overall_score")
        if not comment_date or not isinstance(score, (int, float)):
            continue
        grouped.setdefault(comment_date, []).append(float(score))
    rows: list[dict[str, Any]] = []
    for comment_date in sorted(grouped):
        scores = grouped[comment_date]
        rows.append(
            {
                "comment_date": comment_date,
                "comment_count": len(scores),
                "score_values": scores,
                "average_score": round(sum(scores) / len(scores), 2),
            }
        )
    return rows


def _comment_daily_scores_complete(
    *,
    total_comment_count: int | None,
    comment_scores: list[dict[str, Any]],
) -> bool:
    if total_comment_count is None:
        return True
    if total_comment_count <= 1:
        return True
    return len(comment_scores) >= total_comment_count


def _select_comment_snippets(lines: list[str]) -> list[str]:
    snippets = [
        line
        for line in lines
        if any(term in line for term in _COMMENT_SNIPPET_TERMS)
        or _COMMENT_COUNT_RE.search(line)
        or _COMMENT_RATING_LABEL_RE.search(line)
    ]
    if snippets:
        return _dedupe_keep_order(snippets)[:30]
    return lines[:24]
