from __future__ import annotations

import unittest

from app.domain.browser.ctrip_public_room_rate_parser import (
    _normalize_public_structured_room_rates,
)
from app.domain.browser.ctrip_room_rate_rules import (
    classify_breakfast_kind,
    normalize_breakfast_text,
    room_type_comparison_key,
)


class PublicRoomRateDiscountTests(unittest.TestCase):
    def test_breakfast_text_normalizes_counted_breakfast_to_clear_labels(self) -> None:
        cases = {
            "1份早餐": "含单早",
            "一份早餐": "含单早",
            "单早": "含单早",
            "2份早餐": "含双早",
            "两份早": "含双早",
            "双早": "含双早",
            "3份早餐": "含三早",
            "三早": "含三早",
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_breakfast_text(raw), expected)
                self.assertEqual(classify_breakfast_kind(raw), "included")
                self.assertEqual(classify_breakfast_kind(expected), "included")

    def test_normalizer_keeps_visible_discount_amount(self) -> None:
        rates = _normalize_public_structured_room_rates(
            [
                {
                    "room_type": "高级大床房",
                    "breakfast": "无早餐",
                    "price": {"amount": 389, "currency": "CNY", "display": "¥389"},
                    "original_price": {"amount": 421, "display": "¥421"},
                    "discount": {"amount": 32, "display": "优惠32"},
                    "source_price_line": "¥389",
                    "source_discount_line": "优惠32",
                    "source_text": (
                        "高级大床房\n无早餐\n入住当天18:00前可免费取消\n"
                        "立即确认\n在线付\n优惠32\n¥421\n¥389\n预订"
                    ),
                    "source": "room_rate_dom_display_price",
                },
                {
                    "room_type": "高级大床房",
                    "breakfast": "1份早餐",
                    "price": {"amount": 425, "currency": "CNY", "display": "¥425"},
                    "original_price": {"amount": 461, "display": "¥461"},
                    "source_price_line": "¥425",
                    "source_text": (
                        "高级大床房\n1份早餐\n入住当天18:00前可免费取消\n"
                        "立即确认\n在线付\n优惠36\n¥461\n¥425\n预订"
                    ),
                    "source": "room_rate_dom_display_price",
                },
            ]
        )

        self.assertEqual(rates[0]["discount_amount_yuan"], 32)
        self.assertEqual(rates[0]["discount_display"], "优惠32")
        self.assertEqual(rates[0]["source_discount_line"], "优惠32")
        self.assertEqual(rates[1]["discount_amount_yuan"], 36)
        self.assertEqual(rates[1]["discount_display"], "优惠36")

    def test_normalizer_keeps_single_no_breakfast_plan_without_included_phantom(self) -> None:
        rates = _normalize_public_structured_room_rates(
            [
                {
                    "room_type": "高级大床房",
                    "breakfast": "无早餐",
                    "price": {"amount": 389, "currency": "CNY", "display": "¥389"},
                    "source_price_line": "¥389",
                    "source_text": "高级大床房\n无早餐\n优惠32\n¥389\n预订",
                    "source": "room_rate_dom_display_price",
                },
            ]
        )

        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["breakfast"], "无早餐")
        self.assertEqual(classify_breakfast_kind(rates[0]["breakfast"]), "none")
        self.assertFalse(
            any(classify_breakfast_kind(rate["breakfast"]) == "included" for rate in rates)
        )

    def test_normalizer_dedupes_two_no_breakfast_rows_without_included_plan(self) -> None:
        rates = _normalize_public_structured_room_rates(
            [
                {
                    "room_type": "高级大床房",
                    "breakfast": "无早餐",
                    "price": {"amount": 409, "currency": "CNY", "display": "¥409"},
                    "source_price_line": "¥409",
                    "source_text": "高级大床房\n无早餐\n优惠20\n¥409\n预订",
                    "source": "room_rate_dom_display_price",
                },
                {
                    "room_type": "高级大床房",
                    "breakfast": "无早",
                    "price": {"amount": 389, "currency": "CNY", "display": "¥389"},
                    "source_price_line": "¥389",
                    "source_text": "高级大床房\n无早\n优惠32\n¥389\n预订",
                    "source": "room_rate_dom_display_price",
                },
            ]
        )

        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["price"]["amount"], 389)
        self.assertEqual(rates[0]["breakfast"], "无早餐")
        self.assertEqual(classify_breakfast_kind(rates[0]["breakfast"]), "none")

    def test_normalizer_keeps_counted_breakfast_plans_distinct(self) -> None:
        rates = _normalize_public_structured_room_rates(
            [
                {
                    "room_type": "家庭套房",
                    "breakfast": "1份早餐",
                    "price": {"amount": 500, "currency": "CNY", "display": "¥500"},
                    "source_price_line": "¥500",
                    "source_text": "家庭套房\n1份早餐\n优惠40\n¥500\n预订",
                    "source": "room_rate_dom_display_price",
                },
                {
                    "room_type": "家庭套房",
                    "breakfast": "2份早餐",
                    "price": {"amount": 540, "currency": "CNY", "display": "¥540"},
                    "source_price_line": "¥540",
                    "source_text": "家庭套房\n2份早餐\n优惠42\n¥540\n预订",
                    "source": "room_rate_dom_display_price",
                },
                {
                    "room_type": "家庭套房",
                    "breakfast": "三早",
                    "price": {"amount": 580, "currency": "CNY", "display": "¥580"},
                    "source_price_line": "¥580",
                    "source_text": "家庭套房\n三早\n优惠45\n¥580\n预订",
                    "source": "room_rate_dom_display_price",
                },
            ]
        )

        self.assertEqual(
            [rate["breakfast"] for rate in rates],
            ["含单早", "含双早", "含三早"],
        )
        self.assertTrue(
            all(classify_breakfast_kind(rate["breakfast"]) == "included" for rate in rates)
        )

    def test_normalizer_does_not_infer_missing_discount_amount(self) -> None:
        rates = _normalize_public_structured_room_rates(
            [
                {
                    "room_type": "高级大床房",
                    "breakfast": "无早餐",
                    "price": {"amount": 389, "currency": "CNY", "display": "¥389"},
                    "original_price": {"amount": 421, "display": "¥421"},
                    "source_price_line": "¥389",
                    "source_text": (
                        "高级大床房\n无早餐\n入住当天18:00前可免费取消\n"
                        "立即确认\n在线付\n¥421\n¥389\n预订"
                    ),
                    "source": "room_rate_dom_display_price",
                },
            ]
        )

        self.assertEqual(rates[0]["price"]["amount"], 389)
        self.assertNotIn("discount_amount_yuan", rates[0])
        self.assertNotIn("discount_display", rates[0])
        self.assertNotIn("source_discount_line", rates[0])

    def test_normalizer_keeps_full_ten_room_two_breakfast_matrix(self) -> None:
        raw_rates = []
        for index in range(10):
            room_type = f"房型{index + 1}大床房"
            raw_rates.extend(
                [
                    {
                        "room_type": room_type,
                        "breakfast": "无早餐",
                        "price": {"amount": 300 + index, "currency": "CNY", "display": f"¥{300 + index}"},
                        "discount": {"amount": 20, "display": "优惠20"},
                        "source_price_line": f"¥{300 + index}",
                        "source_discount_line": "优惠20",
                        "source_text": f"{room_type}\n无早餐\n优惠20\n¥{300 + index}\n预订",
                        "source": "room_rate_dom_display_price",
                    },
                    {
                        "room_type": room_type,
                        "breakfast": "含早餐",
                        "price": {"amount": 340 + index, "currency": "CNY", "display": f"¥{340 + index}"},
                        "discount": {"amount": 20, "display": "优惠20"},
                        "source_price_line": f"¥{340 + index}",
                        "source_discount_line": "优惠20",
                        "source_text": f"{room_type}\n含早餐\n优惠20\n¥{340 + index}\n预订",
                        "source": "room_rate_dom_display_price",
                    },
                ]
            )

        rates = _normalize_public_structured_room_rates(raw_rates)

        self.assertEqual(len(rates), 20)
        self.assertEqual(len({rate["room_type"] for rate in rates}), 10)
        self.assertEqual(
            sum(1 for rate in rates if classify_breakfast_kind(rate["breakfast"]) == "none"),
            10,
        )
        self.assertEqual(
            sum(1 for rate in rates if classify_breakfast_kind(rate["breakfast"]) == "included"),
            10,
        )

    def test_room_type_comparison_key_ignores_breakfast_suffix(self) -> None:
        self.assertEqual(
            room_type_comparison_key("静享景观大床房·双早"),
            room_type_comparison_key("静享景观大床房"),
        )
        self.assertEqual(
            room_type_comparison_key("静享景观大床房含三早"),
            room_type_comparison_key("静享景观大床房"),
        )

    def test_room_type_comparison_key_keeps_meaningful_room_descriptors(self) -> None:
        self.assertNotEqual(
            room_type_comparison_key("尊享景观大床房（无窗）"),
            room_type_comparison_key("尊享景观大床房"),
        )
        self.assertNotEqual(
            room_type_comparison_key("云夕·城景大床房（高楼层）"),
            room_type_comparison_key("云夕·城景大床房"),
        )
        self.assertNotEqual(
            room_type_comparison_key("云夕·逸致标准房丨记忆床垫+无线投屏"),
            room_type_comparison_key("云夕·城景大床房"),
        )
        self.assertNotEqual(
            room_type_comparison_key("云夕丨逸致家庭房"),
            room_type_comparison_key("云夕·城景大床房"),
        )

    def test_room_type_comparison_key_keeps_series_prefix_before_middle_dot(self) -> None:
        twin_key = room_type_comparison_key(
            "云·高空江景双床房（零压床垫+全屋声控+mini冰箱）",
        )
        king_key = room_type_comparison_key("云·高空江景大床房")
        self.assertNotEqual(twin_key, king_key)
        self.assertEqual(twin_key, "云·高空江景双床房(零压床垫+全屋声控+mini冰箱)")
        self.assertEqual(king_key, "云·高空江景大床房")
        self.assertEqual(
            room_type_comparison_key("云·高空江景双床房（零压床垫+全屋声控+mini冰箱）·双早"),
            twin_key,
        )
        self.assertNotEqual(
            room_type_comparison_key("景·一线江景大床房"),
            room_type_comparison_key("云·高空江景大床房"),
        )


if __name__ == "__main__":
    unittest.main()
