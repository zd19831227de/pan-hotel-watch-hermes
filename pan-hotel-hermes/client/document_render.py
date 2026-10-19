"""Markdown-lite → xlsx / docx / pptx renderer for operator documents.

2026-10-08 价格办公软件技能：LLM 输出结构化 markdown（#/## 标题、- 要点、
| 表格 |、普通段落），本模块渲染为办公文件 bytes，经后端上传飞书发文件消息。
渲染失败时调用方应回退为纯文本卡片，不得向用户抛原始异常。
"""

from __future__ import annotations

import io
import os
import re
from typing import Any

_SUPPORTED_TYPES = ("xlsx", "docx", "pptx", "pdf")
_FILENAME_FORBIDDEN = re.compile(r'[\\/:*?"<>|\r\n\t]+')
# reportlab 的 TTFont 不支持 .ttc 集合，优先单文件中文 TTF。
_PDF_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _clean_cell(text: str) -> str:
    return str(text or "").strip().replace("|", "/")


def split_sections(markdown: str) -> list[dict[str, Any]]:
    """Split markdown-lite into ordered sections of heading/bullets/table/paragraph."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    table_buffer: list[list[str]] = []

    def _flush_table() -> None:
        nonlocal table_buffer
        if current is not None and table_buffer:
            # 丢掉 |---|---| 分隔行。
            rows = [row for row in table_buffer if not all(set(c) <= {"-", ""} for c in row)]
            if rows:
                current["table"] = rows
        table_buffer = []

    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# ") and not line.startswith("## "):
            _flush_table()
            current = {"title": line[2:].strip(), "bullets": [], "paragraph": ""}
            sections.append(current)
            continue
        if line.startswith("## "):
            _flush_table()
            current = {"title": line[3:].strip(), "bullets": [], "paragraph": ""}
            sections.append(current)
            continue
        if current is None:
            current = {"title": "", "bullets": [], "paragraph": ""}
            sections.append(current)
        if line.startswith("|"):
            cells = [_clean_cell(cell) for cell in line.strip("|").split("|")]
            if any(cell for cell in cells):
                table_buffer.append(cells)
            continue
        _flush_table()
        if line.startswith(("-", "•", "·")):
            bullet = line.lstrip("-•· ").strip()
            if bullet:
                current["bullets"].append(bullet)
        else:
            current["paragraph"] = (current["paragraph"] + " " + line).strip()
    _flush_table()
    return sections


def _sheet_name(title: str, fallback: str) -> str:
    cleaned = _FILENAME_FORBIDDEN.sub("_", str(title or "").strip())
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", cleaned)
    return (cleaned or fallback)[:31] or fallback


def suggested_filename(title: str, doc_type: str) -> str:
    base = _FILENAME_FORBIDDEN.sub("_", str(title or "").strip()).strip("_")
    base = base[:40] or "运营文档"
    return f"{base}.{doc_type}"


def _render_xlsx(sections: list[dict[str, Any]], title: str) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    default_sheet = book.active
    default_sheet.title = _sheet_name(title, "内容")
    used_default = False
    for index, section in enumerate(sections, start=1):
        has_table = bool(section.get("table"))
        if has_table or index > 1:
            sheet = book.create_sheet(_sheet_name(section.get("title") or f"第{index}节", f"第{index}节"))
        else:
            sheet = default_sheet
            used_default = True
        row_cursor = 1
        section_title = str(section.get("title") or "").strip()
        if section_title:
            sheet.cell(row=row_cursor, column=1, value=section_title).font = openpyxl.styles.Font(bold=True)
            row_cursor += 2
        table = section.get("table") or []
        for row in table:
            for col, cell in enumerate(row, start=1):
                target = sheet.cell(row=row_cursor, column=col, value=cell)
                if row_cursor == section_title and False:  # pragma: no cover
                    pass
                if row is table[0] and table:
                    target.font = openpyxl.styles.Font(bold=True)
            row_cursor += 1
        if table:
            row_cursor += 1
        for bullet in section.get("bullets") or []:
            sheet.cell(row=row_cursor, column=1, value=f"• {bullet}")
            row_cursor += 1
        if section.get("paragraph"):
            sheet.cell(row=row_cursor, column=1, value=section["paragraph"])
            row_cursor += 1
    if not used_default and len(book.sheetnames) > 1:
        book.remove(default_sheet)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _render_docx(sections: list[dict[str, Any]], title: str) -> bytes:
    import docx

    document = docx.Document()
    document.add_heading(title or "运营文档", level=0)
    for section in sections:
        section_title = str(section.get("title") or "").strip()
        if section_title:
            document.add_heading(section_title, level=1)
        table = section.get("table") or []
        if table:
            rows, cols = len(table), max(len(row) for row in table)
            doc_table = document.add_table(rows=rows, cols=cols)
            doc_table.style = "Light Grid Accent 1"
            for r, row in enumerate(table):
                for c in range(cols):
                    doc_table.cell(r, c).text = row[c] if c < len(row) else ""
        for bullet in section.get("bullets") or []:
            document.add_paragraph(bullet, style="List Bullet")
        if section.get("paragraph"):
            document.add_paragraph(section["paragraph"])
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_pptx(sections: list[dict[str, Any]], title: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    if title_slide.shapes.title is not None:
        title_slide.shapes.title.text = title or "运营文档"
    for section in sections:
        section_title = str(section.get("title") or "").strip()
        table = section.get("table") or []
        bullets = list(section.get("bullets") or [])
        paragraph = str(section.get("paragraph") or "").strip()
        # 只有标题没有内容的节不出页（封面已带主标题），避免空页。
        if not table and not bullets and not paragraph:
            continue
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        if slide.shapes.title is not None:
            slide.shapes.title.text = section_title or "内容"
        if table:
            # 2026-10-09 质检修复：表格节用真表格（GraphicFrame），不再是
            # 「 | 」拼接的文本行；表头加粗、字号压缩保证 8~12 行可容纳。
            rows, cols = len(table), max(len(row) for row in table)
            shape = slide.shapes.add_table(
                rows, cols, Inches(0.5), Inches(1.6), Inches(9.0), Inches(0.42 * rows)
            )
            ppt_table = shape.table
            for r, row in enumerate(table):
                for c in range(cols):
                    cell = ppt_table.cell(r, c)
                    cell.text = str(row[c] if c < len(row) else "")
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(13)
                            if r == 0:
                                run.font.bold = True
            continue
        body = slide.placeholders[1] if len(slide.placeholders) > 1 else None
        lines: list[str] = []
        lines.extend(bullets)
        if paragraph:
            lines.append(paragraph)
        if body is not None:
            body.text = "\n".join(lines[:12])
            for para in body.text_frame.paragraphs:
                para.level = 0
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _resolve_pdf_font_name() -> str:
    """注册首个可用的中文 TTF；全部缺失时抛错由上层回退文字提示。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in _PDF_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("PanHotelCJK", path))
                return "PanHotelCJK"
            except Exception:
                continue
    raise ValueError("未找到可用的中文字体（simhei/msyh），无法生成 PDF")


# GB 中文字体（simhei 等）缺少的字形：reportlab 单字体不做回退，直接渲染
# 成方框（2026-10-09 生产质检实证 ¥/• 变 □），渲染前归一替换。
_PDF_GLYPH_REPLACEMENTS = {
    "\u00a5": "\uffe5",  # ¥ -> ￥（全角人民币符号）
    "\u2022": "\u00b7",  # • -> ·（中点）
    "\u00a0": " ",       # 不间断空格 -> 普通空格
}


def _pdf_safe_text(text: str) -> str:
    value = str(text or "")
    for src, dst in _PDF_GLYPH_REPLACEMENTS.items():
        value = value.replace(src, dst)
    return value


def _render_pdf(sections: list[dict[str, Any]], title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = _resolve_pdf_font_name()
    styles = getSampleStyleSheet()
    h0 = ParagraphStyle("DocTitleCN", parent=styles["Title"], fontName=font_name, fontSize=18, spaceAfter=10)
    h1 = ParagraphStyle("H1CN", parent=styles["Heading1"], fontName=font_name, fontSize=13, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("BodyCN", parent=styles["BodyText"], fontName=font_name, fontSize=10.5, leading=16)
    bullet = ParagraphStyle("BulletCN", parent=body, leftIndent=12, bulletIndent=4)

    story: list[Any] = [Paragraph(_pdf_safe_text(title or "运营文档"), h0)]
    for section in sections:
        section_title = _pdf_safe_text(str(section.get("title") or "").strip())
        if section_title:
            story.append(Paragraph(section_title, h1))
        table = section.get("table") or []
        if table:
            data = [
                [Paragraph(_pdf_safe_text(cell), body) for cell in row]
                for row in table
            ]
            pdf_table = Table(data, colWidths=[None] * max(len(r) for r in table))
            pdf_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2fe")),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.append(pdf_table)
            story.append(Spacer(1, 4 * mm))
        for item in section.get("bullets") or []:
            story.append(Paragraph(f"· {_pdf_safe_text(item)}", bullet))
        if section.get("paragraph"):
            story.append(Paragraph(_pdf_safe_text(str(section["paragraph"])), body))
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, title=title or "运营文档")
    document.build(story)
    return buffer.getvalue()


def render_document(markdown: str, *, doc_type: str, title: str = "") -> bytes:
    doc = str(doc_type or "").strip().lower()
    if doc not in _SUPPORTED_TYPES:
        raise ValueError(f"不支持的文档类型：{doc_type}（支持 xlsx/docx/pptx/pdf）")
    # 2026-10-09 方案A：pptx/pdf 优先走高档引擎（HTML→原生文件编译管线，
    # 固定样式预设）；未启用/依赖缺失/编译失败一律回退轻量渲染器。
    if doc in ("pptx", "pdf"):
        try:
            from client.render_pro import render_document_pro

            pro_payload = render_document_pro(markdown, doc_type=doc, title=title)
            if pro_payload:
                return pro_payload
        except Exception:
            pass
    sections = _prepare_sections(markdown, title)
    if doc == "xlsx":
        return _render_xlsx(sections, title)
    if doc == "docx":
        return _render_docx(sections, title)
    if doc == "pdf":
        return _render_pdf(sections, title)
    return _render_pptx(sections, title)


def _prepare_sections(markdown: str, title: str) -> list[dict[str, Any]]:
    sections = split_sections(markdown)
    if not sections:
        sections = [{"title": title or "内容", "bullets": [], "paragraph": str(markdown or "").strip()}]
    # markdown 首个一级标题常与文档标题相同：无表格/要点/正文时丢弃，
    # 避免 pptx 空页与 docx 标题重复（封面已有主标题）。
    if (
        sections
        and str(sections[0].get("title") or "").strip() == str(title or "").strip()
        and not (sections[0].get("table") or sections[0].get("bullets"))
        and not str(sections[0].get("paragraph") or "").strip()
    ):
        sections = sections[1:]
    return sections
