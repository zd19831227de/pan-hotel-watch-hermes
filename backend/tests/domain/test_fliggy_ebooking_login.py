from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

FLIGGY_EBK_LOGIN_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"
)


class FliggyEbookingLoginApiTests(unittest.TestCase):
    def test_open_login_uses_fliggy_session_and_returns_safe_payload(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_api",
                    "display_name": "Fliggy API",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9531,
                },
            )
            self.assertEqual(created.status_code, 200)
            self.assertFalse(created.json()["can_auto_write_price"])
            fliggy_api = importlib.import_module("app.api.v1.fliggy_ebooking")
            fake = _FakeFliggyService()
            original = fliggy_api.get_fliggy_ebooking_service
            fliggy_api.get_fliggy_ebooking_service = lambda: fake
            try:
                response = client.post(
                    "/api/v1/fliggy-ebooking/sessions/fliggy_api/open-login",
                    json={"browser_session_id": created.json()["browser_session_id"]},
                )
            finally:
                fliggy_api.get_fliggy_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["platform"], "fliggy")
            self.assertEqual(payload["login_url"], FLIGGY_EBK_LOGIN_URL)
            self.assertEqual(payload["evidence"]["profile_path_returned"], False)
            self.assertEqual(payload["evidence"]["password_returned"], False)
            self.assertNotIn("persistent_profile_dir", payload["evidence"])
            self.assertEqual(
                fake.calls,
                [
                    {
                        "platform_account_id": "fliggy_api",
                        "browser_session_id": created.json()["browser_session_id"],
                        "actor_id": "operator",
                        "headless": False,
                    }
                ],
            )

    def test_open_login_rejects_ctrip_session(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_api",
                    "display_name": "Ctrip API",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "session_port": 9512,
                },
            )

            response = client.post(
                "/api/v1/fliggy-ebooking/sessions/ctrip_api/open-login",
                json={},
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("Fliggy", response.json()["detail"])

    def test_fliggy_non_direct_writable_after_b8_whitelist(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_nd",
                    "display_name": "Fliggy ND",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "non_direct",
                    "session_port": 9532,
                },
            )
            self.assertEqual(created.status_code, 200)
            self.assertTrue(created.json()["can_auto_write_price"])


class FliggyEbookingLaunchChannelTests(unittest.TestCase):
    def test_os_chrome_cdp_launch_args_open_login_without_automation_flag(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        args = fliggy._fliggy_os_chrome_launch_args(
            chrome_path=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            profile_dir=Path("C:/tmp/fliggy-profile"),
            session_port=9531,
            login_url=FLIGGY_EBK_LOGIN_URL,
        )

        self.assertEqual(
            args[0],
            str(Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
        )
        self.assertIn("--remote-debugging-port=9531", args)
        self.assertIn(f"--user-data-dir={Path('C:/tmp/fliggy-profile')}", args)
        self.assertIn("--no-first-run", args)
        self.assertIn("--no-default-browser-check", args)
        self.assertIn(FLIGGY_EBK_LOGIN_URL, args)
        self.assertNotIn("--enable-automation", args)

    def test_profile_dir_is_absolute_under_browser_state_dir(self) -> None:
        with _isolated_browser_state() as state:
            settings = importlib.import_module("app.core.settings")
            settings.get_settings.cache_clear()
            fliggy = importlib.reload(importlib.import_module("app.domain.browser.fliggy_ebooking"))
            service = fliggy.FliggyEbookingBrowserService()
            profile_dir = service._profile_dir("fliggy_abs")
            browser_state = Path(settings.get_settings().browser_state_dir).resolve()
            self.assertTrue(profile_dir.is_absolute())
            self.assertEqual(profile_dir, (browser_state / "fliggy-ebooking" / "fliggy_abs").resolve())
            args = fliggy._fliggy_os_chrome_launch_args(
                chrome_path=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                profile_dir=profile_dir,
                session_port=9531,
                login_url=FLIGGY_EBK_LOGIN_URL,
            )
            user_data = next(item for item in args if item.startswith("--user-data-dir="))
            self.assertTrue(Path(user_data.split("=", 1)[1]).is_absolute())

    def test_os_chrome_cdp_launch_restores_previous_session(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        args = fliggy._fliggy_os_chrome_launch_args(
            chrome_path=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            profile_dir=Path("C:/tmp/fliggy-profile"),
            session_port=9531,
            login_url="about:blank",
        )

        self.assertIn("--restore-last-session", args)

    def test_page_creation_starts_os_chrome_then_attaches_via_cdp(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")

        async def _run() -> None:
            service = fliggy.FliggyEbookingBrowserService()
            fake_page = MagicMock()
            fake_page.is_closed.return_value = False
            subprocess_calls: list[list[str]] = []

            session = MagicMock()
            session.session_port = 9531
            store = MagicMock()
            store.get.return_value = session

            async def connect_side_effect(**kwargs: Any) -> MagicMock | None:
                if not subprocess_calls:
                    return None
                return fake_page

            def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
                subprocess_calls.append(list(args))
                return MagicMock()

            with (
                patch.object(fliggy, "get_browser_session_store", return_value=store),
                patch.object(
                    service,
                    "_connect_existing_debug_page",
                    new=AsyncMock(side_effect=connect_side_effect),
                ),
                patch.object(service, "_get_playwright", new=AsyncMock(return_value=MagicMock())),
                patch.object(service, "_profile_dir", return_value=Path("C:/tmp/fliggy-profile")),
                patch.object(
                    fliggy,
                    "_resolve_installed_chrome_path",
                    return_value=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                ),
                patch.object(fliggy, "_wait_for_cdp_ready", new=AsyncMock(return_value=True)),
                patch.object(fliggy.subprocess, "Popen", side_effect=fake_popen),
            ):
                page = await service._get_or_create_page_unlocked(
                    platform_account_id="fliggy_api",
                    browser_session_id="browser-fliggy",
                    headless=False,
                )

            self.assertIs(page, fake_page)
            self.assertEqual(len(subprocess_calls), 1)
            self.assertIn("--remote-debugging-port=9531", subprocess_calls[0])
            # Read/reuse path must not cold-start on the EBK URL — that races cookies
            # and often lands on login.htm even when the persistent profile is logged in.
            self.assertIn("about:blank", subprocess_calls[0])
            self.assertNotIn(FLIGGY_EBK_LOGIN_URL, subprocess_calls[0])

        asyncio.run(_run())

    def test_read_dataset_retries_once_when_first_navigation_lands_on_login(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")

        async def _run() -> None:
            service = fliggy.FliggyEbookingBrowserService()
            page = MagicMock()
            page.url = "https://hotel.fliggy.com/ebooking/login.htm#/?redirectURL=x"
            page.goto = AsyncMock()
            page.is_closed.return_value = False

            goto_urls: list[str] = []

            async def fake_goto(url: str, **kwargs: Any) -> None:
                goto_urls.append(url)
                if len(goto_urls) == 1:
                    page.url = (
                        "https://hotel.fliggy.com/ebooking/login.htm"
                        "#/?redirectURL=https%3A%2F%2Fhotel.fliggy.com%2Febooking%2FhotelBaseInfoUv.htm"
                    )
                else:
                    page.url = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"

            page.goto.side_effect = fake_goto

            session = MagicMock()
            session.session_port = 9532
            store = MagicMock()
            store.get.return_value = session
            store.mark_authenticated = MagicMock()

            with (
                patch.object(fliggy, "get_browser_session_store", return_value=store),
                patch.object(
                    service,
                    "_get_or_create_page",
                    new=AsyncMock(return_value=page),
                ),
                patch.object(fliggy, "_settle_page", new=AsyncMock()),
                patch.object(fliggy, "_safe_visible_text", new=AsyncMock(return_value="")),
                patch.object(
                    fliggy,
                    "_capture_fliggy_home_text",
                    new=AsyncMock(return_value="退出登录 今日数据 昨日数据 酒店名称"),
                ),
                patch.object(
                    fliggy,
                    "parse_fliggy_dataset_text",
                    return_value={
                        "status": "success",
                        "summary": "ok",
                        "data": {"hotel_name": "曼馨"},
                        "evidence": {},
                    },
                ),
            ):
                result = await service.read_dataset(
                    platform_account_id="manxin-feizhu",
                    browser_session_id="browser-manxin-feizhu",
                    actor_id="operator",
                    dataset="home_v1",
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(goto_urls), 2)
            self.assertTrue(all("homeV1" in url or "hotelBaseInfoUv" in url for url in goto_urls))

        asyncio.run(_run())


class FliggyEbookingLoginStateTests(unittest.TestCase):
    def test_classifier_treats_authenticated_ebk_home_as_logged_in(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        state = fliggy._classify_fliggy_page_state(
            "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1",
            "退出登录 今日数据 昨日数据",
        )
        self.assertEqual(state, "authenticated")

    def test_classifier_treats_login_markers_as_login_or_human_required(self) -> None:
        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        self.assertEqual(
            fliggy._classify_fliggy_page_state(
                "https://login.taobao.com/member/login.jhtml",
                "账号密码登录",
            ),
            "login",
        )
        self.assertEqual(
            fliggy._classify_fliggy_page_state(
                "https://login.taobao.com/member/login.jhtml",
                "请向右拖动滑块",
            ),
            "human_required",
        )

    def test_select_fliggy_page_prefers_authenticated_home_over_login(self) -> None:
        from unittest.mock import MagicMock

        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")
        login_page = MagicMock()
        login_page.is_closed.return_value = False
        login_page.url = "https://login.taobao.com/member/login.jhtml"
        home_page = MagicMock()
        home_page.is_closed.return_value = False
        home_page.url = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"

        selected = fliggy._select_fliggy_page([login_page, home_page])
        self.assertIs(selected, home_page)

    def test_check_login_state_marks_authenticated_when_page_is_logged_in(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "fliggy_check",
                    "display_name": "Fliggy Check",
                    "platform": "fliggy",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9533,
                },
            )
            self.assertEqual(created.status_code, 200)
            browser_session_id = created.json()["browser_session_id"]
            fliggy_api = importlib.import_module("app.api.v1.fliggy_ebooking")

            class _FakeCheckService:
                async def check_login_state(self, **kwargs: Any) -> dict[str, Any]:
                    store = importlib.import_module("app.domain.browser.sessions").get_browser_session_store()
                    store.mark_authenticated(
                        kwargs["browser_session_id"],
                        kwargs["actor_id"],
                        action="Fliggy EBK login confirmed.",
                    )
                    return {
                        "status": "success",
                        "summary": "Fliggy EBK login state is authenticated.",
                        "platform": "fliggy",
                        "platform_account_id": kwargs["platform_account_id"],
                        "browser_session_id": kwargs["browser_session_id"],
                        "login_url": FLIGGY_EBK_LOGIN_URL,
                        "manual_login_required": False,
                        "evidence": {
                            "profile_path_returned": False,
                            "password_returned": False,
                            "page_state": "authenticated",
                        },
                        "next_action": "Fliggy login is ready for later read-only capture.",
                    }

            original = fliggy_api.get_fliggy_ebooking_service
            fliggy_api.get_fliggy_ebooking_service = lambda: _FakeCheckService()
            try:
                response = client.post(
                    "/api/v1/fliggy-ebooking/sessions/fliggy_check/check-login-state",
                    json={"browser_session_id": browser_session_id},
                )
            finally:
                fliggy_api.get_fliggy_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            sessions = client.get("/api/v1/browser-sessions")
            self.assertEqual(sessions.status_code, 200)
            fliggy_session = next(
                item
                for item in sessions.json()["sessions"]
                if item["platform_account_id"] == "fliggy_check"
            )
            self.assertEqual(fliggy_session["login_state"], "logged_in")


class _FakeFliggyService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def open_login(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        headless: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "platform_account_id": platform_account_id,
                "browser_session_id": browser_session_id,
                "actor_id": actor_id,
                "headless": headless,
            }
        )
        return {
            "status": "human_required",
            "summary": "Fliggy EBK login page is open. Complete login manually.",
            "platform": "fliggy",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "login_url": FLIGGY_EBK_LOGIN_URL,
            "manual_login_required": True,
            "evidence": {
                "profile_path_returned": False,
                "password_returned": False,
            },
            "next_action": "Complete Fliggy login in the opened browser.",
        }


class _isolated_browser_state:
    def __enter__(self) -> "_isolated_browser_state":
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_browser_state_dir = os.environ.get("BROWSER_STATE_DIR")
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["BROWSER_STATE_DIR"] = str(Path(self._tmp.name) / "browser-state")
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'shops.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        importlib.reload(importlib.import_module("app.db.session"))
        from tests._db_isolation import create_isolated_test_shop

        self.shop_key = create_isolated_test_shop("EBK 接口测试店铺")
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        importlib.reload(importlib.import_module("app.api.v1.browser_sessions"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_browser_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_browser_state_dir
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        importlib.reload(importlib.import_module("app.domain.browser.sessions"))
        self._tmp.cleanup()


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


class FliggyOpenLoginParkedLoginPageTests(unittest.TestCase):
    """停在 login.htm 的陈旧标签页不代表真实登录态（2026-09-23 立瑞案例）。

    标签页可能是早前服务端会话过期时被弹回的登录页；之后运营已重新登录、
    Cookie 有效。open_login 不能直接亮出这张陈旧登录页——必须先导航到
    EBK 首页：Cookie 有效则展示已登录首页，无效才会被弹回登录页。
    """

    def _run_open_login(self, start_url: str) -> list[str]:
        import asyncio
        from unittest import mock

        fliggy = importlib.import_module("app.domain.browser.fliggy_ebooking")

        class FakePage:
            def __init__(self, url: str) -> None:
                self.url = url
                self.goto_urls: list[str] = []

            async def goto(self, url: str, **kwargs: Any) -> None:
                self.goto_urls.append(url)
                self.url = url

        page = FakePage(start_url)

        async def fake_get_or_create_page(**kwargs: Any) -> Any:
            return page

        async def fake_sync(**kwargs: Any) -> dict[str, Any]:
            return {"status": "synced"}

        async def fake_settle(target: Any) -> None:
            return None

        service = fliggy.FliggyEbookingBrowserService()
        with mock.patch.object(
            service, "_get_or_create_page", new=fake_get_or_create_page
        ), mock.patch.object(
            service, "_sync_login_state_from_page", new=fake_sync
        ), mock.patch.object(
            fliggy, "_settle_page", new=fake_settle
        ):
            asyncio.run(
                service.open_login(
                    platform_account_id="hotel1100001",
                    browser_session_id="browser-hotel1100001",
                    actor_id="operator",
                )
            )
        return page.goto_urls

    def test_open_login_navigates_away_from_parked_login_page(self) -> None:
        goto_urls = self._run_open_login(
            "https://hotel.fliggy.com/ebooking/login.htm#/?redirectURL=https%3A%2F%2Fhotel.fliggy.com"
        )
        self.assertEqual(goto_urls, [FLIGGY_EBK_LOGIN_URL])

    def test_open_login_keeps_existing_ebk_page(self) -> None:
        goto_urls = self._run_open_login(
            "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/homeV1"
        )
        self.assertEqual(goto_urls, [])


if __name__ == "__main__":
    unittest.main()
