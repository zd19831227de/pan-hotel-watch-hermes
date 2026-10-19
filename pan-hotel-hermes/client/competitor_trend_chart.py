from __future__ import annotations

import io
from typing import Any

from client.competitor_trend_model import build_panel_chart_data

_CHART_COLORS = ["#2563eb", "#dc2626", "#059669", "#9333ea", "#ea580c", "#0f766e"]
_PANELS = [
    ("today", "none"),
    ("today", "included"),
    ("tomorrow", "none"),
    ("tomorrow", "included"),
]


def _date_label_text(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {"today": "今日", "tomorrow": "明日", "今日": "今日", "明日": "明日"}
    return mapping.get(text, text or "日期")


# 2026-10-08 生产乱码修复：matplotlib 默认字体无中文字形，标题/店名会全部
# 渲染成豆腐块；渲染前必须把 CJK 字体候选放到 font.sans-serif 首位。
CJK_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "PingFang SC",
)


def _configure_matplotlib_cjk_font() -> None:
    import matplotlib

    preferred = [font for font in CJK_FONT_CANDIDATES]
    existing = list(matplotlib.rcParams.get("font.sans-serif") or [])
    matplotlib.rcParams["font.sans-serif"] = preferred + [f for f in existing if f not in preferred]
    matplotlib.rcParams["axes.unicode_minus"] = False


def _breakfast_title(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "none": "无早",
        "no_breakfast": "无早",
        "included": "含早",
        "with_breakfast": "含早",
        "breakfast": "含早",
    }
    return mapping.get(text, text or "价格")


def _short_label(series: dict[str, Any]) -> str:
    alias = str(series.get("hotel_alias") or series.get("hotel_name") or "酒店")
    if len(alias) > 14:
        alias = alias[:13] + "…"
    if series.get("hotel_role") == "own":
        alias = f"{alias}本店"
    return alias


def _panel_series(series_list: list[dict[str, Any]], date_label: str, breakfast_kind: str) -> list[dict[str, Any]]:
    matched = [
        series
        for series in series_list
        if isinstance(series, dict)
        and str(series.get("date_label") or "") == date_label
        and str(series.get("breakfast_kind") or "") == breakfast_kind
    ]
    return sorted(
        matched,
        key=lambda item: (item.get("hotel_role") != "own", item.get("sort_order", 0)),
    )


def _map_y(price: int, axis_min: float, axis_max: float, plot_top: int, plot_bottom: int) -> int:
    span = max(axis_max - axis_min, 1.0)
    plot_height = max(plot_bottom - plot_top, 1)
    return plot_bottom - int((price - axis_min) * plot_height / span)


def _map_x(index: int, count: int, plot_left: int, plot_right: int) -> int:
    plot_width = max(plot_right - plot_left, 1)
    if count <= 1:
        return plot_left + plot_width // 2
    return plot_left + int(index * plot_width / (count - 1))


def _build_chart_with_matplotlib(series_list: list[dict[str, Any]], *, days: int) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        _configure_matplotlib_cjk_font()
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, axes = plt.subplots(2, 2, figsize=(9.6, 8.4), dpi=110)
    fig.suptitle(
        f"可信 DOM 房价趋势（近{max(1, int(days or 3))}天，按读价时间）",
        fontsize=13,
        y=0.98,
    )
    has_any = False
    for index, (date_label, breakfast_kind) in enumerate(_PANELS):
        ax = axes[index // 2][index % 2]
        panel_series = _panel_series(series_list, date_label, breakfast_kind)[:6]
        panel_data = build_panel_chart_data(panel_series)
        x_labels = panel_data["x_labels"]
        lines = panel_data["lines"]
        if not lines:
            ax.set_title(f"{_date_label_text(date_label)}{_breakfast_title(breakfast_kind)}", fontsize=10)
            ax.text(0.5, 0.5, "暂无观测", ha="center", va="center", fontsize=9, transform=ax.transAxes)
            continue
        has_any = True
        ax.set_ylim(panel_data["axis_min"], panel_data["axis_max"])
        ax.set_xticks(range(len(x_labels)))
        step = max(1, len(x_labels) // 6)
        ax.set_xticklabels(
            [label if idx % step == 0 or idx == len(x_labels) - 1 else "" for idx, label in enumerate(x_labels)],
            rotation=35,
            ha="right",
            fontsize=7,
        )
        for color_index, line in enumerate(lines):
            series = line["series"]
            coords = line["coords"]
            if not coords:
                continue
            xs = [x for x, _ in coords]
            ys = [y for _, y in coords]
            ax.plot(
                xs,
                ys,
                marker="o",
                linewidth=1.8,
                markersize=4,
                color=_CHART_COLORS[color_index % len(_CHART_COLORS)],
                label=_short_label(series),
            )
        ax.set_title(f"{_date_label_text(date_label)}{_breakfast_title(breakfast_kind)}", fontsize=10)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, loc="upper right")

    if not has_any:
        plt.close(fig)
        return None

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _hex_color(value: str) -> tuple[int, int, int]:
    cleaned = value.lstrip("#")
    if len(cleaned) != 6:
        return (37, 99, 235)
    return tuple(int(cleaned[i : i + 2], 16) for i in (0, 2, 4))


def _build_chart_with_pillow(series_list: list[dict[str, Any]], *, days: int) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    width, height = 960, 840
    margin = 48
    panel_w = (width - margin * 3) // 2
    panel_h = (height - margin * 3 - 36) // 2
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("msyh.ttc", 18)
        label_font = ImageFont.truetype("msyh.ttc", 12)
        small_font = ImageFont.truetype("msyh.ttc", 10)
    except OSError:
        title_font = ImageFont.load_default()
        label_font = title_font
        small_font = title_font

    draw.text(
        (margin, 10),
        f"可信 DOM 房价趋势（近{max(1, int(days or 3))}天，按读价时间）",
        fill=(30, 30, 30),
        font=title_font,
    )
    has_any = False

    for index, (date_label, breakfast_kind) in enumerate(_PANELS):
        col = index % 2
        row = index // 2
        left = margin + col * (panel_w + margin)
        top = margin + 28 + row * (panel_h + margin)
        draw.rectangle([left, top, left + panel_w, top + panel_h], outline=(220, 220, 220), fill=(255, 255, 255))
        title = f"{_date_label_text(date_label)}{_breakfast_title(breakfast_kind)}"
        draw.text((left + 8, top + 6), title, fill=(50, 50, 50), font=label_font)

        plot_left = left + 36
        plot_top = top + 28
        plot_right = left + panel_w - 8
        plot_bottom = top + panel_h - 8
        draw.rectangle([plot_left, plot_top, plot_right, plot_bottom], outline=(235, 235, 235))

        panel_series = _panel_series(series_list, date_label, breakfast_kind)[:6]
        panel_data = build_panel_chart_data(panel_series)
        lines = panel_data["lines"]
        bucket_count = len(panel_data["axis_buckets"])
        if not lines or bucket_count == 0:
            draw.text((plot_left + 20, plot_top + 20), "暂无观测", fill=(120, 120, 120), font=small_font)
            continue

        has_any = True
        axis_min = float(panel_data["axis_min"])
        axis_max = float(panel_data["axis_max"])

        for color_index, line in enumerate(lines):
            color = _hex_color(_CHART_COLORS[color_index % len(_CHART_COLORS)])
            coords: list[tuple[int, int]] = []
            for x_index, price in line["coords"]:
                x = _map_x(x_index, bucket_count, plot_left, plot_right)
                y = _map_y(price, axis_min, axis_max, plot_top, plot_bottom)
                coords.append((x, y))
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=color)
            if len(coords) >= 2:
                draw.line(coords, fill=color, width=2)
            label = _short_label(line["series"])
            draw.text((plot_left + 4, plot_top + 4 + color_index * 12), label[:16], fill=color, font=small_font)

        draw.text(
            (plot_left, plot_bottom + 2),
            f"¥{int(axis_min)}",
            fill=(100, 100, 100),
            font=small_font,
        )
        draw.text(
            (plot_right - 48, plot_bottom + 2),
            f"¥{int(axis_max)}",
            fill=(100, 100, 100),
            font=small_font,
        )

    if not has_any:
        return None

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def is_min_comparison_series(series_list: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(series, dict) and str(series.get("breakfast_kind") or "") == "min_compare"
        for series in series_list
    )


def _date_label_order(value: str) -> int:
    return {"today": 0, "tomorrow": 1}.get(str(value or ""), 2)


def _min_comparison_panels(series_list: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """按入住日分组的（date_label, 该日序列）列表，今日在前。"""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for series in series_list:
        if not isinstance(series, dict):
            continue
        by_date.setdefault(str(series.get("date_label") or ""), []).append(series)
    panels: list[tuple[str, list[dict[str, Any]]]] = []
    for date_label in sorted(by_date, key=_date_label_order):
        series = sorted(
            by_date[date_label],
            key=lambda item: (str(item.get("hotel_role") or "") != "own", item.get("sort_order", 0)),
        )
        panels.append((date_label, series))
    return panels


def _build_min_comparison_chart_with_matplotlib(
    series_list: list[dict[str, Any]], *, days: int
) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        _configure_matplotlib_cjk_font()
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
    except ImportError:
        return None

    panels = _min_comparison_panels(series_list)
    panels = [(label, items[:8]) for label, items in panels if items]
    if not panels:
        return None
    fig_height = 5.6 if len(panels) == 1 else 5.6 * len(panels) / 2 + 0.8
    fig, axes = plt.subplots(
        len(panels) if len(panels) > 1 else 1,
        1,
        figsize=(9.6, fig_height),
        dpi=110,
        squeeze=False,
    )
    fig.suptitle("各时段最低价对比（不分早，按读价时间，单位：元）", fontsize=13, y=0.98)
    has_any = False
    for panel_index, (date_label, panel_series) in enumerate(panels):
        ax = axes[panel_index][0]
        panel_data = build_panel_chart_data(panel_series)
        x_labels = panel_data["x_labels"]
        lines = panel_data["lines"]
        if not lines:
            ax.set_title(f"{_date_label_text(date_label)}·各时段最低价", fontsize=10)
            ax.text(0.5, 0.5, "暂无观测", ha="center", va="center", fontsize=9, transform=ax.transAxes)
            continue
        has_any = True
        ax.set_ylim(panel_data["axis_min"], panel_data["axis_max"])
        # 个位数精度：纵轴只取整数刻度（运营明确要求房价精确到个位）。
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_xticks(range(len(x_labels)))
        step = max(1, len(x_labels) // 8)
        ax.set_xticklabels(
            [label if idx % step == 0 or idx == len(x_labels) - 1 else "" for idx, label in enumerate(x_labels)],
            rotation=35,
            ha="right",
            fontsize=7,
        )
        for color_index, line in enumerate(lines):
            series = line["series"]
            coords = line["coords"]
            if not coords:
                continue
            xs = [x for x, _ in coords]
            ys = [y for _, y in coords]
            is_own = str(series.get("hotel_role") or "") == "own"
            ax.plot(
                xs,
                ys,
                marker="o",
                linewidth=2.8 if is_own else 1.8,
                markersize=5 if is_own else 4,
                color="#dc2626" if is_own else _CHART_COLORS[(color_index + 1) % len(_CHART_COLORS)],
                label=_short_label(series),
            )
            for x, y in coords:
                ax.annotate(f"¥{y}", (x, y), textcoords="offset points", xytext=(0, 6 if is_own else 5), fontsize=6.5, ha="center")
        ax.set_title(f"{_date_label_text(date_label)}·各时段最低价", fontsize=10)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, loc="upper right")

    if not has_any:
        plt.close(fig)
        return None
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _build_min_comparison_chart_with_pillow(
    series_list: list[dict[str, Any]], *, days: int
) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    panels = _min_comparison_panels(series_list)
    panels = [(label, items[:8]) for label, items in panels if items]
    if not panels:
        return None

    width = 960
    panel_h = 360
    margin = 48
    height = margin + 36 + panel_h * len(panels) + margin // 2
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("msyh.ttc", 18)
        label_font = ImageFont.truetype("msyh.ttc", 12)
        small_font = ImageFont.truetype("msyh.ttc", 10)
    except OSError:
        title_font = ImageFont.load_default()
        label_font = title_font
        small_font = title_font

    draw.text(
        (margin, 10),
        "各时段最低价对比（不分早，按读价时间，单位：元）",
        fill=(30, 30, 30),
        font=title_font,
    )
    has_any = False
    for index, (date_label, panel_series) in enumerate(panels):
        left = margin
        top = margin + 30 + index * (panel_h + margin // 2)
        draw.rectangle([left, top, left + width - margin * 2, top + panel_h], outline=(220, 220, 220), fill=(255, 255, 255))
        draw.text((left + 8, top + 6), f"{_date_label_text(date_label)}·各时段最低价", fill=(50, 50, 50), font=label_font)
        plot_left = left + 44
        plot_top = top + 30
        plot_right = left + width - margin * 2 - 10
        plot_bottom = top + panel_h - 24
        draw.rectangle([plot_left, plot_top, plot_right, plot_bottom], outline=(235, 235, 235))

        panel_data = build_panel_chart_data(panel_series)
        lines = panel_data["lines"]
        bucket_count = len(panel_data["axis_buckets"])
        if not lines or bucket_count == 0:
            draw.text((plot_left + 20, plot_top + 20), "暂无观测", fill=(120, 120, 120), font=small_font)
            continue
        has_any = True
        axis_min = float(panel_data["axis_min"])
        axis_max = float(panel_data["axis_max"])
        # 个位精度：上下轴标注与逐点数值都取整到元。
        draw.text((plot_left, plot_bottom + 2), f"¥{int(axis_min)}", fill=(100, 100, 100), font=small_font)
        draw.text((plot_right - 48, plot_bottom + 2), f"¥{int(axis_max)}", fill=(100, 100, 100), font=small_font)
        x_labels = panel_data["x_labels"]
        step = max(1, bucket_count // 8)
        for idx, label in enumerate(x_labels):
            if idx % step == 0 or idx == bucket_count - 1:
                x = _map_x(idx, bucket_count, plot_left, plot_right)
                draw.text((x - 16, plot_bottom + 2), label[-5:], fill=(100, 100, 100), font=small_font)
        for color_index, line in enumerate(lines):
            series = line["series"]
            is_own = str(series.get("hotel_role") or "") == "own"
            color = (220, 38, 38) if is_own else _hex_color(_CHART_COLORS[(color_index + 1) % len(_CHART_COLORS)])
            coords: list[tuple[int, int]] = []
            for x_index, price in line["coords"]:
                x = _map_x(x_index, bucket_count, plot_left, plot_right)
                y = _map_y(price, axis_min, axis_max, plot_top, plot_bottom)
                coords.append((x, y))
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=color)
                draw.text((x - 12, y - 16), f"¥{int(price)}", fill=color, font=small_font)
            if len(coords) >= 2:
                draw.line(coords, fill=color, width=3 if is_own else 2)
            draw.text((plot_left + 4, plot_top + 4 + color_index * 12), _short_label(series)[:16], fill=color, font=small_font)

    if not has_any:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def try_build_competitor_trend_chart_png(
    series_list: list[dict[str, Any]],
    *,
    days: int = 3,
) -> bytes | None:
    """生成与控制台可信趋势图一致的时间序列折线图。

    序列携带 min_compare 标记（metric=min 对比模式）时改画「各时段最低价
    对比」单店一线视图：本店置顶加粗、逐点标注到元、纵轴整数刻度。
    """
    if is_min_comparison_series(series_list):
        try:
            matplotlib_png = _build_min_comparison_chart_with_matplotlib(series_list, days=days)
            if matplotlib_png:
                return matplotlib_png
        except Exception:
            pass
        return _build_min_comparison_chart_with_pillow(series_list, days=days)
    try:
        matplotlib_png = _build_chart_with_matplotlib(series_list, days=days)
        if matplotlib_png:
            return matplotlib_png
    except Exception:
        pass
    return _build_chart_with_pillow(series_list, days=days)
