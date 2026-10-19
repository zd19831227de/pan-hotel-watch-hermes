from __future__ import annotations

import unittest

from app.domain.notifications.manual_price_command import (
    classify_manual_price_adjustment_command,
    validate_manual_pricing_fields,
)


class ManualPriceCommandTests(unittest.TestCase):
    def test_complete_command_is_ready(self) -> None:
        result = classify_manual_price_adjustment_command("把今天高级大床房调到420")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "高级大床房")
        self.assertEqual(result["date_label"], "today")
        self.assertEqual(result["target_price"], 420)

    def test_platform_prefix_is_stripped_from_room_type(self) -> None:
        result = classify_manual_price_adjustment_command("把明天携程奢华三卧套房调整到1100元")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["platform"], "ctrip")
        self.assertEqual(result["date_label"], "tomorrow")
        self.assertEqual(result["target_price"], 1100)

    def test_platform_prefix_supported_for_all_platforms(self) -> None:
        for word, platform in (("携程", "ctrip"), ("美团", "meituan"), ("飞猪", "fliggy")):
            result = classify_manual_price_adjustment_command(
                f"把今天{word}的江景大床房调到520"
            )
            self.assertEqual(result["status"], "ready", word)
            self.assertEqual(result["room_type"], "江景大床房", word)
            self.assertEqual(result["platform"], platform, word)

    def test_platform_word_alone_is_not_a_room_type(self) -> None:
        result = classify_manual_price_adjustment_command("把明天携程调到1100")
        self.assertNotEqual(result.get("status"), "ready")

    def test_price_word_suffix_is_stripped_from_room_type(self) -> None:
        # 2026-09-14 实测指令「把今天奢华三卧套房价格调到1013」曾把「价格」
        # 带进房型名，导致 EBK 房型弹窗按全串包含匹配失败。
        result = classify_manual_price_adjustment_command("把今天奢华三卧套房价格调到1013")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["target_price"], 1013)

    def test_price_word_suffix_variants_are_stripped(self) -> None:
        cases = {
            "把今天高级大床房的价格调到420": "高级大床房",
            "把明天江景双床房房价调到399": "江景双床房",
            "把今天豪华套房售价调到588": "豪华套房",
            "把今天亲子房卖价调到268": "亲子房",
        }
        for command, expected in cases.items():
            result = classify_manual_price_adjustment_command(command)
            self.assertEqual(result["status"], "ready", command)
            self.assertEqual(result["room_type"], expected, command)

    def test_price_word_alone_is_not_a_room_type(self) -> None:
        result = classify_manual_price_adjustment_command("把今天价格调到420")
        self.assertNotEqual(result.get("status"), "ready")

    def test_room_name_ending_with_price_like_word_kept_when_unrelated(self) -> None:
        # 「特价房」「钟点房」等真实房型词不受尾部剥离影响。
        result = classify_manual_price_adjustment_command("把今天特价房调到168")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "特价房")

    def test_specified_month_day_date_is_parsed_not_merged_into_room(self) -> None:
        # 2026-09-14 实测：指定日期曾整个被当成房型名。
        result = classify_manual_price_adjustment_command("把9月15日奢华三卧套房调到1013")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["target_price"], 1013)
        self.assertEqual(result["checkin_date"], "2026-09-15")

    def test_specified_iso_date_is_parsed(self) -> None:
        result = classify_manual_price_adjustment_command("把2026-09-15奢华三卧套房调到1013")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["checkin_date"], "2026-09-15")

    def test_bare_month_dash_not_misparsed_as_date(self) -> None:
        # 裸 M-D（9-15）会与「2-3人房」等房型名撞车，不支持；应回退到待确认，
        # 而不是把 9-15 当成日期、把房型截断。
        result = classify_manual_price_adjustment_command("把9-15奢华三卧套房调到1013")
        self.assertNotEqual(result.get("status"), "ready")

    def test_today_tomorrow_still_resolve_to_date_label_without_checkin_date(self) -> None:
        today = classify_manual_price_adjustment_command("把今天奢华三卧套房调到1013")
        self.assertEqual(today["date_label"], "today")
        self.assertNotIn("checkin_date", today)
        tomorrow = classify_manual_price_adjustment_command("把明天奢华三卧套房调到1013")
        self.assertEqual(tomorrow["date_label"], "tomorrow")
        self.assertNotIn("checkin_date", tomorrow)

    def test_partial_command_strips_platform_prefix(self) -> None:
        result = classify_manual_price_adjustment_command("把携程高级大床房调到420")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("date_label", result["missing_fields"])
        self.assertEqual(result["partial"]["room_type"], "高级大床房")
        self.assertEqual(result["partial"]["platform"], "ctrip")
        self.assertEqual(result["partial"]["target_price"], 420)

    def test_missing_date_needs_confirmation(self) -> None:
        result = classify_manual_price_adjustment_command("把高级大床房调到420")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("date_label", result["missing_fields"])
        self.assertEqual(result["partial"]["room_type"], "高级大床房")
        self.assertEqual(result["partial"]["target_price"], 420)

    def test_missing_room_needs_confirmation(self) -> None:
        result = classify_manual_price_adjustment_command("把今天调到420")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("room_type", result["missing_fields"])

    def test_missing_price_needs_confirmation(self) -> None:
        result = classify_manual_price_adjustment_command("把今天高级大床房调价")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("target_price", result["missing_fields"])

    def test_unrelated_command_is_not_manual_adjustment(self) -> None:
        result = classify_manual_price_adjustment_command("今天竞品价格怎么样")
        self.assertEqual(result["status"], "not_manual_adjustment")

    def test_specified_date_within_range_is_ready(self) -> None:
        from datetime import date, timedelta

        target = date.today() + timedelta(days=6)
        text = f"把{target.isoformat()}携程奢华三卧套房调到1013元"
        result = classify_manual_price_adjustment_command(text)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["date_label"], "specified")
        self.assertEqual(result["checkin_date"], target.isoformat())

    def test_specified_date_beyond_15_days_asks_for_date(self) -> None:
        """超出 15 天窗口的具体日期不得静默回退成「今天」写价。"""

        from datetime import date, timedelta

        far = date.today() + timedelta(days=30)
        text = f"把{far.isoformat()}携程奢华三卧套房调到1013元"
        result = classify_manual_price_adjustment_command(text)
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("date_label", result["missing_fields"])
        self.assertNotIn("checkin_date", result.get("partial") or {})

    def test_specified_past_date_asks_for_date(self) -> None:
        from datetime import date, timedelta

        past = date.today() - timedelta(days=1)
        text = f"把{past.isoformat()}携程奢华三卧套房调到1013元"
        result = classify_manual_price_adjustment_command(text)
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("date_label", result["missing_fields"])

    def test_validate_manual_pricing_fields_rejects_guessed_values(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="",
            date_label="today",
            target_price=0,
        )
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("room_type", result["missing_fields"])
        self.assertIn("target_price", result["missing_fields"])

    def test_validate_manual_pricing_fields_strips_platform(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="携程奢华三卧套房",
            date_label="tomorrow",
            target_price=1100,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["platform"], "ctrip")

    def test_validate_manual_pricing_fields_strips_price_word_suffix(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="奢华三卧套房价格",
            date_label="today",
            target_price=1013,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")

    def test_validate_manual_pricing_fields_explicit_platform(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="奢华三卧套房",
            date_label="tomorrow",
            target_price=1100,
            platform="美团",
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["platform"], "meituan")

    def test_validate_manual_pricing_fields_rejects_unknown_platform(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="奢华三卧套房",
            date_label="tomorrow",
            target_price=1100,
            platform="qunar",
        )
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["platform"])


if __name__ == "__main__":
    unittest.main()
