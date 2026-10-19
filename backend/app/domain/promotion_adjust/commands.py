"""活动促销（携程 EBK 促销中心）调整指令解析。

支持两类飞书 @机器人 指令：
- 查询：`促销列表` / `活动促销列表` -> 只读回列出已参加促销卡片
- 调整：`促销调整 天天特价 立减调到5` / `把促销门店新客立减的立减调到35%`
  -> 生成审批卡（仅支持单条「立减 X%」规则的促销）

注意口径：`促销效果` / `促销报名` / `已参加促销` 等是数据中心问答短语，
归 Hermes 读链路，本模块不得截胡。解析是纯函数。
"""
from __future__ import annotations

import re
from typing import Any

# 立减百分比的合理区间：低于 1% 视为误输入，高于 95% 属于极端甩卖价。
PROMOTION_DISCOUNT_MIN_PERCENT = 1.0
PROMOTION_DISCOUNT_MAX_PERCENT = 95.0

_PERCENT_VERB = r"(?:调到|调至|改到|改至|改成|改为|调整到|调整至|调整为|设为|设置为)"
_PERCENT_NUMBER = r"(?P<percent>\d+(?:\.\d+)?)"
_NAME_CHARS = r"[^，。；,;]"
_DISCOUNT_NOUN = r"(?:立减|折扣|优惠|折扣比例)"
_INVALID_PROMOTION_NAMES = frozenset(
    {"立减", "折扣", "优惠", "调整", "列表", "促销", "活动"}
)

# 顺序即优先级：带「立减/折扣」字样的最明确，其次「促销调整」前缀，最后省略。
_ADJUST_WITH_NOUN = re.compile(
    rf"促销(?P<a_name>{_NAME_CHARS}{{0,40}}?)(?:的)?{_DISCOUNT_NOUN}"
    rf"{_PERCENT_VERB}{_PERCENT_NUMBER}\s*%?\s*$"
)
_ADJUST_WITH_PREFIX = re.compile(
    rf"(?:活动)?促销调整\s*(?P<b_name>{_NAME_CHARS}{{0,40}}?)(?:的)?(?:{_DISCOUNT_NOUN})?"
    rf"{_PERCENT_VERB}{_PERCENT_NUMBER}\s*%?\s*$"
)
_ADJUST_BARE = re.compile(
    rf"促销(?P<c_name>{_NAME_CHARS}{{1,40}}?){_PERCENT_VERB}{_PERCENT_NUMBER}\s*%?\s*$"
)
_QUERY = re.compile(
    r"^(?:(?:查询|查|看看?|看一下|帮忙查|帮我查)(?:一下)?)?"
    r"(?:活动)?促销列表$"
)

# 数据中心问答短语白名单：含这些词的文本不是本指令域。
_DATA_QA_PHRASES = ("促销效果", "促销报名", "促销情况", "已参加促销", "参加促销")


def promotion_name_key(name: Any) -> str:
    """促销名匹配键：剔除空格与各类横线变体（与金字塔计划名同口径）。"""
    return re.sub(r"[\s\-—－–_]+", "", str(name or ""))


def is_promotion_adjust_command(text: str) -> bool:
    """Hermes 转发闸门使用的窄口径：只捕获促销列表/促销调整类指令。"""
    compact = _compact(text)
    if "促销" not in compact:
        return False
    if any(phrase in compact for phrase in _DATA_QA_PHRASES):
        return False
    if _QUERY.match(compact):
        return True
    if "促销调整" in compact:
        return True
    if re.search(_DISCOUNT_NOUN + _PERCENT_VERB, compact) and re.search(r"\d", compact):
        return True
    return bool(re.search(_PERCENT_VERB, compact) and re.search(r"\d", compact))


def classify_promotion_adjust_command(command: str) -> dict[str, Any]:
    text = _strip_mentions(command)
    if not is_promotion_adjust_command(text):
        return {"status": "not_promotion"}

    compact = _compact(text)
    if _QUERY.match(compact):
        return {"status": "list_query"}

    for name_key, pattern in (
        ("a_name", _ADJUST_WITH_NOUN),
        ("b_name", _ADJUST_WITH_PREFIX),
        ("c_name", _ADJUST_BARE),
    ):
        match = pattern.search(compact)
        if match is None:
            continue
        name = _normalize_promotion_name(match.group(name_key))
        percent = _safe_float(match.group("percent"))
        if (
            percent is None
            or percent < PROMOTION_DISCOUNT_MIN_PERCENT
            or percent > PROMOTION_DISCOUNT_MAX_PERCENT
        ):
            return {
                "status": "invalid_percent",
                "summary": (
                    f"立减百分比需在 {PROMOTION_DISCOUNT_MIN_PERCENT:g}~"
                    f"{PROMOTION_DISCOUNT_MAX_PERCENT:g} 之间，请确认后重新发送，"
                    "例如：促销调整 天天特价 立减调到5。"
                ),
                "partial": {"promotion_name": name, "target_discount_percent": percent},
            }
        if not name:
            break
        return {"status": "ready", "promotion_name": name, "target_discount_percent": percent}

    return _needs_confirmation(text)


def _needs_confirmation(text: str) -> dict[str, Any]:
    compact = _compact(text)
    percent_match = re.search(rf"{_PERCENT_VERB}\s*{_PERCENT_NUMBER}", compact)
    percent = _safe_float(percent_match.group("percent")) if percent_match else None

    name: str | None = None
    for name_pattern in (
        rf"促销(?:调整)?(?P<name>{_NAME_CHARS}{{1,40}}?)(?:的)?{_DISCOUNT_NOUN}",
        rf"促销(?:调整)?(?P<name>{_NAME_CHARS}{{1,40}}?){_PERCENT_VERB}",
    ):
        name_match = re.search(name_pattern, compact)
        if name_match is None:
            continue
        candidate = _normalize_promotion_name(name_match.group("name"))
        if candidate:
            name = candidate
            break

    partial = {
        key: value
        for key, value in {
            "promotion_name": name,
            "target_discount_percent": percent,
        }.items()
        if value is not None
    }
    missing = [
        field
        for field in ("promotion_name", "target_discount_percent")
        if field not in partial
    ]
    summary = "促销调整信息不完整，还缺 {}。已识别：{}。".format(
        "、".join("促销名称" if field == "promotion_name" else "目标立减百分比" for field in missing)
        or "无",
        "；".join(
            f"促销名称：{partial['promotion_name']}"
            if key == "promotion_name"
            else f"目标立减：{partial['target_discount_percent']:g}%"
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
            "促销调整 天天特价 立减调到5",
            "把促销门店新客立减的立减调到35%",
        ],
    }


def _normalize_promotion_name(raw: str | None) -> str | None:
    value = re.sub(r"\s+", "", str(raw or ""))
    value = re.sub(r"^调整", "", value)
    value = value.strip("的-—－ ")
    if len(value) < 1 or value in _INVALID_PROMOTION_NAMES:
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
