# -*- coding: utf-8 -*-
"""生成 EBK 全局推送三分类卡的浏览器预览页（真实 card JSON 的近似渲染）。

卡样式回归工具（T2.5 决议保留）：卡片 builder 结构调整后重跑本脚本，
对比 docs/preview/ 下的输出页即可回归样式，无需真实读取与飞书投递。
示例数据仅用于样式确认，非真实经营数据；正式数据由调度器读取真实页面产生。
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.ebk_global_push.global_push_cards import build_global_push_cards  # noqa: E402
from app.domain.ebk_global_push.projection import extract_ctrip_score_summary  # noqa: E402

READ_TIME = "2026-09-01T12:00:00+08:00"


def row(platform, dataset_id, label, value, *, value_type="integer", unit="",
        freshness="realtime", dimension=""):
    return {
        "platform": platform,
        "dataset_id": dataset_id,
        "field_key": label,
        "label": label,
        "value": value,
        "value_type": value_type,
        "unit": unit,
        "time_scope": freshness,
        "freshness": freshness,
        "captured_at": READ_TIME,
        "source_path": f"datasets.{dataset_id}.data.metrics.{label}",
        "dimension": dimension,
    }


def score_rows(psi, review):
    rows = []
    if psi is not None:
        r = row("ctrip", "business_outline", "PSI 服务质量分", psi,
                value_type="score", freshness="score_snapshot")
        r["field_key"] = "psi_service_score"
        rows.append(r)
    if review is not None:
        r = row("ctrip", "business_outline", "酒店点评分", review,
                value_type="score", freshness="score_snapshot")
        r["field_key"] = "hotel_review_score"
        rows.append(r)
    return extract_ctrip_score_summary(rows)


# ---------------------------------------------------------------- 场景 A：全成功
# 示例数据取自 2026-09-01 19:04-19:06 三平台 EBK 实测页面（CDP 只读抓取），
# 环比幅度为演示值（生产由 service._inject_comparison 对上一条成功快照计算）。
CTRIP_ROWS = [
    {"platform": "ctrip", "dataset_id": "business_outline", "field_key": "booking_sales", "label": "预订销售额", "value": "23.59万", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "实时", "comparison": "+14400.00 (+6.5%)"},
    {"platform": "ctrip", "dataset_id": "business_outline", "field_key": "realtime_booking_orders", "label": "实时预订订单", "value": 27, "value_type": "integer", "unit": "单", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "comparison": "+6 (+28.6%)"},
    {"platform": "ctrip", "dataset_id": "business_outline", "field_key": "realtime_in_house_room_nights", "label": "实时在店间夜", "value": 44, "value_type": "integer", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "comparison": "+5 (+12.8%)"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "booking_orders", "label": "预订订单量", "value": 382, "value_type": "integer", "unit": "单", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "竞争圈", "competitive_rank": "5"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "booking_sales", "label": "预订销售额", "value": "23.59万", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "竞争圈", "competitive_rank": "6"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "in_house_room_nights", "label": "在店间夜", "value": 524, "value_type": "integer", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "竞争圈", "competitive_rank": "5"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "occupancy_rate", "label": "出租率", "value": "42.78%", "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "竞争圈", "competitive_rank": "7"},
    {"platform": "ctrip", "dataset_id": "flow_data", "field_key": "realtime_visitors", "label": "实时访客量", "value": 202, "value_type": "integer", "unit": "人", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
    {"platform": "ctrip", "dataset_id": "flow_data", "field_key": "realtime_visitor_rank", "label": "实时排名", "value": 240, "value_type": "rank", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
    {"platform": "ctrip", "dataset_id": "flow_data", "field_key": "realtime_starting_price", "label": "实时起价", "value": 229, "value_type": "amount", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "competitive_rank": "3/21"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "ctrip_app_visitors", "label": "携程APP访客", "value": "2,429", "value_type": "integer", "unit": "人", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量", "competitive_rank": "12"},
    {"platform": "ctrip", "dataset_id": "competition_profile", "field_key": "ctrip_app_conversion_rate", "label": "携程APP转化率", "value": "6.09%", "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量", "competitive_rank": "2"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "clicks", "label": "点击(次)", "value": 47, "value_type": "integer", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "impressions", "label": "曝光(次)", "value": 12000, "value_type": "integer", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "avg_impression_rank", "label": "平均曝光位次", "value": 12, "value_type": "rank", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "click_rate", "label": "点击率", "value": "2.83%", "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "total_spend_cny", "label": "总花费(CNY)", "value": "193.78", "value_type": "amount", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "ad_pyramid", "field_key": "roas", "label": "广告投产比(ROAS)", "value": 14.81, "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "广告"},
    {"platform": "ctrip", "dataset_id": "promotion_registered", "field_key": "a1", "label": "暑期早鸟特惠", "value": "预豪华套房：提前7天预订 立减 15%", "value_type": "text", "unit": "", "freshness": "current_state", "time_scope": "current_state", "captured_at": READ_TIME, "source_path": "x", "dimension": "活动"},
]
def _with_field_key(row, field_key):
    row["field_key"] = field_key
    return row


MEITUAN_ROWS = [
    # 收益概览（2026-09-01 19:06 实测·今日实时）：销售额/支付订单数/入住间夜
    {"platform": "meituan", "dataset_id": "data_center_home", "field_key": "销售额", "label": "销售额", "value": "5437.00 元", "value_type": "amount", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "comparison": "+559.00 (+11.5%)", "competitive_rank": "4/11"},
    {"platform": "meituan", "dataset_id": "data_center_home", "field_key": "支付订单数", "label": "支付订单数", "value": "10 单", "value_type": "integer", "unit": "单", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "", "comparison": "+3 (+42.9%)", "competitive_rank": "4/11"},
    {"platform": "meituan", "dataset_id": "data_center_home", "field_key": "入住间夜", "label": "入住间夜", "value": "- 间夜", "value_type": "text", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": ""},
    # 同行榜单：每榜签两张子榜（2026-09-02 实测·同行分析）
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.入住榜.入住间夜", "label": "入住榜·入住间夜", "value": "5/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.入住榜.房费收入", "label": "入住榜·房费收入", "value": "4/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.销售榜.销售间夜", "label": "销售榜·销售间夜", "value": "4/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.销售榜.销售额", "label": "销售榜·销售额", "value": "5/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.流量榜.曝光量", "label": "流量榜·曝光量", "value": "6/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.流量榜.浏览人数", "label": "流量榜·浏览人数", "value": "3/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.转化榜.浏览转化率", "label": "转化榜·浏览转化率", "value": "1/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    {"platform": "meituan", "dataset_id": "data_center_peer_ranking", "field_key": "peer_ranking.转化榜.支付转化率", "label": "转化榜·支付转化率", "value": "6/11", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "同行榜单"},
    # 流量曝光（流量分析·今日实时·流量趋势数据 2026-09-01 实测）
    {"platform": "meituan", "dataset_id": "data_center_traffic", "field_key": "曝光人数", "label": "曝光人数", "value": "1835人", "value_type": "text", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量趋势", "comparison": "+233 (+14.5%)", "competitive_rank": "6/11"},
    {"platform": "meituan", "dataset_id": "data_center_traffic", "field_key": "浏览人数", "label": "浏览人数", "value": "274 人", "value_type": "integer", "unit": "人", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量趋势", "comparison": "+27 (+10.9%)", "competitive_rank": "3/11"},
    {"platform": "meituan", "dataset_id": "data_center_traffic", "field_key": "曝光-浏览转化率", "label": "曝光-浏览转化率", "value": "14.93%", "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量趋势", "comparison": "+0.6 (+4.2%)", "competitive_rank": "1/11"},
    {"platform": "meituan", "dataset_id": "data_center_traffic", "field_key": "浏览-支付转化率", "label": "浏览-支付转化率", "value": "5.47%", "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量趋势", "comparison": "+0.4 (+7.9%)", "competitive_rank": "6/11"},
    {"platform": "meituan", "dataset_id": "workbench_home", "field_key": "hos_score", "label": "HOS 经营分", "value": 88, "value_type": "score", "unit": "", "freshness": "score_snapshot", "time_scope": "score_snapshot", "captured_at": READ_TIME, "source_path": "x", "dimension": ""},
    {"platform": "meituan", "dataset_id": "comment_manage_home", "field_key": "review_score", "label": "点评分", "value": 4.5, "value_type": "score", "unit": "", "freshness": "score_snapshot", "time_scope": "score_snapshot", "captured_at": READ_TIME, "source_path": "x", "dimension": ""},
    {"platform": "meituan", "dataset_id": "promotion_coupon", "field_key": "p1", "label": "【平台主推】踏春赏花特惠", "value": "春季专享特惠活动，精准引流", "value_type": "text", "unit": "", "freshness": "current_state", "time_scope": "current_state", "captured_at": READ_TIME, "source_path": "x", "dimension": "活动"},
]

FLIGGY_ROWS = [
    {"platform": "fliggy", "dataset_id": "home_v1", "field_key": "销售额", "label": "销售额", "value": "1860.00 元", "comparison": "+352.00 (+23.3%)", "value_type": "amount", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "今日"},
    {"platform": "fliggy", "dataset_id": "home_v1", "field_key": "预订订单量", "label": "预订订单量", "value": 6, "comparison": "+1 (+20.0%)", "value_type": "integer", "unit": "单", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "今日"},
    {"platform": "fliggy", "dataset_id": "home_v1", "field_key": "预订间夜", "label": "预订间夜", "value": 7, "comparison": "+2 (+40.0%)", "value_type": "integer", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "今日"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "in_house_data.在店间夜", "label": "在店间夜", "value": "4间夜", "comparison": "+1 (+33.3%)", "value_type": "text", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "在店"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "in_house_data.房费收入", "label": "房费收入", "value": "979元", "comparison": "+119 (+13.8%)", "value_type": "text", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "在店"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "in_house_data.间夜单价", "label": "间夜单价", "value": "245元", "comparison": "+30 (+14.0%)", "value_type": "text", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "在店"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "booking_data.预订间夜", "label": "预订间夜", "value": "0", "comparison": "凌晨数据", "value_type": "text", "unit": "间夜", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "预定"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "booking_data.预订收入金额", "label": "预订收入金额", "value": "0", "comparison": "凌晨数据", "value_type": "text", "unit": "元", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "预定"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "booking_data.预订订单", "label": "预订订单", "value": "0", "comparison": "凌晨数据", "value_type": "text", "unit": "单", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "预定"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "flow_data.详情页曝光人数", "label": "详情页曝光人数", "value": 1208, "value_type": "integer", "unit": "人", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量"},
    {"platform": "fliggy", "dataset_id": "operating_data", "field_key": "flow_data.预订转化率", "label": "预订转化率", "value": '4.5%', "value_type": "percent", "unit": "", "freshness": "realtime", "time_scope": "realtime", "captured_at": READ_TIME, "source_path": "x", "dimension": "流量"},
    {"platform": "fliggy", "dataset_id": "diagnose_report", "field_key": "guest_review_score", "label": "客人点评得分", "value": 4.7, "value_type": "score", "unit": "", "freshness": "score_snapshot", "time_scope": "score_snapshot", "captured_at": READ_TIME, "source_path": "x", "dimension": ""},
    {"platform": "fliggy", "dataset_id": "promotion_agent", "field_key": "f1", "label": "全人群运营活动", "value": "面向全人群及全场景，灵活选择房型及日期，快速管理酒店价格", "value_type": "text", "unit": "", "freshness": "current_state", "time_scope": "current_state", "captured_at": READ_TIME, "source_path": "x", "dimension": "活动"},
]

SCENARIO_A = dict(
    name="场景 A｜三平台全部成功（经营卡多组 → 自动分页连发）",
    desc="数据取自 2026-09-01 19:04-19:06 三平台 EBK 实测页面；环比幅度为演示值（生产对比上一条"
         "成功快照）。经营分类数据组超过单卡 5 表配额时自动拆成多张连发（标题 i/N）。",
    store="示例门店（南京新街口店）",
    status="success",
    summary="三平台读取成功",
    score=score_rows(4.72, 4.6),
    sections=[
        {"platform": "ctrip", "status": "success", "rows": CTRIP_ROWS},
        {"platform": "meituan", "status": "success", "rows": MEITUAN_ROWS},
        {"platform": "fliggy", "status": "success", "rows": FLIGGY_ROWS},
    ],
)

SCENARIO_B = dict(
    name="场景 B｜飞猪登录失效（需人工处理，橙色卡）",
    desc="飞猪 platform=human_required：三个分类卡中飞猪区块均如实显示「读取失败（需人工处理）」"
         "与下一步，不伪装为空；携程/美团正常。整体状态=部分成功。",
    store="示例门店（南京新街口店）",
    status="partial",
    summary="携程、美团读取成功；飞猪需人工处理",
    score=score_rows(None, 4.6),
    sections=[
        {"platform": "ctrip", "status": "success",
         "rows": [r for r in CTRIP_ROWS if r["dataset_id"] != "promotion_registered"]},
        {"platform": "meituan", "status": "success", "rows": MEITUAN_ROWS[:3]},
        {"platform": "fliggy", "status": "human_required", "rows": [],
         "summary": "登录失效，需扫码重新登录"},
    ],
)


# ------------------------------------------------ 场景 D：真实活动促销数据
# 2026-09-02 CDP 实测：携程促销中心「已参加促销」9 项（18 行），
# 飞猪营销助手「已参加活动」3 项；美团「活动报名」1 项。
# 2026-09-02 实测：促销中心已参加促销（活动名 → 房型短名：优惠规则）
_CTRIP_PROMO_ACTIVITIES = [
    ("提前预订", "预家庭双卧套房：提前3天预订 立减 20%"),
    ("限时抢购", "预豪华套房：每天 07:00:00 至 22:00:00 立减 15%"),
    ("今夜甩卖", "每天 16:00:00 至 21:00:00 立减 20%"),
    ("万马聚财红包", "所有预付房型：9折"),
    ("天天特价", "所有预付房型：立减 1%"),
    ("连住优惠", "预豪华套房：连住2天 立减2%；连住3天 立减3%"),
    ("学生专享", "预豪华套房：立减 5%"),
    ("出行特惠", "所有预付房型：立减 3%"),
    ("多间立减", "所有预付房型：多间立减 3%"),
]


def _ctrip_promo_rows():
    rows = []
    for index, (name, content) in enumerate(_CTRIP_PROMO_ACTIVITIES):
        rows.append(row("ctrip", "promotion_registered", name, content,
                        freshness="current_state", value_type="text", dimension="活动"))
        rows[-1]["field_key"] = f"activity:{index}:content"
    return rows


# 2026-09-02 实测：飞猪营销助手-已参加活动（活动名 → 描述）
_PROMO_FLIGGY_ACTIVITIES = [
    ("全人群运营活动", "面向全人群及全场景，灵活选择房型及日期，快速管理酒店价格"),
    ("分场景运营活动", "面向不同场景，获得更精准的预订机会"),
    ("分人群运营活动", "面向分人群投放，提升人群转化"),
]


SCENARIO_D = dict(
    name="场景 D｜活动促销卡（2026-09-02 三平台真实数据，容量实测）",
    desc="2026-09-02 需求改版：活动一行一个（活动名→活动内容：携程=房型短名：优惠规则，"
         "美团/飞猪=活动描述），不再有 已报名/有效期 行。携程 9 项 + 美团 1 项 + 飞猪 3 项"
         "：3 表 ≤ 单卡 5 表上限；活动更多时由分页机制拆成多张连发。",
    store="示例门店（南京新街口店）",
    status="success",
    summary="活动页实测",
    score=score_rows(None, None),
    sections=[
        {"platform": "ctrip", "status": "success", "rows": _ctrip_promo_rows()},
        {"platform": "meituan", "status": "success", "rows": [
            row("meituan", "promotion_coupon", "【平台主推】踏春赏花特惠",
                "春季专享特惠活动，精准引流",
                freshness="current_state", value_type="text", dimension="活动"),
        ]},
        {"platform": "fliggy", "status": "success", "rows": [
            row("fliggy", "promotion_agent", name, description,
                freshness="current_state", value_type="text", dimension="活动")
            for name, description in _PROMO_FLIGGY_ACTIVITIES
        ]},
    ],
)


def _first_pull_rows(rows):
    """早上首次拉取：无上一条成功快照，环比列一律显示「凌晨数据」不输出数值。"""
    out = []
    for r in rows:
        copy = dict(r)
        if copy.get("comparison"):
            copy["comparison"] = "凌晨数据"
        out.append(copy)
    return out


SCENARIO_C = dict(
    name="场景 C｜每天早上第一次拉取（环比列显示「凌晨数据」）",
    desc="基线=本次读取之前最近一条成功快照（自动推送/定时看板/人工刷新都算）；"
         "当天首次拉取无历史可对比时，携程/飞猪收益概览与美团收益概览/流量曝光的环比列"
         "显示「凌晨数据」，不输出数值。",
    store="示例门店（南京新街口店）",
    status="success",
    summary="三平台读取成功（当日首次）",
    score=score_rows(None, None),
    sections=[
        {"platform": "ctrip", "status": "success", "rows": _first_pull_rows(CTRIP_ROWS)},
        {"platform": "meituan", "status": "success", "rows": _first_pull_rows(MEITUAN_ROWS)},
        {"platform": "fliggy", "status": "success", "rows": _first_pull_rows(FLIGGY_ROWS)},
    ],
)

_TEMPLATE_COLORS = {
    "blue": ("#3370FF", "#6C9CFF"),
    "green": ("#29A257", "#54C084"),
    "orange": ("#E67E22", "#F0A35E"),
    "red": ("#E5484D", "#F0777B"),
}
_CATEGORY_ORDER = ("business", "promotion", "advertising")
_CATEGORY_LABELS = {"business": "经营数据", "advertising": "广告数据",
                    "promotion": "活动数据"}


def _md(text):
    t = re.sub(r"\\([*_`~\[\]])", r"\1", str(text or ""))
    t = html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return t.replace("\n", "<br>")


def _element_html(el):
    tag = el.get("tag")
    if tag == "div":
        return f'<div class="el">{_md(el.get("text", {}).get("content"))}</div>'
    if tag == "hr":
        return '<div class="hr"></div>'
    if tag == "table":
        columns = el.get("columns", [])
        head = "".join(
            f'<th style="width:{col.get("width", "160px")}">{html.escape(str(col.get("display_name", "")))}</th>'
            for col in columns
        )
        body_rows = []
        for r in el.get("rows", []):
            cells = []
            for col in columns:
                cell = r.get(col.get("name"), "")
                cells.append(f"<td>{_md(cell)}</td>")
            body_rows.append(f"<tr>{''.join(cells)}</tr>")
        return (
            '<div class="table-wrap"><table>'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
            "</table></div>"
        )
    return ""


def _card_html(card):
    template = str(card.get("header", {}).get("template", "blue"))
    start, end = _TEMPLATE_COLORS.get(template, _TEMPLATE_COLORS["blue"])
    title = html.escape(str(card["header"]["title"]["content"]))
    elements = "".join(_element_html(el) for el in card.get("elements", []))
    return (
        f'<div class="card"><div class="card-head" style="background:linear-gradient(90deg,{start},{end})">'
        f"<span>{title}</span></div><div class='card-body'>{elements}</div></div>"
    )


def _scenario_html(scenario):
    cards = build_global_push_cards(
        store_name=scenario["store"],
        read_time=READ_TIME,
        overall_status=scenario["status"],
        overall_summary=scenario["summary"],
        score_summary=scenario["score"],
        platform_sections=scenario["sections"],
    )
    blocks = [f'<h2>{html.escape(scenario["name"])}</h2>',
              f'<p class="desc">{html.escape(scenario["desc"])}</p>']
    total = 0
    for category in _CATEGORY_ORDER:
        card_list = cards[category]
        total += len(card_list)
        blocks.append(
            f'<h3>{_CATEGORY_LABELS[category]}（{len(card_list)} 张消息）</h3>'
            f'<div class="cards">{"".join(_card_html(c) for c in card_list)}</div>'
        )
    blocks.append(f"<p class='total'>本场景共 {total} 张消息</p>")
    blocks.append(
        "<details><summary>查看真实卡片 JSON（发送到飞书 webhook 的原文）</summary>"
        f"<pre>{html.escape(json.dumps(cards, ensure_ascii=False, indent=2))}</pre></details>"
    )
    return "\n".join(blocks)


def main():
    body = "\n".join(_scenario_html(s) for s in (SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D))
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EBK 全局推送三分类卡预览（2026-09-01 复审版）</title>
<style>
  body {{ background:#ECEEF1; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         margin:0; padding:24px; color:#1F2329; }}
  h1 {{ font-size:20px; }} h2 {{ font-size:17px; margin:28px 0 4px; }}
  h3 {{ font-size:14px; color:#646A73; margin:18px 0 8px; }}
  .desc {{ color:#646A73; font-size:13px; margin:4px 0 8px; max-width:960px; }}
  .total {{ color:#8F959E; font-size:12px; }}
  .banner {{ background:#FFF7E0; border:1px solid #F0D48A; border-radius:8px;
            padding:10px 14px; font-size:13px; max-width:960px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:14px; align-items:flex-start; }}
  .card {{ width:400px; background:#FFFFFF; border-radius:10px; overflow:hidden;
          box-shadow:0 2px 8px rgba(31,35,41,.08); flex:0 0 auto; }}
  .card-head {{ color:#FFFFFF; font-weight:600; font-size:14px; padding:10px 14px; }}
  .card-body {{ padding:10px 14px 14px; }}
  .el {{ font-size:13px; line-height:1.7; }}
  .hr {{ border-top:1px solid #E5E6EB; margin:8px 0; }}
  .table-wrap {{ overflow-x:auto; margin:4px 0 10px; }}
  table {{ border-collapse:collapse; font-size:12.5px; }}
  th {{ color:#646A73; font-weight:600; text-align:left; background:#F5F6F7;
       border:1px solid #E5E6EB; padding:4px 8px; white-space:nowrap; }}
  td {{ border:1px solid #E5E6EB; padding:4px 8px; vertical-align:top; white-space:nowrap; }}
  details {{ margin:16px 0 32px; }} summary {{ cursor:pointer; color:#3370FF; font-size:13px; }}
  pre {{ background:#FFFFFF; border:1px solid #E5E6EB; border-radius:8px; padding:12px;
        font-size:11px; overflow:auto; max-width:1100px; max-height:420px; }}
</style>
</head>
<body>
<h1>EBK 全局定时推送｜三张分类卡样式预览</h1>
<div class="banner">⚠️ 以下卡片由
<code>backend/app/domain/ebk_global_push/global_push_cards.py</code> 真实构建（含分页连发、
状态占位、评分标题），数据为<b>示例数据</b>仅用于样式确认，非真实经营数据；表格支持横向滑动查看完整内容。</div>
{body}
</body>
</html>
"""
    target = ROOT / "docs" / "preview" / "ebk-global-push-cards-preview-2026-09-01.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    print(f"written: {target}")


if __name__ == "__main__":
    main()
