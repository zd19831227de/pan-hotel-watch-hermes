"""平台读取结果 → 投影行的证据适配层（S4 完整版，T1 实测证据接线）。

只把「有结构化时间证据」的数据块转成投影行：
- 携程 business_outline：``_BUSINESS_OUTLINE_DIMENSION_METRICS`` 的 realtime
  白名单字段（页面实时区块解析产物）+ 两个评分（score_snapshot 例外）；
- 携程 competition_profile：页面「实时」单选选中后的 time_dimensions.realtime
  六项经营对比（2026-08-28 实测）；
- 携程 promotion_registered：已报名/进行中活动清单（current_state）；
- 携程 promotion_effect：「今日实时数据」区块指标（parser 标记 realtime）；
- 携程 ad_pyramid：快捷筛选「今天」选中 + date_preset=今日 双证据（实测）；
- 美团 workbench_home：``realtime_data`` 字典（页面「实时数据」区块）；
- 美团 data_center_home/trade/traffic：「今日实时」页签抓取产物
  （today_realtime_metrics + 选中态证据，默认页签为昨日）；
- 飞猪 home_v1：``today_data`` 字典（页面「今日」区块）；
- 飞猪 operating_data：默认「今日」页签 + 当日更新时间证据（today_evidence）；
- 飞猪 promotion_agent：已参加活动清单（current_state）；
- 美团 ad_cpc（2026-09-11）：推广通总览，已开通（推广中>0）才产出
  当前状态行；未开通不产出也不计 needs_review；
- 飞猪 ad_p4p（2026-09-11）：直通车，已开通（有智能投放计划或当日有
  消耗）才产出；数据总览/转化概况在「今日」选中态确认时按 realtime；
  2026-09-16 起只出实时口径行，累计/预估口径不进卡。

其余数据集（美团 data_center_service、promotion_coupon 等）尚无页面时间证据，
按设计 §5.2/§5.3 记入 ``needs_review`` 汇总，不凭字段名进卡。
"""

from __future__ import annotations

from typing import Any

from app.domain.browser.ctrip_ebooking_time_dimensions import (
    _BUSINESS_OUTLINE_DIMENSION_METRICS,
)
from app.domain.ebk_global_push.projection import (
    FRESHNESS_CURRENT_STATE,
    FRESHNESS_REALTIME,
    FRESHNESS_SCORE_SNAPSHOT,
)
from app.domain.pyramid_bidding.cards import pyramid_promotion_table_row

_CTRIP_REALTIME_FIELDS = _BUSINESS_OUTLINE_DIMENSION_METRICS["realtime"]
_CTRIP_SCORE_FIELDS = ("psi_service_score", "hotel_review_score")

# T1.5（2026-08-28 实测）：competition_profile 页面「经营对比」区自带
# 实时/昨日/上周/上月 单选组；time_dimensions.realtime 为页面「实时」单选选中
# 后解析的产物，仅这六项经营对比字段按 realtime 进经营卡（评分字段除外，
# PSI/点评分按 score_snapshot 例外只从 business_outline 取）。
_CTRIP_COMPETITION_REALTIME_FIELDS = (
    "booking_orders",
    "booking_sales",
    "in_house_room_nights",
    "occupancy_rate",
    "ctrip_app_visitors",
    "ctrip_app_conversion_rate",
)

# T1.4（2026-08-28 实测）：广告金字塔页快捷筛选「今天」选中 + 页面标注
# 「当日数据实时更新」；page_filter.selected 与 ad_pyramid.date_preset=今日
# 同时成立才放行。
_CTRIP_AD_PYRAMID_FIELDS = (
    "clicks",
    "impressions",
    "click_rate",
    "total_spend_cny",
    "booking_amount_cny",
    "roas",
)

# 美团流量曝光指标（2026-09-02 需求精简为 2 项：浏览人数 + 浏览-支付转化率；
# 浏览-支付转化率仅流量分析 metrics 有，浏览人数两处同源、卡片去重取流量分析口径）。
_MEITUAN_TRAFFIC_LABELS = frozenset({
    "浏览人数",
    "浏览-支付转化率",
})

# 美团收益概览指标：占位「-」也保留行（页面真实状态）
_MEITUAN_REVENUE_METRIC_KEYS = frozenset({
    "销售额",
    "支付订单数",
    "入住间夜",
    "销售间夜",
})


_CTRIP_FIELD_LABELS = {
    "realtime_booking_orders": ("实时预订订单", "integer", "单"),
    "realtime_in_house_room_nights": ("实时在店间夜", "integer", "间夜"),
    "realtime_visitors": ("实时访客", "integer", "人"),
    "realtime_visitor_rank": ("实时访客排名", "rank", ""),
    # 2026-09-11 审查：单位原为占位「?」会拼进卡片（294.00 ?）；起价为 CNY 数值，不拼单位。
    "realtime_starting_price": ("实时起价", "amount", ""),
    "psi_service_score": ("PSI 服务质量分", "score", ""),
    "hotel_review_score": ("酒店点评分", "score", ""),
}

# 携程全局读取白名单（§8.2）：数据中心 11 项去掉 comment_monitor，
# 含 2026-09-10 新增的金字塔出价列表（pyramid_bid，只读抓取链路复用调价指令）。
GLOBAL_CTRIP_DATASET_IDS = (
    "business_outline",
    "beneficial_data",
    "flow_data",
    "competition_profile",
    "loss_analysis",
    "competition_list",
    "promotion_effect",
    "promotion_registered",
    "ad_pyramid",
    "pyramid_bid",
)


def _ctrip_promotion_content(item: dict[str, Any]) -> str:
    """促销中心活动内容：房型短名：优惠规则（多条「；」连接）。

    房型取参与房型的首个（剥掉括号内的长描述与房型ID）；无规则时退回
    促销类型（立减/折扣）；两者皆无返回空（该活动不进卡）。
    """
    name = str(item.get("name") or item.get("activity_name") or "").strip()
    rules = [str(r).strip() for r in (item.get("rules") or []) if str(r).strip()]
    room = str(
        item.get("room_scope") or item.get("room_types") or ""
    ).strip()
    room_short = room.split("(", 1)[0].strip() if room else ""
    parts: list[str] = []
    if rules:
        parts.extend(rules)
    else:
        promotion_type = str(item.get("promotion_type") or "").strip()
        if promotion_type:
            parts.append(promotion_type)
    # 规则/类型与活动名相同（如 连住优惠）时无信息量，不重复输出
    parts = [part for part in parts if part != name]
    if not parts:
        return room_short
    return f"{room_short}：{'；'.join(parts)}" if room_short else "；".join(parts)


def _metrics_of(dataset: Any) -> dict[str, Any]:
    data = dataset.get("data") if isinstance(dataset, dict) else None
    if not isinstance(data, dict):
        return {}
    for key in ("metrics", "realtime_metrics"):
        metrics = data.get(key)
        if isinstance(metrics, dict):
            return metrics
    return {}


def _activities_of(dataset: Any) -> list[dict[str, Any]]:
    data = dataset.get("data") if isinstance(dataset, dict) else None
    if not isinstance(data, dict):
        return []
    for key in (
        "joined_activities",
        "registered_activities",
        "activities",
        "promotions",
    ):
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _probe_datasets(payload: Any) -> dict[str, Any]:
    """兼容两种真实返回形态：

    - 携程 ``run_watch_group``：``{"dataset_results": [{dataset_id, status, data, ...}]}``；
    - 美团/飞猪平台快照：``{"payload": {"datasets": {dataset_id: {...}}}}``（或顶层 datasets）。
    """

    if not isinstance(payload, dict):
        return {}
    results = payload.get("dataset_results")
    if isinstance(results, list):
        datasets: dict[str, Any] = {}
        for item in results:
            if isinstance(item, dict) and item.get("dataset_id"):
                datasets[str(item["dataset_id"])] = item
        return datasets
    for source in (payload.get("datasets"), (payload.get("payload") or {}).get("datasets")):
        if isinstance(source, dict):
            return source
    return {}


def ctrip_rows_from_run_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    datasets = _probe_datasets(payload)
    rows: list[dict[str, Any]] = []
    needs_review: dict[str, int] = {}

    outline = datasets.get("business_outline")
    if isinstance(outline, dict) and str(outline.get("status") or "") == "success":
        outline_data = (
            outline.get("data") if isinstance(outline.get("data"), dict) else {}
        )
        metrics = _metrics_of(outline)
        captured = outline.get("captured_at")
        dimensions = (
            outline_data.get("time_dimensions")
            if isinstance(outline_data.get("time_dimensions"), dict)
            else {}
        )
        realtime_dimension = (
            dimensions.get("realtime")
            if isinstance(dimensions.get("realtime"), dict)
            else {}
        )
        realtime_metrics = (
            realtime_dimension.get("metrics")
            if str(realtime_dimension.get("status") or "") == "success"
            and isinstance(realtime_dimension.get("metrics"), dict)
            else {}
        )
        for field_key in _CTRIP_REALTIME_FIELDS:
            metric = realtime_metrics.get(field_key)
            value = _metric_value(metric)
            if value is None or str(value).strip() == "":
                continue
            rows.append(
                _row(
                    platform="ctrip",
                    dataset_id="business_outline",
                    field_key=field_key,
                    label=_metric_label(metric),
                    value=value,
                    freshness=FRESHNESS_REALTIME,
                    captured_at=captured,
                )
            )
        # 2026-09-21 需求：收益概览补「离店销售额」。经营概览页昨日数据块
        # （与实时预订/在店间夜同页抓取）落在 daily 维度，只投影销售额一行；
        # 离店间夜量/成交率/平均卖价仍不进全局推送卡。
        daily_dimension = (
            dimensions.get("daily") if isinstance(dimensions.get("daily"), dict) else {}
        )
        daily_metrics = (
            daily_dimension.get("metrics")
            if str(daily_dimension.get("status") or "") == "success"
            and isinstance(daily_dimension.get("metrics"), dict)
            else {}
        )
        departure_metric = daily_metrics.get("departure_sales_amount")
        departure_value = _metric_value(departure_metric)
        if departure_value is not None and str(departure_value).strip() != "":
            rows.append(
                _row(
                    platform="ctrip",
                    dataset_id="business_outline",
                    field_key="departure_sales_amount",
                    label=_metric_label(departure_metric),
                    value=departure_value,
                    freshness=FRESHNESS_REALTIME,
                    captured_at=captured,
                )
            )
        for field_key in _CTRIP_SCORE_FIELDS:
            metric = metrics.get(field_key)
            value = _metric_value(metric)
            if value is not None and str(value).strip() != "":
                rows.append(
                    _row(
                        platform="ctrip",
                        dataset_id="business_outline",
                        field_key=field_key,
                        label=_metric_label(metric),
                        value=value,
                        freshness=FRESHNESS_SCORE_SNAPSHOT,
                        captured_at=captured,
                    )
                )

    registered = datasets.get("promotion_registered")
    if isinstance(registered, dict) and str(registered.get("status") or "") == "success":
        # 2026-09-02 需求：活动卡一行一个活动，值=活动内容（房型短名：优惠规则），
        # 不再输出 已报名/有效期 行。
        for index, item in enumerate(_activities_of(registered)):
            name = str(
                item.get("activity_name")
                or item.get("name")
                or f"活动 {index + 1}"
            )
            content = _ctrip_promotion_content(item)
            if not content:
                continue
            rows.append(
                _row(
                    platform="ctrip",
                    dataset_id="promotion_registered",
                    field_key=f"activity:{index}:content",
                    label=name,
                    value=content,
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=registered.get("captured_at"),
                    dimension="活动",
                )
            )

    promotion_effect = datasets.get("promotion_effect")
    handled_datasets: set[str] = set()
    if isinstance(promotion_effect, dict) and str(promotion_effect.get("status") or "") == "success":
        # T1.3：只放行 parser 标记 realtime 的「今日实时数据」区块指标；
        # 其余为「数据来源」下拉控制的昨日/近7天/近30天口径，不进卡。
        # 2026-09-11 审查：participated_promotions（如 5/15）与促销中心
        # 「已参加促销」列表（4 个）口径冲突，数量以促销中心组为准，此处不进卡。
        effect_metrics = _metrics_of(promotion_effect)
        has_realtime_section = False
        for field_key, metric in effect_metrics.items():
            if not isinstance(metric, dict):
                continue
            if str(metric.get("time_scope") or "") == "realtime":
                has_realtime_section = True
            if field_key == "participated_promotions":
                continue
            if str(metric.get("time_scope") or "") != "realtime":
                continue
            value = metric.get("value")
            if value is None or str(value).strip() == "":
                continue
            display = metric.get("denominator")
            rows.append(
                _row(
                    platform="ctrip",
                    dataset_id="promotion_effect",
                    field_key=field_key,
                    label=str(metric.get("label") or field_key),
                    value=(
                        f"{value}/{display}"
                        if metric.get("denominator_label") and display
                        else value
                    ),
                    freshness=FRESHNESS_REALTIME,
                    captured_at=promotion_effect.get("captured_at"),
                    dimension="活动",
                )
            )
        # 今日实时区块存在（证据成立）时即使只剔剩计数指标也不算 needs_review。
        if has_realtime_section:
            handled_datasets.add("promotion_effect")

    ad_pyramid = datasets.get("ad_pyramid")
    if isinstance(ad_pyramid, dict) and str(ad_pyramid.get("status") or "") == "success":
        ad_data = ad_pyramid.get("data") if isinstance(ad_pyramid.get("data"), dict) else {}
        page_filter = (
            ad_data.get("page_filter") if isinstance(ad_data.get("page_filter"), dict) else {}
        )
        ad_block = (
            ad_data.get("ad_pyramid") if isinstance(ad_data.get("ad_pyramid"), dict) else {}
        )
        today_evidence = (
            bool(page_filter.get("selected"))
            and str(page_filter.get("target") or "") in {"今日", "今天"}
            and str(ad_block.get("date_preset") or "") == "今日"
        )
        if today_evidence:
            summary_metrics = (
                ad_data.get("summary_metrics")
                if isinstance(ad_data.get("summary_metrics"), dict)
                else {}
            )
            for field_key in _CTRIP_AD_PYRAMID_FIELDS:
                metric = summary_metrics.get(field_key)
                if not isinstance(metric, dict):
                    continue
                value = metric.get("value")
                if value is None or str(value).strip() == "":
                    continue
                rows.append(
                    _row(
                        platform="ctrip",
                        dataset_id="ad_pyramid",
                        field_key=field_key,
                        label=str(metric.get("label") or field_key),
                        value=value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=ad_pyramid.get("captured_at"),
                        dimension="广告",
                    )
                )

    pyramid_bid = datasets.get("pyramid_bid")
    if isinstance(pyramid_bid, dict) and str(pyramid_bid.get("status") or "") == "success":
        # 2026-09-10：金字塔出价列表（只读，读取链路与「金字塔出价」指令共用）。
        # 行携带 parts（出价/预算/花费/状态四列），由卡片层渲染成表格；
        # 读取成功但无计划时输出占位行；读取失败不产出（模块从广告卡缺席）。
        pyramid_data = (
            pyramid_bid.get("data") if isinstance(pyramid_bid.get("data"), dict) else {}
        )
        promotions = (
            pyramid_data.get("promotions")
            if isinstance(pyramid_data.get("promotions"), list)
            else []
        )
        campaign_rows: list[dict[str, Any]] = []
        for index, item in enumerate(promotions):
            if not isinstance(item, dict):
                continue
            name = str(item.get("campaign_name") or "").strip()
            if not name:
                continue
            parts = pyramid_promotion_table_row(item)["parts"]
            row = _row(
                platform="ctrip",
                dataset_id="pyramid_bid",
                field_key=f"pyramid:{index}:{name}",
                label=name,
                value=parts[0],
                freshness=FRESHNESS_REALTIME,
                captured_at=pyramid_bid.get("captured_at"),
                dimension="广告",
            )
            row["parts"] = parts
            campaign_rows.append(row)
        if campaign_rows:
            rows.extend(campaign_rows)
        else:
            rows.append(
                _row(
                    platform="ctrip",
                    dataset_id="pyramid_bid",
                    field_key="pyramid_bid.empty",
                    label="推广计划",
                    value="暂无金字塔推广计划",
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=pyramid_bid.get("captured_at"),
                    dimension="广告",
                )
            )

    profile = datasets.get("competition_profile")
    if isinstance(profile, dict) and str(profile.get("status") or "") == "success":
        profile_data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
        dimensions = (
            profile_data.get("time_dimensions")
            if isinstance(profile_data.get("time_dimensions"), dict)
            else {}
        )
        realtime_dim = (
            dimensions.get("realtime") if isinstance(dimensions.get("realtime"), dict) else {}
        )
        if str(realtime_dim.get("status") or "") == "success":
            realtime_metrics = (
                realtime_dim.get("metrics")
                if isinstance(realtime_dim.get("metrics"), dict)
                else {}
            )
            for field_key in _CTRIP_COMPETITION_REALTIME_FIELDS:
                metric = realtime_metrics.get(field_key)
                if not isinstance(metric, dict):
                    continue
                value = metric.get("value")
                if value is None or str(value).strip() == "":
                    continue
                metric_row = _row(
                    platform="ctrip",
                    dataset_id="competition_profile",
                    field_key=field_key,
                    label=str(metric.get("label") or field_key),
                    value=value,
                    freshness=FRESHNESS_REALTIME,
                    captured_at=profile.get("captured_at"),
                    dimension="竞争圈",
                )
                competitive_rank = str(metric.get("competitive_rank") or "").strip()
                if competitive_rank:
                    metric_row["competitive_rank"] = competitive_rank
                rows.append(metric_row)




            # 需求2：收益概览销售额 = competition_profile 实时维度「预订销售额」，作为收益概览行
            # 注入 business_outline 数据集以进入收益概览分组
            sales_metric = realtime_metrics.get("booking_sales")
            if isinstance(sales_metric, dict):
                sales_value = sales_metric.get("value")
                if sales_value is not None and str(sales_value).strip() != "":
                    sales_row = _row(
                        platform="ctrip",
                        dataset_id="business_outline",
                        field_key="booking_sales",
                        label=str(sales_metric.get("label") or "预订销售额"),
                        value=sales_value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=profile.get("captured_at"),
                        dimension="实时",
                    )
                    sales_rank = str(sales_metric.get("competitive_rank") or "").strip()
                    if sales_rank:
                        sales_row["competitive_rank"] = sales_rank
                    rows.append(sales_row)


    flow = datasets.get("flow_data")
    if isinstance(flow, dict) and str(flow.get("status") or "") == "success":
        flow_data = flow.get("data") if isinstance(flow.get("data"), dict) else {}
        flow_dims = (
            flow_data.get("time_dimensions")
            if isinstance(flow_data.get("time_dimensions"), dict)
            else {}
        )
        flow_realtime = (
            flow_dims.get("realtime") if isinstance(flow_dims.get("realtime"), dict) else {}
        )
        if str(flow_realtime.get("status") or "") == "success":
            flow_metrics = (
                flow_realtime.get("metrics")
                if isinstance(flow_realtime.get("metrics"), dict)
                else {}
            )
            # 需求4：流量曝光三指标（流量概况-实时访客量、流量转化分析-实时排名/实时起价）
            for field_key in (
                "realtime_visitors",
                "realtime_visitor_rank",
                "realtime_starting_price",
            ):
                metric = flow_metrics.get(field_key)
                if not isinstance(metric, dict):
                    continue
                value = metric.get("value")
                if value is None or str(value).strip() == "":
                    continue
                flow_row = _row(
                    platform="ctrip",
                    dataset_id="flow_data",
                    field_key=field_key,
                    label=str(metric.get("label") or field_key),
                    value=value,
                    freshness=FRESHNESS_REALTIME,
                    captured_at=flow.get("captured_at"),
                )
                flow_rank = str(metric.get("competitive_rank") or "").strip()
                if flow_rank:
                    flow_row["competitive_rank"] = flow_rank
                rows.append(flow_row)


    # 无时间证据的数据集：计数进 needs_review，不进卡。
    extracted_datasets = {row["dataset_id"] for row in rows} | handled_datasets
    for dataset_id, dataset in datasets.items():
        if not isinstance(dataset, dict):
            continue
        if dataset_id in extracted_datasets:
            continue
        if str(dataset.get("status") or "") == "success":
            needs_review[dataset_id] = 1
    return rows, needs_review


def meituan_rows_from_snapshot_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    datasets = _probe_datasets(payload)
    rows: list[dict[str, Any]] = []
    needs_review: dict[str, int] = {}

    workbench = datasets.get("workbench_home")
    if isinstance(workbench, dict) and str(workbench.get("status") or "") == "success":
        data = workbench.get("data") if isinstance(workbench.get("data"), dict) else {}
        realtime = data.get("realtime_data")
        if isinstance(realtime, dict):
            for key, value in realtime.items():
                rows.append(
                    _row(
                        platform="meituan",
                        dataset_id="workbench_home",
                        field_key=key,
                        label=str(key),
                        value=value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=None,
                    )
                )
        # HOS 经营分：页面存量评分，按评分快照例外处理（同携程 §6 口径）。
        hos = data.get("hos_score") if isinstance(data.get("hos_score"), dict) else {}
        hos_value = hos.get("value") if hos.get("value") is not None else hos.get("score")
        if hos_value is not None and str(hos_value).strip():
            rows.append(
                _row(
                    platform="meituan",
                    dataset_id="workbench_home",
                    field_key="hos_score",
                    label="HOS 经营分",
                    value=hos_value,
                    freshness=FRESHNESS_SCORE_SNAPSHOT,
                    captured_at=None,
                )
            )

    comments = datasets.get("comment_manage_home")
    if isinstance(comments, dict) and str(comments.get("status") or "") == "success":
        data = comments.get("data") if isinstance(comments.get("data"), dict) else {}
        if data.get("review_score") is not None:
            rows.append(
                _row(
                    platform="meituan",
                    dataset_id="comment_manage_home",
                    field_key="review_score",
                    label="点评分",
                    value=data.get("review_score"),
                    freshness=FRESHNESS_SCORE_SNAPSHOT,
                    captured_at=None,
                )
            )

    # T1.1（2026-08-28 实测）：数据中心「基础经营/分栏」区自带
    # 今日实时/昨日/近7天/近30天 页签，默认选中「昨日」；读取流程额外切到
    # 「今日实时」抓取并校验选中态（mtd-radio-button-checked）后，
    # 以 today_realtime_metrics + today_realtime_evidence 落库，仅此放行。
    for dataset_id in ("data_center_home", "data_center_trade", "data_center_traffic"):
        dataset = datasets.get(dataset_id)
        if not isinstance(dataset, dict) or str(dataset.get("status") or "") != "success":
            continue
        data = dataset.get("data") if isinstance(dataset.get("data"), dict) else {}
        evidence = (
            data.get("today_realtime_evidence")
            if isinstance(data.get("today_realtime_evidence"), dict)
            else {}
        )
        realtime_metrics = (
            data.get("today_realtime_metrics")
            if isinstance(data.get("today_realtime_metrics"), dict)
            else {}
        )
        if dataset_id == "data_center_traffic":
            # 流量分析 metrics（2026-09-02）：浏览人数/浏览-支付转化率进流量曝光，
            # 其余指标不进卡。该数据集无今日实时页签，单独处理。
            metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
            for key, value in metrics.items():
                value_text = str(value or "").strip()
                if not value_text or value_text in {"-", "–", "—"} or value_text.startswith("- "):
                    continue
                label = str(key)
                if label not in _MEITUAN_TRAFFIC_LABELS:
                    continue
                traffic_row = _row(
                    platform="meituan",
                    dataset_id=dataset_id,
                    field_key=label,
                    label=label,
                    value=value_text,
                    freshness=FRESHNESS_REALTIME,
                    captured_at=None,
                )
                traffic_row["dimension"] = "流量趋势"
                rows.append(traffic_row)
            continue
        if str(evidence.get("selected_page_tab") or "") != "今日实时":
            continue
        for key, metric in realtime_metrics.items():
            value = (
                metric.get("value")
                if isinstance(metric, dict)
                else metric
            )
            if value is None or str(value).strip() == "":
                continue
            # 页面缺失指标以「-」「- 次」占位（2026-09-01 实测），不进卡；
            # 收益概览指标（含入住间夜）保留占位行——页面真实状态，运营
            # 需要看到该指标当日尚无数据（2026-09-01 复审#1）。
            value_text = str(value).strip()
            if (
                value_text in {"-", "–", "—"} or value_text.startswith("- ")
            ) and str(key) not in _MEITUAN_REVENUE_METRIC_KEYS:
                continue
            label = str(key)
            metric_row = _row(
                platform="meituan",
                dataset_id=dataset_id,
                field_key=str(key),
                label=label,
                value=value,
                freshness=FRESHNESS_REALTIME,
                captured_at=None,
            )
            if isinstance(metric, dict):
                peer_rank = str(metric.get("peer_rank") or "").strip()
                if peer_rank and peer_rank != "-":
                    metric_row["competitive_rank"] = peer_rank
                wow = str(metric.get("week_over_week") or "").strip()
                # 实时页签页面自标「较上期」（默认昨日页签才是较上周）。
                if wow and wow != "-":
                    metric_row["comparison"] = wow if wow.startswith("较") else "较上期 " + wow
            if label in _MEITUAN_TRAFFIC_LABELS:
                metric_row["dimension"] = "流量趋势"
            rows.append(metric_row)

    # 同行榜单（同行分析 tab，四榜）：本店名次/榜内总数（2026-09-02 实测结构）。
    peer = datasets.get("data_center_peer_ranking")
    if isinstance(peer, dict) and str(peer.get("status") or "") == "success":
        peer_data = peer.get("data") if isinstance(peer.get("data"), dict) else {}
        rankings = (
            peer_data.get("peer_rankings_by_tab")
            if isinstance(peer_data.get("peer_rankings_by_tab"), dict)
            else {}
        )
        for tab in ("入住榜", "销售榜", "流量榜", "转化榜"):
            info = rankings.get(tab)
            if not isinstance(info, dict):
                continue
            boards = info.get("boards")
            if not isinstance(boards, list):
                # 兼容旧结构（单榜单值）
                rank_text = str(info.get("rank_text") or "").strip()
                boards = (
                    [{"metric": "", "rank_text": rank_text}] if rank_text else []
                )
            for board in boards:
                if not isinstance(board, dict):
                    continue
                rank_text = str(board.get("rank_text") or "").strip()
                if not rank_text:
                    continue
                metric = str(board.get("metric") or "").strip()
                label = f"{tab}·{metric}" if metric else tab
                rows.append(
                    _row(
                        platform="meituan",
                        dataset_id="data_center_peer_ranking",
                        field_key=f"peer_ranking.{tab}.{metric}" if metric else f"peer_ranking.{tab}",
                        label=label,
                        value=rank_text,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=None,
                        dimension="同行榜单",
                    )
                )

    promotion = datasets.get("promotion_coupon")
    if isinstance(promotion, dict) and str(promotion.get("status") or "") == "success":
        promo_data = (
            promotion.get("data") if isinstance(promotion.get("data"), dict) else {}
        )
        signup_activities = (
            promo_data.get("signup_activities")
            if isinstance(promo_data.get("signup_activities"), list)
            else []
        )
        # T2（2026-08-28 实测）：仅已报名活动进卡；2026-09-02 需求值=活动
        # 内容描述，不再输出 已报名/有效期 行（无描述则跳过）。
        emitted_names: set[str] = set()
        for index, item in enumerate(signup_activities):
            if not isinstance(item, dict) or str(item.get("status") or "") != "已报名":
                continue
            name = str(item.get("name") or f"活动 {index + 1}")
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            emitted_names.add(name)
            rows.append(
                _row(
                    platform="meituan",
                    dataset_id="promotion_coupon",
                    field_key=f"signup:{index}:content",
                    label=name,
                    value=description,
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=None,
                    dimension="活动",
                )
            )
        # 2026-09-11 审查：神券卡片（coupon_activities）里「报名成功」的活动
        # 同样算已参加（活动报名页偶发读不到列表）；内容用页面字段
        # （活动时间+渠道），与活动报名页结果按活动名去重。
        coupon_activities = (
            promo_data.get("coupon_activities")
            if isinstance(promo_data.get("coupon_activities"), list)
            else []
        )
        for item in coupon_activities:
            if not isinstance(item, dict) or str(item.get("status") or "") != "报名成功":
                continue
            name = str(item.get("activity_name") or item.get("name") or "").strip()
            if not name or name in emitted_names:
                continue
            activity_time = str(item.get("activity_time") or "").strip()
            channel = str(item.get("channel") or "").strip()
            if activity_time and channel:
                content = f"{activity_time}（{channel}）"
            else:
                content = activity_time or channel
            if not content:
                continue
            rows.append(
                _row(
                    platform="meituan",
                    dataset_id="promotion_coupon",
                    field_key=f"coupon:{name}:content",
                    label=name,
                    value=content,
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=None,
                    dimension="活动",
                )
            )


    # 推广通（2026-09-11）：仅已开通（推广中>0）的店铺产出广告行；
    # 「推广概览·花费(元)」与财务「今日总花费(元)」同值重复，后者口径更明确。
    ad_cpc = datasets.get("ad_cpc")
    handled_datasets: set[str] = set()
    if isinstance(ad_cpc, dict) and str(ad_cpc.get("status") or "") == "success":
        ad_data = ad_cpc.get("data") if isinstance(ad_cpc.get("data"), dict) else {}
        if bool(ad_data.get("activated")):
            finance = (
                ad_data.get("finance_overview")
                if isinstance(ad_data.get("finance_overview"), dict)
                else {}
            )
            overview = (
                ad_data.get("promotion_overview")
                if isinstance(ad_data.get("promotion_overview"), dict)
                else {}
            )
            ordered: list[tuple[str, str]] = [
                ("今日总花费(元)", finance.get("今日总花费(元)", overview.get("花费(元)"))),
                ("昨日总花费(元)", finance.get("昨日总花费(元)")),
                ("账户余额(元)", finance.get("账户余额(元)")),
                ("曝光(次)", overview.get("曝光(次)")),
                ("点击(次)", overview.get("点击(次)")),
                ("今日预算(元)", overview.get("今日预算(元)")),
                ("推广中(个)", overview.get("推广中(个)")),
                ("待优化(个)", overview.get("待优化(个)")),
            ]
            for label, value in ordered:
                if value is None or str(value).strip() == "":
                    continue
                rows.append(
                    _row(
                        platform="meituan",
                        dataset_id="ad_cpc",
                        field_key=label,
                        label=label,
                        value=value,
                        freshness=FRESHNESS_CURRENT_STATE,
                        captured_at=None,
                        dimension="广告",
                    )
                )
        # 未开通是合法读取结果：无行也不计入 needs_review 汇总。
        handled_datasets.add("ad_cpc")

    extracted_datasets = {row["dataset_id"] for row in rows} | handled_datasets
    for dataset_id, dataset in datasets.items():
        if dataset_id in extracted_datasets or not isinstance(dataset, dict):
            continue
        if str(dataset.get("status") or "") == "success":
            needs_review[dataset_id] = 1
    return rows, needs_review


def fliggy_rows_from_snapshot_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    datasets = _probe_datasets(payload)
    rows: list[dict[str, Any]] = []
    needs_review: dict[str, int] = {}

    home = datasets.get("home_v1")
    if isinstance(home, dict) and str(home.get("status") or "") == "success":
        data = home.get("data") if isinstance(home.get("data"), dict) else {}
        today = data.get("today_data")
        if isinstance(today, dict):
            for key, value in today.items():
                rows.append(
                    _row(
                        platform="fliggy",
                        dataset_id="home_v1",
                        field_key=key,
                        label=str(key),
                        value=value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=None,
                    )
                )

    diagnose = datasets.get("diagnose_report")
    if isinstance(diagnose, dict) and str(diagnose.get("status") or "") == "success":
        data = diagnose.get("data") if isinstance(diagnose.get("data"), dict) else {}
        # diagnose_report 的 trade 概览由 parser 的「实时数据」区块与「昨日数据」
        # 区块合并而成（fliggy_ebooking._merge_fliggy_diagnose_trade）：不带
        # 「昨日」前缀的键即实时区块字段；带前缀的为昨日口径，不进卡。
        trade = data.get("trade_overview")
        if isinstance(trade, dict):
            for key, value in trade.items():
                if str(key).startswith("昨日"):
                    continue
                rows.append(
                    _row(
                        platform="fliggy",
                        dataset_id="diagnose_report",
                        field_key=key,
                        label=str(key),
                        value=value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=None,
                    )
                )
        if data.get("guest_review_score") is not None:
            rows.append(
                _row(
                    platform="fliggy",
                    dataset_id="diagnose_report",
                    field_key="guest_review_score",
                    label="客人点评得分",
                    value=data.get("guest_review_score"),
                    freshness=FRESHNESS_SCORE_SNAPSHOT,
                    captured_at=None,
                )
            )
        if data.get("mci_index") is not None:
            rows.append(
                _row(
                    platform="fliggy",
                    dataset_id="diagnose_report",
                    field_key="mci_index",
                    label="MCI 酒店竞争力指数",
                    value=data.get("mci_index"),
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=None,
                )
            )

    operating = datasets.get("operating_data")
    if isinstance(operating, dict) and str(operating.get("status") or "") == "success":
        data = operating.get("data") if isinstance(operating.get("data"), dict) else {}
        today_evidence = (
            data.get("today_evidence")
            if isinstance(data.get("today_evidence"), dict)
            else {}
        )
        # T1.6（2026-08-28 实测）：经营数据页「在店/预定」区默认选中「今日」
        # （ant-radio-button-wrapper-checked），且每区带当日「数据更新时间」；
        # parser 提取 today_evidence.today_confirmed，仅此放行在店/预定区。
        if bool(today_evidence.get("today_confirmed")):
            for section_key, dimension in (
                ("in_house_data", "在店"),
                ("booking_data", "预定"),
            ):
                section = (
                    data.get(section_key)
                    if isinstance(data.get(section_key), dict)
                    else {}
                )
                for key, value in section.items():
                    if key == "预定间夜" and "预订间夜" in section:
                        # parser 会把「预订间夜」复制为「预定间夜」别名，跳过避免重复行
                        continue
                    if value is None or str(value).strip() == "":
                        continue
                    rows.append(
                        _row(
                            platform="fliggy",
                            dataset_id="operating_data",
                            field_key=f"{section_key}.{key}",
                            label=str(key),
                            value=value,
                            freshness=FRESHNESS_REALTIME,
                            captured_at=None,
                            dimension=dimension,
                        )
                    )

            # 需求8：飞猪流量数据＝经营数据-流量数据-详情页曝光人数 / 预订转化率，
            # 标记「流量」维度以进入经营卡「流量曝光」模块（2026-09-01 修正）。
            flow = data.get("flow_data") if isinstance(data.get("flow_data"), dict) else {}
            for key, value in flow.items():
                if value is None or str(value).strip() == "":
                    continue
                rows.append(
                    _row(
                        platform="fliggy",
                        dataset_id="operating_data",
                        field_key=f"flow_data.{key}",
                        label=str(key),
                        value=value,
                        freshness=FRESHNESS_REALTIME,
                        captured_at=None,
                        dimension="流量",
                    )
                )

    promotion = datasets.get("promotion_agent")
    if isinstance(promotion, dict) and str(promotion.get("status") or "") == "success":
        # 2026-09-02 需求：值=活动内容描述（面向...），旧版式退回活动力度；
        # 不再输出 已报名/有效期 行（两者皆无则跳过）。
        for index, item in enumerate(_activities_of(promotion)):
            name = str(item.get("name") or item.get("activity_name") or f"活动 {index + 1}")
            content = (
                str(item.get("description") or "").strip()
                or str(item.get("strength") or "").strip()
            )
            if not content:
                continue
            rows.append(
                _row(
                    platform="fliggy",
                    dataset_id="promotion_agent",
                    field_key=f"activity:{index}:content",
                    label=name,
                    value=content,
                    freshness=FRESHNESS_CURRENT_STATE,
                    captured_at=None,
                    dimension="活动",
                )
            )

    # 直通车（2026-09-11）：已开通（有智能投放计划或当日有消耗）才产出
    # 广告行；数据总览/转化概况在「今日」页签选中态确认（today_evidence）
    # 时按 realtime 口径放行，否则按 current_state。2026-09-16 起只出实时
    # 口径行（智能投放计划/投放状态为账户当前状态描述）。
    ad_p4p = datasets.get("ad_p4p")
    handled_datasets: set[str] = set()
    if isinstance(ad_p4p, dict) and str(ad_p4p.get("status") or "") == "success":
        ad_data = ad_p4p.get("data") if isinstance(ad_p4p.get("data"), dict) else {}
        if bool(ad_data.get("activated")):
            today_evidence = (
                ad_data.get("today_evidence")
                if isinstance(ad_data.get("today_evidence"), dict)
                else {}
            )
            today_freshness = (
                FRESHNESS_REALTIME
                if bool(today_evidence.get("today_confirmed"))
                else FRESHNESS_CURRENT_STATE
            )
            overview = (
                ad_data.get("overview")
                if isinstance(ad_data.get("overview"), dict)
                else {}
            )
            conversion = (
                ad_data.get("conversion")
                if isinstance(ad_data.get("conversion"), dict)
                else {}
            )
            campaign = (
                ad_data.get("campaign")
                if isinstance(ad_data.get("campaign"), dict)
                else {}
            )
            ordered: list[tuple[str, str, str]] = [
                ("智能投放计划", str(campaign.get("name") or ""), FRESHNESS_CURRENT_STATE),
                ("投放状态", str(campaign.get("status") or ""), FRESHNESS_CURRENT_STATE),
                ("总花费(元)", str(overview.get("总花费(元)") or ""), today_freshness),
                ("广告引导浏览量", str(overview.get("广告引导浏览量") or ""), today_freshness),
                ("广告引导订单金额(元)", str(overview.get("广告引导订单金额(元)") or ""), today_freshness),
                ("ROI", str(overview.get("ROI") or ""), today_freshness),
                ("间夜量", str(overview.get("间夜量") or ""), today_freshness),
                ("浏览量", str(conversion.get("浏览量") or ""), today_freshness),
                ("点击量", str(conversion.get("点击量") or ""), today_freshness),
                ("购买量", str(conversion.get("购买量") or ""), today_freshness),
                ("点击率", str(conversion.get("点击率") or ""), today_freshness),
                ("转化率", str(conversion.get("转化率") or ""), today_freshness),
                # 2026-09-16 运营要求：直通车只展示实时数据，今日预估花费与
                # 累计花费/累计浏览量/累计引导间夜数四项不再进卡。
            ]
            for label, value, freshness in ordered:
                if not value.strip():
                    continue
                rows.append(
                    _row(
                        platform="fliggy",
                        dataset_id="ad_p4p",
                        field_key=label,
                        label=label,
                        value=value,
                        freshness=freshness,
                        captured_at=None,
                        dimension="广告",
                    )
                )
        # 未开通商业化权限是合法读取结果：无行也不计入 needs_review 汇总。
        handled_datasets.add("ad_p4p")

    extracted_datasets = {row["dataset_id"] for row in rows} | handled_datasets
    for dataset_id, dataset in datasets.items():
        if dataset_id in extracted_datasets or not isinstance(dataset, dict):
            continue
        if str(dataset.get("status") or "") == "success":
            needs_review[dataset_id] = 1
    return rows, needs_review


ADAPTERS_BY_PLATFORM = {
    "ctrip": ctrip_rows_from_run_payload,
    "meituan": meituan_rows_from_snapshot_payload,
    "fliggy": fliggy_rows_from_snapshot_payload,
}


def _row(
    *,
    platform: str,
    dataset_id: str,
    field_key: str,
    value: Any,
    freshness: str,
    captured_at: Any,
    label: str | None = None,
    dimension: str = "",
) -> dict[str, Any]:
    preset = _CTRIP_FIELD_LABELS.get(field_key)
    if preset is not None:
        label, value_type, unit = preset
    else:
        label = label or str(field_key)
        value_type, unit = _infer_type_and_unit(value)
    return {
        "platform": platform,
        "dataset_id": dataset_id,
        "field_key": field_key,
        "label": label,
        "value": value,
        "value_type": value_type,
        "unit": unit,
        "time_scope": freshness,
        "freshness": freshness,
        "captured_at": captured_at,
        "source_path": f"datasets.{dataset_id}.data.{field_key}",
        "dimension": dimension,
    }


def _metric_value(metric: Any) -> Any:
    if isinstance(metric, dict):
        return metric.get("value")
    return metric


def _metric_label(metric: Any) -> str | None:
    if not isinstance(metric, dict):
        return None
    return str(metric.get("label") or metric.get("raw_label") or "").strip() or None


def _infer_type_and_unit(value: Any) -> tuple[str, str]:
    if isinstance(value, bool):
        return "text", ""
    if isinstance(value, (int, float)):
        return "number", ""
    text = str(value or "")
    if text.endswith("%"):
        return "percent", "%"
    return "text", ""
