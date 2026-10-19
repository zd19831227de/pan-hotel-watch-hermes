from __future__ import annotations

import unittest

from client.feishu_cards import attach_feishu_card
from client.manual_pricing import (
    manual_price_platform_word,
    split_manual_price_platform_prefix,
    validate_manual_pricing_fields,
)
from client.watch_api import build_manual_pricing_feishu_payload


class ManualPricingClientTests(unittest.TestCase):
    def test_validation_attaches_confirmation_card(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="高级大床房",
            date_label="today",
            target_price=0,
        )
        payload = attach_feishu_card(
            {"status": result["status"], "summary": result["summary"]},
            "manual_price_confirmation",
        )
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(payload["feishu_card_kind"], "manual_price_confirmation")
        self.assertIn("请补充调价信息", str(payload["feishu_card"]))

    def test_validation_strips_platform_prefix_from_room(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="携程奢华三卧套房",
            date_label="tomorrow",
            target_price=1100,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["room_type"], "奢华三卧套房")
        self.assertEqual(result["platform"], "ctrip")

    def test_validation_strips_price_word_suffix_from_room(self) -> None:
        # 与 backend manual_price_command 一致：房型名后的价格词是口语修饰，
        # 带进 EBK 房型匹配会失败。
        for suffix in ("价格", "房价", "售价", "卖价", "的价格"):
            result = validate_manual_pricing_fields(
                room_type=f"奢华三卧套房{suffix}",
                date_label="today",
                target_price=1013,
            )
            self.assertEqual(result["status"], "ready", suffix)
            self.assertEqual(result["room_type"], "奢华三卧套房", suffix)

    def test_price_word_alone_is_not_a_room_type(self) -> None:
        result = validate_manual_pricing_fields(
            room_type="价格",
            date_label="today",
            target_price=1013,
        )
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("room_type", result["missing_fields"])

    def test_split_platform_prefix_variants(self) -> None:
        self.assertEqual(
            split_manual_price_platform_prefix("携程奢华三卧套房"),
            ("奢华三卧套房", "ctrip"),
        )
        self.assertEqual(
            split_manual_price_platform_prefix("美团上的江景双床房"),
            ("江景双床房", "meituan"),
        )
        self.assertEqual(
            split_manual_price_platform_prefix("飞猪的特惠大床房"),
            ("特惠大床房", "fliggy"),
        )
        self.assertEqual(
            split_manual_price_platform_prefix("高级大床房"),
            ("高级大床房", None),
        )

    def test_manual_price_platform_word(self) -> None:
        self.assertEqual(manual_price_platform_word("ctrip"), "携程")
        self.assertEqual(manual_price_platform_word("美团"), "美团")
        self.assertIsNone(manual_price_platform_word("qunar"))

    def test_build_payload_embeds_platform_word(self) -> None:
        import json

        payload = build_manual_pricing_feishu_payload(
            room_type="奢华三卧套房",
            date_label="tomorrow",
            target_price=1100,
            chat_id="oc_test",
            message_id="om_test",
            open_id=None,
            platform="ctrip",
        )
        text = json.loads(payload["event"]["message"]["content"])["text"]
        self.assertEqual(text, "把明天携程奢华三卧套房调到1100")

    def test_build_payload_without_platform_keeps_room_only(self) -> None:
        import json

        payload = build_manual_pricing_feishu_payload(
            room_type="奢华三卧套房",
            date_label="today",
            target_price=1100,
            chat_id="oc_test",
            message_id="om_test",
            open_id=None,
        )
        text = json.loads(payload["event"]["message"]["content"])["text"]
        self.assertEqual(text, "把今天奢华三卧套房调到1100")


if __name__ == "__main__":
    unittest.main()
