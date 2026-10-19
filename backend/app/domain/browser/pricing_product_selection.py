"""房型 → 平台产品映射与页面产品树匹配（B5.2，纯函数）。

匹配来源是审批 action 的 ``room_type``（运营在飞书输入或参考卡带入）。
归一化后按 精确 → 前缀 → 包含 三级匹配平台产品行；多匹配或零匹配返回
``needs_review`` / ``not_found`` 并回显候选列表，**绝不猜测**。

飞猪（B5.1 实测）：产品树 = 母房型行（文本以"含N个"结尾）+ 子价格产品行
（"双早-限时退""无早-任意退【F2面纱专享】 主价格"等）。母房型勾选会联动
全部子产品，因此映射粒度是**子产品行**；breakfast_kind 关键词（双早/单早/
无早）用于在同母房型下筛选。美团：房型行（.room-goods-item-room 文本=
房型名）+ 子产品行（"房型-不含早"等）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_BREAKFAST_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("双早", "双早"),
    ("含双早", "双早"),
    ("单早", "单早"),
    ("含单早", "单早"),
    ("无早", "无早"),
    ("不含早", "无早"),
)

# 2026-09-14 业务核对（三平台统一口径）：调价只调无早价格，单早/双早随无早
# 自动联动。平台的无早基准产品有显式标签：美团「母产品」、飞猪「主价格」；
# 携程是产品名里的 <无早> 变体。预售/钟点类无早是独立售卖形态，作为次选。
CANONICAL_PRODUCT_TAGS: tuple[str, ...] = ("母产品", "主价格")
_CANONICAL_PENALTY_MARKERS: tuple[str, ...] = ("预售", "钟点")
# 不可用状态（业务口径）：携程/美团「已关房」、飞猪「满房」。
UNAVAILABLE_STATUS_TERMS: tuple[str, ...] = ("已关房", "关房", "满房")


def canonical_product_tier(row_text: Any) -> int | None:
    """无早口径分级：0=标签（母产品/主价格）或干净无早；1=预售/钟点类无早；
    None=非无早口径（单早/双早/无早餐标识）。

    母房型行（房型名本身）没有早餐标识，返回 None，由调用方按父行处理。
    """

    text = str(row_text or "")
    if any(tag in text for tag in CANONICAL_PRODUCT_TAGS):
        return 0
    if "单早" in text or "双早" in text:
        return None
    if "无早" not in text and "不含早" not in text:
        return None
    if any(marker in text for marker in _CANONICAL_PENALTY_MARKERS):
        return 1
    return 0


def is_unavailable_status_text(*parts: Any) -> bool:
    """按状态词判断产品当日不可用（已关房/关房/满房）。"""

    text = " ".join(str(part or "") for part in parts)
    return any(term in text for term in UNAVAILABLE_STATUS_TERMS)


@dataclass(frozen=True)
class ProductRow:
    """页面产品树中的一行（由 B5.3 的页面采集器从 DOM 构造）。"""

    row_text: str
    is_parent: bool
    selector_hint: str | None = None


@dataclass(frozen=True)
class ProductMatchResult:
    status: str  # matched | needs_review | not_found
    matched_rows: list[ProductRow] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    reason: str | None = None


def normalize_room_type(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _breakfast_keyword(breakfast_kind: Any) -> str | None:
    text = str(breakfast_kind or "").strip()
    if not text:
        return None
    for needle, keyword in _BREAKFAST_KEYWORDS:
        if needle in text:
            return keyword
    return None


def _parent_core_name(row_text: str) -> str:
    """母房型行文本去掉括号说明与"含N个"尾标后的核心名。"""
    text = re.sub(r"含\d+个$", "", str(row_text or "").strip())
    text = re.sub(r"[（(][^（）()]*[)）]", "", text)
    return normalize_room_type(text)


def match_platform_products(
    room_type: Any,
    rows: list[ProductRow],
    *,
    breakfast_kind: Any = None,
) -> ProductMatchResult:
    """三级匹配（精确 → 前缀 → 包含）母房型，再按统一无早口径锁定子产品。

    2026-09-14 业务核对：三平台统一只调无早（美团「母产品」/飞猪「主价格」/
    携程 <无早>），单早/双早随无早自动联动；``breakfast_kind`` 保留兼容但
    不再驱动筛选。多个同级无早产品或没有无早产品时 needs_review，绝不猜测。
    """
    normalized = normalize_room_type(room_type)
    if not normalized:
        return ProductMatchResult(
            status="not_found",
            reason="审批动作缺少房型（room_type），无法映射平台产品。",
        )
    if not rows:
        return ProductMatchResult(
            status="not_found",
            reason="页面产品树为空，未读取到任何房型产品行。",
        )

    parents = [row for row in rows if row.is_parent]
    parent_core_names = [
        (row, _parent_core_name(row.row_text)) for row in parents
    ]

    matched_parents = [row for row, core in parent_core_names if core == normalized]
    match_level = "精确"
    if not matched_parents:
        matched_parents = [
            row for row, core in parent_core_names if core.startswith(normalized)
        ]
        match_level = "前缀"
    if not matched_parents:
        matched_parents = [
            row for row, core in parent_core_names if normalized in core
        ]
        match_level = "包含"
    if not matched_parents:
        return ProductMatchResult(
            status="not_found",
            candidates=[row.row_text for row in parents][:10],
            reason=f"页面产品树中没有找到与房型「{room_type}」匹配的房型行（精确/前缀/包含均未命中）。",
        )
    if len(matched_parents) > 1:
        return ProductMatchResult(
            status="needs_review",
            candidates=[row.row_text for row in matched_parents][:10],
            reason=(
                f"房型「{room_type}」{match_level}匹配到 {len(matched_parents)} 个房型行，"
                "无法确定唯一目标，请人工确认房型名称。"
            ),
        )

    parent = matched_parents[0]
    parent_index = rows.index(parent)
    children = _child_rows_under_parent(rows, parent_index)
    keyword = _breakfast_keyword(breakfast_kind)
    if keyword is not None and keyword != "无早":
        # 审批粒度与统一无早口径不一致：不得静默改选无早执行（执行必须
        # 与审批一致），拦下请运营按无早口径重新发起。
        return ProductMatchResult(
            status="needs_review",
            candidates=[parent.row_text],
            reason=(
                f"审批粒度为「{keyword}」，与统一无早调价口径不符"
                "（单早/双早随无早自动联动），请按无早重新发起调价。"
            ),
        )
    if not children:
        # 美团等平台房型行本身就是可勾选目标（未展开子产品）。
        return ProductMatchResult(
            status="matched",
            matched_rows=[parent],
            reason=f"房型「{room_type}」{match_level}匹配房型行，直接按房型行勾选。",
        )
    tiers = [
        (canonical_product_tier(row.row_text), row) for row in children
    ]
    canonical = [(tier, row) for tier, row in tiers if tier is not None]
    if not canonical:
        return ProductMatchResult(
            status="needs_review",
            candidates=[row.row_text for row in children][:10],
            reason=(
                f"房型「{room_type}」下没有无早口径（无早/母产品/主价格）的价格产品，"
                "请人工确认该房型是否支持无早调价。"
            ),
        )
    best_tier = min(tier for tier, _row in canonical)
    narrowed = [row for tier, row in canonical if tier == best_tier]
    if len(narrowed) == 1:
        return ProductMatchResult(
            status="matched",
            matched_rows=narrowed,
            reason=(
                f"房型「{room_type}」{match_level}匹配，无早口径（母产品/主价格/无早）"
                "唯一命中价格产品。"
            ),
        )
    return ProductMatchResult(
        status="needs_review",
        candidates=[row.row_text for row in narrowed][:10],
        reason=(
            f"房型「{room_type}」无早口径命中 {len(narrowed)} 个价格产品"
            "（如标准/预售变体），请人工确认目标产品。"
        ),
    )


def _child_rows_under_parent(
    rows: list[ProductRow], parent_index: int
) -> list[ProductRow]:
    """取母房型行之后、下一个母房型行之前的子产品行。"""
    children: list[ProductRow] = []
    for row in rows[parent_index + 1 :]:
        if row.is_parent:
            break
        children.append(row)
    return children
