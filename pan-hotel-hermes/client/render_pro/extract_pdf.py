"""
Measures an HTML page layout in a real browser and dumps a JSON description
of every element tagged data-pdf="..." (position, size, computed style,
text runs, table cells) so build_pdf.py can recreate them as native
PDF content (real vector text/shapes, not a screenshot).

Usage:
    python extract_pdf.py <pages.html> <out.json> <images_dir>

The HTML file must contain one or more:
    <section class="page" style="width:816px;height:1056px">...</section>
Every element meant to become a distinct PDF object must carry a
data-pdf attribute. See ../reference/conventions.md for the full contract.
"""
import sys
import os
import re
import json
import math
import pathlib
from playwright.sync_api import sync_playwright


def rgb_to_hex_alpha(css_color):
    if not css_color or css_color in ("transparent", "none", "rgba(0, 0, 0, 0)"):
        return None, 0.0
    m = re.match(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)", css_color)
    if not m:
        return None, 1.0
    r, g, b = (int(float(m.group(i))) for i in (1, 2, 3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return "%02X%02X%02X" % (r, g, b), a


def is_bold(font_weight):
    if not font_weight:
        return False
    if font_weight.isdigit():
        return int(font_weight) >= 600
    return font_weight == "bold"


def parse_px(val, default=0.0):
    if not val:
        return default
    m = re.match(r"(-?[\d.]+)px", val)
    return float(m.group(1)) if m else default


def parse_radius(val, w, h, default=0.0):
    """border-radius: Chromium keeps a percentage computed value as e.g.
    '50%' rather than resolving it to px, unlike most other CSS length
    properties — resolve it ourselves against the element's own box
    (percentage radii are relative to width/height, per spec)."""
    if not val:
        return default
    pm = re.match(r"(-?[\d.]+)%", val)
    if pm:
        return float(pm.group(1)) / 100.0 * min(w, h)
    return parse_px(val, default)


def parse_rotation(transform):
    if not transform or transform == "none":
        return 0.0
    m = re.match(r"matrix\(([^,]+),([^,]+),", transform)
    if not m:
        return 0.0
    a, b = float(m.group(1)), float(m.group(2))
    return round(math.degrees(math.atan2(b, a)), 2)


def split_top_level(content, sep=","):
    """Split on `sep` but not inside nested parens (e.g. don't split the
    internal commas of an rgb(...) color when splitting gradient stops)."""
    parts = []
    depth = 0
    cur = ""
    for ch in content:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == sep and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def parse_gradient(bg_image):
    """Very small linear-gradient(angle, color a%, color b%, ...) -> first/last stop."""
    if not bg_image or "linear-gradient" not in bg_image:
        return None
    start = bg_image.find("linear-gradient(")
    if start == -1:
        return None
    start += len("linear-gradient(")
    depth = 1
    i = start
    while i < len(bg_image) and depth > 0:
        if bg_image[i] == "(":
            depth += 1
        elif bg_image[i] == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    content = bg_image[start:i - 1]
    parts = split_top_level(content)
    angle = 90.0
    if parts and ("deg" in parts[0] or "to " in parts[0]):
        head = parts.pop(0)
        dm = re.match(r"(-?[\d.]+)deg", head)
        if dm:
            angle = float(dm.group(1))
    colors = []
    for p in parts:
        cm = re.match(r"(rgba?\([^)]+\))", p)
        if cm:
            hexval, alpha = rgb_to_hex_alpha(cm.group(1))
            if hexval:
                colors.append(hexval)
    if len(colors) < 2:
        return None
    return {"angle": angle, "from": colors[0], "to": colors[-1]}


STYLE_PROPS = [
    "backgroundColor", "backgroundImage", "color", "opacity",
    "borderTopWidth", "borderTopColor", "borderTopStyle",
    "borderRadius", "boxShadow", "transform",
    "fontFamily", "fontSize", "fontWeight", "fontStyle",
    "textAlign", "textDecorationLine", "letterSpacing", "lineHeight",
    "zIndex",
]


def get_style(page, el):
    return el.evaluate(
        """(el, props) => {
            const cs = getComputedStyle(el);
            const out = {};
            for (const p of props) out[p] = cs[p];
            return out;
        }""",
        STYLE_PROPS,
    )


def build_paragraphs(page, el):
    """Split element text on <br> into paragraphs; each text node inherits
    the parent's computed style, each direct child element (span/b/i/...)
    is its own run styled with its own computed style."""
    data = el.evaluate(
        """(el, props) => {
            function styleOf(node) {
                const cs = getComputedStyle(node);
                const s = {}; for (const p of props) s[p] = cs[p];
                return s;
            }
            const parentStyle = styleOf(el);
            const raw = [];
            for (const node of el.childNodes) {
                if (node.nodeType === Node.TEXT_NODE) {
                    const collapsed = node.textContent.replace(/\\s+/g, ' ');
                    if (collapsed.trim().length) raw.push({text: collapsed, style: parentStyle});
                } else if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.tagName === 'BR') { raw.push({br: true}); continue; }
                    raw.push({text: node.innerText.replace(/\\s+/g, ' '), style: styleOf(node)});
                }
            }
            const paras = [[]];
            for (const r of raw) {
                if (r.br) { paras.push([]); continue; }
                paras[paras.length - 1].push(r);
            }
            const trimmed = paras.filter(p => p.length);
            for (const p of trimmed) {
                p[0].text = p[0].text.replace(/^\\s+/, '');
                p[p.length - 1].text = p[p.length - 1].text.replace(/\\s+$/, '');
            }
            return trimmed;
        }""",
        STYLE_PROPS,
    )
    base_style = get_style(page, el)
    return data, base_style


def resolve_src(src, html_dir):
    if not src or src.startswith(("http://", "https://", "data:")):
        return src
    if src.startswith("file:///"):
        src = src[8:]
    if os.path.isabs(src):
        return src
    return os.path.normpath(os.path.join(html_dir, src))


def extract_element(page, el, page_box, html_dir):
    kind = el.get_attribute("data-pdf")
    bbox = el.bounding_box()
    style = get_style(page, el)
    rel = {
        "x": round(bbox["x"] - page_box["x"], 2),
        "y": round(bbox["y"] - page_box["y"], 2),
        "w": round(bbox["width"], 2),
        "h": round(bbox["height"], 2),
    }
    bg_hex, bg_alpha = rgb_to_hex_alpha(style["backgroundColor"])
    border_hex, _ = rgb_to_hex_alpha(style["borderTopColor"])
    entry = {
        "kind": kind,
        "tag": el.evaluate("e => e.tagName"),
        **rel,
        "rotation": parse_rotation(style["transform"]),
        "opacity": float(style["opacity"] or 1),
        "fill": bg_hex,
        "fill_alpha": bg_alpha,
        "border_color": border_hex if parse_px(style["borderTopWidth"]) > 0 else None,
        "border_width": parse_px(style["borderTopWidth"]),
        "radius": parse_radius(style["borderRadius"], rel["w"], rel["h"]),
        "shadow": style["boxShadow"] not in (None, "none"),
        "gradient": parse_gradient(style["backgroundImage"]),
    }

    if kind == "text" or kind == "background":
        paragraphs, base_style = build_paragraphs(page, el)
        out_paras = []
        for para in paragraphs:
            runs = []
            for r in para:
                s = r["style"]
                c_hex, _ = rgb_to_hex_alpha(s["color"])
                runs.append({
                    "text": r["text"],
                    "color": c_hex or "000000",
                    "font_family": (s["fontFamily"] or "Arial").split(",")[0].strip().strip('"'),
                    "font_size": parse_px(s["fontSize"], 16) * 0.75,  # px -> pt
                    "bold": is_bold(s["fontWeight"]),
                    "italic": s["fontStyle"] == "italic",
                    "underline": "underline" in (s["textDecorationLine"] or ""),
                })
            out_paras.append({
                "align": base_style["textAlign"],
                "line_height": base_style["lineHeight"],
                "runs": runs,
            })
        entry["paragraphs"] = out_paras

    if kind == "image":
        entry["src"] = resolve_src(el.get_attribute("src"), html_dir)

    if kind == "table":
        rows = el.query_selector_all("tr")
        table_rows = []
        for tr in rows:
            cells = tr.query_selector_all("td, th")
            row = []
            for td in cells:
                cs = get_style(page, td)
                fill_hex, _ = rgb_to_hex_alpha(cs["backgroundColor"])
                txt_hex, _ = rgb_to_hex_alpha(cs["color"])
                row.append({
                    "text": td.inner_text(),
                    "fill": fill_hex,
                    "color": txt_hex or "000000",
                    "bold": is_bold(cs["fontWeight"]),
                    "align": cs["textAlign"],
                    "font_family": (cs["fontFamily"] or "Arial").split(",")[0].strip().strip('"'),
                    "font_size": parse_px(cs["fontSize"], 14) * 0.75,
                    "header": td.evaluate("e => e.tagName") == "TH",
                })
            table_rows.append(row)
        entry["rows"] = table_rows

    return entry


def main():
    html_path, out_json, images_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    images_dir = os.path.abspath(images_dir)
    html_dir = os.path.dirname(os.path.abspath(html_path))
    os.makedirs(images_dir, exist_ok=True)
    html_url = pathlib.Path(html_path).resolve().as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.goto(html_url)
        page.wait_for_timeout(150)  # let webfonts/layout settle

        # Element screenshots (for icon rasterization) auto-scroll their target
        # into view. bounding_box() reflects that scroll for any element measured
        # afterward, but page_box (captured once, up front, per page) does not
        # — the two drift apart, corrupting every position measured post-scroll.
        # Sizing the viewport to the full page height means no scroll ever happens.
        total_height = page.evaluate("document.documentElement.scrollHeight")
        page.set_viewport_size({"width": 1600, "height": total_height + 100})
        page.wait_for_timeout(50)

        pages = page.query_selector_all(".page")
        if not pages:
            print("ERROR: no elements with class='page' found", file=sys.stderr)
            sys.exit(1)

        out = []
        icon_counter = 0
        for pg in pages:
            page_box = pg.bounding_box()
            elements = pg.query_selector_all("[data-pdf]")
            entries = []
            for el in elements:
                entry = extract_element(page, el, page_box, html_dir)
                if entry["kind"] == "icon":
                    icon_counter += 1
                    fname = f"icon_{icon_counter}.png"
                    fpath = os.path.join(images_dir, fname)
                    el.screenshot(path=fpath, omit_background=True)
                    entry["src"] = fpath
                entries.append(entry)
            out.append({
                "width": round(page_box["width"], 2),
                "height": round(page_box["height"], 2),
                "elements": entries,
            })

        browser.close()

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Extracted {len(out)} page(s) -> {out_json}")


if __name__ == "__main__":
    main()
