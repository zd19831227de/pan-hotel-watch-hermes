from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


class MeituanEbookingLoginApiTests(unittest.TestCase):
    def test_open_login_uses_meituan_session_and_returns_safe_payload(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_api",
                    "display_name": "Meituan API",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9511,
                },
            )
            self.assertEqual(created.status_code, 200)
            meituan_api = importlib.import_module("app.api.v1.meituan_ebooking")
            fake = _FakeMeituanService()
            original = meituan_api.get_meituan_ebooking_service
            meituan_api.get_meituan_ebooking_service = lambda: fake
            try:
                response = client.post(
                    "/api/v1/meituan-ebooking/sessions/meituan_api/open-login",
                    json={"browser_session_id": created.json()["browser_session_id"]},
                )
            finally:
                meituan_api.get_meituan_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["platform"], "meituan")
            self.assertEqual(payload["login_url"], "https://eb.meituan.com/ebk/login/login.html")
            self.assertEqual(payload["evidence"]["profile_path_returned"], False)
            self.assertEqual(payload["evidence"]["password_returned"], False)
            self.assertNotIn("persistent_profile_dir", payload["evidence"])
            self.assertEqual(
                fake.calls,
                [
                    {
                        "platform_account_id": "meituan_api",
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
                "/api/v1/meituan-ebooking/sessions/ctrip_api/open-login",
                json={},
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("Meituan", response.json()["detail"])


class MeituanEbookingLaunchChannelTests(unittest.TestCase):
    def test_os_chrome_cdp_launch_args_open_login_without_automation_flag(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        args = meituan._meituan_os_chrome_launch_args(
            chrome_path=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            profile_dir=Path("C:/tmp/meituan-profile"),
            session_port=9521,
            login_url="https://eb.meituan.com/ebk/login/login.html",
        )

        self.assertEqual(
            args[0],
            str(Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
        )
        self.assertIn("--remote-debugging-port=9521", args)
        self.assertIn(f"--user-data-dir={Path('C:/tmp/meituan-profile')}", args)
        self.assertIn("--no-first-run", args)
        self.assertIn("--no-default-browser-check", args)
        self.assertIn("https://eb.meituan.com/ebk/login/login.html", args)
        self.assertNotIn("--enable-automation", args)

    def test_profile_dir_is_absolute_under_browser_state_dir(self) -> None:
        with _isolated_browser_state() as state:
            settings = importlib.import_module("app.core.settings")
            settings.get_settings.cache_clear()
            meituan = importlib.reload(importlib.import_module("app.domain.browser.meituan_ebooking"))
            service = meituan.MeituanEbookingBrowserService()
            profile_dir = service._profile_dir("meituan_abs")
            browser_state = Path(settings.get_settings().browser_state_dir).resolve()
            self.assertTrue(profile_dir.is_absolute())
            self.assertEqual(
                profile_dir,
                (browser_state / "meituan-ebooking" / "meituan_abs").resolve(),
            )
            args = meituan._meituan_os_chrome_launch_args(
                chrome_path=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                profile_dir=profile_dir,
                session_port=9521,
                login_url="https://eb.meituan.com/ebk/login/login.html",
            )
            user_data = next(item for item in args if item.startswith("--user-data-dir="))
            self.assertTrue(Path(user_data.split("=", 1)[1]).is_absolute())

    def test_page_creation_starts_os_chrome_then_attaches_via_cdp(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")

        async def _run() -> None:
            service = meituan.MeituanEbookingBrowserService()
            fake_page = MagicMock()
            fake_page.is_closed.return_value = False
            subprocess_calls: list[list[str]] = []

            session = MagicMock()
            session.session_port = 9521
            store = MagicMock()
            store.get.return_value = session

            async def connect_side_effect(**kwargs: Any) -> MagicMock | None:
                # First probe misses; after OS Chrome launch, CDP attaches.
                if not subprocess_calls:
                    return None
                return fake_page

            def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
                subprocess_calls.append(list(args))
                return MagicMock()

            with (
                patch.object(meituan, "get_browser_session_store", return_value=store),
                patch.object(
                    service,
                    "_connect_existing_debug_page",
                    new=AsyncMock(side_effect=connect_side_effect),
                ),
                patch.object(service, "_get_playwright", new=AsyncMock(return_value=MagicMock())),
                patch.object(service, "_profile_dir", return_value=Path("C:/tmp/meituan-profile")),
                patch.object(
                    meituan,
                    "_resolve_installed_chrome_path",
                    return_value=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                ),
                patch.object(meituan, "_wait_for_cdp_ready", new=AsyncMock(return_value=True)),
                patch.object(meituan.subprocess, "Popen", side_effect=fake_popen),
            ):
                page = await service._get_or_create_page_unlocked(
                    platform_account_id="meituan_api",
                    browser_session_id="browser-meituan",
                    headless=False,
                )

            self.assertIs(page, fake_page)
            self.assertEqual(len(subprocess_calls), 1)
            self.assertIn("--remote-debugging-port=9521", subprocess_calls[0])
            self.assertIn("https://eb.meituan.com/ebk/login/login.html", subprocess_calls[0])

        asyncio.run(_run())


class MeituanEbookingLoginStateTests(unittest.TestCase):
    def test_classifier_treats_authenticated_ebk_home_as_logged_in_even_if_text_mentions_login(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        state = meituan._classify_meituan_page_state(
            "https://eb.meituan.com/ebooking/home",
            "退出登录 账号设置 经营概览",
        )
        self.assertEqual(state, "authenticated")

    def test_classifier_treats_me_meituan_workbench_as_authenticated(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        state = meituan._classify_meituan_page_state(
            "https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html",
            "工作台 退出登录",
        )
        self.assertEqual(state, "authenticated")

    def test_select_meituan_page_prefers_authenticated_workbench_over_login(self) -> None:
        from unittest.mock import MagicMock

        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        login_page = MagicMock()
        login_page.is_closed.return_value = False
        login_page.url = "https://eb.meituan.com/ebk/login/login.html"
        workbench_page = MagicMock()
        workbench_page.is_closed.return_value = False
        workbench_page.url = (
            "https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-workbench%2Findex.html"
        )

        selected = meituan._select_meituan_page([login_page, workbench_page])
        self.assertIs(selected, workbench_page)

    def test_classifier_keeps_login_url_as_login_or_human_required(self) -> None:
        meituan = importlib.import_module("app.domain.browser.meituan_ebooking")
        self.assertEqual(
            meituan._classify_meituan_page_state(
                "https://eb.meituan.com/ebk/login/login.html",
                "账号密码登录",
            ),
            "login",
        )
        self.assertEqual(
            meituan._classify_meituan_page_state(
                "https://eb.meituan.com/ebk/login/login.html",
                "请向右拖动滑块",
            ),
            "human_required",
        )

    def test_check_login_state_marks_authenticated_when_page_is_logged_in(self) -> None:
        with _isolated_browser_state() as state:
            app = _reload_app()
            client = TestClient(app)
            created = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "meituan_check",
                    "display_name": "Meituan Check",
                    "platform": "meituan",
                    "intended_use": "ebk",
                    "shop_key": state.shop_key,
                    "connection_mode": "unknown",
                    "session_port": 9513,
                },
            )
            self.assertEqual(created.status_code, 200)
            browser_session_id = created.json()["browser_session_id"]
            meituan_api = importlib.import_module("app.api.v1.meituan_ebooking")

            class _FakeCheckService:
                async def check_login_state(self, **kwargs: Any) -> dict[str, Any]:
                    store = importlib.import_module("app.domain.browser.sessions").get_browser_session_store()
                    store.mark_authenticated(
                        kwargs["browser_session_id"],
                        kwargs["actor_id"],
                        action="Meituan EBK login confirmed.",
                    )
                    return {
                        "status": "success",
                        "summary": "Meituan EBK login state is authenticated.",
                        "platform": "meituan",
                        "platform_account_id": kwargs["platform_account_id"],
                        "browser_session_id": kwargs["browser_session_id"],
                        "login_url": "https://eb.meituan.com/ebk/login/login.html",
                        "manual_login_required": False,
                        "evidence": {
                            "profile_path_returned": False,
                            "password_returned": False,
                            "page_state": "authenticated",
                        },
                        "next_action": "Meituan login is ready for later read-only capture.",
                    }

            original = meituan_api.get_meituan_ebooking_service
            meituan_api.get_meituan_ebooking_service = lambda: _FakeCheckService()
            try:
                response = client.post(
                    "/api/v1/meituan-ebooking/sessions/meituan_check/check-login-state",
                    json={"browser_session_id": browser_session_id},
                )
            finally:
                meituan_api.get_meituan_ebooking_service = original

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            sessions = client.get("/api/v1/browser-sessions")
            self.assertEqual(sessions.status_code, 200)
            meituan_session = next(
                item
                for item in sessions.json()["sessions"]
                if item["platform_account_id"] == "meituan_check"
            )
            self.assertEqual(meituan_session["login_state"], "logged_in")


class _FakeMeituanService:
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
            "summary": "Meituan EBK login page is open. Complete login manually.",
            "platform": "meituan",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "login_url": "https://eb.meituan.com/ebk/login/login.html",
            "manual_login_required": True,
            "evidence": {
                "profile_path_returned": False,
                "password_returned": False,
            },
            "next_action": "Complete Meituan login in the opened browser.",
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


if __name__ == "__main__":
    unittest.main()
