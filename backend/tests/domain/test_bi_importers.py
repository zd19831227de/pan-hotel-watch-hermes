"""预算/forecast 导入解析测试：CSV/XLSX、列头别名、校验拒绝。"""

from __future__ import annotations

import unittest
from datetime import date

from app.domain.bi_daily.importers import parse_budget_forecast_bytes


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8-sig")


class BudgetForecastImportTest(unittest.TestCase):
    def test_csv_with_chinese_headers_parses(self):
        payload = _csv_bytes(
            "入住日期,房型,预算间夜,预算收入,预测间夜\n"
            "2026-10-10,高级大床房,50,12000.5,48\n"
            "2026-10-11,,60,15000,55\n"
        )
        parsed = parse_budget_forecast_bytes(payload, filename="budget.csv")
        self.assertEqual(parsed.rows_total, 2)
        self.assertEqual(parsed.rows_valid_count, 2)
        self.assertEqual(parsed.errors, [])
        first = parsed.rows[0]
        self.assertEqual(first.stay_date, date(2026, 10, 10))
        self.assertEqual(first.room_type, "高级大床房")
        self.assertEqual(first.budget_room_nights, 50)
        self.assertAlmostEqual(first.budget_revenue, 12000.5)
        self.assertIsNone(first.budget_adr)
        self.assertEqual(first.demand_forecast_room_nights, 48)
        self.assertIsNone(first.demand_forecast_revenue)
        # 房型留空 = 全店口径（归一为空串）。
        self.assertEqual(parsed.rows[1].room_type, "")

    def test_english_headers_and_extra_columns(self):
        payload = _csv_bytes(
            "stay_date,room_type,budget_room_nights,budget_revenue,备注\n"
            "2026-10-10,Deluxe,40,9000,忽略我\n"
        )
        parsed = parse_budget_forecast_bytes(payload, filename="b.csv")
        self.assertEqual(parsed.rows_valid_count, 1)
        self.assertEqual(parsed.rows[0].budget_room_nights, 40)

    def test_xlsx_roundtrip(self):
        from app.domain.exports.xlsx_builder import build_xlsx_bytes

        payload = build_xlsx_bytes(
            headers=["入住日期", "房型", "预算间夜", "预测收入"],
            rows=[["2026-10-10", "套房", 55, 13000.0]],
        )
        parsed = parse_budget_forecast_bytes(payload, filename="budget.xlsx")
        self.assertEqual(parsed.rows_valid_count, 1)
        row = parsed.rows[0]
        self.assertEqual(row.stay_date, date(2026, 10, 10))
        self.assertEqual(row.room_type, "套房")
        self.assertEqual(row.budget_room_nights, 55)
        self.assertAlmostEqual(row.demand_forecast_revenue, 13000.0)

    def test_invalid_rows_rejected_with_reasons(self):
        payload = _csv_bytes(
            "入住日期,房型,预算间夜\n"
            "2026-13-99,房,10\n"      # 非法日期
            "2026-10-10,房,-5\n"      # 负数
            "2026-10-12,房,abc\n"     # 非数值
        )
        parsed = parse_budget_forecast_bytes(payload, filename="b.csv")
        self.assertEqual(parsed.rows_total, 3)
        self.assertEqual(parsed.rows_valid_count, 0)
        self.assertEqual(len(parsed.errors), 3)
        self.assertTrue(any("日期" in err for err in parsed.errors))
        self.assertTrue(any("负数" in err or "非负" in err for err in parsed.errors))
        self.assertTrue(any("数值" in err for err in parsed.errors))

    def test_row_without_any_value_column_rejected(self):
        payload = _csv_bytes("入住日期,房型\n2026-10-10,房\n")
        parsed = parse_budget_forecast_bytes(payload, filename="b.csv")
        self.assertEqual(parsed.rows_valid_count, 0)
        self.assertTrue(any("未提供任何预算/预测数值" in err for err in parsed.errors))

    def test_missing_stay_date_column_is_fatal(self):
        payload = _csv_bytes("房型,预算间夜\n房,10\n")
        with self.assertRaises(ValueError) as ctx:
            parse_budget_forecast_bytes(payload, filename="b.csv")
        self.assertIn("入住日期", str(ctx.exception))

    def test_errors_bounded(self):
        lines = ["入住日期,房型,预算间夜"] + [f"bad-date-{i},房,{i}" for i in range(30)]
        parsed = parse_budget_forecast_bytes(_csv_bytes("\n".join(lines)), filename="b.csv")
        self.assertEqual(len(parsed.errors), 20)

    def test_unsupported_extension_rejected(self):
        with self.assertRaises(ValueError):
            parse_budget_forecast_bytes(b"{}", filename="budget.json")


if __name__ == "__main__":
    unittest.main()
