"""EBK 全局推送调度纯逻辑（设计文档 §7.3/§8.1/§8.3/§8.4）。

无 DB 依赖的 due 计算、单一游标推进、退避阶梯、窗口判断与运行状态聚合；
时间一律为本地（Asia/Shanghai）naive datetime，与仓库其他调度域一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

FIXED_SLOT_TOLERANCE_MINUTES = 30
STALE_RUNNING_MINUTES = 45
BACKOFF_LADDER_MINUTES = (1, 2, 5, 10)
MIN_INTERVAL_MINUTES = 30
MAX_INTERVAL_MINUTES = 1440

_SEVERITY_ORDER = (
    "human_required",
    "failed",
    "partial",
    "needs_review",
    "config_required",
    "success",
)
# 序号越大越严重（heaviest 排最前，取 max 时胜出）。
_SEVERITY_RANK = {
    status: len(_SEVERITY_ORDER) - index for index, status in enumerate(_SEVERITY_ORDER)
}


def severity_rank(status: str) -> int:
    """严重度序：human_required > failed > partial > needs_review > config_required > success。"""

    return _SEVERITY_RANK.get(str(status or "").strip().lower(), len(_SEVERITY_ORDER))


def aggregate_run_status(
    *,
    platform_statuses: Iterable[str],
    all_cards_delivered: bool,
) -> str:
    """运行整体状态聚合（§7.2）：取最重平台结果；卡片部分投递失败至少 partial。"""

    statuses = [str(item or "").strip().lower() for item in platform_statuses if item]
    if not statuses:
        return "config_required" if not all_cards_delivered else "success"
    heaviest = max(statuses, key=severity_rank)
    if heaviest == "success" and not all_cards_delivered:
        return "partial"
    return heaviest


def backoff_minutes(consecutive_failures: int, *, interval_minutes: int) -> int:
    """连续失败退避 1/2/5/10 分钟，上限不超过一个配置间隔（§8.3）。"""

    if consecutive_failures <= 0:
        return 0
    index = min(consecutive_failures, len(BACKOFF_LADDER_MINUTES)) - 1
    minutes = BACKOFF_LADDER_MINUTES[index]
    return max(1, min(minutes, interval_minutes))


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hours, minutes = str(text or "").strip().split(":", 1)
        return int(hours), int(minutes)
    except (ValueError, AttributeError):
        return None


def is_within_window(now: datetime, window_start: str, window_end: str) -> bool:
    """推送窗口判断，允许跨午夜（start > end 时窗口跨零点）。"""

    start = _parse_hhmm(window_start)
    end = _parse_hhmm(window_end)
    if start is None or end is None:
        return True
    minutes = now.hour * 60 + now.minute
    start_minutes = start[0] * 60 + start[1]
    end_minutes = end[0] * 60 + end[1]
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= minutes < end_minutes
    return minutes >= start_minutes or minutes < end_minutes


def _today_at(now: datetime, hours: int, minutes: int) -> datetime:
    return now.replace(hour=hours, minute=minutes, second=0, microsecond=0)


def fixed_slot_for_now(
    config: dict[str, Any],
    now: datetime,
    *,
    tolerance_minutes: int = FIXED_SLOT_TOLERANCE_MINUTES,
) -> datetime | None:
    """当前时刻应执行的最近固定槽位；超过容忍窗口返回 None（missed_slot）。"""

    slots: list[tuple[int, int]] = []
    for raw in config.get("push_times") or []:
        parsed = _parse_hhmm(raw)
        if parsed is not None:
            slots.append(parsed)
    best: datetime | None = None
    for hours, minutes in slots:
        slot = _today_at(now, hours, minutes)
        if slot > now:
            continue
        if now - slot > timedelta(minutes=tolerance_minutes):
            continue  # 超容忍窗口：missed_slot，不补发历史卡
        if best is None or slot > best:
            best = slot
    return best


def schedule_key_for_fixed_slot(config_id: int, slot: datetime, slot_hhmm: str) -> str:
    return f"fixed:{config_id}:{slot.strftime('%Y-%m-%d')}:{slot_hhmm}"


def schedule_key_for_interval(config_id: int, now: datetime) -> str:
    return f"interval:{config_id}:{now.strftime('%Y-%m-%dT%H:%M')}"


def interval_due(now: datetime, next_run_at: datetime | None) -> bool:
    return next_run_at is None or now >= next_run_at


def compute_first_run_at(config: dict[str, Any], enabled_at: datetime) -> datetime:
    """间隔模式首跑基准（C3）：窗口内=启用时刻，否则顺延到下一个 window_start。"""

    start = _parse_hhmm(str(config.get("window_start") or ""))
    end = _parse_hhmm(str(config.get("window_end") or ""))
    if start is None or end is None:
        return enabled_at.replace(second=0, microsecond=0)
    if is_within_window(enabled_at, config.get("window_start"), config.get("window_end")):
        return enabled_at.replace(second=0, microsecond=0)
    start_minutes = start[0] * 60 + start[1]
    end_minutes = end[0] * 60 + end[1]
    now_minutes = enabled_at.hour * 60 + enabled_at.minute
    if start_minutes < end_minutes or now_minutes < start_minutes:
        return _today_at(enabled_at, start[0], start[1])
    return _today_at(enabled_at + timedelta(days=1), start[0], start[1])


def compute_next_run_at(
    config: dict[str, Any],
    *,
    finished_at: datetime,
    succeeded: bool,
    consecutive_failures: int = 0,
) -> datetime:
    """单一游标推进（C5）：成功→结束时间+间隔；失败→结束时间+退避。"""

    if succeeded:
        return finished_at + timedelta(minutes=int(config.get("interval_minutes") or 60))
    backoff = backoff_minutes(
        consecutive_failures + 1,
        interval_minutes=int(config.get("interval_minutes") or 60),
    )
    return finished_at + timedelta(minutes=backoff)
