from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest import mock

CALENDAR_URL = "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar?microJump=true"

# 日历页主内容信号（与 test_ctrip_pricing_page_state_classification 同源样本）。
CALENDAR_BODY = (
    "日历 · 携程酒店eBooking\n快速选择房型\n今天\n明天\n本周\n"
    "房态\n房量\n卖价\n底价\n修改价格\n按周设置\n保存"
)

# 2026-09-14 调价链路实测（preview 34 / dry-run 记录 2）：goto 后 600ms 正文
# 只有 338 字符且无卖价/底价信号，被单次判定误报 not_pricing_page。这里用
# 逐次读取推进的 bodies 模拟「主内容异步渲染」。
SLOW_RENDER_BODIES = [
    "eBooking 携程酒店eBooking",
    "日历 · 携程酒店eBooking\n快速选择房型",
    CALENDAR_BODY,
]

PRICE_BLOCK_EVAL_RESULT = {
    "status": "ok",
    "day_column": "09-14",
    "candidates": [
        {
            "text": "奢华三卧套房 09-14 卖价 1013 底价 900 不限量 已开房",
            "nearby_text": "卖价 1013 底价 900",
            "room_text": "奢华三卧套房",
            "score": 12,
            "day_column": "09-14",
            "selector_hint": "#price-block-1",
        }
    ],
}

READ_REQUEST = {
    "request_id": "test-read-001",
    "actions": [
        {
            "operation": "read_current_room_price",
            "platform": "ctrip",
            "room_type": "奢华三卧套房",
            "checkin_date": "2026-09-14",
            "checkout_date": "2026-09-15",
            "proposed_price_yuan": 1013,
            "proposed_base_price_yuan": 900,
            "commission_rate": 0.12,
        }
    ],
}

WRITE_REQUEST = {
    "request_id": "test-write-001",
    "actions": [
        {
            "operation": "set_room_price",
            "platform": "ctrip",
            "room_type": "奢华三卧套房",
            "checkin_date": "2026-09-14",
            "checkout_date": "2026-09-15",
            "proposed_price_yuan": 1013,
            "proposed_base_price_yuan": "900",
        }
    ],
}


class _FakeBodyLocator:
    def __init__(self, page: "_FakeFlowPage") -> None:
        self._page = page

    async def inner_text(self, timeout: int | None = None) -> str:
        page = self._page
        index = min(page.read_count, len(page.bodies) - 1)
        page.read_count += 1
        return page.bodies[index]


class _FakeLocator:
    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return 0

    async def is_visible(self) -> bool:
        return False

    async def click(self, *args, **kwargs):
        raise AssertionError("无弹窗时不应点击")


class _FakeFlowPage:
    """按读取次数依次返回 bodies，模拟异步渲染中的日历页。"""

    def __init__(self, *, url: str = CALENDAR_URL, bodies: list[str]) -> None:
        self.url = url
        self.bodies = list(bodies)
        self.read_count = 0
        self.goto_urls: list[str] = []

    def locator(self, selector: str, *args, **kwargs):
        if selector == "body":
            return _FakeBodyLocator(self)
        return _FakeLocator()

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def wait_for_timeout(self, ms: float) -> None:
        await asyncio.sleep(0)

    async def title(self) -> str:
        return "日历 · 携程酒店eBooking"

    async def evaluate(self, script: str, arg=None):
        if "price_block_candidates.js" in script:
            return PRICE_BLOCK_EVAL_RESULT
        if "querySelectorAll" in script:
            return []
        return {}


class _FakeService:
    def __init__(self, page: _FakeFlowPage) -> None:
        self._page = page
        self.page_calls: list[dict] = []

    async def _get_or_create_page(self, **kwargs) -> _FakeFlowPage:
        self.page_calls.append(kwargs)
        return self._page


def _flow_module():
    return importlib.import_module("app.domain.browser.ctrip_pricing_write_flow")


def _selector_module():
    return importlib.import_module("app.domain.browser.ctrip_room_type_selector")


def _fast_bounded_wait():
    """压低有界等待上限，避免负例测试真实等待 15 秒。"""
    selector = _selector_module()
    original = selector.wait_for_ctrip_pricing_page_state

    async def fast_wait(page, **kwargs):
        return await original(page, timeout_s=0.05, poll_s=0.01)

    return fast_wait


class ReadCurrentRatesFlowWaitTests(unittest.TestCase):
    """读现价流程：慢渲染页必须等信号收敛，而不是 600ms 后误报结构变化。"""

    def test_slow_render_page_waits_and_reads_current_rate(self) -> None:
        flow = _flow_module()
        page = _FakeFlowPage(bodies=SLOW_RENDER_BODIES)
        service = _FakeService(page)

        selected_calls: list[dict] = []

        async def fake_select(page, *, room_type: str):
            selected_calls.append({"room_type": room_type})
            return {"status": "selected", "reason": "room_type_selected"}

        with mock.patch.object(flow, "_select_pricing_room_type_read_only", fake_select):
            result = asyncio.run(
                flow.read_pricing_calendar_current_rates_flow(
                    service,
                    platform_account_id="15900000000",
                    browser_session_id="browser-test-read",
                    actor_id="test",
                    execute_request=READ_REQUEST,
                )
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(page.goto_urls, [CALENDAR_URL])
        self.assertEqual(selected_calls, [{"room_type": "奢华三卧套房"}])
        self.assertGreaterEqual(page.read_count, 3, "应等待主内容渲染而非首轮即判失败")
        targets = result.data["page_read"]["targets"]
        self.assertEqual(targets[0]["status"], "located")
        self.assertEqual(targets[0]["current_price_yuan"], 1013)
        self.assertEqual(targets[0]["current_base_price_yuan"], 900)

    def test_permanently_unrecognized_page_still_reports_structure_change(self) -> None:
        flow = _flow_module()
        page = _FakeFlowPage(bodies=["eBooking 携程酒店eBooking"])
        service = _FakeService(page)
        selector = _selector_module()

        async def fake_select(page, *, room_type: str):
            raise AssertionError("结构变化时应在选择房型前返回")

        with mock.patch.object(
            selector, "wait_for_ctrip_pricing_page_state", _fast_bounded_wait()
        ), mock.patch.object(flow, "_select_pricing_room_type_read_only", fake_select):
            result = asyncio.run(
                flow.read_pricing_calendar_current_rates_flow(
                    service,
                    platform_account_id="15900000000",
                    browser_session_id="browser-test-read",
                    actor_id="test",
                    execute_request=READ_REQUEST,
                )
            )

        self.assertEqual(result.status, "page_structure_changed")
        self.assertEqual(result.evidence["blocked_reason"], "not_pricing_page")


class DryRunFlowWaitTests(unittest.TestCase):
    """页面写入空跑：慢渲染页等待信号后再读，定位成功才算 preview ready。"""

    def test_slow_render_page_waits_and_builds_preview(self) -> None:
        flow = _flow_module()
        page = _FakeFlowPage(bodies=SLOW_RENDER_BODIES)
        service = _FakeService(page)

        result = asyncio.run(
            flow.prepare_pricing_page_write_dry_run_flow(
                service,
                platform_account_id="15900000000",
                browser_session_id="browser-test-dryrun",
                actor_id="test",
                execute_request=WRITE_REQUEST,
            )
        )

        self.assertEqual(result.status, "page_write_preview_ready")
        self.assertGreaterEqual(page.read_count, 3, "应等待主内容渲染而非首轮即判失败")


class ExecuteFlowWaitTests(unittest.TestCase):
    """执行流程：慢渲染页必须等到 pricing_page 才继续房型选择与写价。"""

    def test_slow_render_page_waits_then_executes(self) -> None:
        flow = _flow_module()
        selector = _selector_module()
        executor = importlib.import_module("app.domain.browser.ctrip_pricing_executor")
        page = _FakeFlowPage(bodies=SLOW_RENDER_BODIES)
        service = _FakeService(page)

        class _FakeStore:
            def get(self, browser_session_id: str):
                return object()

        async def fake_trigger_detect(page):
            return [{"score": 5, "selector_hint": "#trigger", "text": "快速选择房型"}]

        async def fake_true(*args, **kwargs):
            return True

        async def fake_popup(page):
            return {"search_inputs": [{"selector_hint": "#search", "placeholder": "房型"}]}

        async def fake_fill(page, *, target, base_price):
            return {"status": "filled", "base_price": base_price}

        async def fake_submit(page, *, targets, artifact_dir, platform_account_id):
            return {"status": "executed", "submitted": len(targets)}

        with mock.patch.object(
            flow, "get_browser_session_store", lambda: _FakeStore()
        ), mock.patch.object(selector, "detect_room_type_trigger", fake_trigger_detect), \
            mock.patch.object(selector, "safe_click", fake_true), \
            mock.patch.object(selector, "detect_room_type_popup", fake_popup), \
            mock.patch.object(selector, "select_matching_room_results", fake_true), \
            mock.patch.object(selector, "click_room_selection_confirm", fake_true), \
            mock.patch.object(selector, "click_first_price_block", fake_true), \
            mock.patch.object(executor, "fill_ctrip_room_price", fake_fill), \
            mock.patch.object(executor, "submit_ctrip_price_changes", fake_submit):
            result = asyncio.run(
                flow.execute_pricing_page_write_flow(
                    service,
                    platform_account_id="15900000000",
                    browser_session_id="browser-test-exec",
                    actor_id="test",
                    execute_request=WRITE_REQUEST,
                )
            )

        self.assertEqual(result.status, "executed")
        # 首个 body（仅外壳导航）无信号；「快速选择房型」动作词出现即收敛，
        # 至少要等到第二个 body 才能进入房型选择，证明没有首轮即判失败。
        self.assertGreaterEqual(page.read_count, 2, "应等待主内容渲染而非首轮即判失败")


if __name__ == "__main__":
    unittest.main()
