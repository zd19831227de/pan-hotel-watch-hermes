from __future__ import annotations

import importlib
import unittest
from datetime import datetime

from fastapi import HTTPException


class ExportEbkBusinessWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exports = importlib.import_module("app.api.v1.exports")
        self.now = datetime(2026, 8, 24, 10, 30, 45)

    def test_presets_resolve_from_server_local_time(self) -> None:
        resolve = self.exports._resolve_export_window

        self.assertEqual(
            resolve("today", None, None, 180, now=self.now),
            (datetime(2026, 8, 24), None),
        )
        self.assertEqual(
            resolve("week", None, None, 180, now=self.now),
            (datetime(2026, 8, 24), None),
        )
        self.assertEqual(
            resolve("month", None, None, 180, now=self.now),
            (datetime(2026, 8, 1), None),
        )

    def test_custom_uses_exclusive_next_day_upper_bound(self) -> None:
        start_at, end_at = self.exports._resolve_export_window(
            "custom",
            "2026-08-02",
            "2026-08-09",
            180,
            now=self.now,
        )

        self.assertEqual(start_at, datetime(2026, 8, 2))
        self.assertEqual(end_at, datetime(2026, 8, 10))

    def test_days_fallback_preserves_rolling_window(self) -> None:
        start_at, end_at = self.exports._resolve_export_window(
            None,
            None,
            None,
            7,
            now=self.now,
        )

        self.assertEqual(start_at, datetime(2026, 8, 17, 10, 30, 45))
        self.assertIsNone(end_at)

    def test_invalid_range_inputs_return_chinese_422(self) -> None:
        cases = (
            ("custom", None, "2026-08-09", "自定义范围必须同时填写开始日期和结束日期"),
            ("custom", "2026-08-10", "2026-08-09", "开始日期不能晚于结束日期"),
            ("custom", "2026/08/01", "2026-08-09", "日期格式必须为 YYYY-MM-DD"),
            ("quarter", None, None, "不支持的经营数据导出范围"),
        )

        for range_value, start_date, end_date, detail in cases:
            with self.subTest(range_value=range_value, start_date=start_date, end_date=end_date):
                with self.assertRaises(HTTPException) as raised:
                    self.exports._resolve_export_window(
                        range_value,
                        start_date,
                        end_date,
                        180,
                        now=self.now,
                    )
                self.assertEqual(raised.exception.status_code, 422)
                self.assertEqual(raised.exception.detail, detail)


if __name__ == "__main__":
    unittest.main()
