from __future__ import annotations

import unittest

from app.domain.calendar_price_watch.cards import (
    build_calendar_price_alert_fallback_text,
    build_calendar_price_alert_feishu_card,
    build_calendar_price_matrix_feishu_cards,
)


class CalendarPriceCardTests(unittest.TestCase):
    def test_card_shows_only_high_alerts_without_execution_actions(self) -> None:
        result = {
            "status": "alert",
            "summary": "未来15天有1个日期触发日历价预警。",
            "future_days": 15,
            "alerts": [
                {
                    "alert_type": "own_price_high",
                    "checkin_date": "2026-07-08",
                    "own_price_yuan": 420,
                    "reference_price_yuan": 410,
                    "gap_yuan": 10,
                    "reference_hotel_alias": "竞对A",
                },
            ],
        }

        card = build_calendar_price_alert_feishu_card(result, title="日历价监测")
        text = str(card)
        elements = card["elements"]
        alert_lines = [
            element["text"]["content"]
            for element in elements
            if element.get("tag") == "div" and "2026-07-0" in element["text"]["content"]
        ]

        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("**本店偏高**", text)
        self.assertNotIn("本店明显低于竞对", text)
        self.assertNotIn("打开控制台查看", text)
        self.assertEqual(len(alert_lines), 1)
        self.assertIn("**2026-07-08**", alert_lines[0])
        self.assertIn("<font color='red'>", alert_lines[0])
        for forbidden in ["确认调价", "写入携程", "自动执行", "decision=approve", "通过", "拒绝"]:
            self.assertNotIn(forbidden, text)

    def test_card_shows_low_alerts_as_manual_review_only(self) -> None:
        result = {
            "status": "alert",
            "summary": "未来15天有1个日期触发日历价预警。",
            "future_days": 15,
            "alerts": [
                {
                    "alert_type": "own_price_low",
                    "checkin_date": "2026-07-09",
                    "own_price_yuan": 360,
                    "reference_price_yuan": 390,
                    "gap_yuan": 30,
                    "reference_hotel_alias": "竞对A",
                },
            ],
        }

        card = build_calendar_price_alert_feishu_card(result, title="日历价监测")
        text = str(card)

        self.assertIn("本店明显低于竞对", text)
        self.assertIn("**2026-07-09**", text)
        self.assertIn("<font color='green'>", text)
        self.assertIn("人工查看", text)
        for forbidden in ["确认调价", "写入携程", "自动执行", "decision=approve", "通过", "拒绝", "建议涨价"]:
            self.assertNotIn(forbidden, text)

    def test_fallback_text_mentions_only_manual_review(self) -> None:
        fallback = build_calendar_price_alert_fallback_text(
            {
                "summary": "未来15天有1个日期触发日历价预警。",
                "alerts": [{"checkin_date": "2026-07-08", "alert_type": "own_price_high"}],
            }
        )

        self.assertIn("人工查看", fallback)
        self.assertNotIn("调价", fallback)

    def test_matrix_brief_uses_short_alias_legend_and_header(self) -> None:
        matrix = {
            "future_days": 2,
            "own_short_alias": "本店",
            "competitor_hotels": [
                {
                    "alias": "泉吉云州澜江路步行街酒店",
                    "short_alias": "步行街店",
                },
                {
                    "alias": "泉吉云州澜江路步行街云滩江景酒店",
                    "short_alias": "江景店",
                },
            ],
            "dates": [
                {
                    "checkin_date": "2026-07-09",
                    "own": {
                        "hotel_alias": "泉吉云州澜江路步行街高空江景酒店",
                        "price_amount_yuan": 432,
                        "status": "success",
                    },
                    "competitors": [
                        {
                            "hotel_alias": "泉吉云州澜江路步行街酒店",
                            "price_amount_yuan": 428,
                            "status": "success",
                        },
                        {
                            "hotel_alias": "泉吉云州澜江路步行街云滩江景酒店",
                            "price_amount_yuan": 450,
                            "status": "success",
                        },
                    ],
                    "alerts": [],
                },
                {
                    "checkin_date": "2026-07-10",
                    "own": {
                        "hotel_alias": "泉吉云州澜江路步行街高空江景酒店",
                        "price_amount_yuan": 482,
                        "status": "success",
                    },
                    "competitors": [
                        {
                            "hotel_alias": "泉吉云州澜江路步行街酒店",
                            "price_amount_yuan": 470,
                            "status": "success",
                        },
                    ],
                    "alerts": [{"alert_type": "own_price_high"}],
                },
            ],
            "run": {"captured_at": "2026-07-09T10:00:00+08:00", "status": "success"},
        }
        cards = build_calendar_price_matrix_feishu_cards(
            matrix,
            title="澜江路泉吉酒店日历价矩阵",
            slot_label="手动矩阵简报",
        )
        text = str(cards)
        table = next(
            element for element in cards[0]["elements"] if element.get("tag") == "table"
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["header"]["template"], "blue")
        self.assertTrue(table.get("freeze_first_column"))
        self.assertEqual(table.get("page_size"), 2)
        self.assertEqual(table.get("row_height"), "low")
        self.assertIn("步行街店", text)
        self.assertIn("江景店", text)
        self.assertIn("泉吉云州澜江路步行街酒店", text)
        self.assertIn("泉吉云州澜江路步行街云滩江景酒店", text)
        self.assertIn("<font color='blue'>", text)
        self.assertIn("<font color='turquoise'>", text)
        self.assertNotIn("<font color='orange'>", text)
        self.assertNotIn("collapsible_panel", text)
        self.assertIn("左右滑动查看", text)
        self.assertIn("07-09", str(table["rows"]))
        self.assertIn("**¥432**", str(table["rows"]))
        self.assertIn("待复核", str(table["rows"]))
        self.assertIn("<font color='red'>**¥482**</font>", str(table["rows"]))
        self.assertNotIn("竞对1", text)
        self.assertNotIn("竞对2", text)
        self.assertNotIn("竞对酒店", text)
        self.assertNotIn("<font color='turquoise'>¥", text)
        self.assertNotIn("<font color='green'>¥", text)
        for forbidden in ["确认调价", "写入携程", "自动执行", "decision=approve", "通过", "拒绝"]:
            self.assertNotIn(forbidden, text)

    def test_matrix_brief_colors_own_price_low_green(self) -> None:
        matrix = {
            "future_days": 1,
            "own_short_alias": "本店",
            "competitor_hotels": [{"alias": "竞对B", "short_alias": "步行街店"}],
            "dates": [
                {
                    "checkin_date": "2026-07-09",
                    "own": {"hotel_alias": "本店A", "price_amount_yuan": 360, "status": "success"},
                    "competitors": [
                        {"hotel_alias": "竞对B", "price_amount_yuan": 400, "status": "success"},
                    ],
                    "alerts": [{"alert_type": "own_price_low"}],
                }
            ],
            "run": {"captured_at": "2026-07-09T10:00:00+08:00"},
        }
        cards = build_calendar_price_matrix_feishu_cards(
            matrix, title="日历价矩阵", slot_label="早间简报"
        )
        text = str(cards)
        self.assertEqual(len(cards), 1)
        self.assertIn("<font color='green'>**¥360**</font>", text)
        self.assertIn("**¥400**", text)
        self.assertIn("步行街店", text)
        self.assertNotIn("竞对1", text)

    def test_matrix_brief_empty_dates_still_single_card(self) -> None:
        cards = build_calendar_price_matrix_feishu_cards(
            {"future_days": 15, "dates": []},
            title="日历价矩阵",
            slot_label="晚间简报",
        )
        self.assertEqual(len(cards), 1)
        self.assertIn("未读取到", str(cards))

    def test_matrix_brief_comparison_uses_scrollable_table(self) -> None:
        matrix = {
            "future_days": 2,
            "own_short_alias": "本店",
            "competitor_hotels": [
                {"alias": "泉吉云州澜江路步行街酒店", "short_alias": "步行街店"},
                {"alias": "泉吉云州澜江路步行街云滩江景酒店", "short_alias": "云滩店"},
            ],
            "dates": [
                {
                    "checkin_date": "2026-07-09",
                    "own": {
                        "hotel_alias": "泉吉云州澜江路步行街高空江景酒店",
                        "price_amount_yuan": 432,
                        "status": "success",
                    },
                    "competitors": [
                        {
                            "hotel_alias": "泉吉云州澜江路步行街酒店",
                            "price_amount_yuan": 432,
                            "status": "success",
                        },
                        {
                            "hotel_alias": "泉吉云州澜江路步行街云滩江景酒店",
                            "price_amount_yuan": 450,
                            "status": "success",
                        },
                    ],
                    "alerts": [],
                },
                {
                    "checkin_date": "2026-07-10",
                    "own": {
                        "hotel_alias": "泉吉云州澜江路步行街高空江景酒店",
                        "price_amount_yuan": 482,
                        "status": "success",
                    },
                    "competitors": [
                        {
                            "hotel_alias": "泉吉云州澜江路步行街酒店",
                            "price_amount_yuan": 482,
                            "status": "success",
                        },
                        {
                            "hotel_alias": "泉吉云州澜江路步行街云滩江景酒店",
                            "price_amount_yuan": 515,
                            "status": "success",
                        },
                    ],
                    "alerts": [],
                },
            ],
            "run": {"captured_at": "2026-07-09T10:00:00+08:00"},
        }
        cards = build_calendar_price_matrix_feishu_cards(
            matrix, title="日历价矩阵", slot_label="早间简报"
        )
        table = next(
            element for element in cards[0]["elements"] if element.get("tag") == "table"
        )
        self.assertNotIn("collapsible_panel", str(cards))
        self.assertIn("左右滑动查看", str(cards))
        self.assertTrue(table.get("freeze_first_column"))
        self.assertEqual(table.get("page_size"), 2)
        self.assertEqual(table.get("row_height"), "low")
        columns = table["columns"]
        self.assertEqual(columns[0]["display_name"], "日期")
        display_names = [col["display_name"] for col in columns]
        self.assertIn("本店", display_names)
        self.assertIn("步行街店", display_names)
        self.assertIn("云滩店", display_names)
        self.assertEqual(len(table["rows"]), 2)
        first = table["rows"][0]
        self.assertEqual(first["date"], "07-09")
        self.assertIn("¥432", str(first))
        self.assertIn("¥450", str(first))

    def test_matrix_brief_fallback_short_alias_uses_last_six_chars(self) -> None:
        matrix = {
            "future_days": 1,
            "competitor_hotels": [
                {
                    "alias": "泉吉云州澜江路步行街云滩江景酒店",
                    "short_alias": None,
                }
            ],
            "dates": [
                {
                    "checkin_date": "2026-07-09",
                    "own": {
                        "hotel_alias": "泉吉云州澜江路步行街高空江景酒店",
                        "price_amount_yuan": 432,
                        "status": "success",
                    },
                    "competitors": [
                        {
                            "hotel_alias": "泉吉云州澜江路步行街云滩江景酒店",
                            "price_amount_yuan": 450,
                            "status": "success",
                        }
                    ],
                    "alerts": [],
                }
            ],
            "run": {"captured_at": "2026-07-09T10:00:00+08:00"},
        }
        text = str(
            build_calendar_price_matrix_feishu_cards(
                matrix, title="日历价矩阵", slot_label="早间简报"
            )
        )
        self.assertIn("云滩江景酒店", text)
        self.assertNotIn("竞对1", text)
        # Fallback uses the last 6 chars, not the leading prefix.
        self.assertNotIn(">泉吉云州<", text)


if __name__ == "__main__":
    unittest.main()
