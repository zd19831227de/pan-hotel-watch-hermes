from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from app.domain.browser import ctrip_promotion_adjust_flow
from app.domain.browser.ctrip_promotion_adjust_flow import (
    _discount_percent_matches_expected,
    _open_promotion_center_page,
    execute_promotion_adjust_write_flow,
    match_promotion_row,
    parse_promotion_discount,
    parse_promotion_rows,
    pick_promotion_discount_input,
    prepare_promotion_adjust_page_write_dry_run_flow,
)


def _row(
    row_key: str,
    name: str,
    discount: str,
    *,
    rooms: str = "全房型（包括新增）",
    date_text: str = "2026年9月6日-2026年9月12日 到期前自动延期",
) -> dict[str, Any]:
    return {
        "row_key": row_key,
        "cells": [name, rooms, "不限", date_text, discount, "未使用", "暂停 修改 更多"],
        "has_modify_button": True,
    }


class ParsePromotionRowsTests(unittest.TestCase):
    def test_single_rule_promotion_parsed(self) -> None:
        rows = [_row("40", "基础促销组 天天特价", "立减 1%")]
        promotions = parse_promotion_rows(rows)
        self.assertEqual(len(promotions), 1)
        item = promotions[0]
        self.assertEqual(item["promotion_name"], "基础促销组 天天特价")
        self.assertEqual(item["promotion_row_key"], "40")
        self.assertEqual(item["room_scope"], "全房型（包括新增）")
        self.assertEqual(item["date_text"], "2026年9月6日-2026年9月12日 到期前自动延期")
        self.assertEqual(item["discount_text"], "立减 1%")
        self.assertEqual(item["discount_percent"], 1.0)
        self.assertFalse(item["multi_rule"])

    def test_multi_rule_promotion_flagged(self) -> None:
        rows = [
            _row("3", "场景促销组 提前预订", "提前3天预订 立减 35% 提前1天预订 立减 38%"),
        ]
        promotions = parse_promotion_rows(rows)
        self.assertTrue(promotions[0]["multi_rule"])
        self.assertIsNone(promotions[0]["discount_percent"])

    def test_time_window_suffix_still_single(self) -> None:
        """今夜甩卖单时段：时间段前缀+单条立减仍是单规则。"""
        rows = [
            _row(
                "6",
                "场景促销组 今夜甩卖",
                "每天 21:00:00 至 次日 06:00:00 立减 40% 仅入住当天预订时，促销可生效",
            )
        ]
        promotions = parse_promotion_rows(rows)
        self.assertEqual(promotions[0]["discount_percent"], 40.0)
        self.assertFalse(promotions[0]["multi_rule"])

    def test_non_percent_promotion_unsupported(self) -> None:
        rows = [_row("39", "红包促销组 万马聚财红包", "9折")]
        promotions = parse_promotion_rows(rows)
        self.assertIsNone(promotions[0]["discount_percent"])
        self.assertEqual(promotions[0]["discount_kind"], "unsupported")

    def test_duplicate_row_key_deduped(self) -> None:
        rows = [_row("40", "基础促销组 天天特价", "立减 1%"), _row("40", "基础促销组 天天特价", "立减 1%")]
        self.assertEqual(len(parse_promotion_rows(rows)), 1)


class ParsePromotionDiscountTests(unittest.TestCase):
    def test_single(self) -> None:
        self.assertEqual(parse_promotion_discount("立减 35%"), (35.0, "single"))

    def test_single_with_decimal(self) -> None:
        self.assertEqual(parse_promotion_discount("立减 5.5%"), (5.5, "single"))

    def test_multi(self) -> None:
        self.assertEqual(
            parse_promotion_discount("连住2天 立减 2% 连住3天 立减 3%"), (None, "multi")
        )

    def test_unsupported(self) -> None:
        self.assertEqual(parse_promotion_discount("9折"), (None, "unsupported"))
        self.assertEqual(parse_promotion_discount("未读取到"), (None, "unsupported"))


class MatchPromotionRowTests(unittest.TestCase):
    def test_partial_name_matches_full_page_name(self) -> None:
        rows = [_row("40", "基础促销组 天天特价", "立减 1%")]
        matched = match_promotion_row(rows, "天天特价")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["row_key"], "40")

    def test_ambiguous_partial_name_rejected(self) -> None:
        rows = [
            _row("1", "场景促销组 门店新客立减", "立减 35%"),
            _row("42", "场景促销组 学生专享", "立减 8%"),
        ]
        self.assertIsNone(match_promotion_row(rows, "场景促销组"))

    def test_no_match_returns_none(self) -> None:
        rows = [_row("40", "基础促销组 天天特价", "立减 1%")]
        self.assertIsNone(match_promotion_row(rows, "限时抢购"))


class PickPromotionDiscountInputTests(unittest.TestCase):
    def test_single_percent_input(self) -> None:
        inputs = [
            {"input_index": 0, "value": "2026年9月6日", "near": "开始日期"},
            {"input_index": 11, "value": "35", "near": "%"},
        ]
        self.assertEqual(pick_promotion_discount_input(inputs), 11)

    def test_multiple_percent_inputs_rejected(self) -> None:
        inputs = [
            {"input_index": 3, "value": "35", "near": "提前3天预订 立减 %"},
            {"input_index": 7, "value": "38", "near": "提前1天预订 立减 %"},
        ]
        self.assertIsNone(pick_promotion_discount_input(inputs))

    def test_no_percent_input(self) -> None:
        inputs = [{"input_index": 0, "value": "2026年9月6日", "near": "开始日期"}]
        self.assertIsNone(pick_promotion_discount_input(inputs))

    def test_page_discount_must_match_approved_preview_baseline(self) -> None:
        self.assertTrue(_discount_percent_matches_expected("35", 35.0))
        self.assertFalse(_discount_percent_matches_expected("38", 35.0))
        self.assertFalse(_discount_percent_matches_expected("", 35.0))


class PromotionDryRunBaselineTests(unittest.TestCase):
    def test_dry_run_passes_approved_current_discount_to_page_verification(self) -> None:
        async def scenario() -> mock.AsyncMock:
            open_edit = mock.AsyncMock(
                return_value={
                    "status": "needs_review",
                    "summary": "基线变化",
                    "evidence": {},
                }
            )
            with mock.patch.object(
                ctrip_promotion_adjust_flow,
                "_open_promotion_edit_page",
                open_edit,
            ):
                await prepare_promotion_adjust_page_write_dry_run_flow(
                    mock.Mock(),
                    platform_account_id="acct-promotion-1",
                    browser_session_id="bs-promotion-1",
                    actor_id="tester",
                    execute_request={
                        "actions": [
                            {
                                "promotion_name": "基础促销组 天天特价",
                                "current_discount_percent": 35.0,
                                "target_discount_percent": 5.0,
                            }
                        ]
                    },
                )
            return open_edit

        open_edit = asyncio.run(scenario())
        self.assertEqual(open_edit.await_args.kwargs["current_percent"], 35.0)


class PromotionPageOpenTests(unittest.TestCase):
    def test_profile_lock_requires_human_without_exposing_exception(self) -> None:
        class CtripBrowserProfileLockedError(RuntimeError):
            pass

        async def scenario() -> dict[str, Any]:
            service = mock.Mock()
            service._get_or_create_page = mock.AsyncMock(
                side_effect=CtripBrowserProfileLockedError(
                    r"C:\pan-hotel-watch-runtime\ctrip-profile"
                )
            )
            return await _open_promotion_center_page(
                service,
                platform_account_id="acct-promotion-1",
                browser_session_id="bs-promotion-1",
                actor_id="tester",
            )

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "human_required")
        self.assertNotIn("ctrip-profile", str(result))


class _FakeLocator:
    def __init__(self, page: "_FakePromotionPage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return 1

    async def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        return None

    def locator(self, selector: str) -> "_FakeLocator":
        return _FakeLocator(self._page)

    def nth(self, index: int) -> "_FakeLocator":
        return _FakeLocator(self._page)

    async def click(self, timeout: int = 0) -> None:
        self._page.url = (
            "https://ebooking.ctrip.com/promotion/editPromotion"
            "?promotionType=base&ruleIds=1&opera=modify"
        )


class _FakePromotionPage:
    """模拟「列表 -> 点修改 -> 同页进入编辑页」的最小页面。"""

    def __init__(self) -> None:
        self.url = (
            "https://ebooking.ctrip.com/promotion/promotionCenter?microJump=true"
        )
        self.context = mock.Mock()
        self.context.pages = [self]

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self)

    async def evaluate(self, script: str) -> Any:
        if "data-row-key" in script:
            return [_row("row-1", "基础促销组 天天特价", "立减 1%")]
        if "getBoundingClientRect" in script:
            return [{"input_index": 3, "value": "1", "near": "立减 1 %"}]
        return None

    async def wait_for_timeout(self, ms: float) -> None:
        return None

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def wait_for_function(self, script: str, timeout: int = 0) -> None:
        return None

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.url = url


class _FakeAgreementLocator:
    def __init__(self, page: "_FakeAgreementPage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeAgreementLocator":
        return self

    async def count(self) -> int:
        return 1 if self._page.found else 0

    async def click(self, timeout: int = 0) -> None:
        self._page.click_count += 1
        if self._page.click_checks:
            self._page.checked = True


class _FakeAgreementPage:
    def __init__(
        self,
        *,
        found: bool = True,
        checked: bool = False,
        disabled: bool = False,
        click_checks: bool = True,
    ) -> None:
        self.found = found
        self.checked = checked
        self.disabled = disabled
        self.click_checks = click_checks
        self.click_count = 0

    def locator(self, selector: str) -> _FakeAgreementLocator:
        return _FakeAgreementLocator(self)

    async def evaluate(self, script: str) -> dict[str, Any]:
        return {
            "label_found": self.found,
            "control_found": self.found,
            "control_type": "input" if self.found else None,
            "checked": self.checked,
            "disabled": self.disabled,
        }

    async def wait_for_timeout(self, ms: float) -> None:
        return None


_REAL_AGREEMENT_HTML = """
<html>
  <body>
    <div class="he-trip-ui-row">
      <button he-click="footer_pageBack" type="button">取 消</button>
      <button he-click="footer_pageSave" type="button">保 存</button>
      <div class="he-trip-ui-checkbox-group" id="basic_agreement">
        <label class="he-trip-ui-checkbox-wrapper">
          <span class="he-trip-ui-checkbox" he-click="agreement">
            <input type="checkbox" class="he-trip-ui-checkbox-input" value="0">
          </span>
        </label>
      </div>
      <div>我已经阅读并同意<span>促销规则</span></div>
    </div>
  </body>
</html>
"""


async def _agree_real_page(click_toggles: bool = True) -> tuple[dict[str, Any], bool, int]:
    from playwright.async_api import async_playwright

    html = _REAL_AGREEMENT_HTML
    if not click_toggles:
        # 在 label 上吞掉点击默认行为：label/子元素/原生 input 的切换全部失效
        html = _REAL_AGREEMENT_HTML.replace(
            "</body>",
            "<script>document.querySelector('#basic_agreement label')"
            ".addEventListener('click', (e) => e.preventDefault());</script></body>",
        )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        try:
            await page.set_content(html)
            result = await ctrip_promotion_adjust_flow._ensure_promotion_rules_agreed(page)
            checked = await page.locator("#basic_agreement input").is_checked()
            return result, checked, page.click_log if hasattr(page, "click_log") else 0
        finally:
            await browser.close()


class PromotionRulesAgreementTests(unittest.TestCase):
    """2026-09-09 真机探针结论：勾选框文案是「我已经阅读并同意促销规则」
    （旧实现搜「已阅读并同意促销规则」差一个「经」字，永远匹配不上），
    结构为 #basic_agreement 组内 antd 风格 label>span[he-click=agreement]>input。
    """

    def test_real_structure_label_click_checks_input(self) -> None:
        result, checked, _ = asyncio.run(_agree_real_page())

        self.assertEqual(result["status"], "checked")
        self.assertTrue(result["checked"])
        self.assertTrue(result["clicked"])
        self.assertTrue(checked)

    def test_real_structure_without_toggle_is_blocked_not_saved(self) -> None:
        result, checked, _ = asyncio.run(_agree_real_page(click_toggles=False))

        self.assertEqual(result["status"], "not_checked")
        self.assertFalse(result["checked"])
        self.assertFalse(checked)

    def test_already_checked_skips_click(self) -> None:
        page = _FakeAgreementPage(checked=True)

        result = asyncio.run(
            ctrip_promotion_adjust_flow._ensure_promotion_rules_agreed(page)
        )

        self.assertEqual(result["status"], "already_checked")
        self.assertTrue(result["checked"])
        self.assertEqual(page.click_count, 0)

    def test_missing_agreement_control_is_not_treated_as_success(self) -> None:
        page = _FakeAgreementPage(found=False)

        result = asyncio.run(
            ctrip_promotion_adjust_flow._ensure_promotion_rules_agreed(page)
        )

        self.assertEqual(result["status"], "not_located")
        self.assertFalse(result["checked"])
        self.assertEqual(page.click_count, 0)

    def test_submit_prefers_footer_page_save_attribute(self) -> None:
        from playwright.async_api import async_playwright

        async def scenario() -> dict[str, Any]:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.set_content(_REAL_AGREEMENT_HTML)
                    return await ctrip_promotion_adjust_flow._submit_promotion_edit(page)
                finally:
                    await browser.close()

        result = asyncio.run(scenario())
        self.assertTrue(result["clicked"])


class PromotionDryRunSuccessPathTests(unittest.TestCase):
    """线上 2026-09-09 10:58 阻断回归：编辑页核验成功后空跑流读取核验数据。

    _open_promotion_edit_page 成功返回的核验数据键名必须与空跑/执行流的
    verify["edit_read"] 一致（此前误写成 promotion_read，导致审批确认后
    执行链必崩）。
    """

    def test_dry_run_completes_after_edit_page_verified(self) -> None:
        async def scenario() -> dict[str, Any]:
            page = _FakePromotionPage()
            open_center = mock.AsyncMock(
                return_value={
                    "status": "success",
                    "summary": "已参加促销列表页已打开。",
                    "page": page,
                    "page_state": "authenticated",
                    "captured_at": "2026-09-09T02:58:00+00:00",
                    "evidence": {"page_state": "authenticated"},
                }
            )
            with (
                mock.patch.object(
                    ctrip_promotion_adjust_flow, "_open_promotion_center_page", open_center
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_safe_visible_text",
                    mock.AsyncMock(return_value="促销中心 修改 天天特价 保 存"),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_classify_page_state",
                    mock.Mock(return_value="authenticated"),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_return_to_list_page",
                    mock.AsyncMock(),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_sync_ebk_browser_session_state",
                    mock.Mock(),
                ),
            ):
                return await prepare_promotion_adjust_page_write_dry_run_flow(
                    mock.Mock(),
                    platform_account_id="acct-promotion-1",
                    browser_session_id="bs-promotion-1",
                    actor_id="tester",
                    execute_request={
                        "actions": [
                            {
                                "promotion_name": "基础促销组 天天特价",
                                "current_discount_percent": 1.0,
                                "target_discount_percent": 2.0,
                            }
                        ]
                    },
                )

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "page_write_preview_ready")
        promotion_read = result["data"]["promotion_read"]
        self.assertEqual(promotion_read["discount_input_index"], 3)
        self.assertTrue(result["evidence"]["promotion_name_verified"])
        self.assertFalse(result["evidence"]["input_fill_invoked"])


class PromotionExecuteSuccessPathTests(unittest.TestCase):
    def test_execute_completes_when_edit_page_verified_and_readback_matches(self) -> None:
        async def scenario() -> dict[str, Any]:
            edit_page = _FakePromotionPage()
            with (
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_open_promotion_edit_page",
                    mock.AsyncMock(
                        return_value={
                            "status": "edit_page_verified",
                            "summary": "已打开编辑页。",
                            "page": edit_page,
                            "list_page": None,
                            "page_state": "authenticated",
                            "captured_at": "2026-09-09T02:58:00+00:00",
                            "edit_read": {
                                "promotion_name": "基础促销组 天天特价",
                                "discount_input_index": 3,
                                "current_discount_percent": 1.0,
                            },
                            "evidence": {"promotion_name_verified": True},
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_fill_discount_input",
                    mock.AsyncMock(
                        return_value={
                            "status": "filled",
                            "filled_value": "2",
                            "verified_value": "2",
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_ensure_promotion_rules_agreed",
                    mock.AsyncMock(
                        return_value={
                            "status": "checked",
                            "label_found": True,
                            "control_found": True,
                            "checked": True,
                            "clicked": True,
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_submit_promotion_edit",
                    mock.AsyncMock(
                        return_value={"clicked": True, "left_edit_page": True, "status": "left_edit_page"}
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_read_promotion_discount",
                    mock.AsyncMock(
                        return_value={
                            "status": "success",
                            "promotion": {
                                "promotion_name": "基础促销组 天天特价",
                                "discount_percent": 2.0,
                            },
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_safe_screenshot",
                    mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_return_to_list_page",
                    mock.AsyncMock(),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_sync_ebk_browser_session_state",
                    mock.Mock(),
                ),
            ):
                return await execute_promotion_adjust_write_flow(
                    mock.Mock(),
                    platform_account_id="acct-promotion-1",
                    browser_session_id="bs-promotion-1",
                    actor_id="tester",
                    execute_request={
                        "actions": [
                            {
                                "promotion_name": "基础促销组 天天特价",
                                "current_discount_percent": 1.0,
                                "target_discount_percent": 2.0,
                            }
                        ]
                    },
                )

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "executed")
        self.assertEqual(
            result["data"]["readback_result"]["readback_discount_percent"], 2.0
        )
        self.assertTrue(result["evidence"]["browser_write_invoked"])
        self.assertTrue(result["evidence"]["promotion_rules_agreed"])
        self.assertTrue(result["evidence"]["readback_percent_matches_target"])

    def test_execute_stops_before_submit_when_rules_cannot_be_agreed(self) -> None:
        async def scenario() -> tuple[dict[str, Any], mock.AsyncMock, mock.AsyncMock]:
            edit_page = _FakePromotionPage()
            submit = mock.AsyncMock()
            readback = mock.AsyncMock()
            with (
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_open_promotion_edit_page",
                    mock.AsyncMock(
                        return_value={
                            "status": "edit_page_verified",
                            "summary": "已打开编辑页。",
                            "page": edit_page,
                            "list_page": None,
                            "page_state": "authenticated",
                            "captured_at": "2026-09-09T02:58:00+00:00",
                            "edit_read": {
                                "promotion_name": "基础促销组 天天特价",
                                "discount_input_index": 3,
                                "current_discount_percent": 1.0,
                            },
                            "evidence": {"promotion_name_verified": True},
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_fill_discount_input",
                    mock.AsyncMock(
                        return_value={
                            "status": "filled",
                            "filled_value": "2",
                            "verified_value": "2",
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_ensure_promotion_rules_agreed",
                    mock.AsyncMock(
                        return_value={
                            "status": "not_located",
                            "label_found": False,
                            "control_found": False,
                            "checked": False,
                            "clicked": False,
                        }
                    ),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow, "_submit_promotion_edit", submit
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow, "_read_promotion_discount", readback
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_safe_screenshot",
                    mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    ctrip_promotion_adjust_flow,
                    "_return_to_list_page",
                    mock.AsyncMock(),
                ),
            ):
                result = await execute_promotion_adjust_write_flow(
                    mock.Mock(),
                    platform_account_id="acct-promotion-1",
                    browser_session_id="bs-promotion-1",
                    actor_id="tester",
                    execute_request={
                        "actions": [
                            {
                                "promotion_name": "基础促销组 天天特价",
                                "current_discount_percent": 1.0,
                                "target_discount_percent": 2.0,
                            }
                        ]
                    },
                )
            return result, submit, readback

        result, submit, readback = asyncio.run(scenario())
        self.assertEqual(result["status"], "page_structure_changed")
        self.assertFalse(result["evidence"]["submit_invoked"])
        self.assertFalse(result["evidence"]["promotion_rules_agreed"])
        submit.assert_not_awaited()
        readback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
