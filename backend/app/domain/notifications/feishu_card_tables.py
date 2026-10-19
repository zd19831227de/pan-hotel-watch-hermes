"""飞书互动卡片「指标表格」共享渲染（携程盯盘卡 / 美团·飞猪平台卡共用）。

2026-08-26 卡片样式修正的三个目标（对齐飞书官方表格组件约束）：

1. **不翻页**：表格组件 ``page_size`` 官方上限为 10，超出即自动分页；因此超过
   10 行的指标块拆成**多个 ≤10 行的表格纵向堆叠**（每个 page_size == 行数，
   均不触发分页）；仅当卡片表格配额（每卡 5 个）耗尽才回退一列文本。
2. **不截断**：单元格空间不足时飞书会在末尾省略、需点开查看；因此长字段按
   显示宽度切块铺到「说明 / 说明2 / …」多个列，每格内容都在一行的可视宽度内。
3. **整表横向滑动**：不冻结首列，固定列宽总和超过卡片画布宽度时**整个表格**
   （含指标列）一起横向滑动，底部滚动条，内容列宽度可放宽。
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

# 飞书表格组件硬约束：page_size ∈ [1,10]，每卡最多 5 个表格。
TABLE_PAGE_SIZE_LIMIT = 10

_FIELD_MIN_WIDTH = 150
_FIELD_MAX_WIDTH = 320
# 分组表「板块」维度列：窄列即可，只放短标签（在店/预定/流量、实时/按日…）。
_DIMENSION_MIN_WIDTH = 88
_DIMENSION_MAX_WIDTH = 180
# 整表横向滑动（不冻结首列）后列宽可以放宽：每格容纳更多内容，切块更少。
_SINGLE_VALUE_WIDTH = 420
_PART_COLUMN_WIDTH = 260
_PX_PER_UNIT = 7  # 1 显示单位（半角字符）≈ 7px；全角≈14px
_CELL_PADDING_PX = 24

_ROW_KEY_BY_INDEX = ("value", "detail", "detail_2", "detail_3", "detail_4")
_STRONG_BREAKS = " \u3000，、；：）】」』"
_WEAK_BREAKS = "|/-"


def display_width(text: str) -> int:
    """半角=1、全角=2 的显示宽度估算。"""

    width = 0
    for char in str(text or ""):
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def chunk_by_units(text: str, max_units: int) -> list[str]:
    """把长文本按显示宽度切块；每块都能在对应列宽的一行内完整展示。

    断点优先落在空格 / 标点 / 连接符上（且不早于预算的 40%），
    避免把日期「2026-12-31」这类连续片段切碎。
    """

    normalized = str(text or "").strip()
    if max_units <= 0 or display_width(normalized) <= max_units:
        return [normalized] if normalized else []

    def _char_units(char: str) -> int:
        return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1

    chunks: list[str] = []
    remaining = normalized
    while display_width(remaining) > max_units:
        prefix_units = 0
        cut = len(remaining)
        for index, char in enumerate(remaining):
            if prefix_units + _char_units(char) > max_units:
                cut = index
                break
            prefix_units += _char_units(char)
        head = remaining[:cut]
        best_strong = -1
        best_weak = -1
        accumulated = 0
        for index, char in enumerate(head):
            accumulated += _char_units(char)
            if accumulated < max_units * 0.4:
                continue
            if char in _STRONG_BREAKS:
                best_strong = index
            elif char in _WEAK_BREAKS:
                best_weak = index
        best = best_strong if best_strong >= 0 else best_weak
        if best >= 0 and best < len(head) - 1:
            head = remaining[: best + 1]
        head_clean = head.rstrip(" \u3000")
        if not head_clean:
            head_clean = remaining[:cut]
        chunks.append(head_clean)
        remaining = remaining[len(head) :].lstrip(" \u3000")
    tail = remaining.strip()
    if tail:
        chunks.append(tail)
    return [chunk for chunk in chunks if chunk]


def units_for_width(width_px: int) -> int:
    return max(4, (width_px - _CELL_PADDING_PX) // _PX_PER_UNIT)


def split_value_parts(value: Any) -> list[str]:
    """按换行 / 竖线拆出并列内容（每段将占一列）。"""

    text = str(value if value is not None else "").strip()
    if not text or text == "-":
        return []
    return [part.strip() for part in re.split(r"\r?\n|[|｜]", text) if part.strip()]


def parse_metric_line(line: Any) -> tuple[str, list[str]] | None:
    """解析 ``field：value`` 或 ``field｜seg1｜seg2`` 形态的指标行。

    返回 ``(field, parts)``；纯文本行（无冒号且无竖线，如 ``【实时】``、活动名）
    返回 ``None``，由调用方渲染为表格上方的文本行。
    """

    text = str(line or "").strip()
    if text.startswith("- "):
        text = text[2:].strip()
    if not text:
        return None
    segments = [seg.strip() for seg in re.split(r"\r?\n|[|｜]", text) if seg.strip()]
    if not segments:
        return None
    first = segments[0]
    colon_index = first.find("：")
    if colon_index < 0:
        colon_index = first.find(":")
    if colon_index > 0:
        field = first[:colon_index].strip()
        first_value = first[colon_index + 1 :].strip()
        parts = ([first_value] if first_value else []) + segments[1:]
        return (field or "指标", parts)
    if len(segments) > 1:
        return (first, segments[1:])
    return None


def rows_from_lines(lines: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """把指标行列表拆成 (结构化行, 纯文本行)。"""

    rows: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for line in lines or []:
        text = str(line or "").strip()
        if not text:
            continue
        parsed = parse_metric_line(text)
        if parsed is None:
            text_lines.append(text)
            continue
        field, parts = parsed
        if not parts:
            text_lines.append(text)
            continue
        rows.append({"field": field, "parts": parts})
    return rows, text_lines


def escape_lark_md(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([*_`~\[\]])", r"\\\1", text)


def metric_rows_text(rows: list[dict[str, Any]]) -> str:
    """一列 markdown 文本（每行一条指标），用于超 10 行或表格配额满时的兜底。"""

    lines: list[str] = []
    for row in rows or []:
        field = str(row.get("field") or "指标").strip() or "指标"
        parts = _row_parts(row)
        if not parts:
            continue
        lines.append(f"{escape_lark_md(field)}：{escape_lark_md(' / '.join(parts))}")
    return "\n".join(lines)


def render_metric_group_pages(
    groups: list[dict[str, Any]],
    *,
    max_tables: int = 5,
    dimension_header: str = "板块",
    field_header: str = "指标",
    value_header: str = "内容",
) -> list[list[dict[str, Any]]]:
    """分页分组渲染：每组 = ``hr`` + 加粗小节标题（可带副标题）+ 表格。

    2026-08-26 CardUX5 样式约定（用户四点反馈的硬要求）：

    - **每个板块（数据集）独立成组**，绝不跨板块合并；
      行字段只保留具体条目名，数据集内部子结构（今日/昨日、实时/按日、
      在店/预定/流量…）用首列「板块」维度列区分，不拼进字段。
    - **永不截断**：不做「另有 N 项未展示」，不退化纯文本。组按
      10 行/表切块（每表 ``page_size == 行数`` 不翻页），每页最多
      ``max_tables`` 张表（飞书单卡 5 表硬限制）；一张卡放不下时由
      调用方把多页拆成多张卡片连续推送，组跨页时标题加「（续）」。
    """

    cleaned_groups: list[dict[str, Any]] = []
    for group in groups or []:
        rows = _clean_group_rows(group.get("rows"))
        if not rows:
            continue
        cleaned_groups.append(
            {
                "title": str(group.get("title") or "").strip(),
                "subtitle": str(group.get("subtitle") or "").strip(),
                "rows": rows,
                "part_headers": list(group.get("part_headers") or []),
                # 组级首列表头覆盖（如金字塔出价表首列「推广计划」）；
                # 缺省回落调用方传入的通用表头，既有调用行为不变。
                "field_header": str(group.get("field_header") or "").strip(),
            }
        )
    if not cleaned_groups:
        return []

    pages: list[list[dict[str, Any]]] = []
    page: list[dict[str, Any]] = []
    tables_in_page = 0

    def _start_new_page() -> None:
        nonlocal page, tables_in_page
        pages.append(page)
        page = []
        tables_in_page = 0

    for group in cleaned_groups:
        blocks = [
            group["rows"][start : start + TABLE_PAGE_SIZE_LIMIT]
            for start in range(0, len(group["rows"]), TABLE_PAGE_SIZE_LIMIT)
        ]
        for block_index, block in enumerate(blocks):
            if page and tables_in_page >= max_tables:
                _start_new_page()
            is_first_block = block_index == 0
            title = group["title"] if is_first_block else f"{group['title']}（续）"
            header = f"**{escape_lark_md(title)}**"
            if is_first_block and group["subtitle"]:
                header += f"\n{escape_lark_md(group['subtitle'])}"
            page.append({"tag": "hr"})
            page.append(_card_text(header))
            page.append(
                _build_table(
                    block,
                    field_header=group.get("field_header") or field_header,
                    value_header=value_header,
                    dimension_header=dimension_header,
                    part_headers=group.get("part_headers") or None,
                )
            )
            tables_in_page += 1
    if page:
        pages.append(page)
    return pages


def _clean_group_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        field = str(row.get("field") or "").strip()
        parts = _row_parts(row)
        if not field or not parts:
            continue
        dimension = str(row.get("dimension") or "").strip()
        cleaned.append(
            {"field": field, "parts": parts, "dimension": dimension} if dimension
            else {"field": field, "parts": parts}
        )
    return cleaned


def metric_block_elements(
    rows: list[dict[str, Any]],
    *,
    field_header: str = "字段",
    value_header: str = "内容",
    remaining_tables: int | None = None,
    empty_text: str = "暂无可展示指标",
) -> list[dict[str, Any]]:
    """指标块统一渲染（表格优先）：

    - 超过 10 行时**拆成多个 ≤10 行的表格纵向堆叠**（每个 page_size == 行数，
      都不触发飞书分页），不再退化为长文本。
    - ``remaining_tables`` 为本卡剩余表格配额（飞书每卡上限 5）；配额耗尽后
      剩余行才回退为一列 markdown 文本。
    - 表格不冻结首列：固定列宽总和超过卡片画布 → **整表**横向滑动。
    """

    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        field = str(row.get("field") or "").strip()
        parts = _row_parts(row)
        if not parts:
            continue
        cleaned.append({"field": field or "指标", "parts": parts})
    if not cleaned:
        return [_card_text(empty_text)]

    blocks = [
        cleaned[start : start + TABLE_PAGE_SIZE_LIMIT]
        for start in range(0, len(cleaned), TABLE_PAGE_SIZE_LIMIT)
    ]
    elements: list[dict[str, Any]] = []
    leftover: list[dict[str, Any]] = []
    for block in blocks:
        if remaining_tables is None or len(elements) < remaining_tables:
            elements.append(_build_table(block, field_header=field_header, value_header=value_header))
        else:
            leftover.extend(block)
    if leftover:
        # 配额耗尽：剩余行合并为一个文本块兜底。
        elements.append(_card_text(metric_rows_text(leftover)))
    return elements


def _build_table(
    block: list[dict[str, Any]],
    *,
    field_header: str,
    value_header: str,
    dimension_header: str | None = None,
    part_headers: list[str] | None = None,
) -> dict[str, Any]:
    has_dimension = bool(dimension_header) and any(
        row.get("dimension") for row in block
    )
    single_part = all(len(row["parts"]) == 1 for row in block)
    value_width = _SINGLE_VALUE_WIDTH if single_part else _PART_COLUMN_WIDTH
    budget = units_for_width(value_width)

    chunked_rows: list[dict[str, Any]] = []
    max_cells = 1
    for row in block:
        cells: list[str] = []
        for part in row["parts"]:
            if display_width(part) > budget:
                cells.extend(chunk_by_units(part, budget))
            else:
                cells.append(part)
        max_cells = max(max_cells, len(cells))
        chunked = {"field": row["field"], "cells": cells}
        if has_dimension:
            chunked["dimension"] = row.get("dimension") or ""
        chunked_rows.append(chunked)

    field_width = _FIELD_MIN_WIDTH
    for row in chunked_rows:
        field_width = max(field_width, display_width(row["field"]) * _PX_PER_UNIT + _CELL_PADDING_PX)
    field_width = min(field_width, _FIELD_MAX_WIDTH)

    columns: list[dict[str, Any]] = []
    if has_dimension:
        dimension_width = _DIMENSION_MIN_WIDTH
        for row in chunked_rows:
            dimension_width = max(
                dimension_width,
                display_width(row.get("dimension") or "") * _PX_PER_UNIT + _CELL_PADDING_PX,
            )
        columns.append(
            {
                "name": "dimension",
                "display_name": str(dimension_header),
                "data_type": "lark_md",
                "width": f"{min(dimension_width, _DIMENSION_MAX_WIDTH)}px",
                "vertical_align": "top",
                "horizontal_align": "left",
            }
        )
    columns.append(
        {
            "name": "field",
            "display_name": field_header,
            "data_type": "lark_md",
            "width": f"{field_width}px",
            "vertical_align": "top",
            "horizontal_align": "left",
        }
    )
    for index in range(max_cells):
        custom = None
        if part_headers and index < len(part_headers):
            custom = str(part_headers[index] or "").strip() or None
        if custom:
            header = custom
        else:
            header = value_header if index == 0 else (f"说明 {index}" if index >= 2 else "说明")
        columns.append(
            {
                "name": _row_key(index),
                "display_name": header,
                "data_type": "lark_md",
                "width": f"{value_width}px",
                "vertical_align": "top",
                "horizontal_align": "left",
            }
        )

    table_rows: list[dict[str, str]] = []
    for row in chunked_rows:
        table_row: dict[str, str] = {"field": escape_lark_md(row["field"])}
        if has_dimension:
            table_row["dimension"] = escape_lark_md(row.get("dimension") or "")
        for index, cell in enumerate(row["cells"]):
            table_row[_row_key(index)] = escape_lark_md(cell)
        table_rows.append(table_row)

    return {
        "tag": "table",
        "page_size": len(table_rows),
        "row_height": "low",
        # 不冻结首列：整表（含指标列）一起横向滑动，内容列可以放宽。
        "freeze_first_column": False,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": table_rows,
    }


def _row_key(index: int) -> str:
    if index < len(_ROW_KEY_BY_INDEX):
        return _ROW_KEY_BY_INDEX[index]
    return f"detail_{index}"


def _row_parts(row: dict[str, Any]) -> list[str]:
    parts = row.get("parts")
    if isinstance(parts, list):
        return [str(part).strip() for part in parts if str(part or "").strip()]
    return split_value_parts(row.get("value")) + split_value_parts(row.get("detail"))


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}
