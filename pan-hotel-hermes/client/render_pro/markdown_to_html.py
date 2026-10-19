# -*- coding: utf-8 -*-
"""markdown-lite → 风格化 HTML（builder 编译管线的输入）。

2026-10-09 方案A：把 pptx-builder / pdf-builder skill 的「HTML 设计 →
编译」管线收编为 send_operator_document 的高档引擎。聊天场景没有样式
画廊挑款环节，这里用固定预设（默认 A 商务蓝，PAN_HOTEL_DOC_PRO_PRESET
可切）把既有 markdown 节 deterministic 地排成带 data-pptx 标注的
HTML，再交给收编的 extract/build 脚本编译成原生 PPTX / 矢量 PDF。

规范依据：pptx-builder reference/conventions.md（data-pptx 类型、可读
CSS 属性）。字体：PPTX 用雅黑栈（观看机器解析）；PDF 固定 SimHei——
reportlab 单 TTF 注册，雅黑只有 .ttc 集合不可用，且 SimHei 在两台
机器均已验证存在。
"""

from __future__ import annotations

import html as _html
from typing import Any

PRESETS: dict[str, dict[str, str]] = {
    # A — Corporate Blue（rounded, classic）：年报/经营汇报
    "A": dict(
        primary="#1e3a8a", secondary="#3b82f6", accent="#facc15",
        bg="#f8fafc", surface="#ffffff", text="#0f172a", muted="#64748b",
        radius="12px", border="#e2e8f0",
    ),
    # G — Consulting Grid（sharp, data-dense）：数据密集评审
    "G": dict(
        primary="#0b2545", secondary="#5b6b82", accent="#c0392b",
        bg="#ffffff", surface="#f4f6f8", text="#0b2545", muted="#5b6b82",
        radius="0px", border="#0b2545",
    ),
}
DEFAULT_PRESET = "A"

# 画布：pptx 16:9 @96dpi；pdf US-Letter @96dpi（与 builder 模板一致）。
_CANVAS = {"pptx": ("1280px", "720px"), "pdf": ("816px", "1056px")}
# 注意：值内嵌进 style="..."（双引号属性），字体名必须用单引号——双引号
# 会把属性提前截断，字体静默失效回落 Times New Roman（中文变豆腐）。
_FONTS = {
    "pptx": "'Microsoft YaHei', 'SimHei', sans-serif",
    "pdf": "'SimHei', sans-serif",
}


def _esc(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


# PDF 管线（SimHei 单字体无回退）同样要做缺字形归一，与轻量渲染器
# document_render._pdf_safe_text 同一套映射：¥→￥、•→·、nbsp→空格。
def _make_esc(doc_type: str):
    if doc_type != "pdf":
        return _esc

    def esc(value: Any) -> str:
        text = _esc(value)
        return (
            text.replace("¥", "￥")
            .replace("•", "·")
            .replace(" ", " ")
        )

    return esc


def _cover(section_html: list[str], *, title: str, meta_lines: list[str], p: dict[str, str], attr: str, esc) -> None:
    section_html.append(
        f'<div {attr}="background" style="position:absolute;inset:0;background-color:{p["primary"]};"></div>'
    )
    section_html.append(
        f'<div {attr}="rect" style="position:absolute;left:96px;top:210px;width:64px;height:6px;'
        f'background-color:{p["accent"]};"></div>'
    )
    section_html.append(
        f'<div {attr}="text" style="position:absolute;left:96px;top:246px;right:96px;'
        f'color:#ffffff;font-size:{44 if title and len(title) <= 18 else 34}px;'
        f'font-weight:700;line-height:1.35;">{esc(title)}</div>'
    )
    if meta_lines:
        meta = "<br>".join(esc(line) for line in meta_lines[:3])
        section_html.append(
            f'<div {attr}="text" style="position:absolute;left:96px;top:420px;right:96px;'
            f'color:#cbd5e1;font-size:18px;line-height:1.8;">{meta}</div>'
        )


def _table_html(table: list[list[str]], *, p: dict[str, str], font_px: int, attr: str, esc) -> str:
    rows = []
    for r, row in enumerate(table):
        tag = "th" if r == 0 else "td"
        cells = "".join(
            f"<{tag}>{esc(cell)}</{tag}>" for cell in row
        )
        rows.append(f"<tr>{cells}</tr>")
    body = "".join(rows)
    return (
        f'<table {attr}="table" style="width:100%;border-collapse:collapse;'
        f'font-size:{font_px}px;color:{p["text"]};">'
        f'<thead><tr>'
        + "".join(f'<th style="background-color:{p["primary"]};color:#ffffff;padding:8px 10px;'
                  f'border-top:1px solid {p["border"]};text-align:left;">{esc(cell)}</th>'
                  for cell in table[0])
        + "</tr></thead>"
        f'<tbody>'
        + "".join(
            "<tr>"
            + "".join(
                f'<td style="padding:7px 10px;border-top:1px solid {p["border"]};'
                f'background-color:{p["surface"]};">{esc(cell)}</td>'
                for cell in row
            )
            + "</tr>"
            for row in table[1:]
        )
        + "</tbody></table>"
    )


def build_html(
    sections: list[dict[str, Any]],
    *,
    doc_type: str,
    title: str,
    preset: str = DEFAULT_PRESET,
    meta_lines: list[str] | None = None,
) -> str:
    """markdown 节列表 → 单文件 HTML（每个 .slide/.page 一个版面）。"""
    p = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    width, height = _CANVAS[doc_type]
    font_stack = _FONTS[doc_type]
    is_slide = doc_type == "pptx"
    # 两条管线元素标记不同：pptx 用 data-pptx，pdf 用 data-pdf。
    attr = "data-pptx" if is_slide else "data-pdf"
    esc = _make_esc(doc_type)
    # PPT 版面留白与字号比 PDF 页面大一档（同尺寸感）。
    title_px, body_px, table_px = (30, 19, 16) if is_slide else (24, 14, 12)
    pad = 72 if is_slide else 56

    pages: list[str] = []
    cover_parts: list[str] = []
    _cover(cover_parts, title=title, meta_lines=list(meta_lines or []), p=p, attr=attr, esc=esc)
    pages.append(cover_parts)

    for section in sections:
        section_title = str(section.get("title") or "").strip()
        table = section.get("table") or []
        bullets = [str(b) for b in (section.get("bullets") or [])]
        paragraph = str(section.get("paragraph") or "").strip()
        if not section_title and not table and not bullets and not paragraph:
            continue
        parts: list[str] = [
            f'<div {attr}="background" style="position:absolute;inset:0;background-color:{p["bg"]};"></div>'
        ]
        parts.append(
            f'<div {attr}="text" style="position:absolute;left:{pad}px;top:{pad - 8}px;right:{pad}px;'
            f'color:{p["primary"]};font-size:{title_px}px;font-weight:700;">'
            f'{esc(section_title or title)}</div>'
        )
        parts.append(
            f'<div {attr}="rect" style="position:absolute;left:{pad}px;top:{pad + title_px + 6}px;'
            f'width:56px;height:4px;background-color:{p["accent"]};"></div>'
        )
        # 内容容器（无 data-pptx，仅布局），内部元素才成为 PowerPoint/PDF 对象。
        content: list[str] = []
        if table:
            content.append(_table_html(table, p=p, font_px=table_px, attr=attr, esc=esc))
        if bullets:
            joined = "<br>".join("· " + esc(b) for b in bullets[:12])
            content.append(
                f'<div {attr}="text" style="font-size:{body_px}px;color:{p["text"]};'
                f'line-height:2.0;">{joined}</div>'
            )
        if paragraph:
            content.append(
                f'<div {attr}="text" style="font-size:{body_px}px;color:{p["muted"]};'
                f'line-height:1.9;">{esc(paragraph)}</div>'
            )
        inner = "".join(content)
        gap = title_px + 40
        parts.append(
            f'<div style="position:absolute;left:{pad}px;top:{pad + gap}px;right:{pad}px;'
            f'bottom:{pad}px;display:flex;flex-direction:column;gap:18px;'
            f'font-family:{font_stack};">{inner}</div>'
        )
        pages.append(parts)

    canvas_cls = "slide" if is_slide else "page"
    sections_html = "".join(
        f'\n<section class="{canvas_cls}" style="width:{width};height:{height};'
        f'position:relative;overflow:hidden;font-family:{font_stack};'
        f'background-color:{p["bg"]};">'
        + "".join(parts)
        + "</section>"
        for parts in pages
    )
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n"
        f"<title>{_esc(title)}</title>\n"
        "<style>body{margin:0;background:#333;}"
        f".{canvas_cls}{{margin:0 auto 40px;}}</style>\n"
        "</head><body>\n" + sections_html + "\n</body></html>\n"
    )
