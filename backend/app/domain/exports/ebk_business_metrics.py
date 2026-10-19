"""整体业务数据导出规范化：三平台（携程/美团/飞猪）EBK 快照 payload
→ 中文明细长表（每指标一行）+ 跨平台核心指标宽表。

只展开业务指标字段；不展开 evidence / raw_snippets / visible_lines / 页面
元数据，绝不输出 cookie、webhook、browser_session_id、截图路径等敏感信息。
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.ctrip_ebooking_watch.repository import DATASET_LABELS as CTRIP_DATASET_LABELS

PLATFORM_LABELS: dict[str, str] = {
    "ctrip": "携程",
    "meituan": "美团",
    "fliggy": "飞猪",
}

MEITUAN_DATASET_LABELS: dict[str, str] = {
    "workbench_home": "工作台首页",
    "data_center_home": "数据中心首页",
    "data_center_trade": "交易数据",
    "data_center_traffic": "流量数据",
    "data_center_service": "服务数据",
    "promotion_coupon": "促销活动",
    "comment_manage_home": "点评管理",
}

FLIGGY_DATASET_LABELS: dict[str, str] = {
    "home_v1": "工作台首页",
    "diagnose_report": "诊断报告",
    "operating_data": "经营数据",
    "promotion_agent": "促销活动",
}

DETAIL_HEADERS: list[str] = [
    "快照时间",
    "平台",
    "账号",
    "数据来源",
    "酒店",
    "数据集",
    "维度",
    "指标",
    "数值",
    "环比",
    "同比",
    "竞争圈平均",
    "竞争圈排名",
    "排名环比",
    "数据口径",
    "读取状态",
]

CORE_HEADERS: list[str] = [
    "快照时间",
    "平台",
    "账号",
    "数据来源",
    "销售额",
    "订单量",
    "间夜量",
    "出租率",
    "平均房价",
    "转化率",
    "城市排名",
    "PSI",
    "点评分",
]

OPERATING_BRIEF_HEADERS: list[str] = [
    "快照时间",
    "平台",
    "账号",
    "数据来源",
    "酒店",
    "城市排名",
    "APP访客",
    "点评分",
    "昨日好评",
    "昨日差评",
    "PSI",
    "本月间夜量",
    "本月平均卖价",
    "本月销售额",
    "本月间夜同比",
    "本月平均卖价同比",
    "本月销售额同比",
    "离店销售额排名",
    "离店间夜量排名",
    "预订订单数排名",
    "APP访客排名",
    "APP转化率排名",
    "点评分排名",
    "列表页曝光量-我的酒店",
    "列表页曝光量-竞争圈平均",
    "详情页访客量-我的酒店",
    "详情页访客量-竞争圈平均",
    "订单页访客量-我的酒店",
    "订单页访客量-竞争圈平均",
    "订单提交人数-我的酒店",
    "订单提交人数-竞争圈平均",
    "曝光转化率-我的酒店",
    "曝光转化率-竞争圈平均",
    "下单转化率-我的酒店",
    "下单转化率-竞争圈平均",
    "成交转化率-我的酒店",
    "成交转化率-竞争圈平均",
    "口径说明",
]

APP_CONVERSION_HEADERS: list[str] = [
    "快照时间",
    "平台",
    "账号",
    "数据来源",
    "酒店",
    "列表页曝光量-我的酒店",
    "列表页曝光量-竞争圈平均",
    "详情页访客量-我的酒店",
    "详情页访客量-竞争圈平均",
    "订单页访客量-我的酒店",
    "订单页访客量-竞争圈平均",
    "订单提交人数-我的酒店",
    "订单提交人数-竞争圈平均",
    "曝光转化率-我的酒店",
    "曝光转化率-竞争圈平均",
    "下单转化率-我的酒店",
    "下单转化率-竞争圈平均",
    "成交转化率-我的酒店",
    "成交转化率-竞争圈平均",
    "数据口径",
]

COMPETITION_HEADERS: list[str] = [
    "快照时间",
    "平台",
    "账号",
    "数据来源",
    "酒店",
    "指标",
    "本酒店",
    "竞争圈平均",
    "竞争圈排名",
    "排名变化",
    "数据口径",
]

METRIC_CALIBER_ROWS: list[dict[str, str]] = [
    {"指标": "城市排名", "口径": "携程实时访客排名；只有页面返回该字段时展示。"},
    {"指标": "APP访客", "口径": "携程 APP 访客字段；缺失时不使用推算值。"},
    {"指标": "点评分 / PSI", "口径": "携程页面原始评价分和服务质量分。"},
    {"指标": "本月经营概览", "口径": "仅使用 EBK 月度区块明确返回的间夜量、平均卖价、销售额及同比。"},
    {"指标": "APP流量转化", "口径": "数量取经营报告概要页昨日漏斗接口；转换率取页面实际返回，不使用竞争圈动态页的其他时间区间数据替代。"},
    {"指标": "竞争圈", "口径": "采用指标原始 competition_average / competitive_rank 字段，不跨平台计算。"},
    {"指标": "空白字段", "口径": "表示本次 EBK 成功读取结果未提供该数据，不代表数值为 0。"},
]

# 跨平台核心指标候选键：按顺序取第一个非空值（携程为指标 slug，美团/飞猪为中文指标名）。
CORE_METRIC_CANDIDATES: dict[str, dict[str, tuple[str, ...]]] = {
    "销售额": {
        "ctrip": ("departure_sales_amount", "sales_amount", "booking_sales"),
        "meituan": ("销售额",),
        "fliggy": ("房费收入", "销售额", "预订收入金额"),
    },
    "订单量": {
        "ctrip": ("realtime_booking_orders", "booking_orders"),
        "meituan": ("支付订单数",),
        "fliggy": ("预订订单量", "预订单数", "昨日预订订单"),
    },
    "间夜量": {
        "ctrip": (
            "realtime_in_house_room_nights",
            "in_house_room_nights",
            "room_nights",
            "departure_room_nights",
        ),
        "meituan": ("入住间夜", "销售间夜"),
        "fliggy": ("预订间夜", "在店间夜"),
    },
    "出租率": {
        "ctrip": ("occupancy_rate",),
        "meituan": ("满房率",),
        "fliggy": ("入住率",),
    },
    "平均房价": {
        "ctrip": ("average_selling_price",),
        "meituan": ("平均房价",),
        "fliggy": ("间夜单价", "均价"),
    },
    "转化率": {
        "ctrip": (
            "realtime_booking_conversion_rate",
            "booking_conversion_rate",
            "transaction_rate",
            "conversion_rate",
        ),
        "meituan": ("支付转化率",),
        "fliggy": ("预订转化率", "转化率"),
    },
    "城市排名": {
        "ctrip": ("realtime_visitor_rank",),
    },
    "PSI": {
        "ctrip": ("psi_service_score",),
    },
    "点评分": {
        "ctrip": ("hotel_review_score",),
    },
}

_DATASET_STATUS_LABELS: dict[str, str] = {
    "success": "成功",
    "partial": "部分成功",
    "human_required": "需人工",
    "needs_review": "待复核",
    "error": "失败",
}

# 携程 data 中的页面元数据/证据/重复分组键：metrics/tables/rankings 单独处理，其余跳过。
_CTRIP_EXPLICIT_KEYS = {"metrics", "tables", "rankings"}
_CTRIP_NOISE_KEYS = {
    "dataset_id",
    "dataset",
    "dataset_label",
    "status",
    "filters",
    "actor_id",
    "url",
    "current_url",
    "page_title",
    "page_state",
    "page_filter",
    "captured_at",
    "updated_at",
    "raw_snippets",
    "data_quality_notes",
    "warnings",
    "time_dimensions",
    "primary_time_dimension",
    "metric_groups",
    "evidence",
} | _CTRIP_EXPLICIT_KEYS

# 美团 data 中承载指标的分段 dict（"metrics" 优先，其余按序补齐并去重）。
_MEITUAN_METRIC_DICT_KEYS = (
    "metrics",
    "business_performance",
    "traffic_trend",
    "loss_analysis",
    "service_metrics",
)
_MEITUAN_NOISE_KEYS = {"visible_lines", "hotel_name", "tab_label"} | set(
    _MEITUAN_METRIC_DICT_KEYS
)

# 飞猪 data 中的中文指标分段与标量指标。
_FLIGGY_SECTION_LABELS: dict[str, str] = {
    "today_data": "今日数据",
    "yesterday_data": "昨日数据",
    "trade_overview": "交易概览",
    "in_house_data": "在店数据",
    "booking_data": "预订数据",
    "flow_data": "流量数据",
}
_FLIGGY_SCALAR_LABELS: dict[str, str] = {
    "guest_review_score": "宾客评价分",
    "mci_index": "MCI 指数",
}
_FLIGGY_NOISE_KEYS = (
    {"visible_lines", "hotel_name", "joined_activities", "review_extras"}
    | set(_FLIGGY_SECTION_LABELS)
    | set(_FLIGGY_SCALAR_LABELS)
)

_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


def normalize_value(value: Any) -> Any:
    """数字型指标转数值（xlsx 可计算、CSV 不出现 420.0）；其余原样字符串。"""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    text = str(value).strip()
    if _INT_RE.fullmatch(text):
        return int(text)
    if _FLOAT_RE.fullmatch(text):
        number = float(text)
        return int(number) if number.is_integer() else number
    return text


def extract_business_metric_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每指标一行的中文明细长表；仅展开读取成功的数据集。"""

    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        rows.extend(_rows_for_snapshot(snapshot))
    return rows


def build_core_metric_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨平台核心指标宽表：每快照一行，平台缺失的指标留空。"""

    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or "")
        if platform not in PLATFORM_LABELS:
            continue
        lookup = _platform_metric_lookup(platform, snapshot)
        if not lookup:
            continue
        row: dict[str, Any] = {
            "快照时间": str(snapshot.get("created_at") or ""),
            "平台": PLATFORM_LABELS[platform],
            "账号": _snapshot_account_label(snapshot),
            "数据来源": str(snapshot.get("export_source") or "未标记"),
        }
        for metric_name, candidates_by_platform in CORE_METRIC_CANDIDATES.items():
            value: Any = ""
            for candidate in candidates_by_platform.get(platform, ()):
                raw = lookup.get(candidate)
                if raw not in (None, ""):
                    value = normalize_value(raw)
                    break
            row[metric_name] = value
        rows.append(row)
    return rows


def build_operating_brief_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按官方数据简报分区组织真实快照指标；缺少源字段时保持空白。"""

    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or "")
        if platform not in PLATFORM_LABELS:
            continue
        lookup = _metric_record_lookup(platform, snapshot)
        if not lookup:
            continue
        row: dict[str, Any] = {
            "快照时间": str(snapshot.get("created_at") or ""),
            "平台": PLATFORM_LABELS[platform],
            "账号": _snapshot_account_label(snapshot),
            "数据来源": str(snapshot.get("export_source") or "未标记"),
            "酒店": _snapshot_hotel_name(snapshot),
        }
        candidates: dict[str, tuple[str, ...]] = {
            "城市排名": ("realtime_visitor_rank",),
            "APP访客": ("ctrip_app_visitors", "app_visitors"),
            "点评分": ("hotel_review_score", "overall_score"),
            "昨日好评": ("positive_reviews_yesterday", "new_positive_reviews"),
            "昨日差评": ("negative_reviews_yesterday", "new_negative_reviews"),
            "PSI": ("psi_service_score", "psi"),
        }
        for label, keys in candidates.items():
            row[label] = _first_metric_value(lookup, keys)
        monthly_candidates = {
            "本月间夜量": ("monthly_departure_room_nights",),
            "本月平均卖价": ("monthly_average_selling_price",),
            "本月销售额": ("monthly_departure_sales_amount",),
        }
        for label, keys in monthly_candidates.items():
            metric = _first_metric(lookup, keys)
            row[label] = metric.get("value", "") if metric else ""
            yoy_label = {
                "本月间夜量": "本月间夜同比",
                "本月平均卖价": "本月平均卖价同比",
                "本月销售额": "本月销售额同比",
            }[label]
            row[yoy_label] = metric.get("year_over_year", "") if metric else ""
        rank_candidates = {
            "离店销售额排名": ("departure_sales_amount", "booking_sales"),
            "离店间夜量排名": ("departure_room_nights", "in_house_room_nights"),
            "预订订单数排名": ("booking_orders", "realtime_booking_orders"),
            "APP访客排名": ("ctrip_app_visitors", "app_visitors"),
            "APP转化率排名": (
                "ctrip_app_conversion_rate",
                "booking_conversion_rate",
            ),
            "点评分排名": (
                "hotel_review_score",
                "ctrip_review_score",
                "overall_score",
            ),
        }
        for label, keys in rank_candidates.items():
            metric = _first_snapshot_metric_with_field(
                snapshot,
                keys,
                "competitive_rank",
            )
            row[label] = _rank_position(metric.get("competitive_rank")) if metric else ""
        _populate_app_funnel_pairs(row, lookup)
        row["口径说明"] = "；".join(
            part
            for part in (
                "EBK 成功读取",
                "APP漏斗=概要页昨日",
                _metric_scope_label(lookup),
            )
            if part
        )
        rows.append(row)
    return rows


def build_app_conversion_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """输出 APP 漏斗及竞争圈对比；不从漏斗值反算转换率。"""

    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or "")
        if platform not in PLATFORM_LABELS:
            continue
        lookup = _metric_record_lookup(platform, snapshot)
        if not lookup:
            continue
        row: dict[str, Any] = {
            "快照时间": str(snapshot.get("created_at") or ""),
            "平台": PLATFORM_LABELS[platform],
            "账号": _snapshot_account_label(snapshot),
            "数据来源": str(snapshot.get("export_source") or "未标记"),
            "酒店": _snapshot_hotel_name(snapshot),
            "数据口径": _metric_scope_label(lookup),
        }
        _populate_app_funnel_pairs(row, lookup)
        mine_labels = [f"{label}-我的酒店" for label, _ in _APP_FUNNEL_FIELDS]
        if not any(row.get(label) not in (None, "") for label in mine_labels):
            continue
        rows.append(row)
    return rows


def build_competition_rank_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将指标上随页面返回的竞争圈均值、排名展开成可筛选表。"""

    rows: list[dict[str, Any]] = []
    metric_labels = {
        "departure_sales_amount": "离店销售额",
        "departure_room_nights": "离店间夜量",
        "booking_orders": "预订订单数",
        "ctrip_app_visitors": "APP访客",
        "ctrip_app_conversion_rate": "APP转化率",
        "list_exposure": "列表页曝光",
        "app_detail_visitors": "详情页访客",
        "order_page_visitors": "订单页访客",
        "hotel_review_score": "点评分",
        "psi_service_score": "PSI",
        "psi": "PSI",
    }
    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or "")
        if platform not in PLATFORM_LABELS:
            continue
        lookup = _metric_record_lookup(platform, snapshot)
        for key, label in metric_labels.items():
            metric = _first_snapshot_metric_with_field(
                snapshot,
                (key,),
                "competitive_rank",
            ) or _first_snapshot_metric_with_field(
                snapshot,
                (key,),
                "competition_average",
            ) or lookup.get(key)
            if not isinstance(metric, dict):
                continue
            average = metric.get("competition_average", "")
            rank = metric.get("competitive_rank", "")
            change = metric.get("competitive_rank_change", "")
            if average in (None, "") and rank in (None, "") and change in (None, ""):
                continue
            rows.append(
                {
                    "快照时间": str(snapshot.get("created_at") or ""),
                    "平台": PLATFORM_LABELS[platform],
                    "账号": _snapshot_account_label(snapshot),
                    "数据来源": str(snapshot.get("export_source") or "未标记"),
                    "酒店": _snapshot_hotel_name(snapshot),
                    "指标": label,
                    "本酒店": metric.get("value", ""),
                    "竞争圈平均": average,
                    "竞争圈排名": rank,
                    "排名变化": change,
                    "数据口径": metric.get("section", ""),
                }
            )
    return rows


_APP_FUNNEL_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("列表页曝光量", ("list_exposure",)),
    ("详情页访客量", ("app_detail_visitors",)),
    ("订单页访客量", ("order_page_visitors",)),
    ("订单提交人数", ("order_submitters", "order_submitters_count")),
    ("曝光转化率", ("exposure_conversion_rate",)),
    ("下单转化率", ("booking_conversion_rate",)),
    ("成交转化率", ("transaction_conversion_rate",)),
)


def _populate_app_funnel_pairs(
    row: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> None:
    for label, keys in _APP_FUNNEL_FIELDS:
        metric = _first_metric(lookup, keys)
        row[f"{label}-我的酒店"] = metric.get("value", "") if metric else ""
        row[f"{label}-竞争圈平均"] = (
            metric.get("competition_average", "") if metric else ""
        )


def _first_snapshot_metric_with_field(
    snapshot: dict[str, Any],
    keys: tuple[str, ...],
    field: str,
) -> dict[str, Any] | None:
    records = list(_iter_snapshot_metric_records(snapshot))
    for key in keys:
        for record_key, metric in records:
            if record_key == key and metric.get(field) not in (None, ""):
                return metric
    return None


def _iter_snapshot_metric_records(
    snapshot: dict[str, Any],
):
    payload = snapshot.get("payload_json")
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return
    for dataset in datasets.values():
        if not isinstance(dataset, dict) or str(dataset.get("status") or "") != "success":
            continue
        data = dataset.get("data")
        if not isinstance(data, dict):
            continue
        containers: list[Any] = [data.get("metrics")]
        monthly = data.get("monthly")
        if isinstance(monthly, dict) and monthly.get("status") == "success":
            containers.append(monthly.get("metrics"))
        dimensions = data.get("time_dimensions")
        if isinstance(dimensions, dict):
            containers.extend(
                dimension.get("metrics")
                for dimension in dimensions.values()
                if isinstance(dimension, dict)
            )
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key, metric in container.items():
                if isinstance(metric, dict):
                    yield str(key), metric


def _rank_position(value: Any) -> int | str:
    if value in (None, "") or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else ""


# --- 详情行构造 ---


def _rows_for_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    platform = str(snapshot.get("platform") or "")
    payload = snapshot.get("payload_json")
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return []
    created_at = str(snapshot.get("created_at") or "")
    platform_label = _platform_label(platform)
    account = _snapshot_account_label(snapshot)
    rows: list[dict[str, Any]] = []
    for dataset_id, dataset in datasets.items():
        if not isinstance(dataset, dict) or str(dataset.get("status") or "") != "success":
            continue
        data = dataset.get("data")
        if not isinstance(data, dict) or not data:
            continue
        base: dict[str, Any] = {
            "快照时间": created_at,
            "平台": platform_label,
            "账号": account,
            "数据来源": str(snapshot.get("export_source") or "未标记"),
            "酒店": "",
            "数据集": _dataset_label(platform, str(dataset_id)),
            "数据口径": "",
            "读取状态": _DATASET_STATUS_LABELS["success"],
        }
        if platform == "ctrip":
            rows.extend(_ctrip_dataset_rows(data, base))
        elif platform == "meituan":
            rows.extend(_meituan_dataset_rows(data, base))
        elif platform == "fliggy":
            rows.extend(_fliggy_dataset_rows(data, base))
    return rows


def _detail_row(
    scope: dict[str, Any],
    *,
    metric: str,
    value: Any,
    dimension: str = "",
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = dict(scope)
    row["维度"] = str(dimension or "")
    row["指标"] = str(metric)
    row["数值"] = normalize_value(value)
    环比, 同比 = _context_change(raw)
    row["环比"] = 环比
    row["同比"] = 同比
    row["竞争圈平均"] = _scalar(raw, "competition_average")
    row["竞争圈排名"] = _scalar(raw, "competitive_rank")
    row["排名环比"] = _scalar(raw, "competitive_rank_change")
    return row


def _scalar(raw: dict[str, Any] | None, key: str) -> str:
    if not isinstance(raw, dict):
        return ""
    value = raw.get(key)
    return "" if value in (None, "") else str(value)


def _context_change(raw: dict[str, Any] | None) -> tuple[str, str]:
    """环比/同比口径分离：week_over_week 与『较上周/上周同比』入环比；
    year_over_year 与『较去年同期/去年同比』入同比。其余 change 不强行归类。"""
    if not isinstance(raw, dict):
        return "", ""
    week_over_week = raw.get("week_over_week")
    year_over_year = raw.get("year_over_year")
    change = str(raw.get("change") or "")
    环比 = "" if week_over_week in (None, "") else str(week_over_week)
    同比 = "" if year_over_year in (None, "") else str(year_over_year)
    if change:
        compact = change.replace(" ", "")
        if "较去年同期" in compact or "去年同比" in compact:
            同比 = 同比 or change
        elif "较上周" in compact or "上周同比" in compact:
            环比 = 环比 or change
    return 环比, 同比


def _ctrip_dataset_rows(data: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    primary_label = _time_dimension_label(data)
    scope = dict(base, 数据口径=primary_label)
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    for key, metric in metrics.items():
        if isinstance(metric, dict):
            label = str(metric.get("label") or metric.get("key") or key)
            value = metric.get("value")
            if value in (None, ""):
                value = metric.get("display_value")
            rows.append(
                _detail_row(
                    scope,
                    metric=label,
                    value=value,
                    dimension=metric.get("section") or "",
                    raw=metric,
                )
            )
        else:
            rows.append(_detail_row(scope, metric=str(key), value=metric))
    rows.extend(_ctrip_time_dimension_rows(data, base, primary_label))
    rows.extend(_ctrip_monthly_rows(data, base))
    for table in data.get("tables") or []:
        rows.extend(_table_rows(table, scope))
    rankings = data.get("rankings")
    if isinstance(rankings, list):
        for ranking in rankings:
            rows.extend(_table_rows(ranking, scope))
    return rows


def _ctrip_time_dimension_rows(
    data: dict[str, Any],
    base: dict[str, Any],
    primary_label: str,
) -> list[dict[str, Any]]:
    """展开非主时间维度的指标（如 realtime 维度的实时排名/实时订单）。

    主维度指标已随顶层 metrics 落行，这里只补非主维度，避免重复。"""
    rows: list[dict[str, Any]] = []
    dims = data.get("time_dimensions")
    if not isinstance(dims, dict):
        return rows
    primary_raw = data.get("primary_time_dimension")
    if isinstance(primary_raw, dict):
        primary_key = primary_raw.get("value")
    else:
        primary_key = str(primary_raw) if primary_raw else None
    for dim_key, dim_payload in dims.items():
        if dim_key == primary_key or not isinstance(dim_payload, dict):
            continue
        dim_metrics = dim_payload.get("metrics")
        if not isinstance(dim_metrics, dict):
            continue
        dim_label = str(dim_payload.get("label") or dim_key)
        口径 = f"{primary_label} · {dim_label}" if primary_label else dim_label
        dim_scope = dict(base, 数据口径=口径)
        for key, metric in dim_metrics.items():
            if not isinstance(metric, dict):
                continue
            label = str(metric.get("label") or metric.get("key") or key)
            value = metric.get("value")
            if value in (None, ""):
                value = metric.get("display_value")
            rows.append(
                _detail_row(
                    dim_scope,
                    metric=label,
                    value=value,
                    dimension=metric.get("section") or dim_label,
                    raw=metric,
                )
            )
    return rows


def _ctrip_monthly_rows(
    data: dict[str, Any],
    base: dict[str, Any],
) -> list[dict[str, Any]]:
    """展开销售数据页『离店·按月』补充读取的本月经营概览（含页面原文去年同期/同比）。"""
    monthly = data.get("monthly")
    if not isinstance(monthly, dict) or monthly.get("status") != "success":
        return []
    monthly_metrics = monthly.get("metrics")
    if not isinstance(monthly_metrics, dict):
        return []
    monthly_scope = dict(base, 数据口径="本月·总平台（按月）")
    rows: list[dict[str, Any]] = []
    for key, metric in monthly_metrics.items():
        if not isinstance(metric, dict):
            continue
        label = str(metric.get("label") or metric.get("key") or key)
        value = metric.get("value")
        if value in (None, ""):
            value = metric.get("display_value")
        rows.append(
            _detail_row(
                monthly_scope,
                metric=label,
                value=value,
                dimension=metric.get("section") or "本月离店（按月）",
                raw=metric,
            )
        )
    return rows


def _table_rows(table: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(table, dict):
        return rows
    label = str(table.get("label") or table.get("name") or "表格")
    table_rows = table.get("rows")
    if not isinstance(table_rows, list):
        return rows
    for row in table_rows:
        if not isinstance(row, dict):
            continue
        dimension = " · ".join(
            part for part in (label, str(row.get("hotel_name") or "")) if part
        )
        for key, value in row.items():
            if key == "hotel_name":
                continue
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    rows.append(
                        _detail_row(scope, metric=str(sub_key), value=sub_value, dimension=dimension)
                    )
            elif isinstance(value, list):
                continue
            else:
                rows.append(_detail_row(scope, metric=str(key), value=value, dimension=dimension))
    return rows


def _meituan_dataset_rows(data: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scope = dict(base, 酒店=str(data.get("hotel_name") or ""))
    merged = _merge_metric_dicts(data, _MEITUAN_METRIC_DICT_KEYS)
    for metric_name, value in merged.items():
        rows.append(_detail_row(scope, metric=str(metric_name), value=value))
    rows.extend(
        _generic_scalar_rows(data, scope, noise=_MEITUAN_NOISE_KEYS | set(merged))
    )
    return rows


def _fliggy_dataset_rows(data: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scope = dict(base, 酒店=str(data.get("hotel_name") or ""))
    for section_key, section_label in _FLIGGY_SECTION_LABELS.items():
        section = data.get(section_key)
        if not isinstance(section, dict):
            continue
        for metric_name, value in section.items():
            rows.append(
                _detail_row(scope, metric=str(metric_name), value=value, dimension=section_label)
            )
    for scalar_key, label in _FLIGGY_SCALAR_LABELS.items():
        value = data.get(scalar_key)
        if value is None or isinstance(value, (dict, list)):
            continue
        rows.append(_detail_row(scope, metric=label, value=value))
    review_extras = data.get("review_extras")
    if isinstance(review_extras, dict):
        for metric_name, value in review_extras.items():
            rows.append(
                _detail_row(scope, metric=str(metric_name), value=value, dimension="评价补充")
            )
    joined = data.get("joined_activities")
    if isinstance(joined, list):
        for activity in joined:
            if not isinstance(activity, dict):
                continue
            summary = " / ".join(
                str(part)
                for part in (
                    activity.get("activity_type"),
                    activity.get("room_product"),
                    activity.get("valid_period"),
                    activity.get("strength"),
                    activity.get("status"),
                )
                if part
            )
            rows.append(
                _detail_row(
                    scope,
                    metric=str(activity.get("name") or "未命名活动"),
                    value=summary,
                    dimension="已参加活动",
                )
            )
    rows.extend(_generic_scalar_rows(data, scope, noise=_FLIGGY_NOISE_KEYS))
    return rows


def _generic_scalar_rows(
    data: dict[str, Any],
    scope: dict[str, Any],
    *,
    noise: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in data.items():
        if key in noise or value is None or isinstance(value, (dict, list)):
            continue
        rows.append(_detail_row(scope, metric=str(key), value=value))
    return rows


def _merge_metric_dicts(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in keys:
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        for metric_name, value in section.items():
            if metric_name in merged and merged[metric_name] not in (None, ""):
                continue
            merged[metric_name] = value
    return merged


def _time_dimension_label(data: dict[str, Any]) -> str:
    raw = data.get("primary_time_dimension")
    if isinstance(raw, dict):
        for key in ("label", "value", "name"):
            if raw.get(key):
                return str(raw[key])
        return ""
    return str(raw) if raw else ""


def _platform_metric_lookup(platform: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = snapshot.get("payload_json")
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return {}
    lookup: dict[str, Any] = {}
    for dataset in datasets.values():
        if not isinstance(dataset, dict) or str(dataset.get("status") or "") != "success":
            continue
        data = dataset.get("data")
        if not isinstance(data, dict):
            continue
        if platform == "ctrip":
            metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
            for key, metric in metrics.items():
                if isinstance(metric, dict) and key not in lookup:
                    value = metric.get("value")
                    if value not in (None, ""):
                        lookup[key] = value
            # 实时排名等指标位于 time_dimensions.realtime，需合并进来。
            dims = data.get("time_dimensions")
            if isinstance(dims, dict):
                for dim_payload in dims.values():
                    if not isinstance(dim_payload, dict):
                        continue
                    rt_metrics = dim_payload.get("metrics")
                    if not isinstance(rt_metrics, dict):
                        continue
                    for key, metric in rt_metrics.items():
                        if isinstance(metric, dict) and key not in lookup:
                            value = metric.get("value")
                            if value not in (None, ""):
                                lookup[key] = value
        elif platform == "meituan":
            for key, value in _merge_metric_dicts(data, _MEITUAN_METRIC_DICT_KEYS).items():
                if key not in lookup:
                    lookup[key] = value
        elif platform == "fliggy":
            for section_key in _FLIGGY_SECTION_LABELS:
                section = data.get(section_key)
                if not isinstance(section, dict):
                    continue
                for key, value in section.items():
                    if key not in lookup:
                        lookup[key] = value
    return lookup


def _metric_record_lookup(platform: str, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """收集成功数据集中的完整指标记录，供简报读取 value 与比较字段。"""

    payload = snapshot.get("payload_json")
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for dataset in datasets.values():
        if not isinstance(dataset, dict) or str(dataset.get("status") or "") != "success":
            continue
        data = dataset.get("data")
        if not isinstance(data, dict):
            continue
        _merge_metric_records(lookup, data.get("metrics"))
        monthly = data.get("monthly")
        if isinstance(monthly, dict) and monthly.get("status") == "success":
            _merge_metric_records(lookup, monthly.get("metrics"))
        dimensions = data.get("time_dimensions")
        if isinstance(dimensions, dict):
            for dimension in dimensions.values():
                if isinstance(dimension, dict):
                    _merge_metric_records(lookup, dimension.get("metrics"))
        for section_key in (
            "flow_data",
            "traffic_data",
            "business_performance",
            "traffic_trend",
            "service_metrics",
        ):
            _merge_metric_records(lookup, data.get(section_key))
        if platform != "ctrip":
            for key, value in data.items():
                if key in {"monthly", "time_dimensions"} or isinstance(value, (dict, list)):
                    continue
                if value not in (None, ""):
                    lookup.setdefault(key, {"value": value})
    return lookup


def _merge_metric_records(target: dict[str, dict[str, Any]], metrics: Any) -> None:
    if not isinstance(metrics, dict):
        return
    for key, metric in metrics.items():
        if isinstance(metric, dict):
            value = metric.get("value")
            if value in (None, ""):
                value = metric.get("display_value")
            if value in (None, ""):
                continue
            record = dict(metric)
            record["value"] = normalize_value(value)
            target.setdefault(str(key), record)
        elif metric not in (None, ""):
            target.setdefault(str(key), {"value": normalize_value(metric)})


def _first_metric(
    lookup: dict[str, dict[str, Any]], keys: tuple[str, ...]
) -> dict[str, Any] | None:
    for key in keys:
        metric = lookup.get(key)
        if isinstance(metric, dict) and metric.get("value") not in (None, ""):
            return metric
    return None


def _first_metric_value(lookup: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> Any:
    metric = _first_metric(lookup, keys)
    return metric.get("value", "") if metric else ""


def _snapshot_hotel_name(snapshot: dict[str, Any]) -> str:
    payload = snapshot.get("payload_json")
    if not isinstance(payload, dict):
        return str(snapshot.get("hotel_name") or "")
    for key in ("hotel_name", "hotel", "name"):
        if payload.get(key):
            return str(payload[key])
    datasets = payload.get("datasets")
    if isinstance(datasets, dict):
        for dataset in datasets.values():
            if not isinstance(dataset, dict):
                continue
            data = dataset.get("data")
            if isinstance(data, dict) and data.get("hotel_name"):
                return str(data["hotel_name"])
    return str(snapshot.get("hotel_name") or "")


def _snapshot_account_label(snapshot: dict[str, Any]) -> str:
    """Operator-facing account name, falling back to the stable account ID."""

    display_name = str(snapshot.get("account_display_name") or "").strip()
    if display_name:
        return display_name
    return str(snapshot.get("platform_account_id") or "")


def _metric_scope_label(lookup: dict[str, dict[str, Any]]) -> str:
    sections = []
    for metric in lookup.values():
        section = metric.get("section") if isinstance(metric, dict) else None
        if section and str(section) not in sections:
            sections.append(str(section))
    return " / ".join(sections[:3])


def _platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def _dataset_label(platform: str, dataset_id: str) -> str:
    labels = {
        "ctrip": CTRIP_DATASET_LABELS,
        "meituan": MEITUAN_DATASET_LABELS,
        "fliggy": FLIGGY_DATASET_LABELS,
    }.get(platform, {})
    return str(labels.get(dataset_id) or dataset_id)
