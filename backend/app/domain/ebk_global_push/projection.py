"""EBK 全局推送投影层（设计文档 §5/§6）。

职责：校验并标准化「实时/当前状态」数据行，过滤无时间证据的字段，
按白名单分配三分类（经营/活动促销/广告营销），并格式化数值展示。

本层不读取页面、不解析 DOM；输入行由读取编排层（scheduler 阶段）从
parser 结果 + 时间证据适配后传入。行契约见设计文档 §5.1。
"""

from __future__ import annotations

from typing import Any

CATEGORY_BUSINESS = "business"
CATEGORY_PROMOTION = "promotion"
CATEGORY_ADVERTISING = "advertising"

FRESHNESS_REALTIME = "realtime"
FRESHNESS_CURRENT_STATE = "current_state"
FRESHNESS_SCORE_SNAPSHOT = "score_snapshot"
FRESHNESS_UNKNOWN = "unknown"

_ALLOWED_FRESHNESS = {
    FRESHNESS_REALTIME,
    FRESHNESS_CURRENT_STATE,
    FRESHNESS_SCORE_SNAPSHOT,
}

# 数据集 → 三分类白名单（设计文档 §5.2/§5.3/§5.4）。
# 注意：comment_monitor 不在全局读取白名单（§8.2 决议），此处同样不收录。
_DATASET_CATEGORIES: dict[tuple[str, str], str] = {
    ("ctrip", "business_outline"): CATEGORY_BUSINESS,
    ("ctrip", "competition_profile"): CATEGORY_BUSINESS,
    ("ctrip", "flow_data"): CATEGORY_BUSINESS,
    ("meituan", "workbench_home"): CATEGORY_BUSINESS,
    ("meituan", "data_center_home"): CATEGORY_BUSINESS,
    ("meituan", "data_center_trade"): CATEGORY_BUSINESS,
    ("meituan", "data_center_traffic"): CATEGORY_BUSINESS,
    ("meituan", "data_center_service"): CATEGORY_BUSINESS,
    ("meituan", "data_center_peer_ranking"): CATEGORY_BUSINESS,
    ("meituan", "comment_manage_home"): CATEGORY_BUSINESS,
    ("fliggy", "home_v1"): CATEGORY_BUSINESS,
    ("fliggy", "operating_data"): CATEGORY_BUSINESS,
    ("fliggy", "diagnose_report"): CATEGORY_BUSINESS,
    ("ctrip", "promotion_effect"): CATEGORY_PROMOTION,
    ("ctrip", "promotion_registered"): CATEGORY_PROMOTION,
    ("meituan", "promotion_coupon"): CATEGORY_PROMOTION,
    ("fliggy", "promotion_agent"): CATEGORY_PROMOTION,
    ("ctrip", "ad_pyramid"): CATEGORY_ADVERTISING,
    # 2026-09-10：金字塔出价列表（当前状态行）进广告营销卡。
    ("ctrip", "pyramid_bid"): CATEGORY_ADVERTISING,
    # 2026-09-11：美团推广通 / 飞猪直通车（已开通店铺的广告数据行）。
    ("meituan", "ad_cpc"): CATEGORY_ADVERTISING,
    ("fliggy", "ad_p4p"): CATEGORY_ADVERTISING,
}

# 携程评分快照例外（§6）：business_outline 的两个评分字段，允许按
# score_snapshot 进入经营卡标题；页面为昨日口径的存量评分，禁止趋势计算。
_PSI_FIELD_KEY = "psi_service_score"
_REVIEW_FIELD_KEY = "hotel_review_score"
_SCORE_MIN = 0.0
_SCORE_MAX_BY_FIELD = {
    # 携程生产实读与既有简报测试均存在 5.x PSI（如 5.44/5.57）；
    # 酒店点评分仍是五分制，两者不能共用同一个上限。
    _PSI_FIELD_KEY: 6.0,
    _REVIEW_FIELD_KEY: 5.0,
}


def normalize_projection_rows(
    rows: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """校验投影行；返回 (accepted, rejected)。

    拒绝规则（§5.1）：
    - freshness=unknown 或不在允许集合 → ``freshness_unknown``；
    - (platform, dataset_id) 不在分类白名单 → ``dataset_not_whitelisted``；
    - label/value 缺失 → ``missing_field``。
    accepted 行附加 ``category`` 字段。
    """

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        freshness = str(row.get("freshness") or "").strip().lower()
        if freshness not in _ALLOWED_FRESHNESS:
            rejected.append({**row, "reason": "freshness_unknown"})
            continue
        platform = str(row.get("platform") or "").strip().lower()
        dataset_id = str(row.get("dataset_id") or "").strip()
        category = _DATASET_CATEGORIES.get((platform, dataset_id))
        if category is None:
            rejected.append({**row, "reason": "dataset_not_whitelisted"})
            continue
        label = str(row.get("label") or "").strip()
        value = row.get("value")
        if not label or value is None or str(value).strip() == "":
            rejected.append({**row, "reason": "missing_field"})
            continue
        row["platform"] = platform
        row["dataset_id"] = dataset_id
        row["label"] = label
        row["category"] = category
        for extra in ("comparison", "competitive_rank"):
            value_extra = str(row.get(extra) or "").strip()
            if value_extra:
                row[extra] = value_extra
            else:
                row.pop(extra, None)
        accepted.append(row)
    return accepted, rejected


def format_row_value(row: dict[str, Any]) -> str:
    """值 + 单位的展示格式化：仅千分位与单位拼接，不做数量级换算（§5.1）。"""

    value = row.get("value")
    value_type = str(row.get("value_type") or "text").strip().lower()
    unit = str(row.get("unit") or "").strip()
    text: str
    if value_type in {"integer", "amount"}:
        try:
            number = float(value)
            if number == int(number) and value_type == "integer":
                text = f"{int(number):,}"
            else:
                text = f"{number:,.2f}"
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    # percent 值自带 % 时不再重复拼单位，避免「23.43% %」。
    if unit == "%" and text.rstrip().endswith("%"):
        unit = ""
    # 值已含中文单位（如美团「7142.00 元」）时不再重复拼，避免「7142.00 元 元」。
    if unit and text.rstrip().endswith(unit):
        unit = ""
    return f"{text} {unit}".strip()


def extract_ctrip_score_summary(
    rows: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """从本次携程 business_outline 行提取 PSI/店铺评分（§6）。

    校验数值范围（0-5）与 value_type，拒绝把排名或百分比当评分；
    缺失或非法时填「未读取」，不影响经营卡主体。
    """

    psi = _extract_score(rows, _PSI_FIELD_KEY)
    review = _extract_score(rows, _REVIEW_FIELD_KEY)
    return {
        "psi": psi,
        "review_score": review,
        "title_suffix": f"携程 PSI {psi}｜携程店铺评分 {review}",
    }


def _extract_score(rows: list[dict[str, Any]] | None, field_key: str) -> str:
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("field_key") or "").strip() != field_key:
            continue
        if str(raw.get("value_type") or "").strip().lower() not in {"score", "number"}:
            continue
        if str(raw.get("freshness") or "").strip().lower() != FRESHNESS_SCORE_SNAPSHOT:
            continue
        value = raw.get("value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        maximum = _SCORE_MAX_BY_FIELD.get(field_key, 5.0)
        if _SCORE_MIN < number <= maximum:
            if isinstance(value, str):
                return value.strip()
            return format(number, "g")
    return "未读取"
