from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.browser.ctrip_common import _dedupe_keep_order


# EBK visible-text parsers. These are pure functions: no browser actions and no
# external side effects, which keeps browser orchestration easier to review.
@dataclass(frozen=True)
class _CtripDatasetDefinition:
    dataset_id: str
    label: str
    url: str
    expected_terms: tuple[str, ...]

def _extract_business_overview(body_text: str) -> dict[str, Any]:
    lines = _clean_lines(body_text)
    highlights = _extract_highlights(lines)
    numeric_facts = _extract_numeric_facts(lines)
    business_sections = _extract_business_sections(lines)
    possible_orders = _extract_possible_orders(lines)
    dashboard_metrics = _extract_dashboard_metrics(lines)
    analysis_input = {
        "source": "ctrip_ebooking_real_browser_visible_text",
        "read_model": "read_only_snapshot",
        "dashboard_metrics": dashboard_metrics,
        "highlight_count": len(highlights),
        "numeric_fact_count": len(numeric_facts),
        "possible_order_count": len(possible_orders),
        "metrics": _extract_metric_summary(numeric_facts),
        "orders": possible_orders[:10],
        "alerts": _extract_alert_lines(lines),
        "inventory_lines": business_sections.get("inventory", []),
        "rate_lines": business_sections.get("rate", []),
    }
    return {
        "analysis_input": analysis_input,
        "dashboard_metrics": dashboard_metrics,
        "business_sections": business_sections,
        "numeric_facts": numeric_facts[:40],
        "possible_orders": possible_orders[:20],
        "text_snippets": lines[:24],
        "highlight_lines": highlights[:24],
    }


def _extract_comment_monitor_snapshot(
    *,
    body_text: str,
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    lines = _clean_dataset_lines(body_text)
    return _extract_comment_analysis_snapshot(lines=lines, filters=filters)


def _build_monitor_snapshot(dataset_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    datasets = {payload["dataset_id"]: payload.get("data", {}) for payload in dataset_payloads}
    return {
        "revenue_overview": _monitor_revenue_overview(datasets.get("business_outline", {})),
        "sales_data": _monitor_sales_data(datasets.get("beneficial_data", {})),
        "flow_data": _monitor_flow_data(datasets.get("flow_data", {})),
        "competition_profile": _monitor_competition_profile(
            datasets.get("competition_profile", {})
        ),
        "loss_analysis": _monitor_loss_analysis(datasets.get("loss_analysis", {})),
        "sales_ranking": _monitor_sales_ranking(datasets.get("competition_list", {})),
    }

def _monitor_revenue_overview(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "updated_at": dataset.get("updated_at"),
        "realtime": [
            _monitor_metric(metrics, "realtime_booking_orders"),
            _monitor_metric(metrics, "realtime_in_house_room_nights"),
        ],
        "yesterday": [
            _monitor_metric(metrics, "departure_sales_amount"),
            _monitor_metric(metrics, "departure_room_nights"),
            _monitor_metric(metrics, "transaction_rate"),
            _monitor_metric(metrics, "average_selling_price"),
        ],
    }

def _monitor_sales_data(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "booking": [
            _monitor_metric(metrics, "booking_orders"),
            _monitor_metric(metrics, "room_nights"),
            _monitor_metric(metrics, "sales_amount"),
        ],
        "in_house": [
            _monitor_metric(metrics, "in_house_room_nights"),
            _monitor_metric(metrics, "tightness"),
            _monitor_metric(metrics, "occupancy_rate"),
        ],
        "departure": [
            _monitor_metric(metrics, "departure_room_nights"),
            _monitor_metric(metrics, "departure_sales_amount"),
            _monitor_metric(metrics, "transaction_rate"),
            _monitor_metric(metrics, "average_selling_price"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
    }

def _monitor_flow_data(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "overview": [
            _monitor_metric(metrics, "visitors"),
            _monitor_metric(metrics, "conversion_rate"),
        ]
    }

def _monitor_competition_profile(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "business_compare": [
            _monitor_metric(metrics, "booking_orders"),
            _monitor_metric(metrics, "booking_sales"),
            _monitor_metric(metrics, "in_house_room_nights"),
            _monitor_metric(metrics, "occupancy_rate"),
            _monitor_metric(metrics, "ctrip_app_visitors"),
            _monitor_metric(metrics, "ctrip_app_conversion_rate"),
        ]
    }

def _monitor_loss_analysis(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    tables = dataset.get("tables", [])
    top_rows = tables[0].get("rows", [])[:5] if tables else []
    return {
        "summary": [
            _monitor_metric(metrics, "lost_orders"),
            _monitor_metric(metrics, "lost_room_nights"),
            _monitor_metric(metrics, "lost_order_amount"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
        "top_lost_hotels": top_rows,
    }

def _monitor_sales_ranking(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "my_hotel": [
            _monitor_metric(metrics, "sales_ranking_booking_order_rank"),
            _monitor_metric(metrics, "sales_ranking_booking_sales_rank"),
            _monitor_metric(metrics, "sales_ranking_in_house_room_nights_rank"),
            _monitor_metric(metrics, "sales_ranking_occupancy_rate_rank"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
    }

def _monitor_promotion_effect(dataset: dict[str, Any]) -> dict[str, Any]:
    metrics = dataset.get("metrics", {})
    return {
        "overview": [
            _monitor_metric(metrics, "participated_promotions"),
            _monitor_metric(metrics, "promotion_room_nights"),
            _monitor_metric(metrics, "promotion_room_night_share"),
            _monitor_metric(metrics, "average_daily_app_visitors"),
        ],
        "warnings": dataset.get("warnings", [])[:4],
        "tables": dataset.get("tables", [])[:2],
    }

def _monitor_metric(metrics: dict[str, Any], key: str) -> dict[str, Any]:
    metric = metrics.get(key, {})
    if not isinstance(metric, dict):
        metric = {}
    return {
        "key": key,
        "label": metric.get("label") or key,
        "value": metric.get("value"),
        "last_week_same_period": metric.get("last_week_same_period"),
        "week_over_week": metric.get("week_over_week"),
        "change": metric.get("change"),
        "competition_average": metric.get("competition_average"),
        "competitive_rank": metric.get("competitive_rank"),
        "section": metric.get("section"),
        "denominator": metric.get("denominator"),
        "denominator_label": metric.get("denominator_label"),
        "display_value": metric.get("display_value"),
        "related_value": metric.get("related_value"),
        "related_label": metric.get("related_label"),
        "recommendation": metric.get("recommendation"),
    }


def _extract_highlights(lines: list[str]) -> list[str]:
    highlighted = [
        line
        for line in lines
        if any(keyword in line for keyword in _HIGHLIGHT_KEYWORDS)
    ]
    return _dedupe_keep_order(highlighted)[:24]

def _extract_business_sections(lines: list[str]) -> dict[str, list[str]]:
    sections = {
        "orders": ("订单", "预订", "入住", "离店", "取消"),
        "inventory": ("房态", "库存", "可售", "满房", "关房", "开房"),
        "rate": ("房价", "价格", "底价", "促销", "活动"),
        "revenue": ("收益", "销售额", "收入", "间夜", "转化率"),
        "reviews": ("点评", "评分", "差评", "回复"),
        "alerts": ("待处理", "异常", "预警", "违规", "超时"),
    }
    extracted: dict[str, list[str]] = {}
    for section_name, keywords in sections.items():
        matches = [line for line in lines if any(keyword in line for keyword in keywords)]
        extracted[section_name] = _dedupe_keep_order(matches)[:12]
    return extracted

def _extract_dashboard_metrics(lines: list[str]) -> dict[str, Any]:
    realtime_cards = _extract_metric_cards(lines, _REALTIME_METRIC_LABELS, with_rank=True)
    top_summary = _extract_metric_cards(lines, _TOP_SUMMARY_LABELS, with_rank=False)
    pending_summary = _extract_metric_cards(lines, _PENDING_LABELS, with_rank=False)
    psi_summary = _extract_metric_cards(lines, _PSI_LABELS, with_rank=False)
    return {
        "channel_scope": _first_line_containing(lines, ("包含", "携程", "去哪儿", "同程旅行")),
        "updated_at_text": _extract_update_time(lines),
        "realtime": realtime_cards,
        "realtime_by_key": {
            card["key"]: card for card in realtime_cards if card.get("key")
        },
        "top_summary": top_summary,
        "pending_summary": pending_summary,
        "psi_summary": psi_summary,
        "revenue_clues": _extract_revenue_clues(lines),
        "guidance_lines": _extract_guidance_lines(lines),
    }

def _extract_metric_cards(
    lines: list[str],
    labels: dict[str, str],
    *,
    with_rank: bool,
) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        for key, label in labels.items():
            if not _line_matches_metric_label(line, label):
                continue
            value = _extract_value_from_metric_line(line, label) or _next_value_line(
                lines,
                index,
            )
            if not value:
                continue
            card = {
                "key": key,
                "label": label,
                "value": value,
                "raw_label": line,
            }
            if with_rank:
                rank = _next_competitive_rank(lines, index)
                if rank:
                    card["competitive_rank"] = rank
            cards.append(card)
    return _dedupe_metric_cards(cards)

def _line_matches_metric_label(line: str, label: str) -> bool:
    compact_line = re.sub(r"\s+", "", line)
    compact_label = re.sub(r"\s+", "", label)
    if compact_line == compact_label:
        return True
    if not compact_line.startswith(compact_label):
        return False
    return bool(_extract_value_from_metric_line(line, label)) or len(compact_line) <= len(
        compact_label
    ) + 3

def _extract_value_from_metric_line(line: str, label: str) -> str | None:
    suffix = line.replace(label, "", 1)
    suffix = re.sub(r"[ⓘ?？\s]+", " ", suffix).strip()
    match = _BUSINESS_VALUE_RE.search(suffix)
    return match.group(0).strip() if match else None

def _next_value_line(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 : index + 5]:
        if candidate.startswith("竞争圈"):
            continue
        if _is_metric_or_section_label(candidate):
            return None
        if _is_action_or_navigation_line(candidate):
            continue
        match = _BUSINESS_VALUE_RE.search(candidate)
        if match:
            return match.group(0).strip()
    return None

def _next_competitive_rank(lines: list[str], index: int) -> str | None:
    for candidate in lines[index + 1 : index + 7]:
        match = re.search(r"竞争圈\s*([0-9]+/[0-9]+)", candidate)
        if match:
            return match.group(1)
    return None

def _extract_update_time(lines: list[str]) -> str | None:
    for line in lines:
        match = re.search(r"\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}\s*更新", line)
        if match:
            return match.group(0).replace(" ", "")
    return None

def _extract_revenue_clues(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if any(keyword in line for keyword in ("流失金额", "流失", "金额", "销售额"))
        and _BUSINESS_VALUE_RE.search(line)
    ][:8]

def _extract_guidance_lines(lines: list[str]) -> list[str]:
    guidance: list[str] = []
    capture = False
    for line in lines:
        if line in {"经营指导", "平台通知", "待办事项"}:
            capture = True
            continue
        if capture and line in {"首页", "订单管理", "房价房态", "数据中心"}:
            break
        if capture and not _is_action_or_navigation_line(line):
            guidance.append(line)
    return guidance[:10]

def _first_line_containing(lines: list[str], keywords: tuple[str, ...]) -> str | None:
    for line in lines:
        if all(keyword in line for keyword in keywords):
            return line
    return None

def _is_action_or_navigation_line(line: str) -> bool:
    return line in {
        "查看全部",
        "马上行动",
        "立即修改",
        "确认无误",
        "知道了",
        "去查看",
        "去回复",
        "去充值",
        "去续费",
        "更多待办",
    }

def _is_metric_or_section_label(line: str) -> bool:
    labels = {
        *_TOP_SUMMARY_LABELS.values(),
        *_REALTIME_METRIC_LABELS.values(),
        *_PENDING_LABELS.values(),
        *_PSI_LABELS.values(),
        "实时数据",
        "渠道数据",
        "昨日数据",
        "待办事项",
        "经营指导",
        "平台通知",
        "排行榜管理",
    }
    return line in labels

def _dedupe_metric_cards(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in cards:
        key = card["key"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped

def _extract_numeric_facts(lines: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for line in lines:
        if _is_auxiliary_numeric_line(line):
            continue
        if not any(keyword in line for keyword in _HIGHLIGHT_KEYWORDS):
            continue
        numbers = _NUMBER_RE.findall(line)
        if not numbers:
            continue
        facts.append(
            {
                "text": line,
                "numbers": numbers[:8],
                "keyword": _first_matching_keyword(line),
            }
        )
    return facts

def _is_auxiliary_numeric_line(line: str) -> bool:
    if line.startswith("竞争圈"):
        return True
    if "更新" in line and re.search(r"\d{1,2}月\d{1,2}日", line):
        return True
    return False

def _extract_metric_summary(numeric_facts: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for fact in numeric_facts:
        keyword = fact.get("keyword")
        numbers = fact.get("numbers")
        if not isinstance(keyword, str) or not isinstance(numbers, list) or not numbers:
            continue
        summary.setdefault(keyword, numbers[0])
    return summary

def _extract_possible_orders(lines: list[str]) -> list[dict[str, str]]:
    orders: list[dict[str, str]] = []
    for line in lines:
        if not any(keyword in line for keyword in ("订单", "预订", "入住", "离店")):
            continue
        identifiers = _ORDER_ID_RE.findall(line)
        if not identifiers and not _NUMBER_RE.search(line):
            continue
        orders.append(
            {
                "text": line,
                "order_identifiers": ", ".join(identifiers[:3]),
            }
        )
    return orders

def _extract_alert_lines(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if any(keyword in line for keyword in ("待处理", "异常", "预警", "违规", "超时"))
    ][:12]

def _first_matching_keyword(line: str) -> str:
    for keyword in _HIGHLIGHT_KEYWORDS:
        if keyword in line:
            return keyword
    return "unknown"

def _clean_lines(body_text: str) -> list[str]:
    lines = []
    for raw_line in body_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if len(line) >= 2:
            lines.append(line[:220])
    return _dedupe_keep_order(lines)

def _clean_dataset_lines(body_text: str) -> list[str]:
    lines = []
    for raw_line in body_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if len(line) >= 2 or re.fullmatch(r"[+-]?\d+", line):
            lines.append(line[:220])
    return lines

_HIGHLIGHT_KEYWORDS = (
    "订单",
    "预订",
    "入住",
    "离店",
    "取消",
    "房态",
    "库存",
    "可售",
    "满房",
    "关房",
    "开房",
    "房价",
    "价格",
    "促销",
    "活动",
    "收益",
    "销售额",
    "收入",
    "间夜",
    "转化率",
    "点评",
    "评分",
    "待处理",
    "异常",
    "预警",
    "今日",
    "明日",
    "携程",
)

_TOP_SUMMARY_LABELS = {
    "today_new_orders": "今日新订",
    "today_arrivals": "今日入住",
    "pending_reviews": "待回复点评",
    "pending_questions": "待回复问答",
    "pending_audit": "待审核",
}

_REALTIME_METRIC_LABELS = {
    "estimated_sales": "预订销售额",
    "in_house_room_nights": "在店间夜",
    "estimated_orders": "预订订单数",
    "tightness": "紧张度",
}

_PENDING_LABELS = {
    "pending_orders": "待处理订单",
    "todo_items": "待办事项",
}

_PSI_LABELS = {
    "psi_service_score": "PSI服务质量分",
    "psi_base_score": "基础分",
    "psi_bonus_score": "奖励分",
    "psi_deduction": "减分项",
}

_DATASET_SECTION_STOP_LINES = {
    "预订",
    "在店",
    "离店",
    "流量概览",
    "APP流量漏斗",
    "APP流量转化",
    "流量来源",
    "服务概览",
    "服务质量对比",
    "销售排名",
    "流量排名",
    "服务排名",
    "隐私政策规则中心",
    "分享反馈",
}

_BUSINESS_VALUE_RE = re.compile(
    r"[+-]?(?:[¥￥]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?|万|%)?"
)

_NUMBER_RE = re.compile(r"(?:[¥￥]\s*)?\d[\d,]*(?:\.\d+)?%?")

_ORDER_ID_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9-]{7,}\b")

_COMMENT_COUNT_RE = re.compile(r"全部\s*点评\s*[（(]\s*(?P<count>\d[\d,]*)\s*[）)]")

_COMMENT_SCORE_RE = re.compile(r"(?<![\d-])(?P<score>[0-5](?:\.\d+)?)(?![\d-])")

_COMMENT_SCORE_PATTERNS = (
    re.compile(r"(?:总评分|点评分|评分)\s*[:：]?\s*(?P<score>[0-5](?:\.\d+)?)"),
    re.compile(r"(?P<score>[0-5](?:\.\d+)?)\s*分"),
)

_COMMENT_RATING_LABEL_RE = re.compile(r"超棒|优秀|很好|好|一般|较差|差")

_COMMENT_DATE_RE = re.compile(
    r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?"
)

_COMMENT_DIMENSION_LABELS = {
    "设施": "facilities",
    "卫生": "hygiene",
    "环境": "environment",
    "服务": "service",
}

_COMMENT_SNIPPET_TERMS = (
    "全部点评",
    "总评分",
    "点评分",
    "评分",
    "设施",
    "卫生",
    "环境",
    "服务",
    "点评",
)

_CTRIP_DATASETS = {
    "business_outline": _CtripDatasetDefinition(
        dataset_id="business_outline",
        label="经营概览",
        url="https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true",
        expected_terms=("实时预订订单", "实时在店间夜", "昨日离店销售额", "成交率", "平均卖价"),
    ),
    "beneficial_data": _CtripDatasetDefinition(
        dataset_id="beneficial_data",
        label="预订销售数据",
        url=(
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/"
            "beneficialdata?microJump=true"
        ),
        expected_terms=("预订订单数", "间夜量", "销售额", "上周同期", "上周同比"),
    ),
    "flow_data": _CtripDatasetDefinition(
        dataset_id="flow_data",
        label="流量数据",
        url="https://ebooking.ctrip.com/datacenter/inland/businessreport/flowdata?microJump=true",
        expected_terms=("访客量", "转化率", "竞争圈平均", "渠道"),
    ),
    "loss_analysis": _CtripDatasetDefinition(
        dataset_id="loss_analysis",
        label="流失分析",
        url=(
            "https://ebooking.ctrip.com/ebkgrowth/datacenter/competition/"
            "lossanalysis?microJump=true"
        ),
        expected_terms=("流失订单量", "流失间夜量", "流失订单金额", "发现同行", "发现同街"),
    ),
    "competition_list": _CtripDatasetDefinition(
        dataset_id="competition_list",
        label="竞争排名",
        url=(
            "https://ebooking.ctrip.com/ebkgrowth/datacenter/competition/"
            "competitionlist?microJump=true"
        ),
        expected_terms=(
            "我的酒店",
            "预订订单量排名",
            "预订销售额排名",
            "在店间夜排名",
            "出租率排名",
        ),
    ),
    "competition_profile": _CtripDatasetDefinition(
        dataset_id="competition_profile",
        label="竞争画像",
        url=(
            "https://ebooking.ctrip.com/ebkgrowth/datacenter/competition/"
            "competitionprofile?microJump=true"
        ),
        expected_terms=("经营对比", "我的酒店", "竞争圈平均", "竞争圈排名", "携程APP转化率"),
    ),
    "promotion_effect": _CtripDatasetDefinition(
        dataset_id="promotion_effect",
        label="促销效果",
        url="https://ebooking.ctrip.com/revenueroom/prcpromotion/index?target=data",
        expected_terms=("促销中心", "促销效果", "参加促销", "含促销间夜", "竞争圈平均"),
    ),
    "promotion_registered": _CtripDatasetDefinition(
        dataset_id="promotion_registered",
        label="已参加促销",
        url="https://ebooking.ctrip.com/promotion/promotionCenter?tab=Register",
        expected_terms=("促销中心", "已参加促销", "活动", "报名", "促销"),
    ),
    "ad_pyramid": _CtripDatasetDefinition(
        dataset_id="ad_pyramid",
        label="广告金字塔",
        url="https://ebooking.ctrip.com/toolcenter/cpc/dataReport?microJump=true",
        expected_terms=(
            "数据概览",
            "流量",
            "花费",
            "收益",
            "效果",
            "广告投产比",
        ),
    ),
}

_CTRIP_MONITOR_DATASET_IDS = (
    "business_outline",
    "beneficial_data",
    "flow_data",
    "competition_profile",
    "loss_analysis",
    "competition_list",
)


def dataset_expected_terms(dataset: _CtripDatasetDefinition) -> tuple[str, ...]:
    """页面 selector 配置文件化（V7 Step 1）。

    ``page_health_config.json`` 的 ``targets["ctrip:dataset:<id>"].expected_terms``
    可覆盖内置 selector（mtime 变化即热更新，无需改代码/重启）；未配置时回落
    ``_CTRIP_DATASETS`` 内置值。采集逻辑本身不变，仅词表来源文件化。
    """
    from app.domain.page_change_alerts.config import resolve_expected_terms

    return resolve_expected_terms(
        "ctrip", f"dataset:{dataset.dataset_id}", dataset.expected_terms
    )

_GENERIC_DATASET_LABELS = {
    "booking_orders": "预订订单数",
    "room_nights": "间夜量",
    "sales_amount": "销售额",
    "visitors": "访客量",
    "conversion_rate": "转化率",
    "ranking": "竞争圈排名",
}

_DATASET_METRIC_LABELS = {
    "business_outline": {
        "realtime_booking_orders": "实时预订订单",
        "realtime_in_house_room_nights": "实时在店间夜",
        "yesterday_departure_sales": "昨日离店销售额",
        "departure_room_nights": "离店间夜量",
        "transaction_rate": "成交率",
        "average_selling_price": "平均卖价",
        "competition_rank": "竞争圈排名",
    },
    "beneficial_data": {
        "booking_orders": "预订订单数",
        "room_nights": "间夜量",
        "sales_amount": "销售额",
        "last_week_same_period": "上周同期",
        "week_over_week": "上周同比",
    },
    "flow_data": {
        "visitors": "访客量",
        "conversion_rate": "转化率",
        "competition_average": "竞争圈平均",
    },
    "loss_analysis": {
        "lost_orders": "流失订单量",
        "lost_room_nights": "流失间夜量",
        "lost_order_amount": "流失订单金额",
    },
    "competition_profile": {
        "booking_orders": "预订订单量",
        "booking_sales": "预订销售额",
        "in_house_room_nights": "在店间夜",
        "occupancy_rate": "出租率",
        "ctrip_app_visitors": "携程APP访客",
        "ctrip_app_conversion_rate": "携程APP转化率",
    },
    "competition_list": {
        "booking_order_rank": "预订订单量排名",
        "booking_sales_rank": "预订销售额排名",
        "in_house_room_nights_rank": "在店间夜排名",
        "occupancy_rate_rank": "出租率排名",
    },
    "promotion_effect": {
        "participated_promotions": "参加促销",
        "promotion_room_nights": "含促销间夜",
        "promotion_room_night_share": "含促销间夜占比",
        "average_daily_app_visitors": "平均每日 访客量（APP）",
    },
    "ad_pyramid": {
        "clicks": "点击(次)",
        "impressions": "曝光(次)",
        "avg_impression_rank": "平均曝光位次",
        "avg_click_rank": "平均点击位次",
        "total_spend_cny": "总花费(CNY)",
        "avg_click_price": "点击均价",
        "cash_spend": "现金花费",
        "red_packet_spend": "红包花费",
        "booking_amount_cny": "预订金额(CNY)",
        "booking_orders": "预订订单",
        "booking_room_nights": "预订间夜",
        "roas": "广告投产比(ROAS)",
        "click_rate": "点击率",
        "conversion_rate": "转化率",
    },
}

# Re-exports from split modules
from app.domain.browser.ctrip_ebooking_comment_parsers import (  # noqa: F401,E402
    _extract_comment_analysis_snapshot,
    _build_comment_daily_scores,
    _comment_daily_scores_complete,
    _extract_comment_dimension_scores,
    _extract_comment_overall_rating,
    _extract_comment_score_rows,
    _extract_comment_total_count,
    _select_comment_snippets,
)
from app.domain.browser.ctrip_ebooking_dataset_parsers import (  # noqa: F401,E402
    _extract_dataset_metrics,
    _extract_dataset_rankings,
    _extract_dataset_snapshot,
    _extract_dataset_tables,
    _extract_specialized_dataset_payload,
    _resolve_dataset_definition,
)
from app.domain.browser.ctrip_ebooking_promotion_parsers import (  # noqa: F401,E402
    _dedupe_registered_promotions,
    _extract_promotion_effect_metric_table_rows,
    _extract_promotion_effect_promotion_metrics,
    _extract_promotion_effect_suggestions,
    _extract_promotion_effect_table_rows,
    _extract_promotion_page_prompts,
    _extract_promotion_registered_warnings,
    _extract_registered_promotions,
    _extract_registered_promotions_from_date_rows,
    _extract_registered_promotions_from_visible_table,
    _promotion_registered_empty_state,
    _select_promotion_registered_snippets,
)
