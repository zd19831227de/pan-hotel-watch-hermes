"""去年同期同星期映射：只做日期算术，不做任何业务推断。

规则（权威口径见 docs/superpowers/plans/2026-10-08-bi-daily-report.md §1.2）：
- 优先 ``D - 364 天``（整 52 周，星期必然相同）；结果落在去年即为命中。
- 跨年不命中（每年 12 月最后几天）时取 ``D - 371 天``（整 53 周）。
- 两条都不落在去年时（数学上不会发生）返回 none 并给 fallback_reason。
- 不使用「去年同月同日」；holiday_flag 无节假日数据源，恒 None。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

RULE_MINUS_364 = "minus_364"
RULE_MINUS_371 = "minus_371"
RULE_NONE = "none"

_FALLBACK_NO_CANDIDATE = "no_same_weekday_date_in_prior_year"


@dataclass(frozen=True)
class WeekdayDateMappingSpec:
    current_date: date
    current_weekday: int
    prior_year_date: date | None
    matching_rule: str
    fallback_reason: str | None


def map_prior_year_same_weekday(current: date) -> WeekdayDateMappingSpec:
    prior_year = current.year - 1
    for rule, delta in ((RULE_MINUS_364, timedelta(days=364)), (RULE_MINUS_371, timedelta(days=371))):
        candidate = current - delta
        if candidate.year == prior_year:
            return WeekdayDateMappingSpec(
                current_date=current,
                current_weekday=current.isoweekday(),
                prior_year_date=candidate,
                matching_rule=rule,
                fallback_reason=None,
            )
    return WeekdayDateMappingSpec(
        current_date=current,
        current_weekday=current.isoweekday(),
        prior_year_date=None,
        matching_rule=RULE_NONE,
        fallback_reason=_FALLBACK_NO_CANDIDATE,
    )


def build_weekday_mappings_for_range(start: date, end: date) -> list[WeekdayDateMappingSpec]:
    if end < start:
        return []
    specs: list[WeekdayDateMappingSpec] = []
    cursor = start
    while cursor <= end:
        specs.append(map_prior_year_same_weekday(cursor))
        cursor += timedelta(days=1)
    return specs
