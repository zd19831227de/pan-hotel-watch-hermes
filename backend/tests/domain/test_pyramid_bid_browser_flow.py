from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.domain.browser import ctrip_pyramid_bidding_flow as pyramid_browser_flow
from app.domain.browser.ctrip_pyramid_bidding_flow import (
    PYRAMID_LIST_URL,
    _match_promotion_row,
    extract_pyramid_floor_price,
    parse_pyramid_promotion_rows,
    pick_bid_input,
    pick_name_input,
    prepare_pyramid_bid_page_write_dry_run_flow,
)

# 2026-09-08 extensionList 真实表格行（CDP 只读探针抓取）：
# 列 = 推广名称|状态|推广日期|普通日/高峰日预算|点击出价|今日花费|今日曝光|今日点击|操作
_LIVE_ROW_1 = {
    "row_key": "3679057",
    "cells": [
        "周内0-2",
        "推广中（非投放时段）",
        "从2025年12月25日起持续投放",
        "100/100",
        "2.54",
        "40.95",
        "153",
        "16",
        "修改 暂停 更多",
    ],
    "has_modify_button": True,
}

_LIVE_ROW_2 = {
    "row_key": "3193851",
    "cells": [
        "22--24",
        "推广中（非投放时段）",
        "从2025年8月20日起持续投放",
        "400/400",
        "5.50",
        "6.51",
        "1",
        "1",
        "修改 暂停 更多",
    ],
    "has_modify_button": True,
}

# 智能投放计划：无固定点击出价（单元格为空/文案）
_SMART_ROW = {
    "row_key": "900001",
    "cells": ["智能投放", "推广中", "从2025年8月20日起持续投放", "300/300", "—", "0", "0", "0", "修改 暂停 更多"],
    "has_modify_button": True,
}


class PyramidListUrlTests(unittest.TestCase):
    def test_list_url_is_extension_list_page(self) -> None:
        self.assertEqual(
            PYRAMID_LIST_URL,
            "https://ebooking.ctrip.com/advertise/cpc/extensionList?microJump=true",
        )


class ParsePyramidPromotionRowsTests(unittest.TestCase):
    def test_parses_live_table_row(self) -> None:
        promotions = parse_pyramid_promotion_rows([_LIVE_ROW_1, _LIVE_ROW_2])
        self.assertEqual(len(promotions), 2)
        first = promotions[0]
        self.assertEqual(first["campaign_name"], "周内0-2")
        self.assertEqual(first["campaign_id"], "3679057")
        self.assertEqual(first["campaign_status"], "推广中（非投放时段）")
        self.assertEqual(first["promotion_date_text"], "从2025年12月25日起持续投放")
        self.assertEqual(first["click_bid_cny"], 2.54)
        self.assertEqual(first["daily_budget_cny"], 100.0)
        self.assertEqual(first["today_spend_cny"], 40.95)
        self.assertEqual(first["today_impressions"], 153)
        self.assertEqual(first["today_clicks"], 16)
        self.assertFalse(first["smart_campaign"])
        second = promotions[1]
        self.assertEqual(second["campaign_name"], "22--24")
        self.assertEqual(second["click_bid_cny"], 5.5)
        self.assertEqual(second["daily_budget_cny"], 400.0)

    def test_smart_campaign_without_bid(self) -> None:
        promotions = parse_pyramid_promotion_rows([_SMART_ROW])
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["campaign_name"], "智能投放")
        self.assertIsNone(promotions[0]["click_bid_cny"])
        self.assertTrue(promotions[0]["smart_campaign"])

    def test_noise_only_rows_are_skipped(self) -> None:
        rows = [
            {"row_key": "", "cells": ["新建推广"], "has_modify_button": False},
            {"row_key": "1", "cells": [], "has_modify_button": True},
            "not-a-dict",
        ]
        self.assertEqual(parse_pyramid_promotion_rows(rows), [])

    def test_duplicate_row_key_kept_once(self) -> None:
        promotions = parse_pyramid_promotion_rows([_LIVE_ROW_2, dict(_LIVE_ROW_2)])
        self.assertEqual(len(promotions), 1)


class MatchPromotionRowTests(unittest.TestCase):
    def test_exact_name_match_returns_index_and_id(self) -> None:
        matched = _match_promotion_row([_LIVE_ROW_1, _LIVE_ROW_2], "22--24")
        self.assertEqual(matched, {"index": 1, "campaign_id": "3193851"})

    def test_missing_campaign_returns_none(self) -> None:
        self.assertIsNone(_match_promotion_row([_LIVE_ROW_1, _LIVE_ROW_2], "9--10"))

    def test_ambiguous_matches_return_none(self) -> None:
        self.assertIsNone(_match_promotion_row([_LIVE_ROW_1, _LIVE_ROW_1], "周内0-2"))


class DashVariantNameTests(unittest.TestCase):
    """2026-09-08 线上问题：页面计划名「周内0-2」（单横线）与用户指令
    「周内0--2」（双横线）互为变体，横线/空格差异不得导致匹配失败。"""

    def test_double_dash_query_matches_single_dash_row(self) -> None:
        matched = _match_promotion_row([_LIVE_ROW_1], "周内0--2")
        self.assertEqual(matched, {"index": 0, "campaign_id": "3679057"})

    def test_single_dash_query_matches_double_dash_row(self) -> None:
        matched = _match_promotion_row([_LIVE_ROW_2], "22-24")
        self.assertEqual(matched, {"index": 0, "campaign_id": "3193851"})

    def test_campaign_key_unifies_dash_and_space_variants(self) -> None:
        from app.domain.browser.ctrip_pyramid_bidding_flow import _campaign_key

        self.assertEqual(_campaign_key("周内0--2"), _campaign_key("周内0-2"))
        self.assertEqual(_campaign_key("22--24"), _campaign_key("22 - 24"))
        self.assertEqual(_campaign_key("14—16"), _campaign_key("14-16"))
        self.assertNotEqual(_campaign_key("22--24"), _campaign_key("20--22"))

    def test_campaign_names_equivalent_for_edit_page_check(self) -> None:
        from app.domain.browser.ctrip_pyramid_bidding_flow import (
            _campaign_names_equivalent,
        )

        self.assertTrue(_campaign_names_equivalent("周内0--2", "周内0-2"))
        self.assertTrue(_campaign_names_equivalent("22--24", "22--24"))
        self.assertFalse(_campaign_names_equivalent("22--24", "20--22"))
        self.assertFalse(_campaign_names_equivalent("22--24", ""))

    def test_page_name_substr_of_target_must_not_pass(self) -> None:
        """安全闸门只收紧不放宽：页面名「22」是目标「22--24」的子串时不得放行。"""
        from app.domain.browser.ctrip_pyramid_bidding_flow import (
            _campaign_names_equivalent,
        )

        self.assertFalse(_campaign_names_equivalent("22--24", "22"))
        # 反向（用户简写 ⊆ 页面全名）沿用原语义放行
        self.assertTrue(_campaign_names_equivalent("22", "22--24"))


class FloorPriceTests(unittest.TestCase):
    def test_extracts_floor_from_live_text(self) -> None:
        self.assertEqual(
            extract_pyramid_floor_price("单次点击出价 CNY\n 起价2.1，随季节有变动\n建议出价：4.26"),
            2.1,
        )

    def test_missing_floor_returns_none(self) -> None:
        self.assertIsNone(extract_pyramid_floor_price("单次点击出价 CNY\n建议出价：4.26"))


class PickInputTests(unittest.TestCase):
    _INPUTS = [
        {"input_index": 0, "value": "14--16", "near": "推广名称"},
        {"input_index": 3, "value": "300", "near": "根据广告投放数据预估建议预算为800元 使用该预算"},
        {"input_index": 7, "value": "5.5", "near": "起价2.1，随季节有变动 建议出价 市场助手"},
        {"input_index": 9, "value": "100", "near": "%"},
    ]

    def test_bid_input_matched_by_value_and_context(self) -> None:
        self.assertEqual(pick_bid_input(self._INPUTS, current_bid=5.5), 7)

    def test_bid_input_falls_back_to_context_without_value(self) -> None:
        inputs = [dict(item) for item in self._INPUTS]
        inputs[2]["value"] = ""
        self.assertEqual(pick_bid_input(inputs, current_bid=None), 7)

    def test_bid_input_not_located(self) -> None:
        self.assertIsNone(pick_bid_input([self._INPUTS[1]], current_bid=5.5))

    def test_name_input_located(self) -> None:
        self.assertEqual(pick_name_input(self._INPUTS), 0)


class PyramidBidBaselineTests(unittest.TestCase):
    def test_page_bid_must_match_approved_preview_baseline(self) -> None:
        self.assertTrue(pyramid_browser_flow._bid_matches_expected("5.5", 5.5))
        self.assertFalse(pyramid_browser_flow._bid_matches_expected("6", 5.5))
        self.assertFalse(pyramid_browser_flow._bid_matches_expected("", 5.5))

    def test_dry_run_passes_approved_current_bid_to_page_verification(self) -> None:
        async def scenario() -> mock.AsyncMock:
            open_edit = mock.AsyncMock(
                return_value={
                    "status": "needs_review",
                    "summary": "baseline changed",
                    "evidence": {},
                }
            )
            with mock.patch.object(
                pyramid_browser_flow,
                "_open_campaign_edit_page",
                open_edit,
            ):
                await prepare_pyramid_bid_page_write_dry_run_flow(
                    mock.Mock(),
                    platform_account_id="acct-pyramid-1",
                    browser_session_id="bs-pyramid-1",
                    actor_id="tester",
                    execute_request={
                        "actions": [
                            {
                                "campaign_name": "14--16",
                                "current_bid_cny": 5.5,
                                "target_bid_cny": 6.0,
                            }
                        ]
                    },
                )
            return open_edit

        open_edit = asyncio.run(scenario())
        self.assertEqual(open_edit.await_args.kwargs["current_bid"], 5.5)


class PyramidPageOpenFailureTests(unittest.TestCase):
    def test_profile_lock_requires_human_without_leaking_exception_details(self) -> None:
        class CtripBrowserProfileLockedError(RuntimeError):
            pass

        service = mock.Mock()
        service._get_or_create_page = mock.AsyncMock(
            side_effect=CtripBrowserProfileLockedError(
                r"C:\pan-hotel-watch-runtime\ctrip-profile "
                "token=abc webhook=https://secret"
            )
        )

        async def scenario() -> tuple[dict[str, object], str]:
            with mock.patch.object(pyramid_browser_flow.logger, "warning") as warning_log:
                result = await pyramid_browser_flow._open_pyramid_page(
                    service,
                    platform_account_id="acct-pyramid-1",
                    browser_session_id="bs-pyramid-1",
                    actor_id="tester",
                )
            logged = " ".join(
                str(value)
                for call in warning_log.call_args_list
                for value in call.args
            )
            return result, logged

        result, logged = asyncio.run(scenario())
        combined = f"{result} {logged}"
        self.assertEqual(result["status"], "human_required")
        self.assertIn("CtripBrowserProfileLockedError", combined)
        self.assertNotIn("ctrip-profile", combined)
        self.assertNotIn("token=abc", combined)
        self.assertNotIn("https://secret", combined)


if __name__ == "__main__":
    unittest.main()
