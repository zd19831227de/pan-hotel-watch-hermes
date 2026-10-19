from __future__ import annotations

import importlib
import unittest
from typing import Any

from fastapi.testclient import TestClient

from backend.tests.domain.test_meituan_ebooking_login import (
    _isolated_browser_state,
    _reload_app,
)


MEITUAN_TRAFFIC_TREND_TEXT = """
流量分析
流量转化漏斗
今日实时
昨日
近7天
近30天
自定义
~
清晰的视频、丰富的房型图片会吸引更多用户浏览
曝光人数
浏览人数
支付订单数
457
68
8
曝光-浏览 转化率
14.88%
浏览-支付 转化率
11.76%
流量类型
整体曝光
2609
非广告曝光
2609
100%
广告曝光
0
0%
流量趋势数据
今日实时
昨日
近7天
近30天
自定义
~
曝光排名落后同行，购买推广通，获得广告流量
曝光人数
1835人
较上周
14.53%
同行排名
6/11
浏览人数
274人
较上周
11.04%
同行排名
3/11
支付订单数
15单
较上周
36.36%
同行排名
4/11
曝光-浏览转化率
14.93%
较上周
0.59%
同行排名
1/11
浏览-支付转化率
5.47%
较上周
1.90%
同行排名
6/11
流失分析
昨日酒店流失情况
流失金额
2.5万元
"""

# 2026-09-01 实测（同行分析·同行榜单，查看完整排名后 DOM 表格）：
# 行 = [排名, 酒店名称, 指标值, 营销动作]，本店=静里，榜内共 11 家（最大名次 11）。
MEITUAN_PEER_BOARD_TABLES = [
    [
        ["排名", "酒店名称", "入住间夜", "营销动作"],
        ["", "云州澜江路市立医院华芙达安可酒店", "", "首住折扣 限时特惠 连住特惠"],
        ["", "泉吉酒店（云州江汉桥高空江景店）", "", "连住7天折上9折双早"],
        ["4", "云州中心广场希尔硕环朋酒店", "", "美团通兑"],
        ["5", "静里全套房酒店（云州澜江路望川路地铁站店）", "", "限时特惠 连住特惠"],
        ["6", "仰望星空酒店（云州澜江路望川路地铁站店）", "", "首住折扣"],
        ["7", "麗枫酒店（云州澜江路云正街店）", "", "锦川连住5天"],
        ["8", "云州中心广场澜江路步行街娅朵见野酒店", "", ""],
        ["9", "栖客堂·江景影院酒店（澜江路云正街店）", "", "马上来财（人群特惠）"],
        ["10", "云州城中大道望川路地铁站娅朵X酒店", "", ""],
        ["11", "云州阳光酒店（望川路凯德广场店）", "", "连住特惠 早订多减"],
    ],
    [
        ["排名", "酒店名称", "房费收入", "营销动作"],
        ["4", "静里全套房酒店（云州澜江路望川路地铁站店）", "", "限时特惠"],
    ],
]


MEITUAN_ACTIVITY_SIGNUP_TEXT = """
活动报名
可报名活动
参与活动获取流量和转化率，提升门店收益！
已报名活动
活动类型：
全部类型
【平台主推】早订多减
提前锁定未来订单，降低空房风险
促转化
优惠适用时间：全年
免费报名
累积51997家参与
【平台主推】踏春赏花特惠
春季专享特惠活动，精准引流
本活动为主题活动，标签会随时令变化而调整，报名后持续生效。
优惠适用时间：全年
报名详情
累积81327家参与
"""


MEITUAN_DATA_CENTER_REALTIME_TEXT = """
门店

静里全套房酒店（云州澜江路望川路地铁站店）

仅看本账号数据
*每日9点更新前日数据。数据仅作经营参考，不作结算依据
同行管理
同行榜单排名
首页
流量分析
基础经营
数据更新时间：2026/09/01 19:06
今日实时
昨日
近7天
近30天
引流价
246.00 元
较上期
6.77%
同行排名
5/11
同行均值
216.55 元
浏览人数
184 人
较上期
24.59%
同行排名
4/11
同行均值
119.64 人
支付转化率
5.43 %
较上期
2705.68%
同行排名
5/11
同行均值
5.00 %
支付订单数
10 单
较上期
11.11%
同行排名
4/11
同行均值
8.36 单
销售额
5437.00 元
较上期
51.66%
同行排名
4/11
同行均值
3176.67 元
入住间夜
- 间夜
较上期
-
同行排名
-
同行均值
0.0 间夜
项目表现
"""


MEITUAN_DATA_CENTER_TEXT = """
门店
静里全套房酒店（云州澜江路望川路地铁站店）
仅看本账号数据
*每日9点更新前日数据。数据仅作经营参考，不作结算依据
同行管理
进阶经营数据
综合数据分析
同行榜单排名
首页
同行分析
交易分析
流量分析
营销热点
人群分析
服务分析
基础经营
今日实时
昨日
近7天
近30天
引流价
228.15 元
较上周
7.39%
同行排名
5/11
同行均值
174.73 元
曝光量
1542 次
较上周
30.73%
同行排名
5/11
同行均值
1875.82 次
浏览人数
270 人
较上周
27.03%
同行排名
3/11
同行均值
170.82 人
支付转化率
2.59 %
较上周
6.60%
同行排名
8/11
同行均值
4.00 %
支付订单数
7 单
较上周
79.41%
同行排名
7/11
同行均值
10.27 单
销售间夜
8 间夜
较上周
84.91%
同行排名
8/11
同行均值
13.55 间夜
销售均价
347.50 元
较上周
4.62%
同行排名
2/11
同行均值
215.41 元
销售额
2780.00 元
较上周
85.60%
同行排名
4/11
同行均值
4253.45 元
入住间夜
16 间夜
较上周
36.00%
同行排名
3/11
同行均值
13.0 间夜
满房率
56.92 %
较上周
184.62%
同行排名
1/11
同行均值
29.00 %
项目表现
获客宝
赚取获客币换浏览，客人多多订单多
查看详情
获得获客币
0个
同行标杆 40
浏览量
0次
同行标杆 234
推广通
买流量，提升酒店曝光
花费
0元
同行标杆 600
门店会员
提高酒店收益，提高客人复购率，降低运营成本
于2025/10/31开通合作
门店会员数
87人
同行标杆 1380
更新于：2026/07/08
HOS分
4.6
同行排名 4/11
信息分
106
同行排名 5/11
评价分
4.8
营销热点提醒
查看更多
倒计时
1
天
第28届房车之家（云州）国际房车露营博览会
07/10 至 07/12
去维护房价、房量和房态
3
中国材料大会
07/12 至 07/15
8
鸿威·世界宠物博览会-云州展
07/17 至 07/19
昨日同行排名
查看同行动态
经营指标\t本店排名\t同行标杆
5
云州澜江路市立医院华芙达安可酒店
麗枫酒店（云州澜江路云正街店）
浏览支付转化率
7
云州中心广场希尔硕环朋酒店
看懂数据
如何使用数据中心
"""


MEITUAN_HOTEL_NAME = "\u9759\u91cc\u5168\u5957\u623f\u9152\u5e97\uff08\u4e91\u5dde\u6f9c\u6c5f\u8def\u4e91\u80dc\u8def\u5730\u94c1\u7ad9\u5e97\uff09"

MEITUAN_WORKBENCH_TEXT = "\n".join(
    [
        "\u95e8\u5e97",
        MEITUAN_HOTEL_NAME,
        "\u5de5\u4f5c\u53f0",
        "\u4eca\u65e5\u5f85\u5904\u7406",
        "\u5f85\u786e\u8ba4\u8ba2\u5355",
        "3",
        "\u5f85\u5165\u4f4f",
        "6",
        "\u6628\u65e5\u7ecf\u8425",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "7\u5355",
        "\u9500\u552e\u989d",
        "2780.00\u5143",
        "\u5165\u4f4f\u95f4\u591c",
        "16\u95f4\u591c",
    ]
)

MEITUAN_TRADE_ANALYSIS_TEXT = "\n".join(
    [
        "\u95e8\u5e97",
        MEITUAN_HOTEL_NAME,
        "\u4ea4\u6613\u5206\u6790",
        "\u7ecf\u8425\u8868\u73b0",
        "\u9500\u552e\u95f4\u591c",
        "\u9500\u552e\u95f4\u591c\u5360\u6bd4",
        "18",
        "\u8f83\u4e0a\u5468",
        "12.50%",
        "\u9500\u552e\u989d",
        "5767.0\u5143",
        "\u8f83\u4e0a\u5468",
        "10.00%",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "14",
        "\u5165\u4f4f\u95f4\u591c",
        "12",
        "\u9500\u552e\u5747\u4ef7",
        "1",
        "320.39\u5143",
        "\u5165\u4f4f\u91d1\u989d",
        "4320.0\u5143",
        "\u5e73\u5747\u623f\u4ef7",
        "360.00\u5143",
        "\u6210\u4ea4\u7387",
        "4.17%",
        "\u652f\u4ed8\u8f6c\u5316\u7387",
        "2.59%",
        "\u6ee1\u623f\u7387",
        "56.92%",
    ]
)

MEITUAN_TRAFFIC_ANALYSIS_TEXT = "\n".join(
    [
        "\u95e8\u5e97",
        MEITUAN_HOTEL_NAME,
        "\u6d41\u91cf\u5206\u6790",
        "\u6d41\u91cf\u8d8b\u52bf",
        "\u66dd\u5149\u91cf",
        "1542",
        "\u6d4f\u89c8\u4eba\u6570",
        "270",
        "\u8bbf\u95ee\u4eba\u6570",
        "88",
        "\u652f\u4ed8\u8f6c\u5316\u7387",
        "2.59%",
        "\u6d41\u5931\u5206\u6790",
        "\u6d41\u5931\u8ba2\u5355\u91cf",
        "3",
        "\u6d41\u5931\u95f4\u591c\u91cf",
        "5",
        "\u6d41\u5931\u8ba2\u5355\u91d1\u989d",
        "228.15\u5143",
    ]
)

# Real traffic tab: chart toggles (占比/数值) sit near 曝光量; funnel cards use hm-title-txt labels.
MEITUAN_TRAFFIC_ANALYSIS_REAL_TEXT = "\n".join(
    [
        "\u95e8\u5e97",
        MEITUAN_HOTEL_NAME,
        "\u6d41\u91cf\u5206\u6790",
        "\u6d41\u91cf\u8d8b\u52bf",
        "\u66dd\u5149\u91cf",
        "\u5360\u6bd4",
        "\u6570\u503c",
        "\u66dd\u5149\u4eba\u6570",
        "1669\u4eba",
        "\u6d4f\u89c8\u4eba\u6570",
        "336\u4eba",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "14\u5355",
        "\u66dd\u5149-\u6d4f\u89c8\u8f6c\u5316\u7387",
        "20.13%",
        "\u6d4f\u89c8-\u652f\u4ed8\u8f6c\u5316\u7387",
        "4.17%",
        "\u6d41\u5931\u5206\u6790",
        "\u6d41\u5931\u8ba2\u5355\u91cf",
        "3",
        "\u6d41\u5931\u95f4\u591c\u91cf",
        "5",
        "\u6d41\u5931\u8ba2\u5355\u91d1\u989d",
        "228.15\u5143",
    ]
)

MEITUAN_SERVICE_ANALYSIS_TEXT = "\n".join(
    [
        "\u95e8\u5e97",
        MEITUAN_HOTEL_NAME,
        "\u670d\u52a1\u5206\u6790",
        "\u670d\u52a1\u5206",
        "4.9",
        "\u8bc4\u4ef7\u5206",
        "4.8",
        "\u4fe1\u606f\u5206",
        "4.8",
        "\u65b0\u589e\u5dee\u8bc4\u6570",
        "2",
        "\u5f85\u56de\u590d\u5dee\u8bc4\u6570",
        "1",
        "\u65b0\u589e\u597d\u8bc4\u6570",
        "18",
        "5\u5206\u949f\u786e\u8ba4\u7387",
        "96%",
        "\u62d2\u5355\u95ee\u9898\u91cf",
        "1",
        "\u9152\u5e97\u6743\u76ca\u62a5\u540d\u91cf",
        "2",
        "\u6743\u76ca\u8ba2\u5355\u91cf",
        "3",
        "\u8fdd\u89c4\u8fdd\u7ea6\u6b21\u6570",
        "0",
        "\u5904\u7f5a\u5b8c\u6210\u6b21\u6570",
        "0",
        "\u63a8\u7ffb\u95ee\u9898\u91cf",
        "0",
        "\u9003\u5355\u95ee\u9898\u91cf",
        "0",
    ]
)

MEITUAN_PROMOTION_COUPON_TEXT = "\n".join(
    [
        "\u795e\u5238\u6d3b\u52a8",
        "\u6d3b\u52a8\u540d\u79f0",
        "\u6691\u671f\u8ba2\u623f\u795e\u5238",
        "\u72b6\u6001",
        "\u62a5\u540d\u4e2d",
        "\u62a5\u540d\u65f6\u95f4",
        "2026/07/01 00:00 - 2026/07/12 23:59",
        "\u6d3b\u52a8\u65f6\u95f4",
        "2026/07/10 - 2026/07/31",
        "\u5238\u7c7b\u578b",
        "\u6ee1200\u51cf20",
        "\u9884\u7b97",
        "1000\u5143",
        "\u6d88\u8017",
        "126\u5143",
        "\u66dd\u5149",
        "1542",
        "\u8ba2\u5355",
        "7",
    ]
)

MEITUAN_WORKBENCH_REAL_TEXT = "\n".join(
    [
        MEITUAN_HOTEL_NAME,
        "\u5f53\u524d\u89d2\u8272\uff1a\u5e97\u957f",
        "\u5b9e\u65f6\u6570\u636e",
        "\u6628\u65e5\u6570\u636e",
        "\u8fdd\u7ea6\u770b\u677f",
        "1",
        "\u6d4f\u89c8\u4eba\u6570",
        "270",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "7",
        "\u9500\u552e\u989d",
        "2780.00",
    ]
)

MEITUAN_PROMOTION_COUPON_REAL_TEXT = "\n".join(
    [
        "\u6d3b\u52a8\u5e7f\u573a\u5df2\u62a5\u540d\u6d3b\u52a8\u6d3b\u52a8\u6570\u636e",
        "\u7f8e\u56e2\u795e\u5238",
        "\u6d3b\u52a8\u4ecb\u7ecd\uff1a\u7f8e\u56e2\u795e\u5238\u62db\u5546\u706b\u70ed\u6765\u88ad",
        "\u62db\u5546\u65f6\u95f4\uff1a\u81ea 2024/04/24 \u5f00\u59cb",
        "\u6d3b\u52a8\u65f6\u95f4\uff1a\u957f\u671f\u5728\u7ebf",
        "\u5e73\u53f0\u8054\u52a8",
        "\u62a5\u540d\u6210\u529f",
        "\u6d3b\u52a8\u8be6\u60c5",
    ]
)

MEITUAN_WORKBENCH_DETAIL_TEXT = "\n".join(
    [
        MEITUAN_HOTEL_NAME,
        "\u5f53\u524d\u89d2\u8272\uff1a\u5e97\u957f",
        "\u5b9e\u65f6\u6570\u636e",
        "\u6d4f\u89c8\u4eba\u6570",
        "1",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "0",
        "\u9500\u552e\u989d",
        "0.00",
        "\u6628\u65e5\u6570\u636e",
        "\u6d4f\u89c8\u4eba\u6570",
        "270",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "7",
        "\u9500\u552e\u989d",
        "2780.00",
        "HOS\u5206",
        "4.56",
        "\u518d\u63a5\u518d\u5389",
        "HOS\u5206\u4e0e\u6628\u65e5\u6301\u5e73\uff01\u7ee7\u7eed\u63d0\u5206\uff0c\u51b2\u523a\u91d1\u51a0\u6743\u76ca\uff0c\u63d0\u8f6c\u5316\u6da8\u8ba2\u5355",
    ]
)

MEITUAN_PROMOTION_COUPON_DETAIL_TEXT = "\n".join(
    [
        "\u6d3b\u52a8\u5e7f\u573a",
        "\u5df2\u62a5\u540d\u6d3b\u52a8",
        "\u6d3b\u52a8\u6570\u636e",
        "\u7f8e\u56e2\u795e\u5238",
        "\u62db\u5546\u65f6\u95f4\uff1a\u81ea 2024/04/24 \u5f00\u59cb",
        "\u6d3b\u52a8\u65f6\u95f4\uff1a\u957f\u671f\u5728\u7ebf",
        "\u5e73\u53f0\u8054\u52a8",
        "\u62a5\u540d\u6210\u529f",
        "\u6d3b\u52a8\u6570\u636e",
        "\u66dd\u5149\u91cf",
        "1542",
        "\u6d4f\u89c8\u4eba\u6570",
        "270",
        "\u652f\u4ed8\u8ba2\u5355\u6570",
        "7",
        "\u9500\u552e\u989d",
        "2780.00",
    ]
)

MEITUAN_PROMOTION_COUPON_REAL_ACTIVITY_DATA_TEXT = "\n".join(
    [
        "\u6d3b\u52a8\u5e7f\u573a\u5df2\u62a5\u540d\u6d3b\u52a8\u6d3b\u52a8\u6570\u636e",
        "\u5238\u7c7b\u578b:",
        "\u5168\u90e8\u5238",
        "\u4eca\u65e5\u6570\u636e",
        "\u66f4\u65b0",
        "\u6570\u636e\u8bf4\u660e",
        "\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d",
        "2,881.09\u5143",
        "\u8f83\u6628\u59291,408.86104%",
        "\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570",
        "4\u4e2a",
        "\u8f83\u6628\u5929520%",
        "\u7528\u795e\u5238\u6838\u9500\u91d1\u989d",
        "1,773.47\u5143",
        "\u8f83\u6628\u59293,441.9148.47%",
        "\u7528\u795e\u5238\u79bb\u5e97\u8ba2\u5355\u6570",
        "8\u4e2a",
        "\u8f83\u6628\u59291233.33%",
        "\u7528\u795e\u5238\u6838\u9500\u5ba2\u5355\u4ef7",
        "221.68\u5143",
        "\u8f83\u6628\u5929286.8322.71%",
        "\u5386\u53f2\u6570\u636e",
        "\u8fd17\u5929",
        "\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d",
        "39,920.28\u5143",
        "\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570",
        "100\u4e2a",
    ]
)

MEITUAN_PROMOTION_COUPON_DATA_LIST_TEXT = "\n".join(
    [
        "\u7f8e\u56e2\u795e\u5238",
        "\u62a5\u540d\u6210\u529f",
        "\u6d3b\u52a8\u6570\u636e",
        "\u4eca\u65e5\u6570\u636e",
        "\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d",
        "1,582.28",
        "\u5143",
        "\u8f83\u6628\u59291,794.63",
        "11.83%",
        "\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570",
        "5",
        "\u4e2a",
        "\u8f83\u6628\u59296",
        "16.67%",
        "\u7528\u795e\u5238\u6838\u9500\u91d1\u989d",
        "2,580.15",
        "\u5143",
        "\u8f83\u6628\u59291,763.18",
        "46%",
        "\u7528\u795e\u5238\u79bb\u5e97\u8ba2\u5355\u6570",
        "10",
        "\u4e2a",
        "\u8f83\u6628\u59294",
        "150%",
        "\u7528\u795e\u5238\u6838\u9500\u5ba2\u5355\u4ef7",
        "258.02",
        "\u5143",
        "\u8f83\u6628\u5929440.80",
        "41.47%",
        "\u5386\u53f2\u6570\u636e",
    ]
)

MEITUAN_COMMENT_MANAGE_TEXT = "\n".join(
    [
        "\u8bc4\u4ef7\u7ba1\u7406",
        MEITUAN_HOTEL_NAME,
        "\u8bc4\u4ef7\u5f97\u5206",
        "4.8",
        "\u597d\u8bc4\u7387",
        "96%",
        "\u5f85\u56de\u590d\u8bc4\u4ef7",
        "2",
        "\u5dee\u8bc4",
        "1",
        "\u8fd130\u5929\u65b0\u589e\u8bc4\u4ef7",
        "18",
    ]
)

MEITUAN_COMMENT_MANAGE_REAL_TEXT = "\n".join(
    [
        "\u8bc4\u4ef7\u7ba1\u7406",
        "\u4e91\u5dde\u4e91\u79be\u9152\u5e97\u7ba1\u7406\u6709\u9650\u516c\u53f8(JL1700000001)",
        "\u5171\u8ba1513\u6761\u8bc4\u4ef7",
        "\u7f8e\u56e2\u8bc4\u4ef7",
        "\u70b9\u8bc4\u8bc4\u4ef7",
        "\u8bc4\u4ef7\u5f97\u5206",
        "\u8bc4\u5206\u89c4\u5219\u89e3\u8bfb",
        "\u603b\u5f97\u5206",
        "4.8",
        "\u670d\u52a1",
        "4.8",
        "\u4f4d\u7f6e",
        "4.8",
        "\u8bbe\u65bd",
        "4.8",
        "\u536b\u751f",
        "4.8",
        "\u8bc4\u4ef7\u60c5\u51b5",
        "30\u5929",
        "\u65b0\u589e\u6709\u6548\u8bc4\u4ef7\u6570",
        "33\u6761",
        "\u8bc4\u4ef7\u6570\u8868\u73b0",
        "\u9886\u5148\u5168\u57ce80%\u5546\u5bb6",
        "\u5dee\u8bc4\u7387",
        "6.1%",
        "\u5dee\u8bc4\u7387\u8868\u73b0",
        "\u4e2d\u540e",
        "180\u5929",
        "\u65b0\u589e\u6709\u6548\u8bc4\u4ef7\u6570",
        "122\u6761",
        "\u8bc4\u4ef7\u6570\u8868\u73b0",
        "\u9886\u5148\u5168\u57ce80%\u5546\u5bb6",
        "\u5dee\u8bc4\u7387",
        "3.3%",
        "\u5dee\u8bc4\u7387\u8868\u73b0",
        "\u4e2d\u7b49",
        "365\u5929",
        "\u65b0\u589e\u6709\u6548\u8bc4\u4ef7\u6570",
        "154\u6761",
        "\u8bc4\u4ef7\u6570\u8868\u73b0",
        "\u9886\u5148\u5168\u57ce80%\u5546\u5bb6",
        "\u5dee\u8bc4\u7387",
        "3.9%",
        "\u5dee\u8bc4\u7387\u8868\u73b0",
        "\u4e2d\u7b49",
        "\u5168\u90e8\u8bc4\u4ef7(513)",
        "\u6709\u56fe\u8bc4\u4ef7(278)",
        "\u5dee\u8bc4(9)",
        "\u672a\u56de\u590d(46)",
        "\u5df2\u6295\u8bc9(3)",
    ]
)


async def _noop_observe_meituan_read_payload(target, payload, *, webhook_url=None):
    """V7 Step 1 采集后校验层的测试替身：不写库、不推送。"""
    return None


class MeituanDataCenterParserTests(unittest.TestCase):
    def test_parser_extracts_real_data_center_key_metrics(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_data_center_text(
            MEITUAN_DATA_CENTER_TEXT,
            current_url="https://eb.meituan.com/newhb-sub-app/data-center-pc/home/index.html#/index",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["platform"], "meituan")
        self.assertEqual(payload["dataset"], "data_center")
        data = payload["data"]
        self.assertEqual(data["hotel_name"], "静里全套房酒店（云州澜江路望川路地铁站店）")
        self.assertEqual(data["scope"], "仅看本账号数据")
        self.assertIn("每日9点更新前日数据", data["update_note"])
        self.assertEqual(data["period_tabs"], ["今日实时", "昨日", "近7天", "近30天"])

        metrics = data["basic_operation_metrics"]
        self.assertEqual(metrics["引流价"]["value"], "228.15 元")
        self.assertEqual(metrics["引流价"]["week_over_week"], "7.39%")
        self.assertEqual(metrics["引流价"]["peer_rank"], "5/11")
        self.assertEqual(metrics["引流价"]["peer_average"], "174.73 元")
        self.assertEqual(metrics["曝光量"]["value"], "1542 次")
        self.assertEqual(metrics["浏览人数"]["peer_rank"], "3/11")
        self.assertEqual(metrics["支付转化率"]["peer_average"], "4.00 %")
        self.assertEqual(metrics["销售额"]["value"], "2780.00 元")
        self.assertEqual(metrics["满房率"]["peer_average"], "29.00 %")

        project_items = data["project_performance"]
        self.assertEqual(project_items["获客宝"]["获得获客币"]["value"], "0个")
        self.assertEqual(project_items["推广通"]["花费"]["value"], "0元")
        self.assertEqual(project_items["门店会员"]["门店会员数"]["benchmark"], "同行标杆 1380")
        self.assertEqual(data["marketing_hotspots"][0]["title"], "第28届房车之家（云州）国际房车露营博览会")
        self.assertEqual(data["marketing_hotspots"][0]["date_range"], "07/10 至 07/12")
        self.assertEqual(data["peer_rankings"][0]["rank"], "5")
        self.assertEqual(data["peer_rankings"][0]["hotel"], "云州澜江路市立医院华芙达安可酒店")

        evidence = payload["evidence"]
        self.assertFalse(evidence["profile_path_returned"])
        self.assertFalse(evidence["password_returned"])
        self.assertEqual(evidence["parser"], "meituan_data_center_dom_v1")

    def test_parser_returns_needs_review_for_unrecognized_page(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_data_center_text("工作台 暂无数据", current_url="about:blank")

        self.assertEqual(payload["status"], "needs_review")
        self.assertEqual(payload["data"]["basic_operation_metrics"], {})
        self.assertIn("页面结构", payload["summary"])

    def test_signup_parser_captures_activity_description(self) -> None:
        """2026-09-02 需求：活动报名页名称下的描述行（如「春季专享特惠活动，精准引流」）
        作为活动内容进卡；促转化/累积等标签行不算描述。"""
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan._parse_meituan_activity_signup_text(
            MEITUAN_ACTIVITY_SIGNUP_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/promotion/activity#/index",
        )
        activities = payload["data"]["signup_activities"]
        by_name = {a["name"]: a for a in activities}
        self.assertIn("【平台主推】踏春赏花特惠", by_name)
        self.assertEqual(
            by_name["【平台主推】踏春赏花特惠"]["description"],
            "春季专享特惠活动，精准引流",
        )
        self.assertEqual(
            by_name["【平台主推】早订多减"]["description"],
            "提前锁定未来订单，降低空房风险",
        )

    def test_parser_supports_jiaoshangqi_marker_on_realtime_tab(self) -> None:
        """2026-09-01 实测：「今日实时」页签对比标记是「较上期」（昨日页签才是「较上周」），
        解析需两者兼容；缺失指标的「-」占位不得当作排名/幅度。"""
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_data_center_text(
            MEITUAN_DATA_CENTER_REALTIME_TEXT,
            current_url="https://eb.meituan.com/newhb-sub-app/data-center-pc/home/index.html#/index",
        )

        self.assertEqual(payload["status"], "success")
        metrics = payload["data"]["basic_operation_metrics"]
        self.assertEqual(metrics["销售额"]["value"], "5437.00 元")
        self.assertEqual(metrics["销售额"]["week_over_week"], "51.66%")
        self.assertEqual(metrics["销售额"]["peer_rank"], "4/11")
        self.assertEqual(metrics["浏览人数"]["week_over_week"], "24.59%")
        self.assertEqual(metrics["支付转化率"]["peer_rank"], "5/11")
        self.assertEqual(metrics["入住间夜"]["value"], "- 间夜")
        self.assertIsNone(metrics["入住间夜"]["week_over_week"])
        self.assertIsNone(metrics["入住间夜"]["peer_rank"])

    def test_traffic_tab_parses_trend_section_with_rank_and_wow(self) -> None:
        """2026-09-01 实测：流量分析·流量趋势数据（今日实时）每项带较上周+同行排名 N/11，
        含曝光-浏览/浏览-支付转化率；漏斗区(457/68/8)不得串位。"""
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_dataset_text(
            MEITUAN_TRAFFIC_TREND_TEXT,
            current_url="https://eb.meituan.com/newhb-sub-app/data-center-pc/home/index.html#/index",
            dataset="data_center_traffic",
        )

        self.assertEqual(payload["status"], "success")
        basic = payload["data"]["basic_operation_metrics"]
        self.assertEqual(basic["曝光人数"]["value"], "1835人")
        self.assertEqual(basic["曝光人数"]["peer_rank"], "6/11")
        self.assertEqual(basic["浏览人数"]["peer_rank"], "3/11")
        self.assertEqual(basic["曝光-浏览转化率"]["value"], "14.93%")
        self.assertEqual(basic["曝光-浏览转化率"]["peer_rank"], "1/11")
        self.assertEqual(basic["浏览-支付转化率"]["value"], "5.47%")
        self.assertEqual(basic["浏览-支付转化率"]["week_over_week"], "1.90%")

    def test_peer_ranking_parser_reads_both_boards_per_tab(self) -> None:
        """2026-09-02 实测：每个榜签下两张子榜（表头表+数据表成对 DOM），
        如 入住榜 = 入住间夜榜 + 房费收入榜；逐子榜解析本店名次/总数。"""
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        own = "静里全套房酒店（云州澜江路望川路地铁站店）"
        tables = [
            {"header": ["排名", "酒店名称", "入住间夜", "营销动作"], "rows": []},
            {
                "rows": [
                    ["", "云州澜江路市立医院华芙达安可酒店", "", "首住折扣"],
                    ["", "泉吉酒店（云州江汉桥高空江景店）", "", "连住特惠"],
                    ["4", "云州中心广场希尔硕环朋酒店", "", "美团通兑"],
                    ["5", own, "", "限时特惠"],
                    ["6", "仰望星空酒店（云州澜江路望川路地铁站店）", "", "首住折扣"],
                    ["7", "麗枫酒店（云州澜江路云正街店）", "", "锦川连住"],
                    ["8", "云州中心广场澜江路步行街娅朵见野酒店", "", ""],
                    ["9", "栖客堂·江景影院酒店（澜江路云正街店）", "", "马上来财"],
                    ["10", "云州城中大道望川路地铁站娅朵X酒店", "", ""],
                    ["11", "云州阳光酒店（望川路凯德广场店）", "", "连住特惠"],
                ]
            },
            {"header": ["排名", "酒店名称", "房费收入", "营销动作"], "rows": []},
            {
                "rows": [
                    ["", "云州澜江路市立医院华芙达安可酒店", "", "首住折扣"],
                    ["", "泉吉酒店（云州江汉桥高空江景店）", "", "连住特惠"],
                    ["", "昊颐酒店（云州澜江路恒隆广场店）", "", "首住折扣"],
                    ["4", own, "", "限时特惠"],
                    ["5", "仰望星空酒店（云州澜江路望川路地铁站店）", "", "首住折扣"],
                    ["6", "麗枫酒店（云州澜江路云正街店）", "", "锦川连住"],
                    ["7", "云州中心广场澜江路步行街娅朵见野酒店", "", ""],
                    ["8", "栖客堂·江景影院酒店（澜江路云正街店）", "", "马上来财"],
                    ["9", "云州城中大道望川路地铁站娅朵X酒店", "", ""],
                    ["10", "云州阳光酒店（望川路凯德广场店）", "", "连住特惠"],
                    ["11", "云州中心广场希尔硕环朋酒店", "", "美团通兑"],
                ]
            },
        ]
        boards = meituan._parse_meituan_peer_boards(tables, own)
        self.assertEqual(len(boards), 2)
        self.assertEqual(boards[0]["metric"], "入住间夜")
        self.assertEqual(boards[0]["rank_text"], "5/11")
        self.assertEqual(boards[1]["metric"], "房费收入")
        self.assertEqual(boards[1]["rank_text"], "4/11")

    def test_peer_ranking_parser_own_first_when_rank_blank(self) -> None:
        """子榜本店在首位且名次列为空（前三名图标渲染）：按酒店行序兜底第 1 名。"""
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        own = "静里全套房酒店（云州澜江路望川路地铁站店）"
        tables = [
            {"header": ["排名", "酒店名称", "支付转化率", "营销动作"], "rows": []},
            {
                "rows": [
                    ["", own, "", "首住折扣"],
                    ["", "酒店A", "", ""],
                    ["", "酒店B", "", ""],
                ]
            },
        ]
        boards = meituan._parse_meituan_peer_boards(tables, own)
        self.assertEqual(boards[0]["metric"], "支付转化率")
        self.assertEqual(boards[0]["rank"], "1")
        self.assertEqual(boards[0]["rank_text"], "1")

    def test_data_center_tab_parser_extracts_service_metrics(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_data_center_tab_text(
            MEITUAN_SERVICE_ANALYSIS_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fdata-center%2Findex.html",
            dataset="data_center_service",
            tab_label="\u670d\u52a1\u5206\u6790",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "data_center_service")
        data = payload["data"]
        self.assertEqual(data["tab_label"], "\u670d\u52a1\u5206\u6790")
        self.assertEqual(data["service_metrics"]["\u670d\u52a1\u5206"], "4.9")
        self.assertEqual(data["service_metrics"]["5\u5206\u949f\u786e\u8ba4\u7387"], "96%")
        self.assertEqual(data["service_metrics"]["\u62d2\u5355\u95ee\u9898\u91cf"], "1")
        self.assertEqual(data["service_metrics"]["\u8fdd\u89c4\u8fdd\u7ea6\u6b21\u6570"], "0")
        self.assertEqual(data["service_metrics"]["\u65b0\u589e\u5dee\u8bc4\u6570"], "2")
        self.assertEqual(data["service_metrics"]["\u5f85\u56de\u590d\u5dee\u8bc4\u6570"], "1")
        self.assertEqual(data["service_metrics"]["\u65b0\u589e\u597d\u8bc4\u6570"], "18")
        self.assertEqual(data["metrics"]["\u65b0\u589e\u5dee\u8bc4\u6570"], "2")
        self.assertEqual(payload["evidence"]["parser"], "meituan_data_center_tab_dom_v1")

    def test_promotion_coupon_parser_extracts_coupon_activity_rows(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_promotion_coupon_text(
            MEITUAN_PROMOTION_COUPON_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=marketplatform&from=EBOOKING",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "promotion_coupon")
        activity = payload["data"]["coupon_activities"][0]
        self.assertEqual(activity["activity_name"], "\u6691\u671f\u8ba2\u623f\u795e\u5238")
        self.assertEqual(activity["status"], "\u62a5\u540d\u4e2d")
        self.assertEqual(activity["registration_time"], "2026/07/01 00:00 - 2026/07/12 23:59")
        self.assertEqual(activity["coupon_type"], "\u6ee1200\u51cf20")
        self.assertEqual(activity["budget"], "1000\u5143")
        self.assertEqual(activity["orders"], "7")
        self.assertEqual(payload["evidence"]["parser"], "meituan_promotion_coupon_dom_v1")

    def test_workbench_parser_accepts_real_home_without_store_label(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_workbench_text(
            MEITUAN_WORKBENCH_REAL_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["hotel_name"], MEITUAN_HOTEL_NAME)
        self.assertEqual(payload["data"]["business_summary"]["\u6d4f\u89c8\u4eba\u6570"], "270")
        self.assertEqual(payload["data"]["business_summary"]["\u9500\u552e\u989d"], "2780.00")

    def test_workbench_parser_flags_first_visit_role_selection_screen(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        role_screen_text = (
            "请选择你的角色\n"
            "角色用于生成工作台，请根据您的工作岗位进行选择\n"
            "前台\n"
            "店长\n"
            "老板\n"
            "其他\n"
            "我选好了\n"
        )

        payload = meituan.parse_meituan_workbench_text(
            role_screen_text,
            current_url="https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F",
        )

        self.assertEqual(payload["status"], "needs_review")
        self.assertIn("角色", payload["summary"])
        self.assertIn("操作台", payload["summary"])

    def test_workbench_parser_extracts_realtime_yesterday_and_hos_score(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_workbench_text(
            MEITUAN_WORKBENCH_DETAIL_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F",
        )

        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["realtime_data"]["\u6d4f\u89c8\u4eba\u6570"], "1")
        self.assertEqual(data["realtime_data"]["\u652f\u4ed8\u8ba2\u5355\u6570"], "0")
        self.assertEqual(data["yesterday_data"]["\u6d4f\u89c8\u4eba\u6570"], "270")
        self.assertEqual(data["yesterday_data"]["\u652f\u4ed8\u8ba2\u5355\u6570"], "7")
        self.assertEqual(data["hos_score"]["value"], "4.56")
        self.assertEqual(data["hos_score"]["grade_tip"], "\u518d\u63a5\u518d\u5389")
        self.assertIn("HOS\u5206\u4e0e\u6628\u65e5\u6301\u5e73", data["hos_score"]["message"])

    def test_promotion_coupon_parser_accepts_real_meituan_coupon_copy(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_promotion_coupon_text(
            MEITUAN_PROMOTION_COUPON_REAL_TEXT,
            current_url="https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html",
        )

        self.assertEqual(payload["status"], "success")
        activity = payload["data"]["coupon_activities"][0]
        self.assertEqual(activity["activity_name"], "\u7f8e\u56e2\u795e\u5238")
        self.assertEqual(activity["registration_time"], "\u81ea 2024/04/24 \u5f00\u59cb")
        self.assertEqual(activity["activity_time"], "\u957f\u671f\u5728\u7ebf")
        self.assertEqual(activity["status"], "\u62a5\u540d\u6210\u529f")
        self.assertEqual(activity["channel"], "\u5e73\u53f0\u8054\u52a8")

    def test_promotion_coupon_parser_extracts_activity_data_metrics(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_promotion_coupon_text(
            MEITUAN_PROMOTION_COUPON_DETAIL_TEXT,
            current_url="https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html",
        )

        self.assertEqual(payload["status"], "success")
        activity = payload["data"]["coupon_activities"][0]
        self.assertEqual(activity["activity_name"], "\u7f8e\u56e2\u795e\u5238")
        self.assertEqual(activity["status"], "\u62a5\u540d\u6210\u529f")
        self.assertEqual(activity["activity_metrics"]["\u66dd\u5149\u91cf"], "1542")
        self.assertEqual(activity["activity_metrics"]["\u652f\u4ed8\u8ba2\u5355\u6570"], "7")
        self.assertEqual(activity["activity_metrics"]["\u9500\u552e\u989d"], "2780.00")

    def test_promotion_coupon_parser_extracts_real_activity_data_tab_today_metrics(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_promotion_coupon_text(
            MEITUAN_PROMOTION_COUPON_REAL_ACTIVITY_DATA_TEXT,
            current_url="https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html?tabType=data",
        )

        self.assertEqual(payload["status"], "success")
        activity = payload["data"]["coupon_activities"][0]
        metrics = activity["activity_metrics"]
        self.assertEqual(metrics["\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d"], "2,881.09\u5143")
        self.assertEqual(metrics["\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570"], "4\u4e2a")
        self.assertEqual(metrics["\u7528\u795e\u5238\u6838\u9500\u91d1\u989d"], "1,773.47\u5143")
        self.assertNotEqual(metrics["\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d"], "39,920.28\u5143")

    def test_promotion_coupon_parser_extracts_data_list_activity_metrics_with_units(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_promotion_coupon_text(
            MEITUAN_PROMOTION_COUPON_DATA_LIST_TEXT,
            current_url="https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html?tabType=data",
        )

        self.assertEqual(payload["status"], "success")
        activity = payload["data"]["coupon_activities"][0]
        self.assertEqual(activity["activity_name"], "\u7f8e\u56e2\u795e\u5238")
        self.assertEqual(activity["status"], "\u62a5\u540d\u6210\u529f")
        metrics = activity["activity_metrics"]
        self.assertEqual(metrics["\u7528\u795e\u5238\u652f\u4ed8\u91d1\u989d"], "1,582.28\u5143")
        self.assertEqual(metrics["\u7528\u795e\u5238\u652f\u4ed8\u8ba2\u5355\u6570"], "5\u4e2a")
        self.assertEqual(metrics["\u7528\u795e\u5238\u6838\u9500\u91d1\u989d"], "2,580.15\u5143")
        self.assertEqual(metrics["\u7528\u795e\u5238\u79bb\u5e97\u8ba2\u5355\u6570"], "10\u4e2a")
        self.assertEqual(metrics["\u7528\u795e\u5238\u6838\u9500\u5ba2\u5355\u4ef7"], "258.02\u5143")

    def test_comment_manage_parser_extracts_score_and_review_status(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_comment_manage_text(
            MEITUAN_COMMENT_MANAGE_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/comment-manage-react#/home",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dataset"], "comment_manage_home")
        data = payload["data"]
        self.assertEqual(data["hotel_name"], MEITUAN_HOTEL_NAME)
        self.assertEqual(data["review_score"], "4.8")
        self.assertEqual(data["review_status"]["\u597d\u8bc4\u7387"], "96%")
        self.assertEqual(data["review_status"]["\u5f85\u56de\u590d\u8bc4\u4ef7"], "2")

    def test_comment_manage_parser_extracts_real_review_overview(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        payload = meituan.parse_meituan_comment_manage_text(
            MEITUAN_COMMENT_MANAGE_REAL_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/comment-manage-react#/home",
        )

        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertEqual(data["review_score"], "4.8")
        self.assertEqual(data["review_total"], "513")
        self.assertEqual(data["review_dimensions"]["\u670d\u52a1"], "4.8")
        self.assertEqual(data["review_dimensions"]["\u536b\u751f"], "4.8")
        self.assertEqual(data["review_periods"]["30\u5929"]["\u65b0\u589e\u6709\u6548\u8bc4\u4ef7\u6570"], "33\u6761")
        self.assertEqual(data["review_periods"]["30\u5929"]["\u5dee\u8bc4\u7387"], "6.1%")
        self.assertEqual(data["review_periods"]["180\u5929"]["\u5dee\u8bc4\u7387\u8868\u73b0"], "\u4e2d\u7b49")
        self.assertEqual(data["review_filters"]["\u5168\u90e8\u8bc4\u4ef7"], "513")
        self.assertEqual(data["review_filters"]["\u672a\u56de\u590d"], "46")

    def test_workbench_parser_extracts_active_realtime_metrics_after_contract_panel(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        text = "\n".join(
            [
                MEITUAN_HOTEL_NAME,
                "\u5b9e\u65f6\u6570\u636e",
                "\u6628\u65e5\u6570\u636e",
                "\u8fdd\u7ea6\u770b\u677f",
                "1",
                "\u6d4f\u89c8\u4eba\u6570",
                "67\u4eba",
                "\u540c\u884c\u6392\u540d 2/11",
                "\u9500\u552e\u989d",
                "2472\u5143",
                "HOS\u5206\u63d0HOS\u5206\uff0c\u6da8\u8ba2\u5355",
                "4.56",
                "\u518d\u63a5\u518d\u5389",
            ]
        )

        payload = meituan.parse_meituan_workbench_text(text, current_url="about:blank")

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["realtime_data"]["\u6d4f\u89c8\u4eba\u6570"], "67\u4eba")
        self.assertEqual(payload["data"]["realtime_data"]["\u9500\u552e\u989d"], "2472\u5143")

    def test_workbench_parser_extracts_occupancy_and_lost_orders(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        text = "\n".join(
            [
                MEITUAN_HOTEL_NAME,
                "实时数据",
                "浏览人数",
                "123人",
                "销售额",
                "1847元",
                "入住间夜",
                "7间夜",
                "满房率",
                "56.92%",
                "流失订单量",
                "3",
                "昨日数据",
                "浏览人数",
                "336人",
                "销售额",
                "5767元",
                "入住间夜",
                "12间夜",
                "满房率",
                "48.00%",
                "流失订单量",
                "5",
                "HOS分",
                "4.50",
                "再接再厉",
            ]
        )

        payload = meituan.parse_meituan_workbench_text(text, current_url="about:blank")

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["realtime_data"]["满房率"], "56.92%")
        self.assertEqual(payload["data"]["realtime_data"]["流失订单量"], "3")
        self.assertEqual(payload["data"]["yesterday_data"]["满房率"], "48.00%")
        self.assertEqual(payload["data"]["yesterday_data"]["流失订单量"], "5")

    def test_comment_manage_parser_skips_non_numeric_score_explanation(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        text = "\n".join(
            [
                "\u8bc4\u4ef7\u7ba1\u7406",
                MEITUAN_HOTEL_NAME,
                "\u8bc4\u4ef7\u5f97\u5206",
                "\u8bc4\u5206\u89c4\u5219\u89e3\u8bfb",
                "4.8",
                "\u597d\u8bc4\u7387",
                "96%",
            ]
        )

        payload = meituan.parse_meituan_comment_manage_text(text, current_url="about:blank")

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["review_score"], "4.8")


class MeituanDataCenterApiTests(unittest.TestCase):
    def test_read_data_center_uses_meituan_session_and_returns_safe_payload(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_dc",
                    "display_name": "Meituan Data Center",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9521,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan_api = importlib.import_module("app.api.v1.meituan_ebooking")
            fake = _FakeMeituanDataCenterService()
            original = meituan_api.get_meituan_ebooking_service
            meituan_api.get_meituan_ebooking_service = lambda: fake
            original_observe = meituan_api.observe_meituan_read_payload
            meituan_api.observe_meituan_read_payload = _noop_observe_meituan_read_payload
            try:
                response = client.post(
                    "/api/v1/meituan-ebooking/sessions/meituan_dc/read-data-center",
                    json={"browser_session_id": created.json()["browser_session_id"]},
                )
            finally:
                meituan_api.get_meituan_ebooking_service = original
                meituan_api.observe_meituan_read_payload = original_observe

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["dataset"], "data_center")
            self.assertEqual(payload["platform"], "meituan")
            self.assertEqual(payload["data"]["hotel_name"], "静里全套房酒店")
            self.assertFalse(payload["evidence"]["profile_path_returned"])
            self.assertFalse(payload["evidence"]["password_returned"])
            self.assertNotIn("persistent_profile_dir", payload["evidence"])
            self.assertEqual(
                fake.calls,
                [
                    {
                        "platform_account_id": "meituan_dc",
                        "browser_session_id": created.json()["browser_session_id"],
                        "actor_id": "operator",
                        "headless": False,
                    }
                ],
            )

    def test_read_data_center_rejects_ctrip_session(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_dc",
                    "display_name": "Ctrip Data Center",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "session_port": 9522,
                },
            )

            response = client.post(
                "/api/v1/meituan-ebooking/sessions/ctrip_dc/read-data-center",
                json={},
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("Meituan", response.json()["detail"])


    def test_read_dataset_uses_requested_meituan_dataset(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_coupon",
                    "display_name": "Meituan Coupon",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9523,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan_api = importlib.import_module("app.api.v1.meituan_ebooking")
            fake = _FakeMeituanDatasetService()
            original = meituan_api.get_meituan_ebooking_service
            meituan_api.get_meituan_ebooking_service = lambda: fake
            original_observe = meituan_api.observe_meituan_read_payload
            meituan_api.observe_meituan_read_payload = _noop_observe_meituan_read_payload
            try:
                response = client.post(
                    "/api/v1/meituan-ebooking/sessions/meituan_coupon/read-dataset",
                    json={
                        "browser_session_id": created.json()["browser_session_id"],
                        "dataset": "promotion_coupon",
                    },
                )
            finally:
                meituan_api.get_meituan_ebooking_service = original
                meituan_api.observe_meituan_read_payload = original_observe

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["dataset"], "promotion_coupon")
            self.assertEqual(
                fake.calls,
                [
                    {
                        "platform_account_id": "meituan_coupon",
                        "browser_session_id": created.json()["browser_session_id"],
                        "actor_id": "operator",
                        "headless": False,
                        "dataset": "promotion_coupon",
                    }
                ],
            )

    def test_read_dataset_rejects_ctrip_session(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_coupon",
                    "display_name": "Ctrip Coupon",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "session_port": 9524,
                },
            )

            response = client.post(
                "/api/v1/meituan-ebooking/sessions/ctrip_coupon/read-dataset",
                json={"dataset": "promotion_coupon"},
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("Meituan", response.json()["detail"])

    def test_read_dataset_returns_human_required_when_meituan_navigation_closes(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_closed",
                    "display_name": "Meituan Closed",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9525,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
            service = meituan.MeituanEbookingBrowserService()
            page = MagicMock()
            page.url = "about:blank"
            page.goto = AsyncMock(
                side_effect=RuntimeError(
                    "Page.goto: net::ERR_CONNECTION_CLOSED at https://me.meituan.com/ebooking/merchant/ebIframe"
                )
            )
            service._get_or_create_page = AsyncMock(return_value=page)

            async def _run() -> dict[str, Any]:
                return await service.read_dataset(
                    platform_account_id="meituan_closed",
                    browser_session_id=created.json()["browser_session_id"],
                    actor_id="operator",
                    headless=False,
                    dataset="workbench_home",
                )

            payload = asyncio.run(_run())

            self.assertEqual(payload["status"], "human_required")
            self.assertEqual(payload["dataset"], "workbench_home")
            self.assertTrue(payload["manual_login_required"])
            self.assertEqual(payload["evidence"]["page_state"], "navigation_failed")
            self.assertFalse(payload["evidence"]["profile_path_returned"])
            self.assertFalse(payload["evidence"]["password_returned"])

    def test_read_dataset_workbench_clicks_realtime_and_yesterday_tabs(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_workbench_tabs",
                    "display_name": "Meituan Workbench Tabs",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9526,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
            service = meituan.MeituanEbookingBrowserService()
            page = _WorkbenchTabPage()
            service._get_or_create_page = AsyncMock(return_value=page)

            async def _run() -> dict[str, Any]:
                return await service.read_dataset(
                    platform_account_id="meituan_workbench_tabs",
                    browser_session_id=created.json()["browser_session_id"],
                    actor_id="operator",
                    headless=False,
                    dataset="workbench_home",
                )

            payload = asyncio.run(_run())

            self.assertEqual(payload["status"], "success")
            data = payload["data"]
            self.assertEqual(data["realtime_data"]["\u6d4f\u89c8\u4eba\u6570"], "1")
            self.assertEqual(data["yesterday_data"]["\u6d4f\u89c8\u4eba\u6570"], "270")
            self.assertIn("\u5b9e\u65f6\u6570\u636e", page.clicked_labels)
            self.assertIn("\u6628\u65e5\u6570\u636e", page.clicked_labels)

    def test_read_dataset_promotion_coupon_clicks_activity_data(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_coupon_data",
                    "display_name": "Meituan Coupon Data",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9527,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
            service = meituan.MeituanEbookingBrowserService()
            page = _PromotionActivityPage()
            service._get_or_create_page = AsyncMock(return_value=page)

            async def _run() -> dict[str, Any]:
                return await service.read_dataset(
                    platform_account_id="meituan_coupon_data",
                    browser_session_id=created.json()["browser_session_id"],
                    actor_id="operator",
                    headless=False,
                    dataset="promotion_coupon",
                )

            payload = asyncio.run(_run())

            self.assertEqual(payload["status"], "success")
            activity = payload["data"]["coupon_activities"][0]
            self.assertEqual(activity["activity_metrics"]["\u66dd\u5149\u91cf"], "1542")
            self.assertEqual(activity["activity_metrics"]["\u652f\u4ed8\u8ba2\u5355\u6570"], "7")
            self.assertIn("\u6d3b\u52a8\u6570\u636e", page.clicked_labels)

    def test_read_dataset_promotion_coupon_clicks_activity_data_tab_when_text_locator_misses(
        self,
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_coupon_dom_tab",
                    "display_name": "Meituan Coupon DOM Tab",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9528,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
            service = meituan.MeituanEbookingBrowserService()
            page = _PromotionActivityPage(text_locator_count=0)
            service._get_or_create_page = AsyncMock(return_value=page)

            async def _run() -> dict[str, Any]:
                return await service.read_dataset(
                    platform_account_id="meituan_coupon_dom_tab",
                    browser_session_id=created.json()["browser_session_id"],
                    actor_id="operator",
                    headless=False,
                    dataset="promotion_coupon",
                )

            payload = asyncio.run(_run())

            activity = payload["data"]["coupon_activities"][0]
            self.assertEqual(activity["activity_metrics"]["\u66dd\u5149\u91cf"], "1542")
            self.assertEqual(activity["activity_metrics"]["\u652f\u4ed8\u8ba2\u5355\u6570"], "7")
            self.assertIn("js:\u6d3b\u52a8\u6570\u636e", page.clicked_labels)


class MeituanDatasetWaitTests(unittest.TestCase):
    def test_safe_dataset_text_waits_until_frame_text_stabilizes(self) -> None:
        import asyncio

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        final_text = "\n".join(
            [
                "\u95e8\u5e97",
                MEITUAN_HOTEL_NAME,
                "\u6570\u636e\u4e2d\u5fc3",
                "\u66dd\u5149\u91cf",
                "1542",
            ]
        )
        frame = _SequentialFrame(
            url="https://me.meituan.com/ebooking/newhb-sub-app/data-center/index.html",
            texts=["", "\u9875\u9762\u52a0\u8f7d\u4e2d", final_text, final_text],
        )
        page = _SequentialPage(frames=[frame])

        text = asyncio.run(meituan._safe_meituan_dataset_text(page, url_marker="data-center"))

        self.assertEqual(text, final_text)
        self.assertGreaterEqual(frame.body.call_count, 4)
        self.assertGreaterEqual(page.wait_count, 3)

    def test_select_data_center_tab_clicks_playwright_first_property(self) -> None:
        import asyncio

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        tab_locator = _ClickableLocator()
        frame = _TabFrame(tab_locator)
        page = _TabPage(frames=[frame])

        clicked = asyncio.run(
            meituan._select_meituan_data_center_tab(
                page,
                "\u4ea4\u6613\u5206\u6790",
            )
        )

        self.assertTrue(clicked)
        self.assertEqual(frame.requested_text, "\u4ea4\u6613\u5206\u6790")
        self.assertEqual(tab_locator.click_count, 1)
        self.assertEqual(page.wait_count, 1)

    def test_safe_dataset_text_does_not_stop_at_meituan_shell_only_text(self) -> None:
        import asyncio

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        shell_text = "\n".join(
            [
                "\u6570\u636e\u4e2d\u5fc3",
                "\u5546\u6237\u5730\u56fe",
                "\u5fae\u4fe1\u516c\u4f17\u53f7",
                "\u624b\u673a\u7aef",
                "\u95e8\u5e97\u6d88\u606f",
                "\u5b66\u4e60\u4e2d\u5fc3",
                "\u5de5\u4f5c\u53f0",
            ]
        )
        final_text = "\n".join(
            [
                "\u95e8\u5e97",
                MEITUAN_HOTEL_NAME,
                "\u4ec5\u770b\u672c\u8d26\u53f7\u6570\u636e",
                "\u66dd\u5149\u91cf",
                "1542",
            ]
        )
        frame = _SequentialFrame(
            url="https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fdata-center%2Findex.html",
            texts=[shell_text, shell_text, shell_text, final_text, final_text],
        )
        page = _SequentialPage(frames=[frame])

        text = asyncio.run(meituan._safe_meituan_dataset_text(page, url_marker="data-center"))

        self.assertEqual(text, final_text)
        self.assertGreaterEqual(frame.body.call_count, 5)

    def test_safe_dataset_text_prefers_business_iframe_over_shell_url_marker(self) -> None:
        import asyncio

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        shell_text = "\n".join(
            [
                "\u795e\u5238\u62a5\u540d",
                "\u5546\u6237\u5730\u56fe",
                "\u5fae\u4fe1\u516c\u4f17\u53f7",
                "\u624b\u673a\u7aef",
                "\u95e8\u5e97\u6d88\u606f",
                "\u5b66\u4e60\u4e2d\u5fc3",
            ]
        )
        shell_frame = _SequentialFrame(
            url="https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=marketplatform",
            texts=[shell_text, shell_text, shell_text, shell_text],
        )
        business_frame = _SequentialFrame(
            url="https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html",
            texts=[
                "",
                MEITUAN_PROMOTION_COUPON_REAL_TEXT,
                MEITUAN_PROMOTION_COUPON_REAL_TEXT,
            ],
        )
        page = _SequentialPage(frames=[shell_frame, business_frame])

        text = asyncio.run(meituan._safe_meituan_dataset_text(page, url_marker="marketplatform"))

        self.assertEqual(text, MEITUAN_PROMOTION_COUPON_REAL_TEXT)


class _FakeMeituanDataCenterService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def read_data_center(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "actor_id": actor_id,
                "headless": headless,
            }
        )
        return {
            "status": "success",
            "summary": "已读取美团 EBK 数据中心关键经营指标。",
            "platform": "meituan",
            "dataset": "data_center",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "hotel_name": "静里全套房酒店",
                "basic_operation_metrics": {},
            },
            "evidence": {
                "profile_path_returned": False,
                "password_returned": False,
                "parser": "meituan_data_center_dom_v1",
            },
        }


class _FakeMeituanDatasetService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def read_dataset(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool,
        dataset: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "actor_id": actor_id,
                "headless": headless,
                "dataset": dataset,
            }
        )
        return {
            "status": "success",
            "summary": "Meituan EBK dataset read completed.",
            "platform": "meituan",
            "dataset": dataset,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {"coupon_activities": []},
            "evidence": {
                "profile_path_returned": False,
                "password_returned": False,
                "parser": "fake",
            },
        }


class _SequentialPage:
    def __init__(self, *, frames: list[Any]) -> None:
        self.frames = frames
        self.wait_count = 0

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        del timeout_ms
        self.wait_count += 1

    def locator(self, selector: str) -> "_SequentialTextLocator":
        del selector
        return _SequentialTextLocator([""])


class _SequentialFrame:
    def __init__(self, *, url: str, texts: list[str]) -> None:
        self.url = url
        self.body = _SequentialTextLocator(texts)

    def locator(self, selector: str) -> "_SequentialTextLocator":
        del selector
        return self.body


class _SequentialTextLocator:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.call_count = 0

    async def inner_text(self, *, timeout: int) -> str:
        del timeout
        index = min(self.call_count, len(self._texts) - 1)
        self.call_count += 1
        return self._texts[index]


class _TabPage:
    def __init__(self, *, frames: list[Any]) -> None:
        self.frames = frames
        self.wait_count = 0

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        del timeout_ms
        self.wait_count += 1

    def get_by_text(self, text: str, *, exact: bool) -> "_ClickableLocator":
        del text, exact
        return _ClickableLocator(count=0)


class _TabFrame:
    def __init__(self, locator: "_ClickableLocator") -> None:
        self.locator = locator
        self.requested_text: str | None = None

    def get_by_text(self, text: str, *, exact: bool) -> "_ClickableLocator":
        del exact
        self.requested_text = text
        return self.locator


class _ClickableLocator:
    def __init__(self, *, count: int = 1) -> None:
        self._count = count
        self.click_count = 0
        self.first = self

    async def count(self) -> int:
        return self._count

    async def click(self, *, timeout: int) -> None:
        del timeout
        self.click_count += 1


class _WorkbenchTabPage:
    def __init__(self) -> None:
        self.url = "https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html%23%2F"
        self.clicked_labels: list[str] = []
        self.frame = _WorkbenchTabFrame(self)
        self.frames = [self.frame]
        self.active_label = "\u5b9e\u65f6\u6570\u636e"

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        del timeout_ms

    def get_by_text(self, text: str, *, exact: bool) -> "_WorkbenchTabLocator":
        del exact
        return _WorkbenchTabLocator(self, text, count=0)

    def locator(self, selector: str) -> "_WorkbenchBodyLocator":
        del selector
        return _WorkbenchBodyLocator(self)


class _WorkbenchTabFrame:
    def __init__(self, page: _WorkbenchTabPage) -> None:
        self.page = page
        self.url = "https://eb.meituan.com/ebooking/new-workbench/index.html"

    def locator(self, selector: str) -> "_WorkbenchBodyLocator":
        del selector
        return _WorkbenchBodyLocator(self.page)

    def get_by_text(self, text: str, *, exact: bool) -> "_WorkbenchTabLocator":
        del exact
        return _WorkbenchTabLocator(self.page, text)


class _WorkbenchBodyLocator:
    def __init__(self, page: _WorkbenchTabPage) -> None:
        self.page = page

    async def inner_text(self, *, timeout: int) -> str:
        del timeout
        metric_value = "1" if self.page.active_label == "\u5b9e\u65f6\u6570\u636e" else "270"
        return "\n".join(
            [
                MEITUAN_HOTEL_NAME,
                "\u5b9e\u65f6\u6570\u636e",
                "\u6628\u65e5\u6570\u636e",
                "\u8fdd\u7ea6\u770b\u677f",
                "\u6d4f\u89c8\u4eba\u6570",
                metric_value,
                "HOS\u5206\u63d0HOS\u5206\uff0c\u6da8\u8ba2\u5355",
                "4.56",
                "\u518d\u63a5\u518d\u5389",
            ]
        )


class _WorkbenchTabLocator:
    def __init__(self, page: _WorkbenchTabPage, text: str, *, count: int = 1) -> None:
        self.page = page
        self.text = text
        self._count = count if text not in {"\u5b9e\u65f6\u6570\u636e", "\u6628\u65e5\u6570\u636e"} else 1
        self.first = self

    async def count(self) -> int:
        return self._count

    async def click(self, *, timeout: int) -> None:
        del timeout
        self.page.active_label = self.text
        self.page.clicked_labels.append(self.text)


class _PromotionActivityPage:
    def __init__(self, *, text_locator_count: int = 1) -> None:
        self.url = "https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=marketplatform&from=EBOOKING"
        self.clicked_labels: list[str] = []
        self.show_activity_data = False
        self.text_locator_count = text_locator_count
        self.frame = _PromotionActivityFrame(self)
        self.frames = [self.frame]

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        del timeout_ms

    def get_by_text(self, text: str, *, exact: bool) -> "_PromotionActivityLocator":
        return _PromotionActivityLocator(self, text, exact=exact, count=0)

    def locator(self, selector: str) -> "_PromotionActivityBodyLocator":
        del selector
        return _PromotionActivityBodyLocator(self)


class _PromotionActivityFrame:
    def __init__(self, page: _PromotionActivityPage) -> None:
        self.page = page
        self.url = "https://next.meituan.com/2708/activity/kdb/jdmm/activity/list/index.html"

    def locator(self, selector: str) -> "_PromotionActivityBodyLocator":
        del selector
        return _PromotionActivityBodyLocator(self.page)

    def get_by_text(self, text: str, *, exact: bool) -> "_PromotionActivityLocator":
        return _PromotionActivityLocator(self.page, text, exact=exact)

    async def evaluate(self, script: str, label: str) -> bool:
        del script
        if label == "\u6d3b\u52a8\u6570\u636e":
            self.page.clicked_labels.append(f"js:{label}")
            self.page.show_activity_data = True
            return True
        return False


class _PromotionActivityBodyLocator:
    def __init__(self, page: _PromotionActivityPage) -> None:
        self.page = page

    async def inner_text(self, *, timeout: int) -> str:
        del timeout
        if self.page.show_activity_data:
            return MEITUAN_PROMOTION_COUPON_DETAIL_TEXT
        return MEITUAN_PROMOTION_COUPON_REAL_TEXT


class _PromotionActivityLocator:
    def __init__(self, page: _PromotionActivityPage, text: str, *, exact: bool, count: int = 1) -> None:
        self.page = page
        self.text = text
        self.exact = exact
        self._count = count if text != "\u6d3b\u52a8\u6570\u636e" else page.text_locator_count
        self.first = self

    async def count(self) -> int:
        return self._count

    async def click(self, *, timeout: int) -> None:
        del timeout
        self.page.clicked_labels.append(self.text)
        if self.text == "\u6d3b\u52a8\u6570\u636e":
            self.page.show_activity_data = True


if __name__ == "__main__":
    unittest.main()


class MeituanTodayRealtimeAttachTests(unittest.TestCase):
    """T1.1：数据中心「今日实时」页签抓取结果的挂载与证据。"""

    def test_attach_today_realtime_metrics_keeps_default_metrics(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = {
            "status": "success",
            "data": {"basic_operation_metrics": {"引流价": {"value": "220.71 元"}}},
        }
        realtime_parsed = {
            "status": "success",
            "data": {"basic_operation_metrics": {"引流价": {"value": "340.00 元"}, "曝光量": {"value": "0 次"}}},
        }
        result = meituan._attach_meituan_today_realtime(
            parsed, realtime_parsed, selected_page_tab="今日实时"
        )
        data = result["data"]
        self.assertEqual(data["today_realtime_metrics"]["引流价"]["value"], "340.00 元")
        self.assertEqual(data["basic_operation_metrics"]["引流价"]["value"], "220.71 元")
        self.assertEqual(data["today_realtime_evidence"]["selected_page_tab"], "今日实时")
        self.assertEqual(data["today_realtime_evidence"]["default_page_tab"], "昨日")

    def test_attach_rejects_non_today_selection(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = {"status": "success", "data": {}}
        realtime_parsed = {"status": "success", "data": {"basic_operation_metrics": {"引流价": {"value": "340.00 元"}}}}
        result = meituan._attach_meituan_today_realtime(
            parsed, realtime_parsed, selected_page_tab="昨日"
        )
        self.assertNotIn("today_realtime_metrics", result["data"])

    def test_attach_accepts_trade_tab_metric_shape(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = {"status": "success", "data": {"metrics": {"支付订单数": "56"}}}
        realtime_parsed = {
            "status": "success",
            "data": {"business_performance": {"支付订单数": "12"}},
        }
        result = meituan._attach_meituan_today_realtime(
            parsed, realtime_parsed, selected_page_tab="今日实时"
        )
        self.assertEqual(result["data"]["today_realtime_metrics"], {"支付订单数": "12"})


MEITUAN_ACTIVITY_SIGNUP_TEXT = "\n".join(
    [
        "活动报名",
        "商户地图",
        "可报名活动",
        "参与活动获取流量和转化率，提升门店收益！",
        "已报名活动",
        "活动类型：",
        "全部类型",
        "【平台主推】早订多减",
        "提前锁定未来订单，降低空房风险",
        "促转化",
        "优惠适用时间：全年",
        "免费报名",
        "累积51715家参与",
        "【平台主推】连住优惠",
        "吸引客人连续入住，提高客单价！",
        "优惠适用时间：全年",
        "免费报名",
        "累积53131家参与",
        "【平台主推】本地【人群特惠】",
        "本地客群高效促进转化",
        "优惠适用时间：全年",
        "重新报名",
        "累积167475家参与",
        "已报名",
        "【平台主推】踏春赏花特惠",
        "春季专享特惠活动，精准引流",
        "优惠适用时间：全年",
        "报名详情",
        "节日智能生财宝",
        "节日提升转化，保障收益",
        "优惠适用时间：法定节假日及节日前一天生效。",
        "免费报名",
        "累积5577家参与",
        "共 43 条",
    ]
)


class MeituanActivitySignupParserTests(unittest.TestCase):
    """T2：美团「活动报名」页（promotion/activity#/index）的报名状态解析。"""

    def test_parse_signup_activities_with_status_and_valid_period(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = meituan._parse_meituan_activity_signup_text(
            MEITUAN_ACTIVITY_SIGNUP_TEXT,
            current_url="https://me.meituan.com/ebooking/merchant/promotion/activity#/index",
        )
        self.assertEqual(parsed["status"], "success")
        activities = {a["name"]: a for a in parsed["data"]["signup_activities"]}
        # 免费报名 → 未报名（可报名）
        self.assertEqual(activities["【平台主推】早订多减"]["status"], "未报名")
        self.assertEqual(activities["【平台主推】早订多减"]["valid_period"], "全年")
        # 报名详情 → 已报名
        self.assertEqual(activities["【平台主推】踏春赏花特惠"]["status"], "已报名")
        # 重新报名按钮 → 需重新报名（未在报名态）
        self.assertEqual(activities["【平台主推】本地【人群特惠】"]["status"], "需重新报名")
        # 节假日有效期保留原文
        self.assertEqual(
            activities["节日智能生财宝"]["valid_period"], "法定节假日及节日前一天生效。"
        )

    def test_parse_returns_needs_review_when_no_signup_cards(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = meituan._parse_meituan_activity_signup_text(
            "工作台 暂无数据", current_url="about:blank"
        )
        self.assertEqual(parsed["data"]["signup_activities"], [])


class MeituanRealtimeLoadingGuardTests(unittest.TestCase):
    """核心指标占位守卫（2026-09-02 运营反馈：偶发抓到「-」，刷新即恢复）。"""

    def _call(self, metrics: dict) -> bool:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        parsed = {
            "status": "success",
            "data": {"basic_operation_metrics": metrics},
        }
        return meituan._meituan_realtime_core_metrics_loading(parsed)

    def test_core_metrics_dashed_means_loading(self) -> None:
        self.assertTrue(
            self._call({
                "销售额": {"value": "- 元"},
                "支付订单数": {"value": "- 单"},
                "浏览人数": {"value": "130 人"},
            })
        )

    def test_any_core_metric_present_means_ready(self) -> None:
        self.assertFalse(
            self._call({
                "销售额": {"value": "1969.00 元"},
                "支付订单数": {"value": "- 单"},
            })
        )

    def test_no_metrics_or_not_success_means_not_loading(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        self.assertFalse(meituan._meituan_realtime_core_metrics_loading(None))
        self.assertFalse(meituan._meituan_realtime_core_metrics_loading({"status": "needs_review", "data": {}}))
        self.assertFalse(
            meituan._meituan_realtime_core_metrics_loading(
                {"status": "success", "data": {"basic_operation_metrics": {}}}
            )
        )
