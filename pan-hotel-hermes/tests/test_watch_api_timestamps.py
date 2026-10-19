"""后端落库时间戳口径：北京墙上时间（无时区后缀）不得被当成 UTC。

2026-09-14 实测事故：竞品/EBK run 的 ``captured_at`` 落库为北京墙上时间
（如 ``2026-09-14 09:04:14``）。Hermes 侧曾把无 tzinfo 时间按 UTC 解析再
转北京时间，凭空多 8 小时（早上 09:04 显示成 17:04），模型跟着 SOUL 的
「UTC 需 +8」口径复读，被店长当成幻觉数据。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from client.watch_api import _format_overview_time, _persisted_data_is_stale

_BEIJING = timezone(timedelta(hours=8))


class FormatOverviewTimeTests(unittest.TestCase):
    def test_beijing_naive_timestamp_keeps_wall_time(self) -> None:
        self.assertEqual(_format_overview_time("2026-09-14 09:04:14"), "2026-09-14 09:04")
        self.assertEqual(_format_overview_time("2026-09-14 09:04"), "2026-09-14 09:04")

    def test_utc_suffix_timestamp_converts_to_beijing(self) -> None:
        self.assertEqual(
            _format_overview_time("2026-09-14T01:04:14+00:00"), "2026-09-14 09:04"
        )
        self.assertEqual(_format_overview_time("2026-09-14T01:04:14Z"), "2026-09-14 09:04")

    def test_empty_and_invalid_values_fall_back(self) -> None:
        self.assertEqual(_format_overview_time(None), "—")
        self.assertEqual(_format_overview_time(""), "—")
        self.assertEqual(_format_overview_time("时间未知"), "时间未知")


class PersistedDataStaleTests(unittest.TestCase):
    def test_beijing_naive_captured_at_is_not_double_converted(self) -> None:
        # 关键回归：北京墙上时间 09:04 落库后按北京时区算年龄，不是按 UTC。
        now_beijing_wall = datetime.now(_BEIJING).replace(tzinfo=None)
        five_minutes_ago = (now_beijing_wall - timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        nine_hours_ago = (now_beijing_wall - timedelta(hours=9)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.assertFalse(
            _persisted_data_is_stale(
                captured_at=five_minutes_ago, has_data=True, stale_hours=8
            )
        )
        self.assertTrue(
            _persisted_data_is_stale(
                captured_at=nine_hours_ago, has_data=True, stale_hours=8
            )
        )

    def test_missing_data_or_timestamp_is_stale(self) -> None:
        self.assertTrue(
            _persisted_data_is_stale(captured_at=None, has_data=True, stale_hours=8)
        )
        self.assertTrue(
            _persisted_data_is_stale(
                captured_at="2026-09-14 09:04:14", has_data=False, stale_hours=8
            )
        )


if __name__ == "__main__":
    unittest.main()
