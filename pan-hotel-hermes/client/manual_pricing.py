from __future__ import annotations

import re
from typing import Any

# 与 backend manual_price_command 保持一致：指令/参数里房型名开头的平台词
# （携程/美团/飞猪）单独识别为调价平台，避免污染房型名。
_PLATFORM_WORD_TO_KEY: dict[str, str] = {"携程": "ctrip", "美团": "meituan", "飞猪": "fliggy"}
_PLATFORM_KEY_TO_WORD: dict[str, str] = {value: key for key, value in _PLATFORM_WORD_TO_KEY.items()}
_PLATFORM_PREFIX_RE = re.compile(r"^(携程|美团|飞猪)(?:上|平台|渠道|端|账号)?的?")
# 与 backend manual_price_command 保持一致：房型名尾部的价格词是口语修饰，
# 不是房型名的一部分，进入 EBK 房型匹配前必须剥掉。
_PRICE_WORD_SUFFIX_RE = re.compile(r"(?:的)?(?:价格|房价|售价|卖价)$")


def _strip_price_word_suffix(room_type: str | None) -> str | None:
    text = str(room_type or "").strip()
    if not text:
        return room_type
    stripped = _PRICE_WORD_SUFFIX_RE.sub("", text).strip(" 的")
    # 剥完为空说明整个值只是价格词（如「价格」），不是有效房型名。
    return stripped or None


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


def manual_price_platform_word(platform: str | None) -> str | None:
    normalized = _normalize_platform(platform)
    return _PLATFORM_KEY_TO_WORD.get(normalized)


def _normalize_platform(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in _PLATFORM_WORD_TO_KEY.values():
        return normalized
    for word, platform in _PLATFORM_WORD_TO_KEY.items():
        if normalized == word.lower():
            return platform
    return None


def validate_manual_pricing_fields(
    *,
    room_type: str,
    date_label: str,
    target_price: int,
    platform: str | None = None,
    checkin_date: str | None = None,
) -> dict[str, Any]:
    normalized_room, split_platform = split_manual_price_platform_prefix(
        re.sub(r"\s+", " ", str(room_type or "")).strip() or None
    )
    if normalized_room:
        normalized_room = _strip_price_word_suffix(normalized_room)
    normalized_platform = _normalize_platform(platform or split_platform)
    normalized_checkin = str(checkin_date or "").strip() or None
    partial = {
        "room_type": normalized_room,
        "platform": normalized_platform or None,
        "date_label": str(date_label or "").strip() or None,
        "checkin_date": normalized_checkin,
        "target_price": _safe_int(target_price),
    }
    if partial["date_label"] not in {"today", "tomorrow"} and not normalized_checkin:
        partial["date_label"] = None
    if partial["target_price"] is not None and partial["target_price"] <= 0:
        partial["target_price"] = None
    if partial["room_type"] is not None and len(partial["room_type"]) < 2:
        partial["room_type"] = None

    missing_fields = _missing_manual_price_fields(partial)
    if not missing_fields:
        ready = {
            "status": "ready",
            "room_type": partial["room_type"],
            "platform": partial.get("platform"),
            "date_label": partial["date_label"],
            "target_price": partial["target_price"],
        }
        if normalized_checkin:
            ready["checkin_date"] = normalized_checkin
        return ready
    return {
        "status": "needs_confirmation",
        "missing_fields": missing_fields,
        "partial": {
            key: partial[key]
            for key in ("room_type", "platform", "date_label", "checkin_date", "target_price")
            if partial.get(key) is not None
        },
        "summary": _build_summary(partial, missing_fields),
        "examples": [
            "把今天高级大床房调到420",
            "把明天豪华双床房改到399",
            "把2026-09-20携程奢华三卧套房调到1013",
        ],
    }


def _missing_manual_price_fields(partial: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not partial.get("room_type"):
        missing.append("room_type")
    if partial.get("date_label") not in {"today", "tomorrow"} and not partial.get("checkin_date"):
        missing.append("date_label")
    price = partial.get("target_price")
    if not isinstance(price, int) or price <= 0:
        missing.append("target_price")
    return missing


def _build_summary(partial: dict[str, Any], missing_fields: list[str]) -> str:
    labels = {
        "room_type": "房型",
        "date_label": "入住日期（今天/明天或具体日期）",
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


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
