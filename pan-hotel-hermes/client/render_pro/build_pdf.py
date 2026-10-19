"""
Turns the JSON produced by extract_pdf.py into a real, vector PDF — native
text (selectable/copyable, not rasterized), vector rects/lines/tables, and
embedded images — positioned in points converted from the measured pixels.

Usage:
    python build_pdf.py <pages.json> <output.pdf>
"""
import sys
import os
import io
import base64
import math
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

PX_TO_PT = 0.75  # 1 CSS px @ 96dpi -> pt (72/96)

ALIGN_MAP = {
    "left": TA_LEFT, "right": TA_RIGHT, "center": TA_CENTER, "justify": TA_JUSTIFY,
    "start": TA_LEFT, "end": TA_RIGHT,
}
TABLE_ALIGN_MAP = {
    "left": "LEFT", "right": "RIGHT", "center": "CENTER", "justify": "LEFT",
    "start": "LEFT", "end": "RIGHT",
}

# ---------------------------------------------------------------------------
# Font resolution: map common Windows font names to their TTF files so text
# is embedded as real vector outlines and looks identical everywhere the PDF
# is opened (unlike pptx, where the *viewing* machine must have the font).
# Falls back to the 14 standard PDF core fonts if the family isn't found.
# ---------------------------------------------------------------------------
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {
    "arial": dict(normal="arial.ttf", bold="arialbd.ttf", italic="ariali.ttf", boldItalic="arialbi.ttf"),
    "calibri": dict(normal="calibri.ttf", bold="calibrib.ttf", italic="calibrii.ttf", boldItalic="calibriz.ttf"),
    "georgia": dict(normal="georgia.ttf", bold="georgiab.ttf", italic="georgiai.ttf", boldItalic="georgiaz.ttf"),
    "segoe ui": dict(normal="segoeui.ttf", bold="segoeuib.ttf", italic="segoeuii.ttf", boldItalic="segoeuiz.ttf"),
    "times new roman": dict(normal="times.ttf", bold="timesbd.ttf", italic="timesi.ttf", boldItalic="timesbi.ttf"),
    "courier new": dict(normal="cour.ttf", bold="courbd.ttf", italic="couri.ttf", boldItalic="courbi.ttf"),
    "verdana": dict(normal="verdana.ttf", bold="verdanab.ttf", italic="verdanai.ttf", boldItalic="verdanaz.ttf"),
    "tahoma": dict(normal="tahoma.ttf", bold="tahomabd.ttf"),
    "trebuchet ms": dict(normal="trebuc.ttf", bold="trebucbd.ttf", italic="trebucit.ttf", boldItalic="trebucbi.ttf"),
    # pan-hotel 收编补丁（2026-10-09）：中文档需要的中文字体。雅黑只有
    # .ttc 集合（reportlab TTFont 不支持）时注册失败会回落 Helvetica 变
    # 豆腐，因此 HTML 模板侧 PDF 固定用 SimHei；这里同时登记两者，
    # 斜体槽位用同文件占位（中文字体无真斜体）。
    "simhei": dict(normal="simhei.ttf", bold="simhei.ttf", italic="simhei.ttf", boldItalic="simhei.ttf"),
    "黑体": dict(normal="simhei.ttf", bold="simhei.ttf", italic="simhei.ttf", boldItalic="simhei.ttf"),
    "microsoft yahei": dict(normal="msyh.ttf", bold="msyhbd.ttf", italic="msyh.ttf", boldItalic="msyhbd.ttf"),
    "微软雅黑": dict(normal="msyh.ttf", bold="msyhbd.ttf", italic="msyh.ttf", boldItalic="msyhbd.ttf"),
}
_registered = {}


def resolve_family(name):
    key = (name or "").strip().strip('"').lower()
    if key in _registered:
        return _registered[key]
    spec = FONT_FILES.get(key)
    variants = None
    if spec:
        variants = {}
        for slot in ("normal", "bold", "italic", "boldItalic"):
            fname = spec.get(slot) or spec.get("normal")
            path = os.path.join(FONT_DIR, fname)
            if not os.path.exists(path):
                variants = None
                break
            variants[slot] = path
    if not variants:
        _registered[key] = None
        return None
    famname = f"PDFB-{key.replace(' ', '')}"
    try:
        pdfmetrics.registerFont(TTFont(famname, variants["normal"]))
        pdfmetrics.registerFont(TTFont(famname + "-Bold", variants["bold"]))
        pdfmetrics.registerFont(TTFont(famname + "-Italic", variants["italic"]))
        pdfmetrics.registerFont(TTFont(famname + "-BoldItalic", variants["boldItalic"]))
        pdfmetrics.registerFontFamily(
            famname, normal=famname, bold=famname + "-Bold",
            italic=famname + "-Italic", boldItalic=famname + "-BoldItalic",
        )
    except Exception as e:
        print(f"WARNING: failed to register font '{name}': {e}", file=sys.stderr)
        _registered[key] = None
        return None
    _registered[key] = famname
    return famname


pdfmetrics.registerFontFamily(
    "Helvetica", normal="Helvetica", bold="Helvetica-Bold",
    italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique",
)


def font_face(name):
    return resolve_family(name) or "Helvetica"


def hexc(h, alpha=1.0):
    h = h or "000000"
    return Color(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, alpha=alpha)


def parse_line_height(value, base_pt):
    if not value or value == "normal":
        return base_pt * 1.2
    if isinstance(value, str) and value.endswith("px"):
        try:
            return float(value[:-2]) * PX_TO_PT
        except ValueError:
            return base_pt * 1.2
    try:
        return float(value) * base_pt
    except (TypeError, ValueError):
        return base_pt * 1.2


def run_markup(run):
    fam = font_face(run.get("font_family"))
    text = escape(run.get("text", ""))
    if run.get("bold"):
        text = f"<b>{text}</b>"
    if run.get("italic"):
        text = f"<i>{text}</i>"
    if run.get("underline"):
        text = f"<u>{text}</u>"
    size = max(run.get("font_size", 12), 1)
    color = run.get("color", "000000")
    return f'<font face="{fam}" size="{size}" color="#{color}">{text}</font>'


def draw_paragraphs_block(c, paragraphs, x_pt, top_y_pt, w_pt, h_pt):
    """Top-anchored stack of Platypus Paragraphs inside a fixed-width box,
    mirroring the pptx text box's word_wrap=True + MSO_ANCHOR.TOP behavior."""
    cursor_y = top_y_pt  # y of the top edge of the next paragraph, in pdf (bottom-left origin) coords
    for para in paragraphs:
        runs = para.get("runs", [])
        if not runs:
            continue
        base_size = max((r.get("font_size", 12) for r in runs), default=12)
        markup = "".join(run_markup(r) for r in runs)
        style = ParagraphStyle(
            "run", fontName="Helvetica", fontSize=base_size,
            leading=parse_line_height(para.get("line_height"), base_size),
            alignment=ALIGN_MAP.get(para.get("align"), TA_LEFT),
        )
        p = Paragraph(markup, style)
        _, h_used = p.wrap(w_pt, max(cursor_y, 1))
        p.drawOn(c, x_pt, cursor_y - h_used)
        cursor_y -= h_used
    return top_y_pt - cursor_y  # total height consumed


def rect_path(c, x, y, w, h, radius):
    p = c.beginPath()
    if radius and radius > 1:
        p.roundRect(x, y, w, h, radius)
    else:
        p.rect(x, y, w, h)
    return p


def apply_shadow(c, el, x, y, w, h, radius):
    if not el.get("shadow"):
        return
    c.saveState()
    c.setFillColor(Color(0, 0, 0, alpha=0.30))
    c.setStrokeColor(Color(0, 0, 0, alpha=0))
    p = rect_path(c, x + 1.5, y - 1.5, w, h, radius)
    c.drawPath(p, fill=1, stroke=0)
    c.restoreState()


def apply_gradient_fill(c, el, x, y, w, h, radius):
    g = el["gradient"]
    angle = math.radians(g.get("angle", 90))
    dx, dy = math.sin(angle), math.cos(angle)
    cx, cy = x + w / 2, y + h / 2
    extent = abs(dx) * w / 2 + abs(dy) * h / 2
    if extent <= 0:
        extent = max(w, h) / 2 or 1
    x0, y0 = cx - dx * extent, cy - dy * extent
    x1, y1 = cx + dx * extent, cy + dy * extent
    c.saveState()
    clip = rect_path(c, x, y, w, h, radius)
    c.clipPath(clip, stroke=0)
    c.linearGradient(x0, y0, x1, y1, [hexc(g["from"]), hexc(g["to"])])
    c.restoreState()


def add_rect(c, el, page_h):
    top_y_pt = el["y"] * PX_TO_PT
    x = el["x"] * PX_TO_PT
    w = el["w"] * PX_TO_PT
    h = el["h"] * PX_TO_PT
    y = page_h - top_y_pt - h  # bottom-left corner, pdf coords
    radius = min(el.get("radius", 0) * PX_TO_PT, min(w, h) / 2 if min(w, h) > 0 else 0)
    rotation = el.get("rotation", 0)

    c.saveState()
    if rotation:
        cx, cy = x + w / 2, y + h / 2
        c.translate(cx, cy)
        c.rotate(-rotation)
        c.translate(-cx, -cy)

    apply_shadow(c, el, x, y, w, h, radius)

    if el.get("gradient"):
        apply_gradient_fill(c, el, x, y, w, h, radius)
    elif el.get("fill"):
        c.setFillColor(hexc(el["fill"], el.get("fill_alpha", 1.0) * el.get("opacity", 1.0)))

    has_fill = bool(el.get("fill") or el.get("gradient"))
    has_border = bool(el.get("border_color") and el.get("border_width", 0) > 0)
    if has_border:
        c.setStrokeColor(hexc(el["border_color"]))
        c.setLineWidth(max(el["border_width"] * PX_TO_PT, 0.5))
    if has_fill and not el.get("gradient"):
        path = rect_path(c, x, y, w, h, radius)
        c.drawPath(path, fill=1, stroke=1 if has_border else 0)
    elif has_border and not (has_fill and not el.get("gradient")):
        path = rect_path(c, x, y, w, h, radius)
        c.drawPath(path, fill=0, stroke=1)

    if el.get("paragraphs"):
        pad = 4
        draw_paragraphs_block(c, el["paragraphs"], x + pad, y + h - pad, w - 2 * pad, h - 2 * pad)

    c.restoreState()


def add_text(c, el, page_h):
    top_y_pt = el["y"] * PX_TO_PT
    x = el["x"] * PX_TO_PT
    w = el["w"] * PX_TO_PT
    h = el["h"] * PX_TO_PT
    y = page_h - top_y_pt - h
    rotation = el.get("rotation", 0)

    c.saveState()
    if rotation:
        cx, cy = x + w / 2, y + h / 2
        c.translate(cx, cy)
        c.rotate(-rotation)
        c.translate(-cx, -cy)
    draw_paragraphs_block(c, el.get("paragraphs", []), x, y + h, w, h)
    c.restoreState()


def resolve_image_source(src, base_dir):
    if not src:
        return None
    if src.startswith("data:"):
        _, _, b64data = src.partition(",")
        return ImageReader(io.BytesIO(base64.b64decode(b64data)))
    if src.startswith(("http://", "https://")):
        print(f"WARNING: remote image URLs are not fetched, skipping: {src}", file=sys.stderr)
        return None
    path = src if os.path.isabs(src) else os.path.join(base_dir, src)
    if not os.path.exists(path):
        print(f"WARNING: image not found, skipping: {path}", file=sys.stderr)
        return None
    return ImageReader(path)


def add_picture(c, el, page_h, base_dir):
    img = resolve_image_source(el.get("src"), base_dir)
    if img is None:
        return
    top_y_pt = el["y"] * PX_TO_PT
    x = el["x"] * PX_TO_PT
    w = el["w"] * PX_TO_PT
    h = el["h"] * PX_TO_PT
    y = page_h - top_y_pt - h
    rotation = el.get("rotation", 0)
    c.saveState()
    if rotation:
        cx, cy = x + w / 2, y + h / 2
        c.translate(cx, cy)
        c.rotate(-rotation)
        c.translate(-cx, -cy)
    try:
        c.drawImage(img, x, y, width=w, height=h, mask="auto", preserveAspectRatio=False)
    except Exception as exc:
        print(f"WARNING: failed to draw image {el.get('src')}: {exc}", file=sys.stderr)
    c.restoreState()


def add_line(c, el, page_h):
    top_y_pt = el["y"] * PX_TO_PT
    x = el["x"] * PX_TO_PT
    w = el["w"] * PX_TO_PT
    h = el["h"] * PX_TO_PT
    color = el.get("fill") or el.get("border_color") or "000000"
    weight = max(el.get("border_width") or (el["h"] if el["h"] < el["w"] else el["w"]), 1) * PX_TO_PT
    c.saveState()
    c.setStrokeColor(hexc(color))
    c.setLineWidth(max(weight, 0.75))
    if w >= h:
        yy = page_h - top_y_pt - h / 2
        c.line(x, yy, x + w, yy)
    else:
        xx = x + w / 2
        y0 = page_h - top_y_pt
        c.line(xx, y0, xx, y0 - h)
    c.restoreState()


def add_table(c, el, page_h):
    rows_data = el.get("rows", [])
    if not rows_data:
        return
    nrows = len(rows_data)
    ncols = max(len(r) for r in rows_data)
    top_y_pt = el["y"] * PX_TO_PT
    x = el["x"] * PX_TO_PT
    w = el["w"] * PX_TO_PT
    h = el["h"] * PX_TO_PT
    y = page_h - top_y_pt - h

    col_w = w / ncols
    row_h = h / nrows
    data = []
    style_cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for ri, row in enumerate(rows_data):
        row_cells = []
        for ci in range(ncols):
            if ci >= len(row):
                row_cells.append("")
                continue
            cdata = row[ci]
            fam = font_face(cdata.get("font_family"))
            variant = f'<b>{escape(cdata.get("text", ""))}</b>' if cdata.get("bold") else escape(cdata.get("text", ""))
            markup = f'<font face="{fam}" size="{max(cdata.get("font_size", 11), 1)}" color="#{cdata.get("color", "000000")}">{variant}</font>'
            cell_style = ParagraphStyle(
                "cell", fontName=fam, fontSize=max(cdata.get("font_size", 11), 1),
                alignment=ALIGN_MAP.get(cdata.get("align"), TA_LEFT), leading=max(cdata.get("font_size", 11), 1) * 1.25,
            )
            row_cells.append(Paragraph(markup, cell_style))
            if cdata.get("fill"):
                style_cmds.append(("BACKGROUND", (ci, ri), (ci, ri), hexc(cdata["fill"])))
        data.append(row_cells)

    table = Table(data, colWidths=[col_w] * ncols, rowHeights=[row_h] * nrows)
    table.setStyle(TableStyle(style_cmds))
    table.wrapOn(c, w, h)
    table.drawOn(c, x, y)


def add_element(c, el, page_h, base_dir):
    kind = el["kind"]
    try:
        if kind in ("rect", "background"):
            add_rect(c, el, page_h)
        elif kind == "text":
            add_text(c, el, page_h)
        elif kind in ("image", "icon"):
            add_picture(c, el, page_h, base_dir)
        elif kind == "line":
            add_line(c, el, page_h)
        elif kind == "table":
            add_table(c, el, page_h)
        else:
            print(f"WARNING: unknown data-pdf kind '{kind}', skipping", file=sys.stderr)
    except Exception as exc:
        print(f"WARNING: failed to add element {kind}: {exc}", file=sys.stderr)


def main():
    import json
    json_path, out_path = sys.argv[1], sys.argv[2]
    base_dir = os.path.dirname(os.path.abspath(json_path))
    with open(json_path, encoding="utf-8") as f:
        pages_data = json.load(f)

    first = pages_data[0]
    page_w_pt = first["width"] * PX_TO_PT
    page_h_pt = first["height"] * PX_TO_PT

    c = canvas.Canvas(out_path, pagesize=(page_w_pt, page_h_pt))
    for page_data in pages_data:
        for el in page_data["elements"]:
            add_element(c, el, page_h_pt, base_dir)
        c.showPage()
    c.save()
    print(f"Wrote {len(pages_data)} page(s) -> {out_path}")


if __name__ == "__main__":
    main()
