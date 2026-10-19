"""飞书「查询房价」指令识别（房价情况表读取入口）。

2026-09-14 起改为包含式命中：句中出现「房价/房型价格/房型价」即视为查询
（平台词、日期词、「本店」、房型名可任意位置、任意语序），由后端现读当前
群绑定门店的房型价目表并回「房价查询」卡片。2026-10-14 全量 LLM 先行后，
网关不再按词表直转文本消息；本词表成为 dispatch_watch_feishu_event
转发事件的终审闸——Hermes LLM 判断为问价后转发运营原话，仍按此词表识别，
不命中即忽略。

调价动词、竞品对象、日历价、趋势、ADR 指标（平均房价）、历史区间、「上一轮」
持久化快照问法、概念/预测、退款退订问法不属查询指令，返回 None 交回原链路。

日期口径：明天/明日/明晚 → tomorrow；今天/今日/今晚/现在/当前/目前 → today。
「查现在」与「查今天」同口径（都是现读今天入住价目表，卡片标注读取时间，
无需区分）；未带日期词默认 today；今天与明天同时出现以今天为准。

平台口径：句中恰出现一个平台词（携程/美团/飞猪）→ 只读该平台；未点名或
同时点名多个平台（对比问法）→ 读当前群绑定门店的全部已绑定平台。

调整查价口径只改本文件（网关侧词表副本已随 2026-10-14 退役删除），
配套 backend/tests/domain/test_pricing_query_command.py 同步更新。
"""

from __future__ import annotations

import re
from typing import Any

# 调价/改价动词与模糊改价说法：出现即按调价链路处理，不当查询。
_PRICING_QUERY_ADJUST_RE = re.compile(
    r"调到|调至|改到|改至|调整到|调整至|设为|设置为"
    r"|调价|改价|涨价|降价|降到|涨到|降一点|涨一点|改一下|调一下"
    r"|怎么改|怎么调|怎么调整|如何改|如何调|如何调整"
)
# 排除词：竞品对象、日历价、趋势/变化、ADR 指标、历史区间、概念/预测、退款退订。
_PRICING_QUERY_EXCLUDE_RE = re.compile(
    r"竞品|竞对|竞争酒店|竞店"
    r"|日历价|未来房价|未来价格|未来15天|未来十五天"
    r"|趋势|涨幅|跌幅|涨跌|曲线|波动|历史价格|价格走势|历史"
    r"|平均房价"
    r"|变化|变了|提醒|预警|预测"
    r"|是什么|怎么理解|什么意思|概念"
    r"|昨天|昨日|前天|上周|上月|上个月"
    r"|近7|近30|最近一周|过去一周|最近一个月|过去一个月|周度|月度"
    r"|退款|退订|退单|退了"
    r"|上一轮|上轮"
    # 2026-10-08：方法论问法（怎么做/铺排/操盘）不是查价指令，交给 LLM 答方法。
    r"|怎么做|如何做|铺排|操盘"
    # 2026-10-08 晚（生产实证 21:16 立瑞）：图表追问话法（图上加上本店/精确到
    # 个位/重新出图）不是价目表查询，须与网关词表同步排除，交回 LLM 链路。
    r"|曲线图|趋势图|对比图|折线图|走势图"
    r"|图上|图里|图中|重画|再画|重新画|重新出图|出图|画图"
    r"|标注|个位|精确到|准确到|加上本店|加上自己"
)
# 「把/将」开头的祈使句是操作指令，不是查询。
_PRICING_QUERY_LEADING_RE = re.compile(r"^(?:把|将)")
# 2026-09-22 第二批：房态/卖价/售价/房型+多少钱 也是价目表口径（卡片含
# 房型/卖价/底价/房态列）；「退了」排除防止退款语境的「多少钱」误识别。
# 2026-10-14 第三批：补「房间价格/房型的价格/房间的价格」——运营说
# 「各房间价格」「各个房型的价格」时不含连续「房价/房型价格」token，
# dispatch 转发被忽略拿不到卡（生产 17:55-18:03 三连问实证）。
_PRICING_QUERY_TOKENS = (
    "房价",
    "房型价格",
    "房型价",
    "房型的价格",
    "房间价格",
    "房间的价格",
    "卖价",
    "售价",
    "房态",
    "还有房",
    "有房吗",
    "房多少钱",
)
_PLATFORM_WORDS = {"携程": "ctrip", "美团": "meituan", "飞猪": "fliggy"}
_TOMORROW_WORDS = ("明天", "明日", "明晚")
_TODAY_WORDS = ("今天", "今日", "今晚", "现在", "当前", "目前")


def classify_pricing_query_command(command: str) -> dict[str, Any] | None:
    """识别「查询房价」类指令；非查询指令返回 None。"""

    compact = re.sub(r"\s+", "", re.sub(r"@\S+", "", str(command or "")))
    if not compact:
        return None
    if _PRICING_QUERY_ADJUST_RE.search(compact):
        return None
    if _PRICING_QUERY_EXCLUDE_RE.search(compact):
        return None
    if _PRICING_QUERY_LEADING_RE.match(compact):
        return None
    if not any(token in compact for token in _PRICING_QUERY_TOKENS):
        return None

    has_today = any(word in compact for word in _TODAY_WORDS)
    has_tomorrow = any(word in compact for word in _TOMORROW_WORDS)
    platform_words = [word for word in _PLATFORM_WORDS if word in compact]
    return {
        "status": "ready",
        "date_label": "tomorrow" if has_tomorrow and not has_today else "today",
        "platform": (
            _PLATFORM_WORDS[platform_words[0]] if len(platform_words) == 1 else None
        ),
    }
