from __future__ import annotations

import importlib
import unittest


def _executor_module():
    return importlib.import_module("app.domain.browser.ctrip_pricing_executor")


# 2026-09-15 实测（live CDP，修改价格弹窗）：find_price_inputs 因 JS 词表
# 乱码全部 match_score=0，排序按原样返回第一个可编辑输入框=日期框
# #s_dateYearMode；点它弹日期选择器拦截后续点击 -> input_failed。
# 真价格输入框 nearby_text 只有「卖价(元)」，不含房型名。
DATE_CANDIDATE = {
    "index": 0,
    "selector_hint": "#s_dateYearMode",
    "tag": "input",
    "type": "text",
    "aria_label": "",
    "placeholder": "选择日期",
    "value": "2026-09-15",
    "nearby_text": "—",
    "match_score": 0,
}
PRICE_CANDIDATE = {
    "index": 2,
    "selector_hint": "ul.pricetplul > li > div.pop-set > div.rule-number-box.pop-price-input > input.JPrice_CalculateSell",
    "tag": "input",
    "type": "text",
    "aria_label": "",
    "placeholder": "",
    "value": "",
    "nearby_text": "卖价(元)",
    "match_score": 0,
}
ROOM_DATE_CANDIDATE = {
    # 日期框若真带房型名也不允许被选中：填价入口绝不能是日期输入。
    "index": 3,
    "selector_hint": "#someDate",
    "tag": "input",
    "type": "text",
    "aria_label": "奢华三卧套房 日期",
    "placeholder": "YYYY/MM/DD",
    "value": "",
    "nearby_text": "奢华三卧套房 日期",
    "match_score": 0,
}


class MatchPriceInputCandidatesTests(unittest.TestCase):
    def _match(self, candidates, room_type="奢华三卧套房"):
        module = _executor_module()
        return module.match_candidates_to_room(candidates, room_type)

    def test_price_worded_input_outranks_date_input_when_scores_tie(self):
        matched = self._match([dict(DATE_CANDIDATE), dict(PRICE_CANDIDATE)])
        self.assertEqual(
            matched[0]["selector_hint"], PRICE_CANDIDATE["selector_hint"]
        )

    def test_date_like_candidates_are_never_selected(self):
        matched = self._match(
            [dict(DATE_CANDIDATE), dict(ROOM_DATE_CANDIDATE), dict(PRICE_CANDIDATE)]
        )
        self.assertEqual(
            matched[0]["selector_hint"], PRICE_CANDIDATE["selector_hint"]
        )

    def test_existing_match_score_still_dominates(self):
        strong = dict(PRICE_CANDIDATE)
        strong["selector_hint"] = "#strong-scored"
        strong["match_score"] = 5
        weak_price = dict(PRICE_CANDIDATE)
        matched = self._match([weak_price, strong])
        self.assertEqual(matched[0]["selector_hint"], "#strong-scored")

    def test_room_name_boost_still_applies_on_price_candidates(self):
        with_room = dict(PRICE_CANDIDATE)
        with_room["nearby_text"] = "奢华三卧套房 卖价(元)"
        without = dict(PRICE_CANDIDATE)
        without["selector_hint"] = "#plain-price"
        matched = self._match([without, with_room])
        self.assertEqual(matched[0]["selector_hint"], PRICE_CANDIDATE["selector_hint"])


if __name__ == "__main__":
    unittest.main()
