"""EBK 全局推送三分类卡 builder（设计文档 §5.5/§6）。

「三张卡」是逻辑分类边界，不是消息数量上限：同类表格需求超过飞书
单卡 5 表配额时按 CardUX5 机制同类分页连发（标题 (i/N)、组跨页「（续）」）。
本模块只消费投影层输出的「实时/当前状态」行，不读取页面、不解析 DOM，
也不改动旧平台卡 builder（``build_platform_snapshot_feishu_card``）。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")

from app.domain.notifications.feishu_card_tables import render_metric_group_pages
from app.domain.pyramid_bidding.cards import (
    PYRAMID_BID_FIELD_HEADER,
    PYRAMID_BID_PART_HEADERS,
)
from app.domain.ebk_global_push.projection import (
    CATEGORY_ADVERTISING,
    CATEGORY_BUSINESS,
    CATEGORY_PROMOTION,
    FRESHNESS_SCORE_SNAPSHOT,
    format_row_value,
    normalize_projection_rows,
)

_CATEGORY_ORDER = (CATEGORY_BUSINESS, CATEGORY_PROMOTION, CATEGORY_ADVERTISING)
_CATEGORY_TITLES = {
    CATEGORY_BUSINESS: "经营数据",
    CATEGORY_PROMOTION: "活动促销",
    CATEGORY_ADVERTISING: "广告营销",
}

_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}
_PLATFORM_ORDER = ("ctrip", "meituan", "fliggy")
# 平台特有标志（2026-08-28 运营确认）：板块标题前用平台标识，不再用通用数据集图标。
_PLATFORM_ICONS = {"ctrip": "✈️", "meituan": "🦘", "fliggy": "🧳"}

_DATASET_LABELS = {
    "business_outline": "收益概览",
    "competition_profile": "经营对比",
    "workbench_home": "工作台",
    "data_center_home": "数据中心·首页",
    "data_center_trade": "数据中心·交易",
    "data_center_traffic": "数据中心·流量",
    "data_center_service": "数据中心·服务",
    "comment_manage_home": "评价管理",
    "home_v1": "首页",
    "operating_data": "经营数据",
    "diagnose_report": "诊断报告",
    "promotion_effect": "促销效果",
    "promotion_registered": "已参加促销",
    "promotion_coupon": "神券活动",
    "promotion_agent": "已参加活动",
    "ad_pyramid": "广告金字塔",
    "pyramid_bid": "金字塔出价",
    "ad_cpc": "推广通",
    "ad_p4p": "直通车",
}

# 美团/飞猪广告（2026-09-11 起按店铺区分）：已开通的店铺（如立瑞）显示
# ad_cpc/ad_p4p 实读数据；未开通的店铺无广告行，按平台回落「未投放」占位。
_AD_NOT_CONFIGURED_PLATFORMS = ("meituan", "fliggy")
_AD_NOT_CONFIGURED_TEXT = "未投放"

# 经营卡分页（2026-09-04 运营确认）：1/N 只放携程；2/N 起始放美团/飞猪点评分，
# 后接美团、飞猪分区；「评分总览」小节删除。
_BUSINESS_PAGE2_SCORE_LABELS = {
    ("meituan", "review_score"): "美团点评分",
    ("fliggy", "guest_review_score"): "飞猪点评得分",
}

_TRUSTED_READ_STATUSES = {"success", "partial"}
_FAILED_READ_STATUSES = {
    "error",
    "failed",
    "human_required",
    "needs_review",
    "config_required",
}

_NEXT_STEP_BY_STATUS = {
    "human_required": "下一步：人工完成登录或安全验证后重试",
    "needs_review": "下一步：请复核页面结构或字段证据",
    "error": "下一步：稍后自动重试；连续失败会触发告警",
    "failed": "下一步：稍后自动重试；连续失败会触发告警",
    "config_required": "下一步：请在对应平台模块绑定可用的 EBK 账号",
}


def build_global_push_cards(
    *,
    store_name: str | None,
    read_time: str | datetime | None,
    overall_status: str,
    overall_summary: str = "",
    score_summary: dict[str, str] | None = None,
    platform_sections: list[dict[str, Any]] | None = None,
    title_prefixes: dict[str, str] | None = None,
    platform_store_names: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """组装三分类卡片。

    返回 ``{category: [card, ...]}``；同类多卡即分页连发（(i/N)）。
    每张卡顶部重复门店/读取时间/整体状态头（与现有平台卡一致）。
    经营卡（2026-09-04 运营确认）：1/N 只放携程；2/N 起始放美团/飞猪点评分，
    后接美团、飞猪分区；「评分总览」小节删除；各页门店行显示对应平台店铺名
    （platform_store_names 缺省时回落 store_name）。
    """

    prefixes = {key: str(value).strip() for key, value in (title_prefixes or {}).items()}
    store_names = {
        str(key): str(value).strip()
        for key, value in (platform_store_names or {}).items()
        if str(value or "").strip()
    }
    summary = score_summary or {}
    normalized_sections = _normalized_sections(platform_sections)
    header_kwargs = dict(
        read_time=read_time,
        overall_status=overall_status,
        overall_summary=overall_summary,
    )
    cards: dict[str, list[dict[str, Any]]] = {}
    for category in _CATEGORY_ORDER:
        base_title = prefixes.get(category) or _CATEGORY_TITLES[category]
        if category == CATEGORY_BUSINESS:
            suffix = str(summary.get("title_suffix") or "").strip()
            title = f"{base_title}｜{suffix}" if suffix else base_title
            cards[category] = _business_cards(
                title=title,
                platform_sections=normalized_sections,
                store_name=store_name,
                store_names=store_names,
                overall_status=overall_status,
                header_kwargs=header_kwargs,
            )
            continue
        groups = _category_groups(
            category=category,
            platform_sections=normalized_sections,
        )
        pages = render_metric_group_pages(groups, max_tables=5)
        cards[category] = _wrap_pages(
            pages=pages,
            title=base_title,
            header_text=_header_block(store_name=store_name, **header_kwargs),
            overall_status=overall_status,
        )
    return cards


def _business_cards(
    *,
    title: str,
    platform_sections: list[dict[str, Any]],
    store_name: str | None,
    store_names: dict[str, str],
    overall_status: str,
    header_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    """经营卡：携程页在前、美团/飞猪页在后，各自门店行显示对应店铺名。

    2026-09-04 运营确认：
    - 1/N 标题：携程 PSI + 携程店铺评分（score_summary 后缀，原有）。
    - 2/N 标题：美团点评分 + 飞猪点评得分（从 sections score_snapshot 提取）。
    - 不再有表格形式的「点评分」小节（评分在标题里，表格去掉）。
    """

    ctrip_groups = _category_groups(
        category=CATEGORY_BUSINESS,
        platform_sections=platform_sections,
        platforms=("ctrip",),
    )
    non_ctrip_groups = _category_groups(
        category=CATEGORY_BUSINESS,
        platform_sections=platform_sections,
        platforms=("meituan", "fliggy"),
    )
    ctrip_pages = render_metric_group_pages(ctrip_groups, max_tables=5)
    other_pages = render_metric_group_pages(non_ctrip_groups, max_tables=5)

    pages = [page for page in ctrip_pages] + [page for page in other_pages]

    headers = [
        _header_block(
            store_name=store_names.get("ctrip") or store_name, **header_kwargs
        )
    ] * len(ctrip_pages)
    other_store_line = "、".join(
        part
        for part in (
            store_names.get("meituan"),
            store_names.get("fliggy"),
        )
        if part
    )
    other_header = _header_block(
        store_name=other_store_line or store_name, **header_kwargs
    )
    headers.extend([other_header] * len(other_pages))

    # 分页标题：携程页沿用 ctrip 后缀；美团/飞猪页用美团点评分 + 飞猪点评得分。
    page2_suffix = _page2_score_suffix(platform_sections)
    base_title = title.split("｜")[0]
    titles = [title] * len(ctrip_pages)
    other_title = f"{base_title}｜{page2_suffix}" if page2_suffix else base_title
    titles.extend([other_title] * len(other_pages))

    return _wrap_pages_with_headers(
        pages=pages,
        headers=headers,
        titles=titles,
        overall_status=overall_status,
    )


def _page2_score_suffix(
    platform_sections: list[dict[str, Any]] | None,
) -> str:
    """从 sections 提取美团点评分 + 飞猪点评得分，拼成标题后缀。"""

    parts: list[str] = []
    for platform in ("meituan", "fliggy"):
        section = _section_for(platform_sections, platform)
        if section is None:
            continue
        for raw in section.get("rows") or []:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("freshness") or "") != FRESHNESS_SCORE_SNAPSHOT:
                continue
            label = _BUSINESS_PAGE2_SCORE_LABELS.get(
                (str(raw.get("platform") or ""), str(raw.get("field_key") or ""))
            )
            if label is None:
                continue
            parts.append(f"{label} {format_row_value(raw)}")
    return "｜".join(parts)


def _wrap_pages_with_headers(
    *,
    pages: list[list[dict[str, Any]]],
    headers: list[str],
    titles: list[str] | None = None,
    title: str | None = None,
    overall_status: str,
) -> list[dict[str, Any]]:
    if titles is None:
        titles = [title or ""] * len(pages) if pages else [title or ""]
    if not pages:
        return [
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(overall_status),
                    "title": {"tag": "plain_text", "content": titles[0] if titles else ""},
                },
                "elements": [
                    _card_text(headers[0] if headers else ""),
                ],
            }
        ]
    total = len(pages)
    cards: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        page_title = titles[index] if index < len(titles) else (titles[-1] if titles else "")
        card_title = page_title if total == 1 else f"{page_title}（{index + 1}/{total}）"
        header_text = headers[index] if index < len(headers) else (headers[-1] if headers else "")
        cards.append(
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(overall_status),
                    "title": {"tag": "plain_text", "content": card_title},
                },
                "elements": [_card_text(header_text), *page],
            }
        )
    return cards


def _normalized_sections(
    platform_sections: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for section in platform_sections or []:
        if not isinstance(section, dict):
            continue
        accepted, _rejected = normalize_projection_rows(section.get("rows"))
        normalized.append({**section, "rows": accepted})
    return normalized


def _category_groups(
    *,
    category: str,
    platform_sections: list[dict[str, Any]] | None,
    platforms: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for platform in platforms or _PLATFORM_ORDER:
        section = _section_for(platform_sections, platform)
        if section is None:
            continue
        platform_label = _PLATFORM_LABELS.get(platform, platform)
        platform_icon = _PLATFORM_ICONS.get(platform, "📋")
        status = str(section.get("status") or "unknown").strip().lower()

        if status in _FAILED_READ_STATUSES:
            status_icon = "⚠️" if status in {"human_required", "needs_review"} else "❌"
            state_text = "配置未完成" if status == "config_required" else "读取失败"
            groups.append(
                {
                    "title": (
                        f"{platform_icon}{status_icon} {platform_label}：{state_text}"
                        f"（{_status_label(status)}）"
                    ),
                    "rows": _failure_rows(status, section.get("summary")),
                }
            )
            continue

        if category == CATEGORY_ADVERTISING and platform in _AD_NOT_CONFIGURED_PLATFORMS:
            ad_rows = _section_rows(section, category)
            if ad_rows:
                groups.extend(_dataset_groups(platform, platform_label, ad_rows))
            else:
                groups.append(
                    {
                        "title": f"{platform_icon} {platform_label}·广告",
                        "rows": [{"field": "广告数据", "parts": [_AD_NOT_CONFIGURED_TEXT]}],
                    }
                )
            continue

        rows = _section_rows(section, category)
        if rows:
            if category == CATEGORY_BUSINESS and platform == "ctrip":
                groups.extend(_ctrip_business_groups(platform_icon, platform_label, rows))
            elif category == CATEGORY_BUSINESS and platform == "fliggy":
                groups.extend(_fliggy_business_groups(platform_icon, platform_label, rows))
            elif category == CATEGORY_BUSINESS and platform == "meituan":
                groups.extend(_meituan_business_groups(platform_icon, platform_label, rows))
            else:
                groups.extend(_dataset_groups(platform, platform_label, rows))
        elif status in _TRUSTED_READ_STATUSES:
            placeholder = (
                "当前无可展示活动"
                if category == CATEGORY_PROMOTION
                else "已读取，但未识别出可展示指标"
            )
            groups.append(
                {
                    "title": f"{platform_icon} {platform_label}",
                    "rows": [{"field": "数据状态", "parts": [placeholder]}],
                }
            )
    return groups


def _section_for(
    platform_sections: list[dict[str, Any]] | None,
    platform: str,
) -> dict[str, Any] | None:
    for section in platform_sections or []:
        if not isinstance(section, dict):
            continue
        if str(section.get("platform") or "").strip().lower() == platform:
            return section
    return None


def _section_rows(
    section: dict[str, Any],
    category: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in section.get("rows") or []:
        if not isinstance(raw, dict) or raw.get("category") != category:
            continue
        # 经营分类的评分行统一进「评分总览」，数据集表格不再重复展示。
        if category == CATEGORY_BUSINESS and str(raw.get("freshness") or "") == FRESHNESS_SCORE_SNAPSHOT:
            continue
        row: dict[str, Any] = {
            "field": _display_label(raw.get("label")),
            "parts": [format_row_value(raw)],
            "dataset_id": str(raw.get("dataset_id") or ""),
            "field_key": str(raw.get("field_key") or ""),
        }
        raw_dimension = str(raw.get("dimension") or "").strip()
        if raw_dimension:
            row["_raw_dimension"] = raw_dimension
        display_dimension = _display_dimension(raw.get("dimension"))
        if display_dimension:
            row["dimension"] = display_dimension
        # 金字塔出价行携带适配层预格式化的表格列（出价/预算/花费/状态），
        # 透传给共享表格渲染器；其余行维持「单值 + 展示格式化」。
        table_parts = _trusted_table_parts(raw.get("parts"))
        if table_parts:
            row["parts"] = table_parts
        comparison = str(raw.get("comparison") or "").strip()
        if comparison:
            row["comparison"] = comparison
        competitive_rank = str(raw.get("competitive_rank") or "").strip()
        if competitive_rank:
            row["competitive_rank"] = competitive_rank
        rows.append(row)
    return rows


def _display_label(label: Any) -> str:
    """卡片本身即实时/当日口径：展示标签去掉「实时」「今日」前缀，不重复口径说明。"""

    text = str(label or "").strip()
    changed = True
    while changed and len(text) > 2:
        changed = False
        for prefix in ("实时", "今日"):
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text or "指标"


def _trusted_table_parts(parts: Any) -> list[str]:
    """适配层附带的表格列（pyramid_bid 行契约）：非空字符串列表才透传。"""

    if not isinstance(parts, list):
        return []
    cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
    return cleaned if cleaned else []


def _display_dimension(dimension: Any) -> str:
    """板块列同理：「实时」「今日」「今日实时」为默认口径、「活动」与活动卡
    分类重复，均无信息量，省略；有信息量的板块（如「在店」「预定」）保留。
    「广告」与广告卡分类重复（ad_pyramid 行板块前缀），同样剥离（2026-08-28 运营反馈）。"""

    text = str(dimension or "").strip()
    if text in {"实时", "今日", "今日实时", "活动", "广告"}:
        return ""
    return text


# 携程卡片重构分组常量（2026-09-01 需求 1-4）
# 2026-09-21：departure_sales_amount（离店销售额，经营概览页昨日数据块）进收益概览。
_CTRIP_REVENUE_KEYS = (
    "booking_sales",
    "realtime_booking_orders",
    "realtime_in_house_room_nights",
    "departure_sales_amount",
)
# 流量曝光字段（数据集 flow_data 实时维度 + competition_profile 的 APP 流量字段）
_CTRIP_FLOW_KEYS = ("realtime_visitors", "realtime_visitor_rank", "realtime_starting_price")
# 经营概览页流量概览区同名字段（2026-09-01 实测）：流量页缺失时兜底，同现时以 flow_data 为准
_CTRIP_OUTLINE_TRAFFIC_KEYS = ("realtime_visitors", "realtime_visitor_rank")
_CTRIP_COMPETITION_TRAFFIC_FIELDS = ("ctrip_app_visitors", "ctrip_app_conversion_rate")


def _competition_total(rows: list[dict[str, Any]]) -> str:
    """从带「X/总数」格式的竞争圈排名行中提取总数，用于给单数字排名补总数。"""
    for row in rows:
        rank = str(row.get("competitive_rank") or "")
        if "/" in rank:
            return rank.rsplit("/", 1)[-1].strip()
    return ""


def _format_rank(rank: Any, total: str) -> str:
    """竞争圈排名统一为「N/总数」；无总数时保留原值。"""
    text = str(rank or "").strip()
    if not text:
        return "—"
    if "/" in text or not total:
        return text
    return f"{text}/{total}"


def _ctrip_business_groups(
    platform_icon: str,
    platform_label: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    revenue_rows: list[dict[str, Any]] = []
    competition_rows: list[dict[str, Any]] = []
    traffic_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []

    for row in rows:
        dataset_id = row.get("dataset_id") or ""
        field_key = row.get("field_key") or ""
        if dataset_id == "competition_profile":
            if field_key in _CTRIP_COMPETITION_TRAFFIC_FIELDS:
                traffic_rows.append(row)
            else:
                competition_rows.append(row)
        elif dataset_id == "business_outline" and field_key in _CTRIP_REVENUE_KEYS:
            revenue_rows.append(row)
        elif field_key in _CTRIP_FLOW_KEYS and dataset_id in {"flow_data", "business_outline"}:
            traffic_rows.append(row)
        else:
            other_rows.append(row)

    # 经营概览页与流量页都有实时访客量/实时排名：以 flow_data 行为准去重。
    flow_field_keys = {
        str(row.get("field_key") or "")
        for row in traffic_rows
        if row.get("dataset_id") == "flow_data"
    }
    if flow_field_keys:
        traffic_rows = [
            row
            for row in traffic_rows
            if row.get("dataset_id") == "flow_data"
            or str(row.get("field_key") or "") not in flow_field_keys
        ]

    total = _competition_total(rows)

    groups: list[dict[str, Any]] = []
    if revenue_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·收益概览",
            "rows": [_revenue_row(r) for r in revenue_rows],
            "part_headers": ["内容", "较上次"],
        })
    if competition_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·经营对比",
            "rows": [_competition_row(r, total) for r in competition_rows],
            "part_headers": ["内容", "竞争圈排名"],
        })
    if traffic_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·流量曝光",
            "rows": [_traffic_row(r, total) for r in traffic_rows],
            "part_headers": ["内容", "竞争圈排名"],
        })
    groups.extend(_dataset_groups("ctrip", platform_label, other_rows))
    return groups


def _revenue_row(row: dict[str, Any]) -> dict[str, Any]:
    parts = list(row.get("parts") or [])
    comparison = str(row.get("comparison") or "").strip()
    parts.append(comparison if comparison else "—")
    return {"field": row["field"], "parts": parts}


def _competition_row(row: dict[str, Any], total: str = "") -> dict[str, Any]:
    parts = list(row.get("parts") or [])
    parts.append(_format_rank(row.get("competitive_rank"), total))
    return {"field": row["field"], "parts": parts}


def _traffic_row(row: dict[str, Any], total: str = "") -> dict[str, Any]:
    field = row["field"]
    field_key = row.get("field_key") or ""
    if field_key in {"realtime_visitor_rank", "realtime_starting_price"} and not field.startswith("实时"):
        field = "实时" + field
    parts = list(row.get("parts") or [])
    # 实时排名：数值即排名，标注「（排名）」
    if field_key == "realtime_visitor_rank":
        if parts:
            parts[0] = f"{parts[0]}（排名）"
        if "（城市排名）" not in field:
            field = field + "（城市排名）"
    parts.append(_format_rank(row.get("competitive_rank"), total))
    return {"field": field, "parts": parts}


def _fliggy_business_groups(
    platform_icon: str,
    platform_label: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """飞猪：首页+预订间夜合并进「收益概览」（带较上次幅度），流量数据进「流量曝光」。"""
    revenue_rows: list[dict[str, Any]] = []
    traffic_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    revenue_seen: set[str] = set()
    for row in rows:
        dimension = str(row.get("_raw_dimension") or row.get("dimension") or "")
        dataset_id = row.get("dataset_id") or ""
        field_key = str(row.get("field_key") or "")
        # 首页「浏览人数」是流量指标（2026-09-01 需求：流量数据归流量曝光模块）。
        if dimension == "流量" or (dataset_id == "home_v1" and field_key == "浏览人数"):
            traffic_rows.append(row)
        elif dataset_id in {"home_v1", "operating_data"} and dimension in {"", "今日", "预定", "在店"}:
            # 首页与经营数据页同名指标去重（预订间夜；订单量/订单互为别名），保留先出现的首页行。
            dedupe_key = re.sub(r"量$", "", str(row.get("field") or ""))
            if dedupe_key in revenue_seen:
                continue
            revenue_seen.add(dedupe_key)
            revenue_rows.append(row)
        else:
            other_rows.append(row)

    groups: list[dict[str, Any]] = []
    if revenue_rows:
        # 2026-09-04 运营反馈：飞猪经营行少时会把诊断报告等 other_rows 挤到第三张卡，
        # 超过每卡 5 表上限。把 other_rows 折进收益概览组（同为飞猪经营指标），
        # 保证美团+飞猪内容能收进第二张卡。
        groups.append({
            "title": f"{platform_icon} {platform_label}·收益概览",
            "rows": (
                [_revenue_row(r) for r in revenue_rows]
                + [{"field": r["field"], "parts": list(r.get("parts") or [])} for r in other_rows]
            ),
            "part_headers": ["内容", "较上次"],
        })
    if traffic_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·流量曝光",
            "rows": [{"field": r["field"], "parts": list(r.get("parts") or [])} for r in traffic_rows],
        })
    if not revenue_rows and other_rows:
        groups.extend(_dataset_groups("fliggy", platform_label, other_rows))
    return groups


# 2026-09-01 复审#1：收益概览显示入住间夜（页面当日可能为「-」占位也保留），
# 不显示销售间夜；满房率不属于收益/流量口径（复审#3），两者整行不展示。
_MEITUAN_REVENUE_KEYS = ("销售额", "支付订单数", "入住间夜")
# 2026-09-02 需求：流量曝光精简为 2 项（浏览人数/浏览-支付转化率），
# 其余流量字段不进卡；工作台「流失订单量」暂不需要展示。
_MEITUAN_SUPPRESSED_FIELDS = frozenset({
    "销售间夜",
    "满房率",
    "引流价",
    "曝光量",
    "曝光人数",
    "支付转化率",
    "曝光-浏览转化率",
    "流失订单量",
})


def _meituan_business_groups(
    platform_icon: str,
    platform_label: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """美团经营卡（2026-09-01 复审）：收益概览（值+较上期幅度+同行排名 N/M）放第一，
    流量曝光（浏览人数/支付转化率等）同口径；工作台实时行仅在数据中心无收益行时兜底。"""

    revenue_rows: list[dict[str, Any]] = []
    traffic_rows: list[dict[str, Any]] = []
    peer_rows: list[dict[str, Any]] = []
    workbench_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    for row in rows:
        dimension = str(row.get("_raw_dimension") or row.get("dimension") or "")
        dataset_id = row.get("dataset_id") or ""
        field_key = str(row.get("field_key") or "")
        field = str(row.get("field") or "")
        if field in _MEITUAN_SUPPRESSED_FIELDS or field_key in _MEITUAN_SUPPRESSED_FIELDS:
            continue
        if dimension == "流量趋势":
            traffic_rows.append(row)
        elif dimension == "同行榜单":
            peer_rows.append(row)
        elif dataset_id == "workbench_home":
            workbench_rows.append(row)
        elif field_key in _MEITUAN_REVENUE_KEYS:
            revenue_rows.append(row)
        else:
            other_rows.append(row)

    # 首页与流量分析的「浏览人数」等同名行去重：保留后出现的流量分析值
    #（数据集顺序 首页 → 流量分析，流量分析页签为流量口径权威来源）。
    traffic_by_field: dict[str, dict[str, Any]] = {}
    for row in traffic_rows:
        traffic_by_field[str(row.get("field") or "")] = row
    traffic_rows = list(traffic_by_field.values())

    if revenue_rows:
        # 数据中心收益行已覆盖时，工作台同名实时行（无对比/排名的子集）去重不重复展示。
        covered = {str(r.get("field_key") or "") for r in revenue_rows}
        covered.update(str(r.get("field_key") or "") for r in traffic_rows)
        other_rows.extend(
            r for r in workbench_rows if str(r.get("field_key") or "") not in covered
        )
    else:
        revenue_rows = workbench_rows

    groups: list[dict[str, Any]] = []
    if revenue_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·收益概览",
            "rows": [_meituan_metric_row(r) for r in revenue_rows],
            "part_headers": ["内容", "较上期", "同行排名"],
        })
    if peer_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·同行榜单",
            "rows": [_meituan_peer_ranking_row(r) for r in peer_rows],
        })
    if traffic_rows:
        groups.append({
            "title": f"{platform_icon} {platform_label}·流量曝光",
            "rows": [_meituan_metric_row(r) for r in traffic_rows],
            "part_headers": ["内容", "较上期", "同行排名"],
        })
    groups.extend(_dataset_groups("meituan", platform_label, other_rows))
    return groups


def _meituan_peer_ranking_row(row: dict[str, Any]) -> dict[str, Any]:
    # 同行榜单行单值：本店名次/榜内总数（值本身已是 N/M，不再拼列）。
    return {"field": row["field"], "parts": list(row.get("parts") or [])}


def _meituan_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    # 美团指标行三栏：值、较上期幅度（环比注入/页面较上期）、同行排名（N/总数）。
    # 页面缺失占位（「- 间夜」等）统一显示「—」，避免与负值混淆。
    parts = list(row.get("parts") or [])
    if parts:
        value_text = str(parts[0]).strip()
        if value_text in {"-", "–", "—"} or value_text.startswith("- "):
            parts[0] = "—"
    comparison = str(row.get("comparison") or "").strip()
    parts.append(comparison if comparison else "—")
    parts.append(_format_rank(row.get("competitive_rank"), ""))
    return {"field": row["field"], "parts": parts}


def _dataset_groups(
    platform: str,
    platform_label: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        dataset_id = row.get("dataset_id") or ""
        if dataset_id not in by_dataset:
            by_dataset[dataset_id] = []
            order.append(dataset_id)
        by_dataset[dataset_id].append(row)
    platform_icon = _PLATFORM_ICONS.get(platform, "📋")
    groups: list[dict[str, Any]] = []
    for dataset_id in order:
        label = _DATASET_LABELS.get(dataset_id, dataset_id or "数据")
        group: dict[str, Any] = {
            "title": f"{platform_icon} {platform_label}·{label}",
            "rows": by_dataset[dataset_id],
        }
        # 金字塔出价：行携带四列表格 parts 时套用与指令卡一致的列头；
        # 占位行（暂无推广计划）不套出价列头。
        if dataset_id == "pyramid_bid" and all(
            len(_trusted_table_parts(row.get("parts")))
            >= len(PYRAMID_BID_PART_HEADERS)
            for row in by_dataset[dataset_id]
        ):
            group["field_header"] = PYRAMID_BID_FIELD_HEADER
            group["part_headers"] = list(PYRAMID_BID_PART_HEADERS)
        groups.append(group)
    return groups


def _wrap_pages(
    *,
    pages: list[list[dict[str, Any]]],
    title: str,
    header_text: str,
    overall_status: str,
) -> list[dict[str, Any]]:
    if not pages:
        return [
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(overall_status),
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [_card_text(header_text)],
            }
        ]
    total = len(pages)
    cards: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        card_title = title if total == 1 else f"{title}（{index + 1}/{total}）"
        cards.append(
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": _card_template(overall_status),
                    "title": {"tag": "plain_text", "content": card_title},
                },
                "elements": [_card_text(header_text), *page],
            }
        )
    return cards


def _failure_rows(status: str, summary: Any) -> list[dict[str, Any]]:
    parts = [_status_label(status)]
    summary_text = str(summary or "").strip()
    if summary_text:
        parts.append(summary_text)
    rows = [{"field": "读取状态", "parts": parts}]
    next_step = _NEXT_STEP_BY_STATUS.get(status)
    if next_step:
        rows.append({"field": "下一步", "parts": [next_step]})
    return rows


def _header_block(
    *,
    store_name: str | None,
    read_time: str | datetime | None,
    overall_status: str,
    overall_summary: str,
) -> str:
    lines: list[str] = []
    if store_name:
        lines.append(f"**🏨 门店**：{_escape_lark_md(store_name)}")
    lines.append(f"**🕒 读取时间**：{_format_read_time(read_time)}")
    lines.append(f"**{_status_icon(overall_status)} 整体状态**：{_status_label(overall_status)}")
    summary_text = str(overall_summary or "").strip()
    if summary_text:
        lines.append(f"**📌 结果**：{_escape_lark_md(summary_text)}")
    return "\n".join(lines)


def _card_text(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _escape_lark_md(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([*_`~\[\]])", r"\\\1", text)


def _format_read_time(value: str | datetime | None) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_SHANGHAI)
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_SHANGHAI)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


def _status_label(status: str) -> str:
    mapping = {
        "success": "成功",
        "partial": "部分成功",
        "human_required": "需人工处理",
        "needs_review": "需复核",
        "error": "失败",
        "failed": "失败",
        "skipped": "已跳过",
        "config_required": "需配置",
    }
    return mapping.get(str(status or "").strip().lower(), str(status or "未知"))


def _status_icon(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "success":
        return "✅"
    if normalized in {"partial", "human_required", "needs_review"}:
        return "⚠️"
    if normalized in {"error", "failed"}:
        return "❌"
    if normalized == "skipped":
        return "⏭️"
    return "ℹ️"


def _card_template(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "success":
        return "green"
    if normalized in {"human_required", "needs_review", "partial"}:
        return "orange"
    if normalized in {"error", "failed"}:
        return "red"
    return "blue"
