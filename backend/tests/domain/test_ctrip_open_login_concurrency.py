"""携程 EBK open-login 并发导航互踩回归（2026-10-08 生产事故）。

生产日志形态：open-login POST 连续成串、200/500 交错，traceback 落在
`Page.goto: net::ERR_ABORTED`。根因＝操作员连点「打开登录」时，后一个
open_login 复用同一缓存页发起新 goto，把上一次仍在加载的导航打断；被打断
的请求未捕获直达 API 层变成裸 500「Internal Server Error」。

锁定两个行为：
- 并发 open_login 按页串行（同一缓存页的 goto 不允许重叠）；
- ERR_ABORTED 类瞬态打断有界重试一次（覆盖跨进程残余竞态）。
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from playwright.async_api import Error as PlaywrightError

from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService


class _FakePage:
    """goto 记录事件序列；可注入首跳 ERR_ABORTED。"""

    def __init__(self, *, abort_first: bool = False) -> None:
        self.url = "https://ebooking.ctrip.com/home/mainland"
        self.events: list[str] = []
        self.goto_calls = 0
        self._abort_first = abort_first

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_calls += 1
        if self._abort_first and self.goto_calls == 1:
            raise PlaywrightError(
                f"Page.goto: net::ERR_ABORTED at {url}"
            )
        self.events.append("start")
        await asyncio.sleep(0.05)
        self.events.append("end")
        self.url = url


def _service_with_page(page: _FakePage) -> CtripEbookingBrowserService:
    service = CtripEbookingBrowserService()

    # _open_login_flow 持有外层页锁后走不加锁版本（防 asyncio.Lock 重入
    # 死锁），因此这里 mock _get_or_create_page_unlocked 而非加锁版。
    async def fake_get_or_create_page_unlocked(**kwargs):
        return page

    service._get_or_create_page_unlocked = fake_get_or_create_page_unlocked  # type: ignore[method-assign]

    async def fake_settle_page(page_arg, **kwargs):
        return None

    async def fake_fill(page_arg, credentials):
        return {"attempted": False}

    async def fake_visible_text(page_arg):
        return "订单 收益 房型 房价"

    def fake_classify(url, body_text):
        return "authenticated"

    def fake_sync(**kwargs):
        return None

    store = SimpleNamespace(
        get=lambda sid: SimpleNamespace(session_port=9221),
        mark_login_window_opened=lambda *args, **kwargs: None,
    )

    patches = [
        mock.patch(
            "app.domain.browser.ctrip_ebooking._settle_page", new=fake_settle_page
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._try_fill_login_form", new=fake_fill
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._safe_visible_text",
            new=fake_visible_text,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._classify_page_state",
            new=fake_classify,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._sync_ebk_browser_session_state",
            new=fake_sync,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking.get_browser_session_store",
            return_value=store,
        ),
    ]
    for patcher in patches:
        patcher.start()
    return service, patches


def _stop_patches(patches) -> None:
    for patcher in patches:
        patcher.stop()


def _service_with_real_lock_path(page: _FakePage):
    """不 mock _get_or_create_page：走真实页锁+CDP 复用路径。

    2026-10-09 生产事故：open_login 外层拿 _lock_for(cache_key) 后，
    _get_or_create_page 内层对同一把 asyncio.Lock 二次 acquire——
    asyncio.Lock 不可重入，登录链路 100% 自死锁（浏览器永远打不开、
    请求无响应无日志）。现有用例 mock 掉了整个 _get_or_create_page，
    故此前不可见。
    """
    service = CtripEbookingBrowserService()

    async def fake_get_playwright():
        return SimpleNamespace()

    async def fake_connect_existing_debug_page(**kwargs):
        return page

    def fake_profile_dir(platform_account_id):
        import tempfile

        return Path(tempfile.mkdtemp(prefix="ctrip-ebk-test-"))

    service._get_playwright = fake_get_playwright  # type: ignore[method-assign]
    service._connect_existing_debug_page = (  # type: ignore[method-assign]
        fake_connect_existing_debug_page
    )
    service._profile_dir = fake_profile_dir  # type: ignore[method-assign]

    async def fake_settle_page(page_arg, **kwargs):
        return None

    async def fake_fill(page_arg, credentials):
        return {"attempted": False}

    async def fake_visible_text(page_arg):
        return "订单 收益 房型 房价"

    def fake_classify(url, body_text):
        return "authenticated"

    def fake_sync(**kwargs):
        return None

    store = SimpleNamespace(
        get=lambda sid: SimpleNamespace(session_port=9221),
        mark_login_window_opened=lambda *args, **kwargs: None,
    )

    patches = [
        mock.patch(
            "app.domain.browser.ctrip_ebooking._settle_page", new=fake_settle_page
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._try_fill_login_form", new=fake_fill
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._safe_visible_text",
            new=fake_visible_text,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._classify_page_state",
            new=fake_classify,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking._sync_ebk_browser_session_state",
            new=fake_sync,
        ),
        mock.patch(
            "app.domain.browser.ctrip_ebooking.get_browser_session_store",
            return_value=store,
        ),
    ]
    for patcher in patches:
        patcher.start()
    return service, patches


def _open_login_kwargs() -> dict:
    return dict(
        platform_account_id="15900000001",
        browser_session_id="browser-15900000001",
        account_phone=None,
    )


class OpenLoginConcurrencyTests(unittest.TestCase):
    def test_concurrent_open_logins_serialize_page_navigation(self) -> None:
        page = _FakePage()
        service, patches = _service_with_page(page)
        try:

            async def scenario():
                return await asyncio.gather(
                    service.open_login(**_open_login_kwargs()),
                    service.open_login(**_open_login_kwargs()),
                )

            results = asyncio.run(scenario())
        finally:
            _stop_patches(patches)
        self.assertTrue(all(result.status == "success" for result in results))
        # 串行：start/end 交替；互踩形态是 start,start,end,end。
        self.assertEqual(page.events, ["start", "end", "start", "end"])

    def test_open_login_must_not_deadlock_on_reentrant_page_lock(self) -> None:
        # 外层页锁与 _get_or_create_page 内层同一把锁重入＝死锁：
        # 登录链路必须在不超时的前提下走完（wait_for 只防测试挂死）。
        page = _FakePage()
        service, patches = _service_with_real_lock_path(page)
        try:

            async def scenario():
                return await asyncio.wait_for(
                    service.open_login(**_open_login_kwargs()), timeout=3
                )

            result = asyncio.run(scenario())
        finally:
            _stop_patches(patches)
        self.assertEqual(result.status, "success")
        self.assertEqual(page.events, ["start", "end"])

    def test_transient_abort_retried_once(self) -> None:
        page = _FakePage(abort_first=True)
        service, patches = _service_with_page(page)
        try:
            result = asyncio.run(service.open_login(**_open_login_kwargs()))
        finally:
            _stop_patches(patches)
        self.assertEqual(result.status, "success")
        self.assertEqual(page.goto_calls, 2)

    def test_persistent_navigation_error_still_raises(self) -> None:
        # 非瞬态失败（如域名解析失败）不吞：保持原有异常路径供上层定位。
        page = _FakePage()
        page_goto_calls = {"n": 0}

        async def always_fail(url: str, **kwargs) -> None:
            page_goto_calls["n"] += 1
            raise PlaywrightError(f"Page.goto: net::ERR_NAME_NOT_RESOLVED at {url}")

        page.goto = always_fail  # type: ignore[method-assign]
        service, patches = _service_with_page(page)
        try:
            with self.assertRaises(PlaywrightError):
                asyncio.run(service.open_login(**_open_login_kwargs()))
        finally:
            _stop_patches(patches)
        self.assertEqual(page_goto_calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
