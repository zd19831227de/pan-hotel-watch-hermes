from __future__ import annotations

import re
from typing import Any

_MANUAL_PRICE_VERB = r"(?:调到|调至|改到|改至|调整到|调整至|设为|设置为)"
_DATE_TODAY = frozenset({"今天", "今日"})
_DATE_TOMORROW = frozenset({"明天", "明日"})
_INVALID_ROOM_TYPES = frozenset({"调", "调到", "价格", "售价", "房价", "的"})
# 指令里房型名后的价格词（如「奢华三卧套房价格调到1013」）是口语修饰，
# 不是房型名的一部分；带进 EBK 房型弹窗会按全串包含匹配失败。
_PRICE_WORD_SUFFIX_RE = re.compile(r"(?:的)?(?:价格|房价|售价|卖价)$")

# 日期令牌：今天/明天，或具体日期（2026-09-15 / 9月15日）。
# 不收裸 M-D（如 9-15）：会与「2-3人房」「1-8人套房」等房型名里的
# 数字-分隔-数字撞车误判成日期；月日带「日」后缀、ISO 带 4 位年，无歧义。
_MANUAL_DATE_TOKENS = r"今天|今日|明天|明日"
_MANUAL_DATE_CONCRETE = r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日"
_MANUAL_DATE_GROUP = rf"(?P<date>{_MANUAL_DATE_TOKENS}|{_MANUAL_DATE_CONCRETE})"
# 具体入住日期窗口：今天起 15 天内（与 EBK 日历/日历价可见范围一致）。
# 超窗口日期不得静默回退「今天」写价，按缺日期走确认卡。
MANUAL_CHECKIN_MAX_DAYS_AHEAD = 15

# 指令里的平台词（如「把明天携程奢华三卧套房调到1100」）属于调价目标平台，
# 不是房型名的一部分；解析时剥离并单独返回，避免页面房型匹配被污染。
_PLATFORM_WORD_TO_KEY: dict[str, str] = {"携程": "ctrip", "美团": "meituan", "飞猪": "fliggy"}
_PLATFORM_PREFIX_RE = re.compile(r"^(携程|美团|飞猪)(?:上|平台|渠道|端|账号)?的?")


def split_manual_price_platform_prefix(
    room_type: str | None,
) -> tuple[str | None, str | None]:
    """把房型文本开头的平台词剥掉，返回 ``(房型, platform|None)``。"""

    text = str(room_type or "").strip()
    if not text:
        return None, None
    match = _PLATFORM_PREFIX_RE.match(text)
    if match is None:
        return text or None, None
    platform = _PLATFORM_WORD_TO_KEY[match.group(1)]
    remainder = _PLATFORM_PREFIX_RE.sub("", text, count=1).strip(" 的")
    return (remainder or None), platform


def classify_manual_price_adjustment_command(command: str) -> dict[str, Any]:
    ready = parse_manual_price_adjustment_command(command)
    if ready is not None:
        return {"status": "ready", **ready}

    partial = parse_partial_manual_price_adjustment(command)
    if not partial.get("intent_detected"):
        return {"status": "not_manual_adjustment"}

    missing_fields = _missing_manual_price_fields(partial)
    if not missing_fields:
        ready = {
            "status": "ready",
            "room_type": partial["room_type"],
            "platform": partial.get("platform"),
            "date_label": partial["date_label"],
            "target_price": partial["target_price"],
        }
        if partial.get("checkin_date"):
            ready["checkin_date"] = partial["checkin_date"]
        return ready

    return {
        "status": "needs_confirmation",
        "missing_fields": missing_fields,
        "partial": {
            key: partial[key]
            for key in ("room_type", "platform", "date_label", "checkin_date", "target_price")
            if partial.get(key) is not None
        },
        "summary": build_manual_price_confirmation_summary(partial, missing_fields),
        "examples": [
            "把今天高级大床房调到420",
            "把明天豪华双床房改到399",
        ],
    }


def parse_manual_price_adjustment_command(command: str) -> dict[str, Any] | None:
    text = _strip_mentions(command)
    room_pattern = r"(?P<room>[^，。]{1,60}?)"
    verb_pattern = _MANUAL_PRICE_VERB
    date_first = re.compile(
        r"(?:把|将)?"
        rf"{_MANUAL_DATE_GROUP}"
        r"(?:的)?"
        rf"{room_pattern}"
        rf"{verb_pattern}"
        r"(?P<price>\d{2,5})"
    )
    room_first = re.compile(
        r"(?:把|将)?"
        rf"{room_pattern}"
        rf"{_MANUAL_DATE_GROUP}"
        rf"{verb_pattern}"
        r"(?P<price>\d{2,5})"
    )
    match = date_first.search(text) or room_first.search(text)
    if match is None:
        return None
    room_type = _normalize_room_type(match.group("room"))
    room_type, platform = split_manual_price_platform_prefix(room_type)
    price = _safe_int(match.group("price"))
    if room_type is None or price is None or price <= 0:
        return None
    date_label, checkin_date = _resolve_manual_date(match.group("date"))
    # 具体日期超窗口（过去/15 天外）：不当 ready，落确认卡让店长改日期。
    if date_label is None:
        return None
    result = {
        "room_type": room_type,
        "platform": platform,
        "date_label": date_label,
        "target_price": price,
    }
    if checkin_date is not None:
        result["checkin_date"] = checkin_date
    return result


def parse_partial_manual_price_adjustment(command: str) -> dict[str, Any]:
    text = _strip_mentions(command)
    normalized = re.sub(r"\s+", "", text)
    has_adjust_verb = bool(re.search(_MANUAL_PRICE_VERB, text))
    has_tune_price = "调价" in normalized and "调价建议" not in normalized
    has_leading = bool(re.match(r"(?:把|将)", text.strip()))
    has_price_number = bool(re.search(r"\d{2,5}", text))
    if not (has_adjust_verb or (has_tune_price and has_leading) or (has_leading and has_price_number)):
        return {"intent_detected": False}

    date_label = None
    checkin_date = None
    if re.search(r"今天|今日", text):
        date_label = "today"
    elif re.search(r"明天|明日", text):
        date_label = "tomorrow"
    else:
        concrete = re.search(_MANUAL_DATE_CONCRETE, text)
        if concrete:
            resolved = _parse_concrete_date(concrete.group(0))
            if resolved:
                date_label = "specified"
                checkin_date = resolved

    target_price = None
    price_match = re.search(rf"{_MANUAL_PRICE_VERB}\s*(?P<price>\d{{2,5}})", text)
    if price_match:
        target_price = _safe_int(price_match.group("price"))
    else:
        trailing = re.search(r"(?P<price>\d{2,5})\s*(?:元|块)?\s*$", text)
        if trailing:
            target_price = _safe_int(trailing.group("price"))

    room_text = text
    room_text = re.sub(r"^(?:把|将)", "", room_text).strip()
    # 剥离日期令牌（含具体日期），避免混入房型名。
    room_text = _MANUAL_ANY_DATE_RE.sub("", room_text).strip(" 的")
    room_text = re.sub(rf"{_MANUAL_PRICE_VERB}.*", "", room_text).strip()
    room_text = re.sub(r"调价.*", "", room_text).strip()
    room_text = re.sub(r"\d{2,5}\s*(?:元|块)?\s*$", "", room_text).strip()
    room_type = _normalize_room_type(re.sub(r"\s+", " ", room_text).strip() or None)
    room_type, platform = split_manual_price_platform_prefix(room_type)

    return {
        "intent_detected": True,
        "room_type": room_type,
        "platform": platform,
        "date_label": date_label,
        "target_price": target_price,
        "checkin_date": checkin_date,
    }


def build_manual_price_confirmation_summary(
    partial: dict[str, Any],
    missing_fields: list[str],
) -> str:
    labels = {
        "room_type": "房型",
        "date_label": "入住日期（今天/明天）",
        "target_price": "目标售价",
    }
    missing_text = "、".join(labels[field] for field in missing_fields if field in labels)
    known_parts: list[str] = []
    if partial.get("room_type"):
        known_parts.append(f"房型：{partial['room_type']}")
    if partial.get("checkin_date"):
        known_parts.append(f"日期：{partial['checkin_date']}")
    elif partial.get("date_label") in {"today", "tomorrow"}:
        known_parts.append(
            f"日期：{'今天' if partial['date_label'] == 'today' else '明天'}"
        )
    if partial.get("target_price"):
        known_parts.append(f"目标价：¥{partial['target_price']}")
    known_text = "；".join(known_parts) if known_parts else "暂未识别到完整字段"
    return f"手动调价信息不完整，还缺 {missing_text}。已识别：{known_text}。"


def validate_manual_pricing_fields(
    *,
    room_type: str,
    date_label: str,
    target_price: int,
    platform: str | None = None,
) -> dict[str, Any]:
    normalized_room, split_platform = split_manual_price_platform_prefix(
        re.sub(r"\s+", " ", str(room_type or "")).strip() or None
    )
    if normalized_room:
        normalized_room = _normalize_room_type(normalized_room)
    normalized_platform = _normalize_manual_price_platform(
        platform or split_platform
    )
    partial = {
        "room_type": normalized_room,
        "platform": normalized_platform,
        "date_label": str(date_label or "").strip() or None,
        "target_price": _safe_int(target_price),
    }
    if partial["date_label"] not in {"today", "tomorrow"}:
        partial["date_label"] = None
    if partial["target_price"] is not None and partial["target_price"] <= 0:
        partial["target_price"] = None
    if partial["room_type"] is not None and len(partial["room_type"]) < 2:
        partial["room_type"] = None

    missing_fields = _missing_manual_price_fields(partial)
    if not missing_fields:
        return {
            "status": "ready",
            "room_type": partial["room_type"],
            "platform": partial.get("platform"),
            "date_label": partial["date_label"],
            "target_price": partial["target_price"],
        }
    return {
        "status": "needs_confirmation",
        "missing_fields": missing_fields,
        "partial": {
            key: partial[key]
            for key in ("room_type", "platform", "date_label", "target_price")
            if partial.get(key) is not None
        },
        "summary": build_manual_price_confirmation_summary(partial, missing_fields),
        "examples": [
            "把今天高级大床房调到420",
            "把明天豪华双床房改到399",
        ],
    }


def _normalize_manual_price_platform(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in _PLATFORM_WORD_TO_KEY.values():
        return normalized
    for word, platform in _PLATFORM_WORD_TO_KEY.items():
        if normalized == word.lower():
            return platform
    return None


def _normalize_room_type(room_type: str | None) -> str | None:
    value = re.sub(r"\s+", " ", str(room_type or "")).strip(" 的")
    value = _PRICE_WORD_SUFFIX_RE.sub("", value).strip(" 的")
    if len(value) < 2:
        return None
    if value in _INVALID_ROOM_TYPES:
        return None
    return value or None


_MANUAL_CONCRETE_DATE_RES = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日"),
)
_MANUAL_ANY_DATE_RE = re.compile(
    rf"(?:{_MANUAL_DATE_TOKENS}|{_MANUAL_DATE_CONCRETE})"
)


def _parse_concrete_date(token: str) -> str | None:
    """把具体日期令牌归一为 ISO YYYY-MM-DD；无年份的按当前年补。

    不合法的月/日（如 13月45日）或超出今天起 15 天窗口的日期（过去/
    太远）返回 None：前者是垃圾输入，后者按缺日期走确认卡，绝不静默
    回退「今天」造成写错日（2026-09-14「9月20日」被写成今天的教训）。
    """
    from datetime import date, timedelta

    for pattern in _MANUAL_CONCRETE_DATE_RES:
        m = pattern.fullmatch(token)
        if not m:
            continue
        groups = m.groupdict()
        year = int(groups["y"]) if "y" in groups and groups.get("y") else date.today().year
        month = int(groups["m"])
        day = int(groups["d"])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        today = date.today()
        if parsed < today or (parsed - today).days > MANUAL_CHECKIN_MAX_DAYS_AHEAD:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _resolve_manual_date(date_token: str) -> tuple[str | None, str | None]:
    """返回 (date_label, checkin_date_iso_or_None)。具体日期给 ISO，label=specified。

    具体日期形状合法但超窗口时返回 (None, None)：调用方按缺日期处理，
    不允许落到「今天」兜底。
    """
    if date_token in _DATE_TODAY:
        return "today", None
    if date_token in _DATE_TOMORROW:
        return "tomorrow", None
    checkin_date = _parse_concrete_date(date_token)
    if checkin_date is not None:
        return "specified", checkin_date
    if re.fullmatch(_MANUAL_DATE_CONCRETE, date_token):
        return None, None
    return "today", None


def _missing_manual_price_fields(partial: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not partial.get("room_type"):
        missing.append("room_type")
    # 日期满足：今天/明天，或已解析出具体入住日 ISO。
    if partial.get("date_label") not in {"today", "tomorrow"} and not partial.get("checkin_date"):
        missing.append("date_label")
    price = partial.get("target_price")
    if not isinstance(price, int) or price <= 0:
        missing.append("target_price")
    return missing


def _strip_mentions(text: str) -> str:
    return re.sub(r"@\S+", "", text or "").strip()


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
