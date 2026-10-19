"""金字塔（携程 EBK 广告金字塔）出价指令解析。

支持两类飞书 @机器人 指令：
- 查询：`金字塔出价` / `金字塔推广列表` 等 -> 只读回列出价卡片
- 调价：`金字塔调价 14--16 出价调到6` / `把金字塔14--16的出价调到6元` -> 审批卡

解析是纯函数，不接触浏览器与数据库。
"""
from __future__ import annotations

import re
from typing import Any

# 出价上限保护：金字塔点击出价为个位数到十位数 CNY 量级，超过视为误输入。
PYRAMID_BID_MAX_CNY = 99.0
PYRAMID_BID_MIN_CNY = 0.1

_PYRAMID_BID_VERB = r"(?:调到|调至|改到|改至|改成|改为|调整到|调整至|调整为|设为|设置为)"
_BID_NUMBER = r"(?P<bid>\d+(?:\.\d+)?)"
_NAME_CHARS = r"[^，。；,;]"
_INVALID_CAMPAIGN_NAMES = frozenset({"出价", "调价", "价格", "广告", "推广"})

# 顺序即优先级：带「出价」字样的最明确，其次「金字塔调价」前缀，最后省略「出价」。
_ADJUST_WITH_CHUJIA = re.compile(
    rf"金字塔(?P<a_name>{_NAME_CHARS}{{0,40}}?)(?:的)?(?:点击)?出价"
    rf"{_PYRAMID_BID_VERB}{_BID_NUMBER}\s*(?:元|块)?\s*$"
)
_ADJUST_WITH_PREFIX = re.compile(
    rf"金字塔调价\s*(?P<b_name>{_NAME_CHARS}{{0,40}}?)(?:的)?(?:点击)?(?:出价)?"
    rf"{_PYRAMID_BID_VERB}{_BID_NUMBER}\s*(?:元|块)?\s*$"
)
_ADJUST_BARE = re.compile(
    rf"金字塔(?P<c_name>{_NAME_CHARS}{{1,40}}?){_PYRAMID_BID_VERB}{_BID_NUMBER}\s*(?:元|块)?\s*$"
)
_QUERY = re.compile(
    r"^(?:查询|查|看看?|看一下|帮忙查一下?|帮我查一下?)?"
    r"金字塔(?:广告|推广)?(?:的)?"
    r"(?:出价|出价列表|出价情况|列表|计划列表|计划|推广列表|推广计划)$"
)


def pyramid_campaign_key(name: Any) -> str:
    """计划名匹配键：剔除空格与各类横线（-/--/—/－/–/_）后再比较。

    2026-09-08 线上问题：页面计划名「周内0-2」（单横线）与用户指令
    「周内0--2」（双横线）互为变体；精确字符串比较会漏配。
    """
    return re.sub(r"[\s\-—－–_]+", "", str(name or ""))


def is_pyramid_command(text: str) -> bool:
    """Hermes 转发闸门使用的窄口径：只捕获出价/调价/推广列表类指令。

    与 `classify_pyramid_bid_command(...)["status"] != "not_pyramid"` 语义一致；
    「金字塔投放情况怎么样」等数据问答仍留给 Hermes LLM 读链路。
    """
    compact = _compact(text)
    if "金字塔" not in compact:
        return False
    if (
        "出价" in compact
        or "金字塔调价" in compact
        or "推广列表" in compact
        or "推广计划" in compact
        or "计划列表" in compact
        or "出价列表" in compact
    ):
        return True
    # 省略「出价」的说法：金字塔 20--22 调到4.2
    return bool(
        re.search(_PYRAMID_BID_VERB, compact) and re.search(r"\d", compact)
    )


def classify_pyramid_bid_command(command: str) -> dict[str, Any]:
    text = _strip_mentions(command)
    if not is_pyramid_command(text):
        return {"status": "not_pyramid"}

    compact = _compact(text)
    if _QUERY.match(compact):
        return {"status": "list_query"}

    for name_key, pattern in (
        ("a_name", _ADJUST_WITH_CHUJIA),
        ("b_name", _ADJUST_WITH_PREFIX),
        ("c_name", _ADJUST_BARE),
    ):
        match = pattern.search(compact)
        if match is None:
            continue
        name = _normalize_campaign_name(match.group(name_key))
        bid = _safe_float(match.group("bid"))
        if bid is None or bid > PYRAMID_BID_MAX_CNY or bid < PYRAMID_BID_MIN_CNY:
            return {
                "status": "invalid_bid",
                "summary": (
                    f"出价需在 {PYRAMID_BID_MIN_CNY:g}~{PYRAMID_BID_MAX_CNY:g} 元之间，"
                    "请确认后重新发送，例如：金字塔调价 14--16 出价调到6。"
                ),
                "partial": {"campaign_name": name, "target_bid": bid},
            }
        if not name:
            break
        return {"status": "ready", "campaign_name": name, "target_bid": bid}

    return _needs_confirmation(text)


def _needs_confirmation(text: str) -> dict[str, Any]:
    compact = _compact(text)
    bid_match = re.search(rf"{_PYRAMID_BID_VERB}\s*{_BID_NUMBER}", compact)
    bid = _safe_float(bid_match.group("bid")) if bid_match else None

    name: str | None = None
    for name_pattern in (
        rf"金字塔(?:调价)?(?P<name>{_NAME_CHARS}{{1,40}}?)(?:的)?(?:点击)?出价",
        rf"金字塔(?:调价)?(?P<name>{_NAME_CHARS}{{1,40}}?){_PYRAMID_BID_VERB}",
    ):
        name_match = re.search(name_pattern, compact)
        if name_match is None:
            continue
        candidate = _normalize_campaign_name(name_match.group("name"))
        if candidate:
            name = candidate
            break

    partial = {
        key: value
        for key, value in {"campaign_name": name, "target_bid": bid}.items()
        if value is not None
    }
    missing = [
        field
        for field in ("campaign_name", "target_bid")
        if field not in partial
    ]
    summary = "金字塔调价信息不完整，还缺 {}。已识别：{}。".format(
        "、".join("推广名称" if field == "campaign_name" else "目标出价" for field in missing)
        or "无",
        "；".join(
            f"推广名称：{partial['campaign_name']}" if key == "campaign_name" else f"目标出价：¥{partial['target_bid']:g}"
            for key in partial
        )
        or "暂未识别到完整字段",
    )
    return {
        "status": "needs_confirmation",
        "missing_fields": missing,
        "partial": partial,
        "summary": summary,
        "examples": [
            "金字塔调价 14--16 出价调到6",
            "把金字塔22--24的出价调到5.5元",
        ],
    }


def _normalize_campaign_name(raw: str | None) -> str | None:
    value = re.sub(r"\s+", "", str(raw or ""))
    value = re.sub(r"^调价", "", value)
    value = value.strip("的-—－ ")
    if len(value) < 1 or value in _INVALID_CAMPAIGN_NAMES:
        return None
    return value or None


def _strip_mentions(text: str) -> str:
    return re.sub(r"@\S+", "", text or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", _strip_mentions(text))


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed
