"""问答回复卡片模板库（V7 Step 0）。

后端独立模块：参考 ``pan-hotel-hermes/client/feishu_cards.py`` 的排版结构
（table + section / block + hr / 三段式 / alert header）为自由文本重新实现，
不 import Hermes 包（后端与 Hermes 是两个独立 venv）。

职责边界：
- 本模块只做「分类 + 渲染」，输入应为已经过泄漏过滤的安全文本；
- 分类器纯关键词 + 正则，不调 LLM，保证低延迟；
- 任何渲染异常都回退 ``qa_plain``，绝不阻断回复链路。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")

QA_PLAIN = "qa_plain"
QA_DATA_REPORT = "qa_data_report"
QA_PRICE_COMPARE = "qa_price_compare"
QA_ANALYSIS = "qa_analysis"
QA_ALERT = "qa_alert"

QA_CARD_TEMPLATE_IDS: tuple[str, ...] = (
    QA_DATA_REPORT,
    QA_PRICE_COMPARE,
    QA_ANALYSIS,
    QA_PLAIN,
    QA_ALERT,
)

# 与 client/feishu_cards.py 的 WEBHOOK_BODY_BYTE_LIMIT 保持一致。
CARD_BYTE_LIMIT = 28000
_ALERT_BODY_CHAR_LIMIT = 4800
_ALERT_BODY_LINE_LIMIT = 24
TABLE_ROW_LIMIT = 30
FEISHU_MAX_TABLES_PER_CARD = 5
FEISHU_LONG_VALUE_CHARS = 36


# --------------------------------------------------------------------------
# 分类规则（可通过 feishu_card_templates.json 覆盖关键词，见 _load_rules）
# --------------------------------------------------------------------------


@dataclass
class QaClassificationRules:
    data_metric_keywords: tuple[str, ...] = (
        "出租率",
        "入住率",
        "均价",
        "房价",
        "间夜",
        "订单",
        "评分",
        "营收",
        "收入",
        "ADR",
        "RevPAR",
        "曝光",
        "流量",
    )
    price_keywords: tuple[str, ...] = (
        "竞品",
        "比价",
        "价格对比",
        "对比价",
        "本店价",
        "竞品价",
        "同房型",
    )
    analysis_keywords: tuple[str, ...] = ("结论", "发现", "建议", "建议行动", "下一步")
    alert_keywords: tuple[str, ...] = ("异常", "失败", "告警", "错误", "超时", "无法读取")
    # 房型/酒店词用于价格对比的二次确认，避免单条价格数字误判。
    price_context_keywords: tuple[str, ...] = ("房型", "酒店", "竞品", "本店", "大床", "双床")

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "data_metric_keywords": list(self.data_metric_keywords),
            "price_keywords": list(self.price_keywords),
            "analysis_keywords": list(self.analysis_keywords),
            "alert_keywords": list(self.alert_keywords),
            "price_context_keywords": list(self.price_context_keywords),
        }

    def update_from(self, data: dict[str, Any]) -> None:
        for name in self.to_dict():
            values = data.get(name)
            if isinstance(values, list) and values:
                setattr(self, name, tuple(str(item) for item in values if str(item)))


_PRICE_PATTERN = re.compile(r"(?:¥|￥|\bCNY\s?|\bRMB\s?)?\d+(?:\.\d+)?\s*[元块]|(?:¥|￥)\s?\d+")
_DIGIT_PATTERN = re.compile(r"\d")
_ANALYSIS_SECTION_RE = re.compile(r"\*\*(结论|关键发现|发现|建议行动|建议|下一步)[：:）)】]?\*\*|^#{1,6}\s*(结论|关键发现|发现|建议行动|建议|下一步)")
_NUMBERED_LIST_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s+")


def _rules() -> QaClassificationRules:
    return _load_rules()


def _load_rules() -> QaClassificationRules:
    """加载分类规则；``feishu_card_templates.json`` 存在时合并覆盖。"""
    rules = QaClassificationRules()
    path = _rules_config_path()
    if not path.is_file():
        return rules
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return rules
    if isinstance(data, dict) and isinstance(data.get("classification"), dict):
        rules.update_from(data["classification"])
    return rules


def _rules_config_path() -> Path:
    import os

    override = os.environ.get("FEISHU_QA_CARD_RULES_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "feishu_card_templates.json"


def classify_qa_content(text: str) -> str:
    """根据文本内容特征选择卡片模板（纯关键词 + 正则，不调 LLM）。"""
    content = str(text or "")
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return QA_PLAIN
    rules = _rules()

    # 1. 数字密集 + 指标关键词，或本身就是 markdown 表格 → 数据报表
    digit_lines = [line for line in lines if _DIGIT_PATTERN.search(line)]
    has_metric = any(keyword in content for keyword in rules.data_metric_keywords)
    has_table = any(line.lstrip("|").strip().count("|") >= 1 for line in lines if line.startswith("|"))
    if has_table or (has_metric and len(digit_lines) >= 2) or (has_metric and _PRICE_PATTERN.search(content)):
        return QA_DATA_REPORT

    # 2. 价格/房型/竞品关键词 + 至少两个价格数字 → 价格对比
    price_hits = len(_PRICE_PATTERN.findall(content))
    has_price_keyword = any(keyword in content for keyword in rules.price_keywords)
    has_price_context = any(keyword in content for keyword in rules.price_context_keywords)
    if (has_price_keyword or has_price_context) and price_hits >= 2:
        return QA_PRICE_COMPARE

    # 3. 结论/发现/建议 结构 → 分析
    marker_hits = sum(1 for keyword in rules.analysis_keywords if keyword in content)
    if marker_hits >= 2 or _ANALYSIS_SECTION_RE.search(content):
        return QA_ANALYSIS

    # 4. 异常/失败/告警/错误 → 告警
    if any(keyword in content for keyword in rules.alert_keywords):
        return QA_ALERT

    return QA_PLAIN


# --------------------------------------------------------------------------
# Markdown 解析（识别 # 标题 / |表格| / 列表 / 段落，拆分为渲染块）
# --------------------------------------------------------------------------


@dataclass
class _MarkdownBlock:
    kind: str  # heading / table / list / paragraph
    lines: list[str] = field(default_factory=list)


_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
# 指标值形态：以数字开头（可带正负/约等符号），如「82%」「-30.27%」「176 人」。
_METRIC_VALUE_START_RE = re.compile(r"^[+＋\-−≈~～]?\d")
# 冒号把一个数字劈成两段（时间戳 09:20 等）的特征：字段以数字结尾 + 值以数字开头。
_TIME_SPLIT_FIELD_TAIL_RE = re.compile(r"\d\s*$")
_TIME_SPLIT_VALUE_HEAD_RE = re.compile(r"^\s*\d")
# 更长的「值」是叙述句：进表会被按空格劈成 数值/说明 两列（实测 17:21 坏行）。
_QA_METRIC_VALUE_MAX_CHARS = 48


def _parse_markdown_blocks(text: str) -> list[_MarkdownBlock]:
    blocks: list[_MarkdownBlock] = []
    current: _MarkdownBlock | None = None

    def _flush() -> None:
        nonlocal current
        if current is not None and current.lines:
            blocks.append(current)
        current = None

    def _open(kind: str, line: str) -> _MarkdownBlock:
        nonlocal current
        if current is None or current.kind != kind:
            _flush()
            current = _MarkdownBlock(kind=kind)
        current.lines.append(line)
        return current

    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            _flush()
            continue
        if _HEADING_RE.match(stripped):
            _flush()
            blocks.append(_MarkdownBlock(kind="heading", lines=[stripped]))
        elif stripped.startswith("|") and stripped.count("|") >= 2:
            if _TABLE_SEPARATOR_RE.match(stripped) and current is not None and current.kind == "table":
                current.lines.append(stripped)
            else:
                _open("table", stripped)
        elif _NUMBERED_LIST_RE.match(stripped):
            _open("list", stripped)
        else:
            _open("paragraph", stripped)
    _flush()
    return blocks


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _table_columns(header_cells: list[str]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, cell in enumerate(header_cells):
        name = re.sub(r"\s+", "_", cell.strip()) or f"col_{index}"
        if name in used:
            name = f"{name}_{index}"
        used.add(name)
        columns.append(
            {
                "name": name,
                "display_name": _escape(cell),
                "data_type": "lark_md",
                # Explicit widths let Feishu keep a real wide table on mobile
                # instead of shrinking every metric into an unreadable column.
                "width": (
                    "132px"
                    if index == 0
                    else ("360px" if len(header_cells) == 2 else "120px")
                ),
                "vertical_align": "top",
                "horizontal_align": "left",
            }
        )
    return columns


def _split_table_line(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [cell.strip() for cell in cells]


def _markdown_table_element(block: _MarkdownBlock) -> dict[str, Any] | None:
    lines = [line for line in block.lines if not _TABLE_SEPARATOR_RE.match(line)]
    if not lines:
        return None
    header_cells = _split_table_line(lines[0])
    if not any(header_cells):
        return None
    columns = _table_columns(header_cells)
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        cells = _split_table_line(line)
        row: dict[str, Any] = {}
        for index, column in enumerate(columns):
            row[column["name"]] = _escape(cells[index]) if index < len(cells) else ""
        rows.append(row)
    if len(rows) > TABLE_ROW_LIMIT:
        rows = rows[:TABLE_ROW_LIMIT]
    if not rows:
        return None
    return {
        "tag": "table",
        "page_size": min(max(len(rows), 1), 10),
        "row_height": "low",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def _prepare_qa_field_rows(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    prepared: list[dict[str, str]] = []
    for key, value in rows:
        text = str(value or "-").strip() or "-"
        parts = [part.strip() for part in re.split(r"\r?\n|[|｜]", text) if part.strip()]
        if len(parts) > 1:
            value_text, detail_text = parts[0], " / ".join(parts[1:])
        elif len(text) > FEISHU_LONG_VALUE_CHARS:
            split_at = text.rfind(" ", 12, FEISHU_LONG_VALUE_CHARS)
            if split_at < 12:
                split_at = FEISHU_LONG_VALUE_CHARS
            value_text, detail_text = text[:split_at].rstrip(), text[split_at:].lstrip()
        else:
            value_text, detail_text = text, ""
        row = {"field": _escape(key), "value": _escape(value_text)}
        if detail_text:
            row["detail"] = _escape(detail_text)
        prepared.append(row)
    return prepared


def _field_table(rows: list[tuple[str, str]]) -> dict[str, Any]:
    table = {
        "tag": "table",
        "page_size": min(max(len(rows), 1), 10),
        "row_height": "low",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            {
                "name": "field",
                "display_name": "项目",
                "data_type": "lark_md",
                "width": "132px",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
            {
                "name": "value",
                "display_name": "数值",
                "data_type": "lark_md",
                "width": "220px",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
        ],
        "rows": _prepare_qa_field_rows(rows),
    }
    has_detail = any("detail" in row for row in table["rows"])
    table["columns"][1]["width"] = "180px" if has_detail else "360px"
    if has_detail:
        table["columns"].append(
            {
                "name": "detail",
                "display_name": "对比 / 说明",
                "data_type": "lark_md",
                "width": "220px",
                "vertical_align": "top",
                "horizontal_align": "left",
            }
        )
    table["page_size"] = min(max(len(table["rows"]), 1), TABLE_ROW_LIMIT)
    return table


def _note_element(content: str) -> dict[str, Any]:
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": content[:200]}],
    }


# --------------------------------------------------------------------------
# 模板 builder（参考 client/feishu_cards.py 排版，适配自由文本）
# --------------------------------------------------------------------------


def _build_qa_plain_card(text: str) -> list[dict[str, Any]]:
    content = str(text or "").strip() or "（无内容）"
    return [
        _card_text(f"💬 {_escape(content)}"),
        _note_element(f"🕒 运营助手 · {datetime.now(_SHANGHAI):%H:%M}"),
    ]


def _is_metric_field_row(field_text: str, value_text: str) -> bool:
    """只有「字段：短指标值」才进数据报表表格（2026-09-01 实测 17:21 卡片坏行修复）。

    - 标题/句子里时间戳的冒号（09:20）会把行劈成「…09 | 20 读取）」：
      字段以数字结尾且值以数字开头即为此类，保持文本；
    - 冒号后不以数字开头的是叙述句（如「📊 结论：今天金字塔…」），保持文本；
    - 值过长是叙述句，进表会被从空格处劈成两列，保持文本。
    """

    field = str(field_text or "").strip()
    value = str(value_text or "").strip()
    if _TIME_SPLIT_FIELD_TAIL_RE.search(field) and _TIME_SPLIT_VALUE_HEAD_RE.match(value):
        return False
    if not _METRIC_VALUE_START_RE.match(value):
        return False
    return len(value) <= _QA_METRIC_VALUE_MAX_CHARS


def _build_qa_data_report_card(text: str) -> list[dict[str, Any]]:
    """参考 build_beneficial_data_card 的 table + section 排版。"""
    blocks = _parse_markdown_blocks(text)
    elements: list[dict[str, Any]] = []
    field_rows: list[tuple[str, str]] = []
    for block in blocks:
        if block.kind == "table":
            table = _markdown_table_element(block)
            if table is not None:
                elements.append(table)
            else:
                elements.append(_card_text(_escape("\n".join(block.lines))))
            continue
        block_text = "\n".join(block.lines)
        if block.kind in {"list", "heading"} or not _DIGIT_PATTERN.search(block_text):
            elements.append(
                _card_text(
                    f"{_qa_data_block_icon(block_text)} " + _escape(block_text)
                )
            )
            continue
        for line in block.lines:
            match = re.match(r"^(.{1,24})[：:]\s*(.+)$", line)
            if (
                match
                and _DIGIT_PATTERN.search(match.group(2))
                and _is_metric_field_row(match.group(1), match.group(2))
            ):
                field_rows.append((match.group(1).strip(), match.group(2).strip()))
            else:
                elements.append(_card_text(f"{_qa_data_block_icon(line)} {_escape(line)}"))
    if field_rows:
        field_rows = field_rows[:TABLE_ROW_LIMIT]
        elements.append(_field_table(field_rows))
    if not elements:
        return _build_qa_plain_card(text)
    return elements


def _qa_data_block_icon(text: str) -> str:
    """按问答内容语义选择分区图标，避免所有数据行都使用同一图标。"""

    normalized = str(text or "")
    if any(keyword in normalized for keyword in ("订单", "预订", "成交")):
        return "🛒"
    if any(keyword in normalized for keyword in ("流量", "曝光", "访客", "转化")):
        return "🚦"
    if any(keyword in normalized for keyword in ("点评", "评分", "好评", "差评")):
        return "💬"
    if any(keyword in normalized for keyword in ("说明", "口径", "备注")):
        return "📝"
    if any(keyword in normalized for keyword in ("经营", "销售", "收入", "均价", "出租率")):
        return "🧾"
    return "📋"


def _build_qa_price_compare_card(text: str) -> list[dict[str, Any]]:
    """参考 _competitor_mobile_block_elements 的 block + hr 排版。"""
    blocks = _parse_markdown_blocks(text)
    comparison_rows: list[tuple[str, str]] = []
    for block in blocks:
        if block.kind != "paragraph" or len(block.lines) != 1:
            continue
        match = re.match(r"^(.{1,32})[：:]\s*(.+)$", block.lines[0])
        if match and _DIGIT_PATTERN.search(match.group(2)):
            comparison_rows.append((match.group(1).strip(), match.group(2).strip()))
    if len(comparison_rows) >= 4:
        return [
            _card_text("🏷️ 价格对比明细"),
            _comparison_table(comparison_rows),
        ]
    elements: list[dict[str, Any]] = []
    for block in blocks:
        content = "\n".join(block.lines)
        if not content.strip():
            continue
        if elements:
            elements.append({"tag": "hr"})
        elements.append(_card_text(f"🏷️ {_escape(content)}"))
    return elements or _build_qa_plain_card(text)


def _comparison_table(rows: list[tuple[str, str]]) -> dict[str, Any]:
    cleaned_rows = rows[:TABLE_ROW_LIMIT]
    return {
        "tag": "table",
        "page_size": min(max(len(cleaned_rows), 1), 10),
        "row_height": "low",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            {
                "name": "field",
                "display_name": "房型",
                "data_type": "lark_md",
                "width": "132px",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
            {
                "name": "value",
                "display_name": "价格对比",
                "data_type": "lark_md",
                "width": "360px",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
        ],
        "rows": [
            {"field": _escape(field), "value": _escape(value)}
            for field, value in cleaned_rows
        ],
    }


def _build_qa_analysis_card(text: str) -> list[dict[str, Any]]:
    """参考 build_mobile_summary_card 的三段式排版。"""
    content = str(text or "").strip()
    if not content:
        return _build_qa_plain_card(text)
    sections = _split_analysis_sections(content)
    if not sections:
        return _build_qa_plain_card(text)
    elements: list[dict[str, Any]] = []
    for title, body in sections:
        body_text = "\n".join(body).strip()
        if not body_text and not title:
            continue
        if title:
            title_icon = {
                "结论": "✅",
                "关键发现": "🔎",
                "发现": "🔎",
                "建议行动": "👉",
                "建议": "👉",
                "下一步": "👉",
            }.get(title, "📌")
            chunk = f"**{title_icon} {title}**\n{_escape(body_text) if body_text else '—'}"
        else:
            chunk = _escape(body_text)
        if elements:
            elements.append({"tag": "hr"})
        elements.append(_card_text(chunk))
    return elements or _build_qa_plain_card(text)


def _split_analysis_sections(content: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    def _cut(title: str | None, first_line: str | None = None) -> None:
        nonlocal current_title, current_lines
        if current_lines or sections or current_title is not None:
            sections.append((current_title, current_lines))
        current_title = title
        current_lines = [first_line] if first_line else []

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 独立标题行：**结论** / # 建议
        standalone = re.match(
            r"^\**\s*(结论|关键发现|发现|建议行动|建议|下一步)\s*[：:）)】]?\**\s*$",
            stripped,
        )
        if standalone:
            _cut(standalone.group(1))
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            _cut(heading.group(1).strip())
            continue
        # 行内标题：**结论**：正文
        inline = re.match(
            r"^\*\*\s*(结论|关键发现|发现|建议行动|建议|下一步)\s*[：:）)】]?\*\*\s*(.*)$",
            stripped,
        )
        if inline:
            _cut(inline.group(1), inline.group(2).strip() or None)
            continue
        current_lines.append(stripped)
    if current_lines or sections or current_title is not None:
        sections.append((current_title, current_lines))
    return sections


def _build_qa_alert_card(text: str) -> list[dict[str, Any]]:
    """参考 build_generic_operator_card 的 alert 色 header + 精简正文。"""
    content = str(text or "").strip() or "（无内容）"
    lines = [line for line in content.splitlines() if line.strip()]
    truncated = False
    if len(lines) > _ALERT_BODY_LINE_LIMIT:
        lines = lines[:_ALERT_BODY_LINE_LIMIT]
        truncated = True
    body = "\n".join(lines)
    if len(body) > _ALERT_BODY_CHAR_LIMIT:
        body = body[:_ALERT_BODY_CHAR_LIMIT]
        truncated = True
    if truncated:
        body += "\n……（内容过长已截断）"
    elements = [_card_text(f"🔴 {_escape(body)}")]
    elements.append(_note_element(f"🕒 异常提醒 · 运营助手 · {datetime.now(_SHANGHAI):%H:%M}"))
    return elements


_QaCardBuilder = Callable[[str], list[dict[str, Any]]]

_QA_CARD_TEMPLATES: dict[str, _QaCardBuilder] = {
    QA_DATA_REPORT: _build_qa_data_report_card,
    QA_PRICE_COMPARE: _build_qa_price_compare_card,
    QA_ANALYSIS: _build_qa_analysis_card,
    QA_PLAIN: _build_qa_plain_card,
    QA_ALERT: _build_qa_alert_card,
}


def register_qa_card_template(
    template_id: str,
    builder: _QaCardBuilder | None,
) -> None:
    """注册/注销问答回复卡片模板（可插拔扩展点）。

    ``builder`` 传 ``None`` 时移除自定义模板；内置模板不可移除。
    """
    if builder is None:
        if template_id not in (
            QA_DATA_REPORT,
            QA_PRICE_COMPARE,
            QA_ANALYSIS,
            QA_PLAIN,
            QA_ALERT,
        ):
            _QA_CARD_TEMPLATES.pop(template_id, None)
        return
    _QA_CARD_TEMPLATES[template_id] = builder


def render_qa_card_elements(template_id: str, text: str) -> list[dict[str, Any]]:
    """按模板渲染 elements；未知模板或渲染异常一律回退 ``qa_plain``。"""
    builder = _QA_CARD_TEMPLATES.get(str(template_id or ""))
    try:
        if builder is None:
            raise ValueError(f"unknown qa card template: {template_id}")
        elements = builder(str(text or ""))
        if not isinstance(elements, list) or not elements:
            raise ValueError(f"template {template_id} produced no elements")
        return _cap_table_elements(elements)
    except Exception:  # noqa: BLE001 - 模板失败绝不阻断回复
        return _build_qa_plain_card(text)


def _cap_table_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep Feishu's native table count bounded without dropping table data."""
    result: list[dict[str, Any]] = []
    table_count = 0
    for element in elements:
        if element.get("tag") != "table":
            result.append(element)
            continue
        table_count += 1
        if table_count <= FEISHU_MAX_TABLES_PER_CARD:
            result.append(element)
        else:
            result.append(_card_text(_table_fallback_text(element)))
    return result


def _table_fallback_text(table: dict[str, Any]) -> str:
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    names = [str(column.get("display_name") or column.get("name") or "") for column in columns if isinstance(column, dict)]
    lines = ["📋 " + " | ".join(names)] if names else ["📋 数据明细"]
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    keys = [str(column.get("name") or "") for column in columns if isinstance(column, dict)]
    for row in rows[:TABLE_ROW_LIMIT]:
        if not isinstance(row, dict):
            continue
        lines.append(" | ".join(str(row.get(key) or "") for key in keys))
    return _escape("\n".join(lines))


# --------------------------------------------------------------------------
# 入口：分类 + 渲染 + 兜底
# --------------------------------------------------------------------------


def build_qa_reply_card(
    text: str,
    *,
    title: str = "运营助手",
    status: str = "success",
    template_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """问答回复卡片入口：内容分类 → 模板渲染 → 字节兜底。"""
    content = str(text or "").strip() or "（无内容）"
    chosen = str(template_id or "").strip() or classify_qa_content(content)
    elements = render_qa_card_elements(chosen, content)
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _header_template(status, chosen, content),
            "title": {
                "tag": "plain_text",
                "content": f"{str(title or '运营助手').strip() or '运营助手'} · {(now or datetime.now(_SHANGHAI)):%H:%M}"[:80],
            },
        },
        "elements": elements,
    }
    return _enforce_card_byte_limit(card)


def _header_template(status: str, template_id: str, content: str) -> str:
    if template_id == QA_ALERT:
        hard_markers = ("失败", "错误")
        return "red" if any(marker in content for marker in hard_markers) else "orange"
    return {
        "success": "green",
        "attention_required": "orange",
        "failed": "red",
        "config_required": "orange",
    }.get(str(status or "").strip(), "blue")


def _enforce_card_byte_limit(card: dict[str, Any], *, limit: int = CARD_BYTE_LIMIT) -> dict[str, Any]:
    """超限时从尾部丢弃 element，与 client 的 enforce_card_byte_limit 口径一致。

    单 element 仍超限时对内容本身截断，保证回复不因超大文本失败。
    """

    def _size() -> int:
        return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))

    elements = card.get("elements")
    if not isinstance(elements, list) or not elements:
        return card
    while len(elements) > 1 and _size() > limit:
        elements.pop()
    if _size() <= limit:
        return card
    element = elements[0]
    if element.get("tag") == "table":
        rows = element.get("rows") or []
        while rows and _size() > limit:
            rows.pop()
        if rows:
            return card
        card["elements"] = [_card_text("（内容过长，已截断）")]
        return card
    content = str(element.get("content") or "")
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        element["content"] = f"{content[:middle]}……（内容过长已截断）"
        if _size() <= limit:
            low = middle
        else:
            high = middle - 1
    element["content"] = f"{content[:low]}……（内容过长已截断）"
    return card
