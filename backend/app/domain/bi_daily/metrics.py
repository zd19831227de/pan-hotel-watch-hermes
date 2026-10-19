"""BI 计算层：pickup 与三类完成率。

铁律：缺数返回 ``unavailable`` + 原因码，绝不补零；只有真实的 0 实际值
才输出 0.0 完成率（0 是事实，不是缺失）。
"""

from __future__ import annotations

STATUS_AVAILABLE = "available"
STATUS_UNAVAILABLE = "unavailable"

# 未来在手预订（OTB）当前无数据源：pickup 缺数的固定原因码。
REASON_NO_OTB_SOURCE = "no_on_the_books_source"

_BASELINE_KINDS = ("prior_year", "budget", "forecast")


def pickup(
    *,
    current_booked: int | None,
    prior_booked: int | None,
    reason_when_missing: str = REASON_NO_OTB_SOURCE,
) -> dict[str, object]:
    """两个 as-of 快照的在手量之差；任一侧缺失即 unavailable。"""
    if current_booked is None or prior_booked is None:
        return {"value": None, "status": STATUS_UNAVAILABLE, "reason": reason_when_missing}
    return {"value": current_booked - prior_booked, "status": STATUS_AVAILABLE, "reason": None}


def completion_rate(
    *,
    actual: float | None,
    baseline: float | None,
    baseline_kind: str,
) -> dict[str, object]:
    """完成率 = actual / baseline。baseline_kind ∈ {prior_year, budget, forecast}。"""
    if baseline_kind not in _BASELINE_KINDS:
        raise ValueError(f"未知基线类型：{baseline_kind}")
    if actual is None:
        return {
            "value": None,
            "status": STATUS_UNAVAILABLE,
            "reason": "actual_missing",
        }
    if baseline is None:
        return {
            "value": None,
            "status": STATUS_UNAVAILABLE,
            "reason": f"{baseline_kind}_baseline_missing",
        }
    if baseline == 0:
        return {
            "value": None,
            "status": STATUS_UNAVAILABLE,
            "reason": f"{baseline_kind}_baseline_zero",
        }
    return {"value": actual / baseline, "status": STATUS_AVAILABLE, "reason": None}
