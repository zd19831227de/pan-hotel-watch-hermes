"""价格输入框候选的共享采集过滤与打分（B4 收紧，三平台复用）。

实测（docs/pan-hotel-watch-three-platform-pricing-write-verification-2026-08-24.md
§四/§五）：飞猪/美团的房型搜索框 nearby_text 含"子价格"等字样会命中价格词，
综合得分反超 nearby_text 为空的真实价格框；复选框/日期框也会进入候选。本模块
统一收紧：

1. 类型过滤：checkbox/radio/search/button/submit/reset/file/hidden 一律排除，
   仅保留可编辑的 text/number/tel/无 type 输入框（采集 JS 与本模块双重兜底）。
2. 语义排除：placeholder/nearby_text 含"搜索/查询/名称/ID/日期/AI"的候选排除。
3. 价格形态加分：value 呈 `\\d{2,5}(\\.\\d{1,2})?` 且 >=30 的候选加分，
   对齐 ctrip reader `_money_like_numbers` 的启发式。
4. 不匹配时保持空结果——上游继续走 not_found -> page_structure_changed 的
   诚实失败，不猜测回退。
"""

from __future__ import annotations

import re
from typing import Any

EXCLUDED_INPUT_TYPES = frozenset(
    {"checkbox", "radio", "search", "button", "submit", "reset", "file", "hidden"}
)
SEARCH_LIKE_EXCLUDE_TERMS = ("搜索", "查询", "名称", "ID", "日期", "AI")
PRICE_INPUT_TERMS = ("价格", "房价", "卖价", "底价", "¥", "￥")
_PRICE_SHAPE_PATTERN = re.compile(r"^\d{2,5}(?:\.\d{1,2})?$")
_PRICE_SHAPE_MIN_VALUE = 30.0
_PRICE_SHAPE_BONUS = 2


def _is_excluded_price_input(candidate: dict[str, Any]) -> bool:
    tag = str(candidate.get("tag") or "").lower()
    input_type = str(candidate.get("type") or "").lower()
    if tag == "input" and input_type in EXCLUDED_INPUT_TYPES:
        return True
    context = " ".join(
        str(candidate.get(key) or "") for key in ("placeholder", "nearby_text")
    )
    return any(term in context for term in SEARCH_LIKE_EXCLUDE_TERMS)


def _price_shape_bonus(value: Any) -> int:
    text = str(value if value is not None else "").strip()
    if not _PRICE_SHAPE_PATTERN.match(text):
        return 0
    try:
        number = float(text)
    except ValueError:
        return 0
    return _PRICE_SHAPE_BONUS if number >= _PRICE_SHAPE_MIN_VALUE else 0


def match_price_input_candidates(
    candidates: list[dict[str, Any]],
    terms: list[str | None],
    *,
    price_terms: tuple[str, ...] = PRICE_INPUT_TERMS,
) -> list[dict[str, Any]]:
    """按房型/日期/当前价词匹配价格输入框候选，返回按得分降序的精简候选。"""
    scored: list[tuple[int, dict[str, Any]]] = []
    normalized_terms = [term for term in terms if term]
    for candidate in candidates:
        if _is_excluded_price_input(candidate):
            continue
        haystack = " ".join(
            str(candidate.get(key) or "")
            for key in ("nearby_text", "placeholder", "aria_label", "value")
        )
        score = sum(1 for term in normalized_terms if term and term in haystack)
        if any(term in haystack for term in price_terms):
            score += 1
        score += _price_shape_bonus(candidate.get("value"))
        if score > 0:
            scored.append((score, candidate))
    return [
        {
            "selector_hint": item.get("selector_hint"),
            "nearby_text": item.get("nearby_text"),
            "placeholder": item.get("placeholder"),
            "value": item.get("value"),
            "match_score": score,
        }
        for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True)
    ]
