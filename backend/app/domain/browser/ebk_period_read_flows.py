"""三平台 EBK 周/月维度按需读取（只读页面交互 + 结构解析）。

页面控件与结构均来自 2026-09-07 三平台真实页面实测：
- 携程经营：预订销售页 预订/在店/离店 三组各有 实时/按日/按周/按月/按季/自定义
  按钮组（a.btn）；周/月视图对比口径为「去年同期/去年同比」，另有「本周/本月间夜量」
  汇总行与「2026年9月」式月周期标记。
- 携程排名：竞争排名页 销售排名/流量排名 各有 实时/昨日/上周/上月/自定义 单选钮
  （label.he-trip-ui-radio-button-wrapper）；概览行随周期变为「过去一周/过去一月…」。
- 美团经营：数据中心「交易分析」tab 有 今日实时/按日/按周/按月/自定义 按钮组
  （button.mtd-btn，选中态 mtd-btn-primary）；指标卡为 值/较上期/较去年/同行排名。
- 美团排名：数据中心「同行分析」tab 有 今日实时/昨日/近7天/近30天 单选钮
  （label.mtd-radio-button，选中态 mtd-radio-button-checked），四榜表格复用既有解析。
- 飞猪经营：诊断报告页有 日报/周报 切换（周报=上周概览 DOM 表格，含
  上周/上上周/环比/同行均值/同行排名 行）；经营数据页 在店/预订两组各有
  今日/昨日/近7天/近30天 单选钮（label.ant-radio-button-wrapper）。
- 飞猪排名：周报各区块的「同行排名」行与「同行榜单排名」表（需关注同行，未关注时为空）。

所有读取只切换视图后读可见文本/DOM 表格并恢复默认视图，不做任何写操作。
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# 注意导入顺序：ctrip_ebooking_parsers 尾部会回导 dataset_parsers，
# 先导入 parsers 再导入 dataset_parsers 才能避开循环初始化（与 read_flows 一致）。
from app.domain.browser.ctrip_ebooking_parsers import _clean_dataset_lines
from app.domain.browser.ctrip_ebooking_dataset_parsers import (
    _extract_competition_list_dataset_metrics,
)
from app.domain.browser.ctrip_ebooking_session_helpers import (
    _classify_page_state,
    _dismiss_ebk_read_blocking_popups,
    _safe_visible_text,
    _settle_page,
)
from app.domain.browser.fliggy_ebooking import (
    _classify_fliggy_page_state,
    _click_fliggy_visible_text,
    _settle_page as _settle_fliggy_page,
)
from app.domain.browser.meituan_ebooking import (
    _classify_meituan_page_state,
    _clean_meituan_text_lines,
    _read_meituan_peer_rankings,
    _select_meituan_data_center_tab,
    _settle_page as _settle_meituan_page,
)

_CTPIP_BENEFICIAL_URL = (
    "https://ebooking.ctrip.com/datacenter/inland/businessreport/"
    "beneficialdata?microJump=true"
)
_CTIRP_OUTLINE_URL = (
    "https://ebooking.ctrip.com/datacenter/inland/businessreport/"
    "outline?microJump=true"
)
_CTRIP_COMPETITION_LIST_URL = (
    "https://ebooking.ctrip.com/ebkgrowth/datacenter/competition/"
    "competitionlist?microJump=true"
)
_MEITUAN_EBK_DATA_CENTER_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fdata-center%2Findex.html"
)
_FLIGGY_EBK_DIAGNOSE_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm"
    "#/ebk-sycm/dataCenter/diagnoseReport"
)
_FLIGGY_EBK_OPERATING_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm"
    "#/ebk-sycm/dataCenter/operatingData"
)

_CTIRP_PERIOD_BUTTON_LABELS = {"week": "按周", "month": "按月"}
_CTRIP_RANKING_RADIO_LABELS = {"day": "昨日", "week": "上周", "month": "上月"}
_MEITUAN_TRADE_BUTTON_LABELS = {"day": "按日", "week": "按周", "month": "按月"}
_MEITUAN_RANKING_RADIO_LABELS = {"day": "昨日", "week": "近7天", "month": "近30天"}
_FLIGGY_OPERATING_RADIO_LABELS = {"day": "昨日", "week": "近7天", "month": "近30天"}
# 金字塔数据报告（toolcenter/cpc/dataReport，2026-09-16 实测）：今天/过去7天/
# 过去30天/自定义 按钮组；自定义弹出 bootstrap daterangepicker（月/年下拉 +
# td 日格 + applyBtn），区间文本格式「2026年9月7日 - 2026年9月13日」。
_CTRIP_PYRAMID_REPORT_URL = (
    "https://ebooking.ctrip.com/toolcenter/cpc/dataReport"
)
_CTRIP_PYRAMID_PRESET_BUTTONS = {"day": "今天", "week": "过去7天", "month": "过去30天"}
_CTRIP_PYRAMID_DEFAULT_PRESET = "过去7天"

_CTIRP_BENEFICIAL_GROUP_METRICS: dict[str, tuple[str, ...]] = {
    "预订": ("预订订单数", "间夜量", "销售额"),
    "在店": ("间夜量", "紧张度", "出租率"),
    "离店": ("间夜量", "销售额", "成交率", "平均卖价"),
}
_CTRIP_GROUP_ORDER = ("预订", "在店", "离店")
_CTRIP_MONTH_PERIOD_RE = re.compile(r"^\d{4}年\d{1,2}月$")
_CTRIP_COMPARISON_RE = re.compile(r"^(去年|上周)?\s*(同期|同比)\s*[:：]?\s*(.+)$")

_MEITUAN_TRADE_METRIC_LABELS = (
    "销售间夜",
    "销售额",
    "支付订单数",
    "入住间夜",
    "入住金额",
    "平均房价",
    "成交率",
    "支付转化率",
    "销售均价",
    "满房率",
    "引流价",
    "曝光量",
    "浏览人数",
    "访问人数",
)

_FLIGGY_WEEKLY_TABLE_MATCHERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("在店数据", ("在店间夜", "房费收入")),
    ("预订数据", ("预订间夜", "预订收入金额")),
    ("客人点评得分", ("点评得分",)),
    ("MCI酒店竞争力指数", ("总分值",)),
    ("APP流量转化", ("列表页曝光人数",)),
)
_FLIGGY_WEEKLY_ROW_LABELS = ("上周", "上上周", "环比", "同行均值", "同行排名")
_FLIGGY_WEEKLY_PERIOD_RE = re.compile(r"(\d{2}/\d{2}-\d{2}/\d{2})")
_FLIGGY_OPERATING_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("在店数据", ("在店间夜", "房费收入", "间夜单价")),
    ("预订数据", ("预订间夜", "预订收入金额", "预订订单")),
)

_VISIBLE_TABLES_JS = (
    "() => Array.from(document.querySelectorAll('table'))"
    ".filter(t => t.getClientRects().length > 0)"
    ".map(t => ({"
    "header: Array.from(t.querySelectorAll('th'))"
    ".map(th => (th.innerText || '').trim()),"
    "rows: Array.from(t.querySelectorAll('tr'))"
    ".map(tr => Array.from(t.querySelectorAll('td'))"
    ".map(td => (td.innerText || '').trim()))"
    ".filter(cells => cells.length > 0)"
    "}))"
)


# ---------------------------------------------------------------------------
# 携程：预订销售页 周/月 经营数据
# ---------------------------------------------------------------------------


async def read_ctrip_business_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return await _read_ctrip_business_custom_range(page, start, end)
    if period == "day":
        return await _read_ctrip_outline_yesterday(page)
    label = _CTIRP_PERIOD_BUTTON_LABELS.get(period, "按周")
    await page.goto(_CTPIP_BENEFICIAL_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    clicked = 0
    buttons = page.locator("a.btn", has_text=re.compile(f"^{label}$"))
    try:
        count = await buttons.count()
        for index in range(count):
            await buttons.nth(index).click(timeout=4_000, force=True)
            clicked += 1
            await page.wait_for_timeout(1_200)
    except Exception:
        pass
    await page.wait_for_timeout(2_500)
    # 选中态硬校验：页面默认「按日」，点击被吞或 SPA 不响应时仍停留在按日，
    # 按日数据不得被标成周/月口径落库（未选中即 not_available）。
    selected_state = await _ctrip_period_button_selected(page, label)
    body_text = await _safe_visible_text(page)
    sections = _parse_ctrip_beneficial_period_sections(body_text, period=period)
    # 恢复默认「按日」视图，避免影响后续常规读取。
    with suppress(Exception):
        restore = page.locator("a.btn", has_text=re.compile("^按日$"))
        for index in range(min(clicked, await restore.count())):
            await restore.nth(index).click(timeout=4_000, force=True)
        await page.wait_for_timeout(1_000)

    if clicked == 0:
        return {
            "status": "not_available",
            "caliber": f"预订销售·{label}（自然{'周' if period == 'week' else '月'}）",
            "summary": f"携程预订销售页未找到「{label}」按钮，无法读取该维度。",
            "sections": {},
            "evidence": {"page_url": page.url, "selected_control": None},
        }
    if not selected_state:
        return {
            "status": "not_available",
            "caliber": (
                f"预订销售·{label}（自然{'周' if period == 'week' else '月'}，较去年同期）"
            ),
            "summary": (
                f"携程预订销售页「{label}」按钮未切换到选中态，无法确认口径，请人工复核。"
            ),
            "sections": sections,
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "clicked_buttons": clicked,
                "selected_state": False,
            },
        }
    has_metrics = any(
        key != "_summary_lines" and key != "_period_label"
        for group in sections.values()
        for key in group
    )
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": (
            f"预订销售·{label}（自然{'周' if period == 'week' else '月'}，较去年同期）"
        ),
        "summary": (
            f"已读取携程预订/在店/离店{label}经营指标（页面对比口径为去年同期）。"
            if has_metrics
            else f"携程预订销售页{label}视图未解析到指标，请人工复核页面。"
        ),
        "sections": sections,
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "clicked_buttons": clicked,
            "selected_state": True,
        },
    }


async def _ctrip_period_button_selected(page: Page, label: str) -> bool:
    """按周/按月 a.btn 的选中态校验：按钮需带 active/selected/current/primary
    类或 aria-pressed/aria-checked=true；判不出选中态按未选中处理（fail-closed）。"""
    with suppress(Exception):
        checked = await page.evaluate(
            """(label) => Array.from(document.querySelectorAll('a.btn'))
            .filter(node => ((node.innerText || '').trim()) === label)
            .map(node => ({
                className: String(node.className || ''),
                pressed: node.getAttribute('aria-pressed')
                    || node.getAttribute('aria-checked') || '',
            }))""",
            label,
        )
        if isinstance(checked, list) and checked:
            return any(
                re.search(
                    r"active|selected|current|primary",
                    str(item.get("className") or ""),
                    re.I,
                )
                or str(item.get("pressed") or "").lower() == "true"
                for item in checked
                if isinstance(item, dict)
            )
    return False


async def _read_ctrip_outline_yesterday(page: Page) -> dict[str, Any]:
    """携程经营概览页「昨日数据」块：昨日完整经营口径 + 上周同期 + 竞争圈排名。

    页面该区块由日报视图直接渲染（2026-09-07 实测：「昨日数据 2026/09/06的数据」
    下含 离店销售额/离店间夜量/成交率/平均卖价），无需点击任何周期控件。
    """
    await page.goto(_CTIRP_OUTLINE_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)
    result = _parse_ctrip_outline_yesterday_section(body_text)
    metrics = result["metrics"]
    return {
        "status": "ok" if metrics else "not_available",
        "caliber": "经营概览·昨日数据（含上周同期与竞争圈排名）",
        "summary": (
            "已读取携程经营概览昨日数据（离店销售额/间夜量/成交率/平均卖价）。"
            if metrics
            else "携程经营概览页未解析到「昨日数据」区块，请人工复核页面。"
        ),
        "sections": {"昨日数据": metrics},
        "data_period_label": result["data_period_label"],
        "evidence": {"page_url": page.url, "selected_control": "日报(默认)"},
    }


def _parse_ctrip_outline_yesterday_section(body_text: str) -> dict[str, Any]:
    """解析经营概览页「昨日数据」块。

    行序（清理后）：昨日数据 2026/09/06的数据 → 指标标签 → 值 → 上周同期 → 值 →
    竞争圈排名 → N / M →（下一指标）…，直到「流量概览」等后续区块。
    值中的渲染空格（如「54 .39%」）按数字口径折叠。
    """
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("昨日数据")),
        None,
    )
    if start is None:
        return {"data_period_label": None, "metrics": {}}
    period_label = lines[start][len("昨日数据") :].replace("的数据", "").strip() or None
    metric_labels = ("离店销售额", "离店间夜量", "成交率", "平均卖价")
    stop_markers = ("流量概览", "实时数据", "收益概览")
    metrics: dict[str, dict[str, Any]] = {}
    cursor = start + 1
    current: dict[str, Any] | None = None
    pending_key: str | None = None
    while cursor < len(lines):
        line = lines[cursor]
        if line in metric_labels:
            if current:
                metrics[current["label"]] = current
            current = {"label": line, "value": None, "上周同期": None, "竞争圈排名": None}
            pending_key = "value"
            cursor += 1
            continue
        if line in stop_markers or line.startswith("查看更多"):
            break
        if current is not None:
            if line == "上周同期":
                pending_key = "上周同期"
            elif line == "竞争圈排名":
                pending_key = "竞争圈排名"
            elif pending_key and current.get(pending_key) is None:
                current[pending_key] = _normalize_day_metric_value(line)
        cursor += 1
    if current:
        metrics[current["label"]] = current
    return {"data_period_label": period_label, "metrics": metrics}


def _normalize_day_metric_value(value: str) -> str:
    """折叠数字口径值里的渲染空格（「54 .39%」→「54.39%」），其余原样返回。"""
    text = str(value or "").strip()
    if re.fullmatch(r"[\d,\s./%元万-]+", text):
        return re.sub(r"\s+", "", text)
    return text


def _parse_ctrip_beneficial_period_sections(
    body_text: str, *, period: str
) -> dict[str, dict[str, Any]]:
    """解析预订销售页 周/月 视图的三组指标卡。

    每组结构（清理后行序列）：组名 → 按钮序列行 →（月视图：周期标记行）→
    指标标签 → 值 → 「去年/上周 同期|同比」对比行 → … → 「本周/本月…」汇总行 → 下载数据。
    """
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    headers: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        if line in _CTRIP_GROUP_ORDER and index not in {h[1] for h in headers}:
            # 组头后紧跟按钮序列（真实页面每行一个：实时/按日/按周/按月/按季/自定义），
            # 两种形态都认：合并行或分行按钮。
            window = " ".join(lines[index + 1 : index + 7])
            if "按周" in window and "按月" in window:
                headers.append((line, index))
    sections: dict[str, dict[str, Any]] = {}
    for position, (group, start) in enumerate(headers):
        end = headers[position + 1][1] if position + 1 < len(headers) else len(lines)
        section = lines[start + 1 : end]
        metrics: dict[str, Any] = {}
        summary_lines: list[str] = []
        period_label: str | None = None
        labels = _CTIRP_BENEFICIAL_GROUP_METRICS[group]
        cursor = 0
        while cursor < len(section):
            line = section[cursor]
            if line in labels:
                metric: dict[str, Any] = {"value": None, "comparison": {}}
                cursor += 1
                if cursor < len(section):
                    metric["value"] = section[cursor]
                    cursor += 1
                while cursor < len(section):
                    candidate = section[cursor]
                    if candidate in labels or candidate == "下载数据":
                        break
                    match = _CTRIP_COMPARISON_RE.match(
                        re.sub(r"\s+", " ", candidate).strip()
                    )
                    if match:
                        prefix, kind, value = match.groups()
                        metric["comparison"][f"{prefix or ''}{kind}"] = value.strip()
                        cursor += 1
                        continue
                    if candidate.startswith(("本周", "本月")):
                        summary_lines.append(candidate)
                    cursor += 1
                metrics[line] = metric
                continue
            if _CTRIP_MONTH_PERIOD_RE.match(line):
                period_label = line
            cursor += 1
        if summary_lines:
            metrics["_summary_lines"] = summary_lines
        if period_label:
            metrics["_period_label"] = period_label
        sections[group] = metrics
    return sections


# ---------------------------------------------------------------------------
# 携程：竞争排名页 周/月 排名数据
# ---------------------------------------------------------------------------


async def read_ctrip_ranking_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return await _read_ctrip_ranking_custom_range(page, start, end)
    label = _CTRIP_RANKING_RADIO_LABELS.get(period, "上周")
    await page.goto(
        _CTRIP_COMPETITION_LIST_URL, wait_until="domcontentloaded", timeout=45_000
    )
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    clicked = 0
    radios = page.locator(
        "label.he-trip-ui-radio-button-wrapper",
        has_text=re.compile(f"^{label}$"),
    )
    try:
        count = await radios.count()
        for index in range(count):
            await radios.nth(index).click(timeout=4_000, force=True)
            clicked += 1
        await page.wait_for_timeout(3_500)
    except Exception:
        pass
    # 选中态校验：页面默认选中「上周」，若点「上月」失败会静默解析到上周数据却标成上月口径。
    checked = await page.evaluate(
        "() => Array.from(document.querySelectorAll("
        "'.he-trip-ui-radio-button-wrapper-checked'))"
        ".map(node => (node.innerText || '').trim())"
    )
    selected_state = isinstance(checked, list) and label in [str(c) for c in checked]
    body_text = await _safe_visible_text(page)
    result = _parse_ctrip_competition_rankings(body_text)
    if not selected_state:
        return {
            "status": "not_available",
            "caliber": f"竞争排名·{label}（页面四榜口径）",
            "summary": (
                f"携程竞争排名页「{label}」单选钮未切换到选中态，无法确认口径，请人工复核。"
            ),
            "sections": {"rankings": result["rankings"], "overview": result["overview"]},
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "clicked_radios": clicked,
                "selected_state": False,
            },
        }
    return {
        "status": "ok" if result["rankings"] else "not_available",
        "caliber": f"竞争排名·{label}（页面四榜口径）",
        "summary": (
            f"已读取携程竞争圈四榜{label}排名（我的酒店各榜名次）。"
            if result["rankings"]
            else f"携程竞争排名页{label}视图未解析到「我的酒店」排名行。"
        ),
        "sections": {"rankings": result["rankings"], "overview": result["overview"]},
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "clicked_radios": clicked,
            "selected_state": True,
        },
    }


def _parse_ctrip_competition_rankings(body_text: str) -> dict[str, Any]:
    lines = _clean_dataset_lines(body_text)
    overview = next(
        (ln for ln in lines if ln.startswith(("过去一周", "过去一月"))),
        None,
    )
    metrics = _extract_competition_list_dataset_metrics(lines)
    rankings: dict[str, str] = {}
    for metric in metrics.values():
        label = str(metric.get("label") or "")
        column = label.split(" ", 1)[1] if " " in label else label
        value = str(metric.get("value") or "")
        if column and value:
            rankings[column] = value
    return {"overview": overview, "rankings": rankings}


# ---------------------------------------------------------------------------
# 携程：流量数据页 昨天/过去7天/过去30天 + 自定义区间
# ---------------------------------------------------------------------------

_CTRIP_FLOWDATA_URL = (
    "https://ebooking.ctrip.com/datacenter/inland/businessreport/"
    "flowdata?microJump=true"
)
# 流量数据页时间 tabs（2026-09-16 实测）：a.btn.group-tabs，选中态 class 含
# active；he-click 锚点区分平台 tabs（…_ctrip/_qunar）与时间 tabs，按锚点点
# 击不会误切平台。概况区与流量来源区各一组同锚点 tabs，全部点击。
_CTRIP_FLOW_PRESET_TABS = {
    "day": ("昨天", "datacenter_report_flowdata_yes"),
    "week": ("过去7天", "datacenter_report_flowdata_week"),
    "month": ("过去30天", "datacenter_report_flowdata_mon"),
}
_CTRIP_FLOW_CUSTOM_HECLICK = "datacenter_report_flowdata_custom"
_CTRIP_FLOW_DEFAULT_HECLICK = "datacenter_report_flowdata_yes"
_CTRIP_FLOW_SECTION_LABELS = (
    "访客量",
    "转化率",
    "曝光转化率",
    "下单转化率",
    "成交转化率",
)


def _parse_ctrip_flow_section(body_text: str) -> dict[str, dict[str, Any]]:
    """解析流量数据页指标卡：复用快照链路的行解析，输出中文标签键。

    访客量/转化率取「流量数据概况」卡（含较上周同期与竞争圈平均），
    曝光/下单/成交转化率取「我的酒店 vs 竞争圈平均」漏斗列。
    """
    from app.domain.browser.ctrip_ebooking_dataset_parsers import (
        _extract_flow_data_dataset_metrics,
    )

    lines = [ln.strip() for ln in str(body_text or "").splitlines() if ln.strip()]
    metrics = _extract_flow_data_dataset_metrics(lines)
    section: dict[str, dict[str, Any]] = {}
    for metric in metrics.values():
        if not isinstance(metric, dict):
            continue
        label = str(metric.get("label") or "").strip()
        if label not in _CTRIP_FLOW_SECTION_LABELS or not metric.get("value"):
            continue
        entry: dict[str, Any] = {"value": str(metric["value"])}
        if metric.get("change"):
            entry["change"] = str(metric["change"])
        if metric.get("competition_average"):
            entry["competition_average"] = str(metric["competition_average"])
        section[label] = entry
    return {"流量": section}


async def _ctrip_flow_tab_selected(page: Page, he_click: str) -> bool:
    """时间 tab 选中态校验：目标锚点节点需带 active 类（fail-closed）。"""
    with suppress(Exception):
        states = await page.evaluate(
            "(selector) => Array.from(document.querySelectorAll(selector))"
            ".map(node => String(node.className || ''))",
            f"a[he-click='{he_click}']",
        )
        if isinstance(states, list) and states:
            return any("active" in str(item) for item in states)
    return False


async def read_ctrip_flow_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return await _read_ctrip_flow_custom_range(page, start, end)
    label, he_click = _CTRIP_FLOW_PRESET_TABS.get(period, _CTRIP_FLOW_PRESET_TABS["week"])
    await page.goto(_CTRIP_FLOWDATA_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    clicked = 0
    tabs = page.locator(f"a[he-click='{he_click}']")
    try:
        count = await tabs.count()
        for index in range(count):
            await tabs.nth(index).click(timeout=4_000, force=True)
            clicked += 1
            await page.wait_for_timeout(800)
    except Exception:
        pass
    await page.wait_for_timeout(2_500)
    selected_state = await _ctrip_flow_tab_selected(page, he_click)
    section = _parse_ctrip_flow_section(await _safe_visible_text(page))
    # 恢复默认「昨天」视图，避免影响后续常规读取。
    with suppress(Exception):
        restore = page.locator(f"a[he-click='{_CTRIP_FLOW_DEFAULT_HECLICK}']")
        for index in range(min(clicked, await restore.count())):
            await restore.nth(index).click(timeout=4_000, force=True)
        await page.wait_for_timeout(800)

    if clicked == 0:
        return {
            "status": "not_available",
            "caliber": f"流量数据·{label}",
            "summary": f"携程流量数据页未找到「{label}」按钮，无法读取该维度。",
            "sections": {},
            "evidence": {"page_url": page.url, "selected_control": None},
        }
    if not selected_state:
        return {
            "status": "not_available",
            "caliber": f"流量数据·{label}",
            "summary": (
                f"携程流量数据页「{label}」按钮未切换到选中态，无法确认口径，请人工复核。"
            ),
            "sections": section,
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "clicked_tabs": clicked,
                "selected_state": False,
            },
        }
    has_metrics = bool(section["流量"])
    return {
        "status": "ok" if has_metrics else "not_available",
        # 对比口径随视图变化（昨天=较上周同期，过去7天/30天=较去年同期），
        # caliber 不写死具体对比方式，由指标行自带的 change 标注。
        "caliber": f"流量数据·{label}（含对比与竞争圈平均）",
        "summary": (
            f"已读取携程流量数据{label}指标（访客量/转化率/漏斗三段转化率）。"
            if has_metrics
            else f"携程流量数据页{label}视图未解析到指标，请人工复核页面。"
        ),
        "sections": section,
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "clicked_tabs": clicked,
            "selected_state": True,
        },
    }


async def _read_ctrip_flow_custom_range(
    page: Page, date_from: str, date_to: str
) -> dict[str, Any]:
    target_text = f"{date_from} ~ {date_to}"
    await page.goto(_CTRIP_FLOWDATA_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    clicked = 0
    tabs = page.locator(f"a[he-click='{_CTRIP_FLOW_CUSTOM_HECLICK}']")
    try:
        count = await tabs.count()
        for index in range(count):
            await tabs.nth(index).click(timeout=4_000, force=True)
            clicked += 1
            await page.wait_for_timeout(600)
    except Exception:
        pass
    await page.wait_for_timeout(1_500)
    inputs = page.locator("input.form-control.date-input")
    input_count = await inputs.count()
    if clicked == 0 or input_count == 0:
        return _range_not_available(
            "携程流量数据",
            date_from,
            date_to,
            "携程流量数据页未找到「自定义」按钮或区间输入框，无法读取自定义区间。",
        )
    try:
        for index in range(input_count):
            field = inputs.nth(index)
            await field.fill("")
            await field.type(target_text, delay=25)
            await field.press("Enter")
            await page.wait_for_timeout(600)
    except Exception:
        pass
    await page.wait_for_timeout(3_000)
    values: list[str] = []
    try:
        for index in range(input_count):
            values.append(str(await inputs.nth(index).input_value()))
    except Exception:
        pass
    section = _parse_ctrip_flow_section(await _safe_visible_text(page))
    # 恢复默认「昨天」视图。
    with suppress(Exception):
        restore = page.locator(f"a[he-click='{_CTRIP_FLOW_DEFAULT_HECLICK}']")
        for index in range(min(clicked, await restore.count())):
            await restore.nth(index).click(timeout=4_000, force=True)
        await page.wait_for_timeout(1_000)
    # values 读空或回读不一致（DOM 重渲染/输入未生效）视同未生效：不得把默认
    # 视图数据当自定义区间 ok 返回。
    if not values or any(value != target_text for value in values):
        return _range_not_available(
            "携程流量数据",
            date_from,
            date_to,
            (
                f"携程流量数据页自定义区间未生效（当前显示 {values[:1]}），"
                "请人工复核页面后重试。"
            ),
        )
    has_metrics = bool(section["流量"])
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": f"流量数据·自定义 {date_from}~{date_to}",
        "summary": (
            f"已读取携程流量数据自定义区间（{target_text}）指标。"
            if has_metrics
            else f"携程流量数据页自定义区间视图未解析到指标，请人工复核页面。"
        ),
        "sections": section,
        "evidence": {
            "page_url": page.url,
            "selected_control": "自定义",
            "range_inputs": values,
        },
    }


# ---------------------------------------------------------------------------
# 美团：交易分析 周/月 经营数据
# ---------------------------------------------------------------------------


async def read_meituan_business_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return await _read_meituan_business_custom_range(page, start, end)
    label = _MEITUAN_TRADE_BUTTON_LABELS.get(period, "按周")
    await page.goto(
        _MEITUAN_EBK_DATA_CENTER_URL, wait_until="domcontentloaded", timeout=45_000
    )
    await _settle_meituan_page(page)
    await page.wait_for_timeout(4_000)
    body_text = await _meituan_visible_text(page)
    page_state = _classify_meituan_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("meituan", page.url, page_state)
    await _select_meituan_data_center_tab(page, "交易分析")
    await page.wait_for_timeout(3_500)

    frame = await _find_meituan_period_frame(page, must_have=(label,))
    if frame is None:
        return {
            "status": "not_available",
            "caliber": f"交易分析·{label}",
            "summary": f"美团交易分析页未找到「{label}」按钮，无法读取该维度。",
            "sections": {},
            "evidence": {"page_url": page.url, "selected_control": None},
        }
    clicked = False
    try:
        button = frame.locator("button", has_text=re.compile(f"^{label}$")).first
        if await button.count() > 0:
            await button.click(timeout=4_000, force=True)
            clicked = True
            await page.wait_for_timeout(4_000)
    except Exception:
        pass
    if not clicked:
        return {
            "status": "not_available",
            "caliber": f"交易分析·{label}",
            "summary": f"美团交易分析页「{label}」按钮点击失败。",
            "sections": {},
            "evidence": {"page_url": page.url, "selected_control": None},
        }
    checked = await frame.evaluate(
        "() => Array.from(document.querySelectorAll('.mtd-btn-primary'))"
        ".map(node => (node.innerText || '').trim())"
    )
    selected_state = isinstance(checked, list) and label in [str(c) for c in checked]
    text = await _meituan_frame_body(frame)
    metrics = _parse_meituan_trade_period_metrics(text)
    # day 维度提取页面数据日期参与业务日期判定：美团按日口径页面每日 9 点
    # 更新，9 点前仍展示前前日数据；用页面真实数据日期落库，避免前前日
    # 数据被 captured-1 错标成昨日业务日期。
    meituan_day_data_label = None
    if period == "day":
        meituan_day_data_label = _extract_meituan_day_data_label(text)
    # 恢复默认「按日」视图。
    with suppress(Exception):
        restore = frame.locator("button", has_text=re.compile("^按日$")).first
        if await restore.count() > 0:
            await restore.click(timeout=3_000, force=True)
            await page.wait_for_timeout(1_500)
    if not selected_state:
        # 选中态校验：页面默认「按日」，若点「按周/按月」失败会静默解析到按日数据却标错口径。
        return {
            "status": "not_available",
            "caliber": _meituan_trade_caliber(label, period),
            "summary": f"美团交易分析页「{label}」按钮未切换到选中态，无法确认口径，请人工复核。",
            "sections": {"经营表现": metrics},
            "data_period_label": meituan_day_data_label,
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "selected_state": False,
            },
        }
    return {
        "status": "ok" if metrics else "not_available",
        "caliber": _meituan_trade_caliber(label, period),
        "summary": (
            f"已读取美团交易分析{label}经营指标（含较上期/较去年与同行排名）。"
            if metrics
            else f"美团交易分析{label}视图未解析到指标，请人工复核页面。"
        ),
        "sections": {"经营表现": metrics},
        "data_period_label": meituan_day_data_label,
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "selected_state": True,
        },
    }


def _extract_meituan_day_data_label(body_text: str) -> str | None:
    """美团交易分析按日视图页面数据日期：优先带「更新/数据/日期」关键字的行，
    回退到首个日期串。返回原始匹配文本（service 侧再解析成业务日期）。"""

    text = str(body_text or "")
    if not text:
        return None
    date_pattern = re.compile(r"20\d{2}[/\-年.]\d{1,2}[/\-月.]\d{1,2}")
    for line in text.splitlines():
        if re.search(r"更新|数据|日期|时间", line):
            match = date_pattern.search(line)
            if match:
                return match.group(0)
    match = date_pattern.search(text)
    return match.group(0) if match else None



def _meituan_trade_caliber(label: str, period: str) -> str:
    if period == "day":
        return f"交易分析·{label}（前日数据，页面每日9点更新，较上周/同行排名）"
    return f"交易分析·{label}（自然{'周' if period == 'week' else '月'}，较上期/较去年）"


def _meituan_ranking_caliber(label: str, period: str) -> str:
    if period == "day":
        return f"同行分析·{label}（昨日口径，四榜）"
    return f"同行分析·{label}（滚动{'7天' if period == 'week' else '30天'}，四榜口径）"


def _parse_meituan_trade_period_metrics(body_text: str) -> dict[str, dict[str, Any]]:
    """解析交易分析 周/月 视图指标卡：值/较上期/较去年/同行排名/同行均值。"""
    lines = _clean_meituan_text_lines(body_text)
    labels = set(_MEITUAN_TRADE_METRIC_LABELS)
    metrics: dict[str, dict[str, Any]] = {}
    marker_keys = {
        "较上期": "vs_prev_period",
        "较上周": "vs_prev_period",
        "较去年": "vs_last_year",
        "同行排名": "peer_rank",
        "同行均值": "peer_average",
    }
    index = 0
    while index < len(lines):
        line = lines[index]
        if line in labels and line not in metrics:
            card: dict[str, Any] = {"value": None}
            index += 1
            if index < len(lines) and lines[index] not in labels:
                card["value"] = lines[index]
                index += 1
            while index < len(lines):
                candidate = lines[index]
                # 遇到下一个指标标签或表格区段头即结束本卡扫描。
                if candidate in labels or candidate in ("间夜", "房型销售排行"):
                    break
                if candidate in marker_keys:
                    key = marker_keys[candidate]
                    if index + 1 < len(lines) and lines[index + 1] not in labels:
                        card[key] = lines[index + 1]
                        index += 2
                        continue
                if candidate.startswith("同行管理"):
                    break
                index += 1
            metrics[line] = card
            continue
        index += 1
    return metrics


async def _find_meituan_period_frame(page: Page, *, must_have: tuple[str, ...]):
    for frame in page.frames:
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=3_000)
            if all(token in text for token in must_have) and any(
                signal in text
                for signal in ("销售间夜", "销售额", "同行榜单", "支付订单数")
            ):
                return frame
    return None


async def _meituan_frame_body(frame) -> str:
    with suppress(Exception):
        return await frame.locator("body").inner_text(timeout=5_000)
    return ""


async def _meituan_visible_text(page: Page) -> str:
    """美团页面状态分类用文本：取任一非空 frame 的可见文本。"""
    return await _meituan_any_frame_text(page)


# ---------------------------------------------------------------------------
# 美团：同行分析 周/月 排名数据（近7天/近30天）
# ---------------------------------------------------------------------------


async def read_meituan_ranking_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        # 同行分析页只有 今日实时/昨日/近7天/近30天 四个固定口径（2026-09-16 实测）。
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return _custom_range_not_supported_payload("美团同行分析", start, end)
    label = _MEITUAN_RANKING_RADIO_LABELS.get(period, "近7天")
    await page.goto(
        _MEITUAN_EBK_DATA_CENTER_URL, wait_until="domcontentloaded", timeout=45_000
    )
    await _settle_meituan_page(page)
    await page.wait_for_timeout(4_000)
    body_text = await _meituan_any_frame_text(page)
    page_state = _classify_meituan_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("meituan", page.url, page_state)
    await _select_meituan_data_center_tab(page, "同行分析")
    await page.wait_for_timeout(3_500)

    frame = await _find_meituan_period_frame(page, must_have=(label,))
    clicked = False
    if frame is not None:
        with suppress(Exception):
            radio = frame.locator(
                "label.mtd-radio-button", has_text=re.compile(f"^{label}$")
            ).first
            if await radio.count() > 0:
                await radio.click(timeout=4_000, force=True)
                clicked = True
                await page.wait_for_timeout(4_500)
    if not clicked:
        return {
            "status": "not_available",
            "caliber": _meituan_ranking_caliber(label, period),
            "summary": f"美团同行分析页未找到「{label}」单选钮，无法读取该维度排名。",
            "sections": {},
            "evidence": {"page_url": page.url, "selected_control": None},
        }
    # 选中态校验：页面默认「昨日」，若点「近7天/近30天」失败会静默解析到昨日数据却标错口径。
    selected_state = False
    if frame is not None:
        with suppress(Exception):
            checked = await frame.evaluate(
                "() => Array.from(document.querySelectorAll('.mtd-radio-button-checked'))"
                ".map(node => (node.innerText || '').trim())"
            )
            selected_state = isinstance(checked, list) and label in [str(c) for c in checked]
    rankings = await _read_meituan_peer_rankings(page)
    # 恢复默认「昨日」视图。
    if frame is not None:
        with suppress(Exception):
            back = frame.locator(
                "label.mtd-radio-button", has_text=re.compile("^昨日$")
            ).first
            if await back.count() > 0:
                await back.click(timeout=3_000, force=True)
                await page.wait_for_timeout(1_500)
    if not selected_state:
        return {
            "status": "not_available",
            "caliber": _meituan_ranking_caliber(label, period),
            "summary": f"美团同行分析页「{label}」单选钮未切换到选中态，无法确认口径，请人工复核。",
            "sections": {"四榜": rankings},
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "selected_state": False,
            },
        }
    return {
        "status": "ok" if rankings else "not_available",
        "caliber": _meituan_ranking_caliber(label, period),
        "summary": (
            f"已读取美团同行榜单{label}四榜排名（入住榜/销售榜/流量榜/转化榜）。"
            if rankings
            else f"美团同行分析{label}视图未解析到榜单数据。"
        ),
        "sections": {"四榜": rankings},
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "selected_state": True,
        },
    }


async def _meituan_any_frame_text(page: Page) -> str:
    for frame in page.frames:
        with suppress(Exception):
            text = await frame.locator("body").inner_text(timeout=2_500)
            if text.strip():
                return text
    return ""


# ---------------------------------------------------------------------------
# 飞猪：诊断报告 周报（经营周数据 + 同行排名）
# ---------------------------------------------------------------------------


async def read_fliggy_business_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        # 经营数据页在店/预订两张卡各有 ant-picker-range 区间控件（2026-09-17
        # 实测；09-16 只查 label/button 短文本漏检）。
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return await _read_fliggy_operating_custom_range(page, start, end)
    # day/week/month 统一走「经营数据页」(operatingData)：昨日/近7天/近30天均含
    # 在店间夜/房费收入/间夜单价 + 预订间夜/预订收入金额/预订订单（+上周同期与
    # 同行对比）。诊断报告日报昨日块无房费收入/预订收入金额字段（2026-09-16 实测），
    # 故经营问法一律读经营数据页，保证收入与单价齐全。
    return await _read_fliggy_operating_period(page, period=period)


async def _read_fliggy_daily_yesterday(page: Page) -> dict[str, Any]:
    """飞猪诊断报告·日报默认视图的「昨日数据」块（含上周同期对比）。

    日报为页面默认视图，无需切换；昨日块行序（2026-09-07 实测）：昨日数据 →
    更新时间 YYYY/MM/DD → 指标标签 → 值 → 上周同期X% → …，止于「客人点评得分」。
    """
    await _goto_fliggy_with_reload(page, _FLIGGY_EBK_DIAGNOSE_URL)
    body_text = await _fliggy_page_text(page)
    page_state = _classify_fliggy_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("fliggy", page.url, page_state)
    result = _parse_fliggy_daily_yesterday_section(body_text)
    metrics = result["metrics"]
    return {
        "status": "ok" if metrics else "not_available",
        "caliber": "诊断报告·日报·昨日数据（含上周同期）",
        "summary": (
            "已读取飞猪诊断报告昨日数据（在店间夜/间夜单价/预订订单）。"
            if metrics
            else "飞猪诊断报告日报视图未解析到「昨日数据」区块，请人工复核页面。"
        ),
        "sections": {"昨日数据": metrics},
        "data_period_label": result["data_period_label"],
        "evidence": {"page_url": page.url, "selected_control": "日报(默认)"},
    }


def _parse_fliggy_daily_yesterday_section(body_text: str) -> dict[str, Any]:
    """解析诊断报告·日报的「昨日数据」块。"""
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln == "昨日数据"), None)
    if start is None:
        return {"data_period_label": None, "metrics": {}}
    period_label = None
    update_match = next(
        (ln for ln in lines[start + 1 : start + 3] if ln.startswith("更新时间")), None
    )
    if update_match:
        period_label = update_match.replace("更新时间", "").strip() or None
    stop_markers = ("客人点评得分", "MCI酒店竞争力指数", "APP流量转化", "同行榜单排名")
    metrics: dict[str, dict[str, Any]] = {}
    cursor = start + 1
    pending_metric: dict[str, Any] | None = None
    while cursor < len(lines):
        line = lines[cursor]
        if any(line.startswith(m) for m in stop_markers):
            break
        match = re.match(r"^上周同期(.+)$", line)
        if match and pending_metric is not None:
            pending_metric["上周同期"] = match.group(1).strip()
            cursor += 1
            continue
        if pending_metric is not None and pending_metric.get("value") is None:
            if not line.startswith("更新时间"):
                pending_metric["value"] = line
                cursor += 1
                continue
        if re.fullmatch(r"[一-龥]{2,6}", line) and line != "更多详情":
            if pending_metric:
                metrics[pending_metric["label"]] = pending_metric
            pending_metric = {"label": line, "value": None, "上周同期": None}
        cursor += 1
    if pending_metric:
        metrics[pending_metric["label"]] = pending_metric
    return {"data_period_label": period_label, "metrics": metrics}


async def _read_fliggy_weekly_report(page: Page) -> dict[str, Any]:
    await _goto_fliggy_with_reload(page, _FLIGGY_EBK_DIAGNOSE_URL)
    body_text = await _fliggy_page_text(page)
    page_state = _classify_fliggy_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("fliggy", page.url, page_state)
    clicked = False
    rendered = False
    for attempt in range(3):
        clicked = (await _click_fliggy_visible_text(page, "周报")) or clicked
        await page.wait_for_timeout(4_000 if attempt == 0 else 3_000)
        body_text = await _fliggy_page_text(page)
        # 周报视图渲染成功的标志：上周概览/上周总结区块出现。
        if "上周概览" in body_text or "上周总结" in body_text:
            rendered = True
            break
        if attempt == 1:
            # SPA 偶发不响应点击：整页重载后再试一次（重载后回到日报默认视图）。
            await _goto_fliggy_with_reload(page, _FLIGGY_EBK_DIAGNOSE_URL, force=True)
    tables = await page.evaluate(_VISIBLE_TABLES_JS)
    parsed = _parse_fliggy_weekly_tables(tables or [])
    # 读取完恢复「日报」默认视图。
    with suppress(Exception):
        await _click_fliggy_visible_text(page, "日报")
        await page.wait_for_timeout(1_000)
    overview = _extract_fliggy_weekly_overview(body_text)
    sections = parsed["sections"]
    has_metrics = rendered and any(
        row.get(column)
        for section in sections.values()
        for row_key, row in section.items()
        if row_key == "上周"
        for column in section.get("columns", [])
    )
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": "诊断报告·周报（上周 周一至周日，含上上周环比与同行均值）",
        "summary": (
            "已读取飞猪诊断报告周报（在店/预订/点评/MCI/流量转化 上周概览）。"
            if has_metrics
            else "飞猪诊断报告周报视图未渲染出数据，请人工复核页面或稍后重试。"
        ),
        "sections": sections,
        "weekly_period_label": parsed["weekly_period_label"],
        "overview": overview,
        "evidence": {
            "page_url": page.url,
            "selected_control": "周报",
            "clicked": clicked,
            "weekly_rendered": rendered,
        },
    }


async def _goto_fliggy_with_reload(page: Page, url: str, *, force: bool = False) -> None:
    """导航到飞猪数据页；force=True 时整页重载（SPA 偶发不响应 hash 切换）。"""
    with suppress(Exception):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if force:
            await page.reload(wait_until="domcontentloaded", timeout=45_000)
    await _settle_fliggy_page(page)
    await page.wait_for_timeout(3_500)


def _extract_fliggy_weekly_overview(body_text: str) -> list[str]:
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    markers = ("上周总结", "预定间夜较上周", "环比")
    out: list[str] = []
    for index, line in enumerate(lines):
        if line == "上周总结" and index + 1 < len(lines):
            cursor = index + 1
            while cursor < len(lines) and lines[cursor] not in {"任务完成结果将于下周更新", "上周概览"}:
                if lines[cursor].startswith(("预定", "提升", "客人", "持续")):
                    out.append(lines[cursor])
                cursor += 1
            break
    if not out:
        out = [ln for ln in lines if any(m in ln for m in markers)][:3]
    return out


def _split_flattened_weekly_row(cells: list[str]) -> list[tuple[str, list[str]]]:
    """把一行拆成 (行标签, 值列表) 组。

    真实页面存在两种 DOM 形态（2026-09-07 实测）：整页重载后每个行标签是独立
    tr；而已挂载 SPA 内切换周报时整组数据被压平进一个 tr（连续出现
    上周/上上周/环比/同行均值/同行排名 多组「标签+值」）。此函数对两种形态统一。
    """
    groups: list[tuple[str, list[str]]] = []
    current_label: str | None = None
    current_values: list[str] = []
    for cell in cells:
        matched = next(
            (label for label in _FLIGGY_WEEKLY_ROW_LABELS if cell.startswith(label)),
            None,
        )
        if matched is not None:
            if current_label is not None:
                groups.append((current_label, current_values))
            current_label, current_values = matched, []
        elif current_label is not None:
            current_values.append(cell)
    if current_label is not None:
        groups.append((current_label, current_values))
    return groups


def _parse_fliggy_weekly_tables(tables: list[dict[str, Any]]) -> dict[str, Any]:
    sections: dict[str, dict[str, Any]] = {}
    peer_board: dict[str, dict[str, str]] = {}
    weekly_period_label: str | None = None
    for table in tables:
        header = [str(cell or "").strip() for cell in (table.get("header") or [])]
        columns = [cell for cell in header if cell]
        rows = table.get("rows") or []
        matched = next(
            (
                name
                for name, needles in _FLIGGY_WEEKLY_TABLE_MATCHERS
                if all(needle in columns for needle in needles)
            ),
            None,
        )
        if matched is None:
            if columns and columns[0] == "酒店名称":
                for row in rows:
                    cells = [str(c or "").strip() for c in row]
                    if cells and cells[0]:
                        peer_board[cells[0]] = {
                            column: (cells[i + 1] if i + 1 < len(cells) else "")
                            for i, column in enumerate(columns[1:])
                        }
            continue
        section: dict[str, Any] = {"columns": columns}
        for row in rows:
            cells = [str(c or "").strip() for c in row]
            if not cells:
                continue
            for row_label, values in _split_flattened_weekly_row(cells):
                if row_label == "上周" and weekly_period_label is None:
                    match = _FLIGGY_WEEKLY_PERIOD_RE.search(cells[0])
                    if match:
                        weekly_period_label = match.group(1)
                section[row_label] = {
                    column: (values[i] if i < len(values) else "")
                    for i, column in enumerate(columns)
                }
        sections[matched] = section
    return {"sections": sections, "peer_board": peer_board, "weekly_period_label": weekly_period_label}


async def _read_fliggy_operating_period(page: Page, *, period: str) -> dict[str, Any]:
    label = _FLIGGY_OPERATING_RADIO_LABELS.get(period, "近30天")
    await _goto_fliggy_with_reload(page, _FLIGGY_EBK_OPERATING_URL)
    body_text = await _fliggy_page_text(page)
    page_state = _classify_fliggy_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("fliggy", page.url, page_state)

    async def _switched() -> int:
        """返回选中态为 label 的单选钮数量（在店+预订两组应 ≥2；0=未切换）。"""
        with suppress(Exception):
            checked = await page.evaluate(
                "() => Array.from(document.querySelectorAll("
                "'.ant-radio-button-wrapper-checked'))"
                ".map(node => (node.innerText || '').trim())"
            )
            if isinstance(checked, list):
                return sum(1 for item in checked if label in str(item))
        return 0

    clicked = 0
    for attempt in range(3):
        radios = page.locator(
            "label.ant-radio-button-wrapper", has_text=re.compile(f"^{label}$")
        )
        with suppress(Exception):
            count = await radios.count()
            for index in range(count):
                await radios.nth(index).click(timeout=4_000, force=True)
                clicked += 1
        await page.wait_for_timeout(4_500 if attempt == 0 else 3_000)
        if await _switched() >= 1:
            break
        if attempt == 1:
            await _goto_fliggy_with_reload(page, _FLIGGY_EBK_OPERATING_URL, force=True)

    # 选中态计数必须在「恢复今日」之前读取，否则恒为 0。
    checked_count = await _switched()
    body_text = await _fliggy_page_text(page)
    sections = _parse_fliggy_operating_period_sections(body_text)
    # 恢复默认「今日」视图。
    with suppress(Exception):
        restore = page.locator(
            "label.ant-radio-button-wrapper", has_text=re.compile("^今日$")
        )
        for index in range(min(clicked, await restore.count())):
            await restore.nth(index).click(timeout=3_000, force=True)
        await page.wait_for_timeout(1_000)
    has_metrics = any(
        isinstance(value, dict) and value.get("value")
        for section in sections.values()
        for key, value in section.items()
        if key != "_update_time"
    )
    caliber_suffix = {
        "day": "含上周同期与同行对比",
        "week": "滚动7天",
        "month": "滚动30天",
    }.get(period, label)
    # 选中态硬校验：3 次切换尝试全部失败时页面仍是默认「今日」，
    # 今日数据不得被标成昨日/滚动 7/30 天口径落库。
    if checked_count < 1:
        return {
            "status": "not_available",
            "caliber": f"经营数据·{label}（{caliber_suffix}）",
            "summary": (
                f"飞猪经营数据页「{label}」单选钮未切换到选中态，无法确认口径，请人工复核。"
            ),
            "sections": sections,
            "evidence": {
                "page_url": page.url,
                "selected_control": label,
                "clicked": clicked,
                "checked_count": checked_count,
                "selected_state": False,
            },
        }
    return {
        "status": "ok" if (clicked and has_metrics) else "not_available",
        "caliber": f"经营数据·{label}（{caliber_suffix}）",
        "summary": (
            f"已读取飞猪经营数据{label}在店/预订指标（含房费收入/间夜单价/预订收入金额）。"
            if has_metrics
            else f"飞猪经营数据页{label}视图未渲染出指标，请人工复核页面或稍后重试。"
        ),
        "sections": sections,
        "evidence": {
            "page_url": page.url,
            "selected_control": label,
            "clicked": clicked,
            "checked_count": checked_count,
            "selected_state": True,
        },
    }


async def _read_fliggy_operating_custom_range(
    page: Page, date_from: str, date_to: str
) -> dict[str, Any]:
    """经营数据页自定义区间：在店/预订两张卡各有 ant-picker-range 控件。

    交互（2026-09-17 CDP 实测）：点 picker 展开面板 → 起始输入框 fill+回车 →
    结束输入框 fill+回车，面板自动提交（无确定按钮）；区间生效后该卡「今日」
    单选钮自动取消选中。自定义区间视图无同期/同行对比列（均显示暂无）。
    """
    await _goto_fliggy_with_reload(page, _FLIGGY_EBK_OPERATING_URL)
    body_text = await _fliggy_page_text(page)
    page_state = _classify_fliggy_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("fliggy", page.url, page_state)

    async def _applied() -> tuple[int, int]:
        """返回 (生效数, 可见 picker 总数)；双输入值都等于请求区间才算生效。"""
        with suppress(Exception):
            values = await page.evaluate(
                "() => Array.from(document.querySelectorAll('.ant-picker-range'))"
                ".filter(el => el.offsetWidth || el.offsetHeight || el.getClientRects().length)"
                ".map(p => Array.from(p.querySelectorAll('input')).map(i => i.value))"
            )
            if isinstance(values, list):
                pairs = [v for v in values if isinstance(v, list)]
                applied = sum(1 for v in pairs if v[:2] == [date_from, date_to])
                return applied, len(pairs)
        return 0, 0

    applied = found = 0
    for attempt in range(3):
        with suppress(Exception):
            pickers = page.locator(".ant-picker-range")
            count = await pickers.count()
            for index in range(count):
                picker = pickers.nth(index)
                await picker.click(timeout=4_000)
                await page.wait_for_timeout(800)
                first = picker.locator("input").first
                await first.click(timeout=3_000)
                await page.wait_for_timeout(200)
                await first.fill(date_from)
                await page.wait_for_timeout(400)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(400)
                second = picker.locator("input").nth(1)
                await second.click(timeout=3_000)
                await page.wait_for_timeout(200)
                await second.fill(date_to)
                await page.wait_for_timeout(400)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(3_000)
        # 值读取必须在恢复「今日」之前（恢复后 picker 清空）。
        applied, found = await _applied()
        if found and applied == found:
            break
        if attempt == 1:
            await _goto_fliggy_with_reload(page, _FLIGGY_EBK_OPERATING_URL, force=True)

    # 应用区间后两张卡各自异步刷新，固定等待过早捕获会拿到空卡（09-17
    # 连续实测第二跑起复现）：轮询到每张卡都解析出指标再捕获，约 12 秒上限。
    body_text = await _fliggy_page_text(page)
    sections = _parse_fliggy_operating_period_sections(body_text)
    for _ in range(8):
        if sections and all(
            any(
                isinstance(value, dict) and value.get("value")
                for key, value in section.items()
                if key != "_update_time"
            )
            for section in sections.values()
        ):
            break
        await page.wait_for_timeout(1_500)
        body_text = await _fliggy_page_text(page)
        sections = _parse_fliggy_operating_period_sections(body_text)
    # 恢复默认「今日」视图（radio 回选后 picker 自动清空）。
    with suppress(Exception):
        restore = page.locator(
            "label.ant-radio-button-wrapper", has_text=re.compile("^今日$")
        )
        for index in range(await restore.count()):
            await restore.nth(index).click(timeout=3_000, force=True)
        await page.wait_for_timeout(1_000)
    # 选中态硬校验：任一卡未生效即整单拒答（在店/预订混口径比无答更危险）。
    if not found or applied != found:
        return _range_not_available(
            "飞猪经营数据",
            date_from,
            date_to,
            (
                f"飞猪经营数据页自定义区间未生效（{applied}/{found} 张卡确认），"
                "请人工复核页面后重试。"
            ),
        )
    has_metrics = any(
        isinstance(value, dict) and value.get("value")
        for section in sections.values()
        for key, value in section.items()
        if key != "_update_time"
    )
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": f"经营数据·自定义 {date_from}~{date_to}",
        "summary": (
            f"已读取飞猪经营数据自定义区间（{date_from}~{date_to}）在店/预订指标"
            "（含房费收入/间夜单价/预订收入金额，无同期/同行对比）。"
            if has_metrics
            else f"飞猪经营数据页自定义区间视图未渲染出指标，请人工复核页面或稍后重试。"
        ),
        "sections": sections,
        "evidence": {
            "page_url": page.url,
            "selected_control": "自定义",
            "range_inputs": [date_from, date_to],
            "applied_cards": applied,
            "found_cards": found,
            "selected_state": True,
        },
    }


def _parse_fliggy_operating_period_sections(body_text: str) -> dict[str, dict[str, Any]]:
    """解析经营数据页周期视图：在店数据/预订数据两组 指标→值(+同期/同行占位)。"""
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
    section_positions: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        if line in {name for name, _ in _FLIGGY_OPERATING_SECTIONS}:
            if not any(pos == index for _, pos in section_positions):
                section_positions.append((line, index))
    sections: dict[str, dict[str, Any]] = {}
    for position, (name, start) in enumerate(section_positions):
        end = (
            section_positions[position + 1][1]
            if position + 1 < len(section_positions)
            else len(lines)
        )
        section = lines[start + 1 : end]
        labels = dict(_FLIGGY_OPERATING_SECTIONS)[name]
        metrics: dict[str, Any] = {}
        update_time = next(
            (ln for ln in section if ln.startswith("数据更新时间")), None
        )
        index = 0
        while index < len(section):
            line = section[index]
            if line in labels:
                card: dict[str, Any] = {"value": None, "context_lines": []}
                if index + 1 < len(section):
                    card["value"] = section[index + 1]
                    index += 2
                while index < len(section):
                    candidate = section[index]
                    if candidate in labels:
                        break
                    if candidate.startswith(("暂无", "较")):
                        card["context_lines"].append(candidate)
                    index += 1
                metrics[line] = card
                continue
            index += 1
        if update_time:
            metrics["_update_time"] = update_time
        sections[name] = metrics
    return sections


# ---------------------------------------------------------------------------
# 飞猪：同行排名（周报内）
# ---------------------------------------------------------------------------


async def read_fliggy_ranking_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if date_from or date_to:
        # 飞猪排名仅有日报/周报同行榜单（2026-09-16 实测）。
        start = str(date_from or date_to or "").strip()
        end = str(date_to or date_from or "").strip()
        return _custom_range_not_supported_payload("飞猪数据页", start, end)
    if period == "day":
        return await _read_fliggy_daily_peer_board(page)
    weekly = await _read_fliggy_weekly_report(page)
    if weekly.get("status") == "human_required":
        return weekly
    sections = weekly.get("sections") or {}
    peer_ranks: dict[str, dict[str, str]] = {}
    for name, section in sections.items():
        rank_row = section.get("同行排名") or {}
        avg_row = section.get("同行均值") or {}
        if rank_row or avg_row:
            peer_ranks[name] = {
                "同行排名": rank_row,
                "同行均值": avg_row,
            }
    return {
        "status": "ok" if peer_ranks else "not_available",
        "caliber": "诊断报告·周报·同行排名（上周；需在飞猪关注同行后才有数据）",
        "summary": (
            "已读取飞猪周报各区块同行排名/同行均值（页面未关注同行时为空值）。"
            if peer_ranks
            else "飞猪周报视图未解析到同行排名区块，请人工复核页面。"
        ),
        "sections": {"per_section": peer_ranks},
        "weekly_period_label": weekly.get("weekly_period_label"),
        "evidence": weekly.get("evidence") or {},
    }


async def _read_fliggy_daily_peer_board(page: Page) -> dict[str, Any]:
    """飞猪诊断报告·日报默认视图的「同行榜单排名」表（需关注同行，未关注为空值）。"""
    await _goto_fliggy_with_reload(page, _FLIGGY_EBK_DIAGNOSE_URL)
    body_text = await _fliggy_page_text(page)
    page_state = _classify_fliggy_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("fliggy", page.url, page_state)
    tables = await page.evaluate(_VISIBLE_TABLES_JS)
    parsed = _parse_fliggy_weekly_tables(tables or [])
    peer_board = parsed["peer_board"]
    update_time = next(
        (ln.strip() for ln in body_text.splitlines() if ln.strip().startswith("数据更新时间")),
        None,
    )
    return {
        "status": "ok" if peer_board else "not_available",
        "caliber": "诊断报告·日报·同行榜单排名（需在飞猪关注同行后才有数据）",
        "summary": (
            "已读取飞猪日报同行榜单排名表（页面未关注同行时为空值）。"
            if peer_board
            else "飞猪日报视图未解析到同行榜单排名表（或还未关注同行），请人工复核。"
        ),
        "sections": {"peer_board": peer_board},
        "data_period_label": (update_time or "").replace("数据更新时间", "").strip() or None,
        "evidence": {"page_url": page.url, "selected_control": "日报(默认)"},
    }


async def _fliggy_page_text(page: Page) -> str:
    with suppress(Exception):
        return await page.locator("body").inner_text(timeout=5_000)
    return ""


def _human_required_payload(platform: str, url: str, page_state: str) -> dict[str, Any]:
    return {
        "status": "human_required",
        "summary": "需要人工完成 EBK 登录或安全验证后才能读取周/月维度数据。",
        "next_action": "请在对应平台 EBK 浏览器窗口完成人工登录或安全验证后重试。",
        "evidence": {"page_url": url, "page_state": page_state},
    }


# ---------------------------------------------------------------------------
# 自定义日期区间读取（2026-09-16 三平台页面 CDP 实测）
#
# - 携程经营（预订销售页）：每组 实时/按日/按周/按月/按季/自定义 按钮组；
#   点「自定义」后组内 input 变为可编辑区间框（"YYYY-MM-DD ~ YYYY-MM-DD"），
#   清空+输入+回车生效；三组需逐一设置，值断言防静默失败。
# - 携程排名（竞争排名页）：销售/流量两组各有 自定义 单选钮；激活后
#   he-trip-ui 区间日历（td[title="YYYY-MM-DD"]，JS click 生效；页面限制
#   最少1天最多31天，当日及以后置灰）。翻月用 header prev/next 按钮。
# - 美团经营（交易分析 iframe）：「自定义」按钮激活 mtd 区间选择器；日历
#   popper 渲染在 iframe 内（.mtd-popper 双月面板），单元格为 span，需完整
#   鼠标事件序列（pointerdown/mousedown/pointerup/mouseup/click）才提交。
# - 美团排名/飞猪经营/飞猪排名：页面无自定义控件（service 层已拦截，
#   reader 内再兜底一次）。
# ---------------------------------------------------------------------------


def _parse_range_date(value: str) -> _date:
    return _datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def _format_ctrip_picker_date(value: _date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _format_meituan_picker_date(value: _date) -> str:
    return value.strftime("%Y/%m/%d")


# —— he-trip-ui（携程排名日历）JS 交互 ——
_JS_HE_CELL_VISIBLE = (
    "(title) => Array.from(document.querySelectorAll('td[title=\"' + title + '\"]'))"
    ".some(el => el.getClientRects().length > 0)"
)
# he-click 委托对 JS click 只稳定响应首个日期；起止统一改用真实鼠标坐标点击
# 提交（2026-09-16 CDP 实测：JS click 选起始有效，坐标点击起止均有效）。
_JS_HE_CELL_BOX = (
    "(title) => {"
    "const vis = el => el.getClientRects().length > 0;"
    "const dd = Array.from(document.querySelectorAll('.he-trip-ui-picker-dropdown')).find(vis);"
    "if (!dd) return null;"
    "const td = dd.querySelector('td[title=\"' + title + '\"]');"
    "if (!td) return null;"
    "const r = td.getBoundingClientRect();"
    "return {x: r.x + r.width / 2, y: r.y + r.height / 2};"
    "}"
)
_JS_HE_HEADER_NAV = (
    "(direction) => {"
    "const vis = el => el.getClientRects().length > 0;"
    "const cls = direction === 'prev'"
    " ? '.he-trip-ui-picker-header-prev-btn'"
    " : '.he-trip-ui-picker-header-next-btn';"
    "const btn = Array.from(document.querySelectorAll(cls)).find(vis);"
    "if (!btn) return false;"
    "btn.click();"
    "return true;"
    "}"
)


async def _click_he_cell_by_mouse(page: Page, title: str) -> bool:
    box = await page.evaluate(_JS_HE_CELL_BOX, title)
    if not isinstance(box, dict) or "x" not in box:
        return False
    await page.mouse.click(box["x"], box["y"])
    return True


async def _click_he_cell(page: Page, title: str, *, prefer_mouse: bool) -> None:
    """点选日历单元格：坐标鼠标点击 / locator 可见点击两种策略。

    2026-09-16 live 实测：JS click 只稳定响应起始选择；翻月后坐标解析偶发
    落到错误面板（点击无效）。两种方式交替尝试，由调用方读值验证是否生效。
    """
    if prefer_mouse:
        if await _click_he_cell_by_mouse(page, title):
            return
    loc = page.locator(f'td[title="{title}"]')
    for index in range(await loc.count()):
        element = loc.nth(index)
        try:
            if await element.is_visible():
                await element.click(timeout=2_500)
                return
        except Exception:
            continue
    if not prefer_mouse:
        await _click_he_cell_by_mouse(page, title)


async def _read_he_pair_values(page: Page) -> list[list[str]]:
    """读取全部 开始日期/结束日期 输入框，按组配对（[[组1起,组1止],[组2起,组2止]]）。"""
    with suppress(Exception):
        raw = await page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'input[placeholder=开始日期], input[placeholder=结束日期]'))"
            ".map(el => el.value)"
        )
        if isinstance(raw, list):
            return [[str(v) for v in raw[i : i + 2]] for i in range(0, len(raw), 2)]
    return []


_JS_HE_CELL_DISABLED = (
    "(title) => {"
    "const vis = el => el.getClientRects().length > 0;"
    "const dd = Array.from(document.querySelectorAll('.he-trip-ui-picker-dropdown')).find(vis);"
    "if (!dd) return false;"
    "const td = dd.querySelector('td[title=\"' + title + '\"]');"
    "return td ? String(td.className).includes('picker-cell-disabled') : false;"
    "}"
)


async def _select_he_range_cells(
    page: Page, start: _date, end: _date, *, pair_index: int = 0
) -> str:
    """在已打开的携程 he-trip-ui 区间日历上选起止日期（含翻月导航）。

    返回 "ok" / "disabled"（日期超出页面可选窗口）/ "failed"。
    每次点击后读输入框值验证是否真正生效；未生效换点击策略重试（最多 3 次）。
    页面自定义窗口约最近33天（2026-09-16 实测：9/16 往前最早可选 8/14），
    更早的整月排名需走页面「上月」单选钮（period=month）。
    """
    for side, probe in ((0, start), (1, end)):
        expected = _format_ctrip_picker_date(probe)
        for _ in range(24):
            if await page.evaluate(_JS_HE_CELL_VISIBLE, probe.isoformat()):
                break
            nav = await page.evaluate(_JS_HE_HEADER_NAV, "prev" if side == 0 else "next")
            if not nav:
                return "failed"
            await page.wait_for_timeout(500)
        else:
            return "failed"
        if await page.evaluate(_JS_HE_CELL_DISABLED, probe.isoformat()):
            return "disabled"
        applied = False
        for attempt in range(3):
            await _click_he_cell(page, probe.isoformat(), prefer_mouse=(attempt % 2 == 0))
            await page.wait_for_timeout(1_000 if side == 0 else 1_800)
            pairs = await _read_he_pair_values(page)
            if len(pairs) > pair_index and pairs[pair_index][side] == expected:
                applied = True
                break
        if not applied:
            return "failed"
    return "ok"


# —— mtd（美团交易分析日历，iframe 内）JS 交互 ——
_JS_MT_PANEL_MONTHS = (
    "() => Array.from(document.querySelectorAll('.mtd-popper .mtd-date-picker-header'))"
    ".filter(el => el.getClientRects().length > 0)"
    ".map(el => (el.innerText || '').replace(/\\s+/g, ''))"
)
_JS_MT_HEADER_NAV = (
    "(direction) => {"
    "const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);"
    "const popper = Array.from(document.querySelectorAll('.mtd-popper')).find(vis);"
    "if (!popper) return 'no-popper';"
    "const icon = popper.querySelector("
    "direction === 'prev' ? '.mtdicon-left' : '.mtdicon-right');"
    "if (!icon) return 'no-icon';"
    "let target = icon;"
    "while (target && target.tagName !== 'BUTTON') target = target.parentElement;"
    "if (!target) return 'no-button';"
    "for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {"
    "target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));"
    "}"
    "return 'clicked';"
    "}"
)
_JS_MT_CELL_CLICK = (
    "(arg) => {"
    "const day = arg.day; const monthText = arg.month;"
    "const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);"
    "const popper = Array.from(document.querySelectorAll('.mtd-popper')).find(vis);"
    "if (!popper) return 'no-popper';"
    "const panels = Array.from("
    "popper.querySelectorAll('.mtd-picker-panel-body-wrapper')).filter(vis);"
    "for (const panel of panels) {"
    "const header = panel.querySelector('.mtd-date-picker-header');"
    "const label = header ? (header.innerText || '').replace(/\\s+/g, '') : '';"
    "if (monthText && !label.includes(monthText)) continue;"
    "for (const c of panel.querySelectorAll('.mtd-date-picker-cells-cell')) {"
    "const cls = String(c.className || '');"
    "const t = (c.innerText || '').trim();"
    "if (t === String(day) && !cls.includes('prev-month') && !cls.includes('next-month')) {"
    "for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {"
    "c.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));"
    "}"
    "return 'clicked';"
    "}"
    "}"
    "}"
    "return 'not-found';"
    "}"
)


def _mt_month_label(value: _date) -> str:
    return f"{value.year}年{value.month}月"


def _mt_panel_month_values(labels: list[str]) -> list[int]:
    """把面板头文案（如「2026年8月」）解析成 (year*12+month) 列表。"""
    out: list[int] = []
    for label in labels:
        match = re.search(r"(\d{4})年(\d{1,2})月", label)
        if match:
            out.append(int(match.group(1)) * 12 + int(match.group(2)))
    return out


async def _select_mt_range_cells(frame, start: _date, end: _date) -> bool:
    """在已打开的美团 mtd 区间日历上选起止日期（含翻月导航）。"""
    for probe in (start, end):
        target = probe.year * 12 + probe.month
        for _ in range(24):
            labels = await frame.evaluate(_JS_MT_PANEL_MONTHS)
            months = _mt_panel_month_values(labels)
            if months and target in months:
                break
            current = months[0] if months else target
            direction = "prev" if target < current else "next"
            if await frame.evaluate(_JS_MT_HEADER_NAV, direction) != "clicked":
                return False
            await frame.wait_for_timeout(400)
        else:
            return False
        outcome = await frame.evaluate(
            _JS_MT_CELL_CLICK, {"day": probe.day, "month": _mt_month_label(probe)}
        )
        if outcome != "clicked":
            return False
        await frame.wait_for_timeout(1_000 if probe is start else 1_500)
    return True


def _range_not_available(platform_label: str, date_from: str, date_to: str, detail: str) -> dict[str, Any]:
    return {
        "status": "not_available",
        "caliber": f"{platform_label}·自定义 {date_from}~{date_to}",
        "summary": detail,
        "sections": {},
        "evidence": {"selected_control": "自定义"},
    }


async def _read_ctrip_business_custom_range(
    page: Page, date_from: str, date_to: str
) -> dict[str, Any]:
    target_text = f"{date_from} ~ {date_to}"
    await page.goto(_CTPIP_BENEFICIAL_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    clicked = 0
    buttons = page.locator("a.btn", has_text=re.compile("^自定义$"))
    try:
        count = await buttons.count()
        for index in range(count):
            await buttons.nth(index).click(timeout=4_000, force=True)
            clicked += 1
            await page.wait_for_timeout(600)
    except Exception:
        pass
    await page.wait_for_timeout(1_500)
    inputs = page.locator("input.form-control.date-input")
    input_count = await inputs.count()
    if clicked == 0 or input_count == 0:
        return _range_not_available(
            "携程预订销售",
            date_from,
            date_to,
            "携程预订销售页未找到「自定义」按钮或区间输入框，无法读取自定义区间。",
        )
    try:
        for index in range(input_count):
            field = inputs.nth(index)
            await field.fill("")
            await field.type(target_text, delay=25)
            await field.press("Enter")
            await page.wait_for_timeout(600)
    except Exception:
        pass
    await page.wait_for_timeout(3_000)
    values: list[str] = []
    try:
        for index in range(input_count):
            values.append(str(await inputs.nth(index).input_value()))
    except Exception:
        pass
    body_text = await _safe_visible_text(page)
    sections = _parse_ctrip_beneficial_period_sections(body_text, period="custom")
    # 恢复默认「按日」视图。
    with suppress(Exception):
        restore = page.locator("a.btn", has_text=re.compile("^按日$"))
        for index in range(min(clicked, await restore.count())):
            await restore.nth(index).click(timeout=4_000, force=True)
        await page.wait_for_timeout(1_000)
    # values 读空（DOM 重渲染致 input_value 全抛）视同未生效：否则页面可能
    # 仍停在默认「按日」视图，会把默认口径数据当自定义区间 ok 返回。
    if not values or any(value != target_text for value in values):
        return _range_not_available(
            "携程预订销售",
            date_from,
            date_to,
            (
                f"携程预订销售页自定义区间未生效（当前显示 {values[:1]}），"
                "请人工复核页面后重试。"
            ),
        )
    has_metrics = any(
        key != "_summary_lines" and key != "_period_label"
        for group in sections.values()
        for key in group
    )
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": f"预订销售·自定义 {date_from}~{date_to}（较去年同期）",
        "summary": (
            f"已读取携程预订/在店/离店自定义区间（{target_text}）经营指标。"
            if has_metrics
            else f"携程预订销售页自定义区间视图未解析到指标，请人工复核页面。"
        ),
        "sections": sections,
        "evidence": {
            "page_url": page.url,
            "selected_control": "自定义",
            "range_inputs": values,
        },
    }


async def _read_ctrip_ranking_custom_range(
    page: Page, date_from: str, date_to: str
) -> dict[str, Any]:
    start = _parse_range_date(date_from)
    end = _parse_range_date(date_to)
    await page.goto(
        _CTRIP_COMPETITION_LIST_URL, wait_until="domcontentloaded", timeout=45_000
    )
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    radios = page.locator(
        "label.he-trip-ui-radio-button-wrapper", has_text=re.compile("^自定义$")
    )
    radio_count = await radios.count()
    if radio_count == 0:
        return _range_not_available(
            "携程竞争排名",
            date_from,
            date_to,
            "携程竞争排名页未找到「自定义」单选钮，无法读取自定义区间排名。",
        )
    start_inputs = page.locator('input[placeholder="开始日期"]')
    applied = 0
    window_disabled = False
    for index in range(radio_count):
        try:
            await radios.nth(index).click(timeout=4_000, force=True)
            await page.wait_for_timeout(1_200)
            if await start_inputs.count() > index:
                await start_inputs.nth(index).click(timeout=3_000, force=True)
                await page.wait_for_timeout(1_200)
                outcome = await _select_he_range_cells(page, start, end, pair_index=index)
                if outcome == "ok":
                    applied += 1
                elif outcome == "disabled":
                    window_disabled = True
        except Exception:
            continue
    await page.wait_for_timeout(2_000)
    body_text = await _safe_visible_text(page)
    result = _parse_ctrip_competition_rankings(body_text)
    # 值断言必须在「恢复上周」之前：恢复点击会重置自定义区间的输入框显示
    # （2026-09-16 live 实测：恢复后读值恒为中间态，会误判选择失败）。
    pairs = await _read_he_pair_values(page)
    expected_pair = [_format_ctrip_picker_date(start), _format_ctrip_picker_date(end)]
    # 2026-10-08 生产实测定性：第二组区间是页面自动联动的「对比期」（本期
    # 10/1-10/7 时恒为紧邻上期 9/28-10/4，不可设成本期），只校验第一组
    # （本期）是否命中；组1 静默失败时先有界重试一轮再复核。此前要求两组
    # 全等导致自定义区间榜单读取必然失败。
    def _current_period_mismatch() -> bool:
        return bool(pairs) and len(pairs[0]) == 2 and pairs[0] != expected_pair

    if _current_period_mismatch():
        logger.warning(
            "ctrip ranking custom range mismatch (pairs=%s expected=%s), retrying once",
            pairs,
            expected_pair,
        )
        for index in range(radio_count):
            try:
                await radios.nth(index).click(timeout=4_000, force=True)
                await page.wait_for_timeout(1_200)
                if await start_inputs.count() > index:
                    await start_inputs.nth(index).click(timeout=3_000, force=True)
                    await page.wait_for_timeout(1_200)
                    await _select_he_range_cells(page, start, end, pair_index=index)
            except Exception:
                continue
        await page.wait_for_timeout(2_000)
        body_text = await _safe_visible_text(page)
        result = _parse_ctrip_competition_rankings(body_text)
        pairs = await _read_he_pair_values(page)
    # 恢复默认「上周」视图。
    with suppress(Exception):
        restore = page.locator(
            "label.he-trip-ui-radio-button-wrapper", has_text=re.compile("^上周$")
        )
        for index in range(min(radio_count, await restore.count())):
            await restore.nth(index).click(timeout=3_000, force=True)
        await page.wait_for_timeout(1_000)
    if applied == 0:
        detail = (
            "携程竞争排名页自定义区间仅支持最近约33天，所选起始日期超出页面可选范围；"
            "整月排名请改用「上月」口径（period=month，页面原生单选钮）。"
            if window_disabled
            else "携程竞争排名页自定义区间日历选择未生效，请人工复核页面后重试。"
        )
        return _range_not_available("携程竞争排名", date_from, date_to, detail)
    # 只校验本期（组1）；组2 是页面联动的对比期，不要求等于目标区间。
    if _current_period_mismatch():
        return _range_not_available(
            "携程竞争排名",
            date_from,
            date_to,
            f"携程竞争排名页自定义区间与目标不一致（当前 {pairs[:1]}），请人工复核。",
        )
    return {
        "status": "ok" if result["rankings"] else "not_available",
        "caliber": f"竞争排名·自定义 {date_from}~{date_to}（页面四榜口径）",
        "summary": (
            f"已读取携程竞争圈四榜自定义区间（{date_from}~{date_to}）排名。"
            if result["rankings"]
            else f"携程竞争排名页自定义区间视图未解析到「我的酒店」排名行。"
        ),
        "sections": {"rankings": result["rankings"], "overview": result["overview"]},
        "evidence": {
            "page_url": page.url,
            "selected_control": "自定义",
            "range_pairs": pairs,
        },
    }


async def _read_meituan_business_custom_range(
    page: Page, date_from: str, date_to: str
) -> dict[str, Any]:
    start = _parse_range_date(date_from)
    end = _parse_range_date(date_to)
    await page.goto(
        _MEITUAN_EBK_DATA_CENTER_URL, wait_until="domcontentloaded", timeout=45_000
    )
    await _settle_meituan_page(page)
    await page.wait_for_timeout(4_000)
    body_text = await _meituan_visible_text(page)
    page_state = _classify_meituan_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("meituan", page.url, page_state)
    await _select_meituan_data_center_tab(page, "交易分析")
    await page.wait_for_timeout(3_500)

    # 冷启动浏览器（新拉起店铺页面）的交易分析 iframe 渲染较慢：轮询重试
    # 找「自定义」按钮所在帧，总预算约 20 秒（2026-09-16 立瑞实测首载超时）。
    frame = None
    for _ in range(5):
        frame = await _find_meituan_period_frame(page, must_have=("自定义",))
        if frame is not None:
            break
        await _select_meituan_data_center_tab(page, "交易分析")
        await page.wait_for_timeout(3_000)
    if frame is None:
        return _range_not_available(
            "美团交易分析",
            date_from,
            date_to,
            "美团交易分析页未找到「自定义」按钮，无法读取自定义区间。",
        )
    clicked = False
    try:
        button = frame.locator("button", has_text=re.compile("^自定义$")).first
        if await button.count() > 0:
            await button.click(timeout=4_000, force=True)
            clicked = True
            await page.wait_for_timeout(1_200)
    except Exception:
        pass
    if not clicked:
        return _range_not_available(
            "美团交易分析",
            date_from,
            date_to,
            "美团交易分析页「自定义」按钮点击失败。",
        )
    popper_open = False
    try:
        await frame.locator("div.mtd-date-picker").first.click(timeout=3_000, force=True)
        await page.wait_for_timeout(1_500)
        popper_open = bool(
            await frame.evaluate(
                "() => Array.from(document.querySelectorAll('.mtd-popper'))"
                ".filter(el => el.getClientRects().length > 0).length"
            )
        )
    except Exception:
        pass
    selected = False
    if popper_open:
        selected = await _select_mt_range_cells(frame, start, end)
        # 点交易分析标题收起日历，让页面按新区间刷新。
        with suppress(Exception):
            await frame.locator("text=交易分析").first.click(timeout=2_000, force=True)
        await page.wait_for_timeout(3_000)
    values: list[str] = []
    with suppress(Exception):
        values = [
            str(v)
            for v in await frame.evaluate(
                "() => Array.from(document.querySelectorAll("
                "'input.mtd-date-picker-editor')).map(el => el.value)"
            )
        ]
    text = await _meituan_frame_body(frame)
    metrics = _parse_meituan_trade_period_metrics(text)
    # 恢复默认「按日」视图。
    with suppress(Exception):
        restore = frame.locator("button", has_text=re.compile("^按日$")).first
        if await restore.count() > 0:
            await restore.click(timeout=3_000, force=True)
        await page.wait_for_timeout(1_500)
    expected = [_format_meituan_picker_date(start), _format_meituan_picker_date(end)]
    # values 读空同样视同未生效（与携程经营口径一致，防默认视图误报 ok）。
    if not selected or not values or values[:2] != expected:
        return _range_not_available(
            "美团交易分析",
            date_from,
            date_to,
            (
                f"美团交易分析页自定义区间未生效（当前 {values[:2]}），"
                "请人工复核页面后重试。"
            ),
        )
    return {
        "status": "ok" if metrics else "not_available",
        "caliber": f"交易分析·自定义 {date_from}~{date_to}（较上期/较去年与同行排名）",
        "summary": (
            f"已读取美团交易分析自定义区间（{date_from}~{date_to}）经营指标。"
            if metrics
            else f"美团交易分析自定义区间视图未解析到指标，请人工复核页面。"
        ),
        "sections": {"经营表现": metrics},
        "evidence": {
            "page_url": page.url,
            "selected_control": "自定义",
            "range_inputs": values[:2],
        },
    }


def _custom_range_not_supported_payload(
    page_label: str, date_from: str, date_to: str
) -> dict[str, Any]:
    return _range_not_available(
        page_label,
        date_from,
        date_to,
        "该平台页面未提供自定义日期区间控件（service 层应已拦截），本次为兜底返回。",
    )


# ---------------------------------------------------------------------------
# 携程：金字塔数据报告（今天/过去7天/过去30天 + daterangepicker 自定义区间）
# ---------------------------------------------------------------------------

# daterangepicker（jQuery 组件）交互要点（2026-09-16 实测）：
# - 日历挂在隐藏的 #dateRange_b（knockout data-bind）上：首击只挂载（hidden），
#   再击才展开——循环点击直到 monthselect 可读；
# - 日格绑定的是 mousedown：必须发完整鼠标事件序列（与美团 span 单元格同坑）；
# - 组件 autoApply：选完起止即提交并刷新报表，无需 apply 按钮；
# - 该 build 无导航箭头：跳月用月/年下拉 set+change，每次重新查询节点
#   （年变更会重渲染替换 select），并读回 month/year 验证；
# - 起止可在不同月：左右两个面板都按「面板月+日号」匹配点击。
_JS_DRP_MONTH_YEAR = (
    "() => {"
    "const box = document.querySelector('.daterangepicker');"
    "const left = box && box.querySelector('.drp-calendar.left');"
    "const msel = left && left.querySelector('select.monthselect');"
    "const ysel = left && left.querySelector('select.yearselect');"
    "return msel && ysel ? {month: Number(msel.value), year: Number(ysel.value)} : null;"
    "}"
)
_JS_DRP_SET_SELECTS = (
    "(arg) => {"
    "const box = document.querySelector('.daterangepicker');"
    "const left = box && box.querySelector('.drp-calendar.left');"
    "if (!left) return 'no-left';"
    "if (arg.year !== null) {"
    "const ysel = left.querySelector('select.yearselect');"
    "if (!ysel) return 'no-ysel';"
    "ysel.value = String(arg.year);"
    "ysel.dispatchEvent(new Event('change', {bubbles: true}));"
    "}"
    "const msel = left.querySelector('select.monthselect');"
    "if (!msel) return 'no-msel';"
    "msel.value = String(arg.month);"
    "msel.dispatchEvent(new Event('change', {bubbles: true}));"
    "return 'set';"
    "}"
)
_JS_DRP_CLICK_DAY = (
    "(arg) => {"
    "const box = document.querySelector('.daterangepicker');"
    "if (!box) return 'no-daterangepicker';"
    "for (const cal of box.querySelectorAll('.drp-calendar')) {"
    "const msel = cal.querySelector('select.monthselect');"
    "if (!msel || Number(msel.value) !== arg.month) continue;"
    "for (const td of cal.querySelectorAll('td')) {"
    "const cls = String(td.className || '');"
    "if (cls.includes('off') || cls.includes('disabled')) continue;"
    "if ((td.innerText || '').trim() === String(arg.day)) {"
    "for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {"
    "td.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));"
    "}"
    "return 'clicked';"
    "}"
    "}"
    "}"
    "return 'not-found';"
    "}"
)
_JS_PYRAMID_RANGE_TEXT = (
    "() => {"
    "const box = document.querySelector('div.date-picker-box');"
    "return box ? (box.innerText || '').replace(/\\s+/g, '').trim() : '';"
    "}"
)


def _format_pyramid_range_text(start: _date, end: _date) -> str:
    a = f"{start.year}年{start.month}月{start.day}日"
    b = f"{end.year}年{end.month}月{end.day}日"
    return f"{a}-{b}"


def _pyramid_payload_from_body(body_text: str, *, preset_label: str) -> dict[str, Any]:
    from app.domain.browser.ctrip_ebooking_ad_parsers import _extract_ad_pyramid_payload

    lines = [ln.strip() for ln in str(body_text or "").splitlines() if ln.strip()]
    payload = _extract_ad_pyramid_payload(lines)
    # 报告页与首页卡同源解析；把口径标签从硬编码「今日」改成实际预设/区间。
    ad = payload.get("ad_pyramid")
    if isinstance(ad, dict):
        ad["date_preset"] = preset_label
    summary = payload.get("summary")
    if isinstance(summary, dict):
        summary["date_preset"] = preset_label
    return payload


async def read_ctrip_pyramid_period(
    page: Page,
    *,
    period: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    start = end = None
    preset_label: str
    if date_from or date_to:
        start = _parse_range_date(str(date_from or date_to))
        end = _parse_range_date(str(date_to or date_from))
        preset_label = f"自定义 {start.isoformat()}~{end.isoformat()}"
    else:
        preset_label = _CTRIP_PYRAMID_PRESET_BUTTONS.get(period, "过去7天")

    await page.goto(_CTRIP_PYRAMID_REPORT_URL, wait_until="domcontentloaded", timeout=45_000)
    await _settle_page(page)
    dismissal = await _dismiss_ebk_read_blocking_popups(page)
    if dismissal.get("dismissed"):
        await _settle_page(page)
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    if page_state != "authenticated":
        return _human_required_payload("ctrip", page.url, page_state)

    if start is not None and end is not None:
        applied = await _select_pyramid_custom_range(page, start, end)
        if not applied:
            actual_range = ""
            with suppress(Exception):
                actual_range = str(await page.evaluate(_JS_PYRAMID_RANGE_TEXT))
            return _range_not_available(
                "金字塔数据报告",
                start.isoformat(),
                end.isoformat(),
                (
                    "金字塔数据报告页自定义区间未生效"
                    + (f"（当前显示 {actual_range}）" if actual_range else "")
                    + "，请人工复核页面后重试。"
                ),
            )
    else:
        clicked = False
        try:
            button = page.locator(
                "button.btn", has_text=re.compile(f"^{preset_label}$")
            ).first
            if await button.count() > 0:
                await button.click(timeout=4_000, force=True)
                clicked = True
                await page.wait_for_timeout(3_500)
        except Exception:
            pass
        if not clicked:
            return {
                "status": "not_available",
                "caliber": f"金字塔数据报告·{preset_label}",
                "summary": f"金字塔数据报告页未找到「{preset_label}」按钮，无法读取。",
                "sections": {},
                "evidence": {"page_url": page.url, "selected_control": None},
            }
        # 选中态校验：cur 类应落在目标预设上（点击被吞时仍停留在旧口径）。
        selected = await page.evaluate(
            "() => Array.from(document.querySelectorAll('button.btn.cur'))"
            ".map(node => (node.innerText || '').trim())"
        )
        if preset_label not in [str(s) for s in (selected or [])]:
            return {
                "status": "not_available",
                "caliber": f"金字塔数据报告·{preset_label}",
                "summary": f"金字塔数据报告页「{preset_label}」按钮未切换到选中态，请人工复核。",
                "sections": {},
                "evidence": {
                    "page_url": page.url,
                    "selected_control": preset_label,
                    "selected_state": False,
                },
            }
    await page.wait_for_timeout(1_500)
    body_text = await _safe_visible_text(page)
    payload = _pyramid_payload_from_body(body_text, preset_label=preset_label)
    range_text = await page.evaluate(_JS_PYRAMID_RANGE_TEXT)
    # 恢复默认「过去7天」视图。
    if preset_label != _CTRIP_PYRAMID_DEFAULT_PRESET:
        with suppress(Exception):
            restore = page.locator(
                "button.btn", has_text=re.compile(f"^{_CTRIP_PYRAMID_DEFAULT_PRESET}$")
            ).first
            if await restore.count() > 0:
                await restore.click(timeout=3_000, force=True)
                await page.wait_for_timeout(800)
    metrics = payload.get("summary_metrics")
    has_metrics = isinstance(metrics, dict) and len(metrics) >= 6
    return {
        "status": "ok" if has_metrics else "not_available",
        "caliber": f"金字塔数据报告·{preset_label}",
        "summary": (
            f"已读取携程金字塔投放{preset_label}效果数据（流量/花费/收益/效果四组）。"
            if has_metrics
            else "金字塔数据报告页未解析到效果指标，请人工复核页面。"
        ),
        "sections": {"金字塔": payload.get("ad_pyramid")},
        "data_period_label": (
            _date.today().strftime("%Y/%m/%d") if preset_label == "今天" else None
        ),
        "evidence": {
            "page_url": page.url,
            "selected_control": preset_label,
            "range_text": range_text or None,
        },
    }


async def _select_pyramid_custom_range(page: Page, start: _date, end: _date) -> bool:
    """直接经 #dateRange_b 打开 daterangepicker 选区间（autoApply），断言区间文本。

    不点「自定义」模式按钮：日历直连隐藏 input，选完区间 KO 自动应用并刷新
    报表（2026-09-16 实测；先点模式按钮反而会让控件进入吞点击的残留态）。
    """
    # 打开日历：首击挂载（hidden）再击展开，循环点击直到 monthselect 可读。
    for _ in range(4):
        try:
            await page.evaluate(
                "() => { const el = document.querySelector('#dateRange_b')"
                " || document.querySelector('input.date-picker-input');"
                "if (el) el.click(); }"
            )
        except Exception:
            return False
        await page.wait_for_timeout(1_200)
        if await page.evaluate(_JS_DRP_MONTH_YEAR) is not None:
            break
    if await page.evaluate(_JS_DRP_MONTH_YEAR) is None:
        return False

    for probe in (start, end):
        target = probe.year * 12 + (probe.month - 1)
        for _ in range(4):
            current = await page.evaluate(_JS_DRP_MONTH_YEAR)
            if not isinstance(current, dict):
                return False
            if current.get("year", 0) * 12 + int(current.get("month", -1)) == target:
                break
            if (
                await page.evaluate(
                    _JS_DRP_SET_SELECTS,
                    {"year": probe.year, "month": probe.month - 1},
                )
                != "set"
            ):
                return False
            await page.wait_for_timeout(700)
        else:
            return False
        if (
            await page.evaluate(
                _JS_DRP_CLICK_DAY, {"month": probe.month - 1, "day": probe.day}
            )
            != "clicked"
        ):
            return False
        await page.wait_for_timeout(1_000)
    # autoApply：选完即提交；等报表刷新后断言区间文本。
    await page.wait_for_timeout(2_500)
    expected_text = _format_pyramid_range_text(start, end)
    actual = await page.evaluate(_JS_PYRAMID_RANGE_TEXT)
    return actual == expected_text
