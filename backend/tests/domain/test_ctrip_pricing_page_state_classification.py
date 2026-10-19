from __future__ import annotations

import asyncio
import importlib
import unittest


CALENDAR_URL = "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar?microJump=true"
BUSINESS_REPORT_URL = "https://ebooking.ctrip.com/report/businessreport"
BATCH_PRICE_URL = "https://ebooking.ctrip.com/rateplan/batchPriceSetting?microJump=true"

# 实测样本结构（验证报告 2026-08-24 §三）：经营报告页正文含左侧导航
# "房价房态/日历"与"平均房价"等经营词——旧词表下任意两词命中即误判 pricing_page。
BUSINESS_REPORT_BODY = (
    "eBooking 携程酒店eBooking\n经营报告\n房价房态\n日历\n订单\n房量\n数据中心\n"
    "昨日经营概况\n间夜量\n营业额\n出租率\n平均房价\nrevpar\n近7日\n近30日\n导出"
)

# 日历页正文样本（验证报告 §三-2）：主内容区含"快速选择房型"触发器与房态/房量/
# 卖价/底价列。
CALENDAR_BODY = (
    "日历 · 携程酒店eBooking\n快速选择房型\n今天\n明天\n本周\n"
    "房态\n房量\n卖价\n底价\n修改价格\n按周设置\n保存"
)

# 批量页正文样本（验证报告 §三-3）：含"设置价格/卖价+佣金/房型"，但不是执行面。
BATCH_PRICE_BODY = (
    "房价维护\n选择房型\n输入名称或者ID\n要修改的日期\n设置价格\n"
    "分开改价\n统一加减价\n卖价+佣金\n底价+佣金\n保存"
)


class CtripPricingPageStateTests(unittest.TestCase):
    """B3 — 携程页面态分类词表统一收紧。"""

    def _selector(self):
        return importlib.import_module("app.domain.browser.ctrip_room_type_selector")

    def _reader(self):
        return importlib.import_module("app.domain.browser.ctrip_pricing_page_reader")

    def test_calendar_page_classified_as_pricing_page(self) -> None:
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(CALENDAR_URL, CALENDAR_BODY),
            "pricing_page",
        )

    def test_business_report_page_not_classified_as_pricing_page(self) -> None:
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(
                BUSINESS_REPORT_URL, BUSINESS_REPORT_BODY
            ),
            "authenticated_non_pricing_page",
        )

    def test_batch_price_page_not_classified_as_pricing_page(self) -> None:
        """批量页不是携程执行面（方案 A：仅日历页），不得判 pricing_page。"""
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(BATCH_PRICE_URL, BATCH_PRICE_BODY),
            "authenticated_non_pricing_page",
        )

    def test_navigation_terms_alone_do_not_make_pricing_page(self) -> None:
        """左侧导航词（房价房态/日历）在任何 URL 下都不足以判 pricing_page。"""
        selector = self._selector()
        nav_only_body = "eBooking 携程酒店eBooking\n房价房态\n日历\n订单\n房量\n首页"
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(CALENDAR_URL, nav_only_body),
            "authenticated_non_pricing_page",
        )

    def test_empty_body_on_calendar_url_is_unknown(self) -> None:
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(CALENDAR_URL, ""),
            "unknown",
        )

    def test_login_and_verification_priority_preserved(self) -> None:
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(
                "https://passport.ctrip.com/verify?token=1", CALENDAR_BODY
            ),
            "verification_required",
        )
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(
                CALENDAR_URL, "登录\n密码\n手机验证码登录"
            ),
            "login_required",
        )

    def test_non_ctrip_url_is_unknown(self) -> None:
        selector = self._selector()
        self.assertEqual(
            selector.classify_ctrip_pricing_page_state(
                "https://example.com/ebkovsroom/inventory/calendar", CALENDAR_BODY
            ),
            "unknown",
        )

    def test_reader_reuses_selector_implementation(self) -> None:
        """两份词表/classify 收敛为单一实现，不再漂移。"""
        reader = self._reader()
        selector = self._selector()
        self.assertIs(reader.classify_ctrip_pricing_page_state,
                      selector.classify_ctrip_pricing_page_state)
        self.assertFalse(hasattr(reader, "_PRICING_PAGE_TERMS"))


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


class _FakeBodyLocator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def inner_text(self, timeout: int | None = None) -> str:
        page = self._page
        index = min(page.read_count, len(page.bodies) - 1)
        page.read_count += 1
        return page.bodies[index]


class _FakePage:
    """按读取次数依次返回 bodies，模拟异步渲染中的日历页。"""

    def __init__(self, *, url: str, bodies: list[str]) -> None:
        self.url = url
        self.bodies = list(bodies)
        self.read_count = 0
        self.wait_count = 0

    def locator(self, selector: str, *args, **kwargs):
        if selector == "body":
            return _FakeBodyLocator(self)
        return _FakeLocator()

    async def wait_for_timeout(self, ms: float) -> None:
        self.wait_count += 1
        await asyncio.sleep(0)


class WaitForPricingPageStateTests(unittest.TestCase):
    """2026-09-14：日历页主内容异步渲染（叠加消息弹窗/节假日提醒横幅），
    goto+固定 600ms 后正文常无房价信号，被判 not_pricing_page 误报
    「页面结构变化」。改为有界等待信号收敛，超时才判结构变化。"""

    def _selector(self):
        return importlib.import_module("app.domain.browser.ctrip_room_type_selector")

    def test_signals_present_returns_immediately(self) -> None:
        async def scenario() -> tuple[str, _FakePage]:
            page = _FakePage(url=CALENDAR_URL, bodies=[CALENDAR_BODY])
            state = await self._selector().wait_for_ctrip_pricing_page_state(
                page, timeout_s=5.0, poll_s=0.01
            )
            return state, page

        state, page = asyncio.run(scenario())
        self.assertEqual(state, "pricing_page")
        self.assertEqual(page.read_count, 1)
        self.assertEqual(page.wait_count, 0)

    def test_slow_render_polls_until_signals_appear(self) -> None:
        async def scenario() -> tuple[str, _FakePage]:
            page = _FakePage(
                url=CALENDAR_URL,
                bodies=["", "日历 · 携程酒店eBooking\n全日房", CALENDAR_BODY],
            )
            state = await self._selector().wait_for_ctrip_pricing_page_state(
                page, timeout_s=5.0, poll_s=0.01
            )
            return state, page

        state, page = asyncio.run(scenario())
        self.assertEqual(state, "pricing_page")
        self.assertEqual(page.read_count, 3)
        self.assertGreaterEqual(page.wait_count, 2)

    def test_timeout_returns_last_non_pricing_state(self) -> None:
        async def scenario() -> tuple[str, _FakePage]:
            page = _FakePage(url=CALENDAR_URL, bodies=[BUSINESS_REPORT_BODY])
            state = await self._selector().wait_for_ctrip_pricing_page_state(
                page, timeout_s=0.05, poll_s=0.01
            )
            return state, page

        state, page = asyncio.run(scenario())
        self.assertEqual(state, "authenticated_non_pricing_page")
        self.assertGreaterEqual(page.read_count, 2)

    def test_login_required_is_terminal_without_waiting(self) -> None:
        async def scenario() -> tuple[str, _FakePage]:
            page = _FakePage(
                url="https://passport.ctrip.com/login?backurl=calendar",
                bodies=["登录\n密码\n手机验证码登录"],
            )
            state = await self._selector().wait_for_ctrip_pricing_page_state(
                page, timeout_s=5.0, poll_s=0.01
            )
            return state, page

        state, page = asyncio.run(scenario())
        self.assertEqual(state, "login_required")
        self.assertEqual(page.read_count, 1)
        self.assertEqual(page.wait_count, 0)


if __name__ == "__main__":
    unittest.main()
