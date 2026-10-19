from __future__ import annotations

import importlib
import unittest

# 实测样本来自 docs/pan-hotel-watch-three-platform-pricing-write-verification-2026-08-24.md
# 附表：飞猪/美团页面的搜索框、复选框、日期框与真实价格框。
FLIGGY_PAGE_INPUTS = [
    {
        "selector_hint": "input.search",
        "tag": "input",
        "type": "search",
        "placeholder": "输入房型产品名称搜索",
        "aria_label": "",
        "value": "",
        "nearby_text": "豪华套房 子价格 非子价格 全部房型产品 已选5个/共44个",
    },
    {
        "selector_hint": "input.checkbox",
        "tag": "input",
        "type": "checkbox",
        "placeholder": "",
        "aria_label": "",
        "value": "",
        "nearby_text": "豪华大床房 含5个",
    },
    {
        "selector_hint": "input.radio",
        "tag": "input",
        "type": "radio",
        "placeholder": "",
        "aria_label": "",
        "value": "",
        "nearby_text": "分开改价 统一加减价",
    },
    {
        "selector_hint": "input.start-date",
        "tag": "input",
        "type": "text",
        "placeholder": "开始日期",
        "aria_label": "",
        "value": "2026-08-25",
        "nearby_text": "",
    },
    {
        "selector_hint": "input.ai",
        "tag": "input",
        "type": "text",
        "placeholder": "AI 一句话改价",
        "aria_label": "",
        "value": "",
        "nearby_text": "",
    },
    {
        "selector_hint": "input.price-1",
        "tag": "input",
        "type": "",
        "placeholder": "",
        "aria_label": "",
        "value": "654.00",
        "nearby_text": "",
    },
    {
        "selector_hint": "input.price-2",
        "tag": "input",
        "type": "",
        "placeholder": "",
        "aria_label": "",
        "value": "678.00",
        "nearby_text": "",
    },
]


class PriceInputMatchTests(unittest.TestCase):
    """B4 — 价格输入框采集与匹配收紧（三平台共享逻辑）。"""

    def _matcher(self):
        return importlib.import_module("app.domain.browser.pricing_input_match")

    def test_search_box_placeholder_is_excluded(self) -> None:
        matcher = self._matcher()
        result = matcher.match_price_input_candidates(
            FLIGGY_PAGE_INPUTS, ["豪华套房", "25", "654"]
        )
        hints = [item["selector_hint"] for item in result]
        self.assertNotIn("input.search", hints)

    def test_checkbox_and_radio_are_excluded(self) -> None:
        matcher = self._matcher()
        result = matcher.match_price_input_candidates(
            FLIGGY_PAGE_INPUTS, ["豪华大床房", "654"]
        )
        hints = [item["selector_hint"] for item in result]
        self.assertNotIn("input.checkbox", hints)
        self.assertNotIn("input.radio", hints)

    def test_date_input_is_excluded_and_not_price_shaped(self) -> None:
        matcher = self._matcher()
        result = matcher.match_price_input_candidates(
            FLIGGY_PAGE_INPUTS, ["25", "654"]
        )
        hints = [item["selector_hint"] for item in result]
        self.assertNotIn("input.start-date", hints)

    def test_real_price_input_outranks_other_candidates(self) -> None:
        matcher = self._matcher()
        result = matcher.match_price_input_candidates(
            FLIGGY_PAGE_INPUTS, ["豪华套房", "25", "654"]
        )
        self.assertTrue(result)
        self.assertEqual(result[0]["selector_hint"], "input.price-1")
        self.assertGreater(result[0]["match_score"], 0)

    def test_price_shape_requires_money_like_value(self) -> None:
        matcher = self._matcher()
        self.assertEqual(matcher._price_shape_bonus("654.00"), 2)
        self.assertEqual(matcher._price_shape_bonus("654"), 2)
        self.assertEqual(matcher._price_shape_bonus("12"), 0)  # < 30
        self.assertEqual(matcher._price_shape_bonus("2026-08-24"), 0)
        self.assertEqual(matcher._price_shape_bonus("20260824"), 0)
        self.assertEqual(matcher._price_shape_bonus(""), 0)
        self.assertEqual(matcher._price_shape_bonus(None), 0)

    def test_empty_candidates_return_empty(self) -> None:
        matcher = self._matcher()
        self.assertEqual(matcher.match_price_input_candidates([], ["654"]), [])


class PlatformReaderDelegationTests(unittest.TestCase):
    """三平台 reader 的 _matching_input_candidates 复用共享实现。"""

    def _readers(self):
        return [
            importlib.import_module("app.domain.browser.fliggy_pricing_page_reader"),
            importlib.import_module("app.domain.browser.meituan_pricing_page_reader"),
            importlib.import_module("app.domain.browser.ctrip_pricing_page_reader"),
        ]

    def test_all_platform_readers_delegate_to_shared_matcher(self) -> None:
        matcher = importlib.import_module("app.domain.browser.pricing_input_match")
        for reader in self._readers():
            self.assertIs(
                reader._matching_input_candidates, matcher.match_price_input_candidates
            )

    def test_platform_readers_exclude_search_and_checkbox_inputs(self) -> None:
        for reader in self._readers():
            result = reader._matching_input_candidates(
                FLIGGY_PAGE_INPUTS, ["豪华套房", "25", "654"]
            )
            hints = [item["selector_hint"] for item in result]
            self.assertNotIn("input.search", hints, reader.__name__)
            self.assertNotIn("input.checkbox", hints, reader.__name__)
            self.assertNotIn("input.radio", hints, reader.__name__)
            self.assertEqual(hints[:1], ["input.price-1"], reader.__name__)


if __name__ == "__main__":
    unittest.main()
