from __future__ import annotations

import asyncio
import importlib
import unittest

from fastapi.testclient import TestClient

from backend.tests.domain.test_competitor_watch_group_config import (
    _configured_test_agent,
    _create_active_agent_binding,
    _isolated_sqlite_database,
    _reload_browser_sessions,
    _reload_db_session,
    _reload_repository,
    _restore_competitor_browser_session,
    _restore_ebk_browser_session,
)


class CompetitorBrowserPoolAccountLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        agent_config = _configured_test_agent()
        agent_config.start()
        self.addCleanup(agent_config.stop)

    def test_delete_competitor_price_account_removes_pool_member_without_touching_ebk_pricing_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repository = _reload_repository(db_url)
            sessions = _reload_browser_sessions()
            _restore_competitor_browser_session(
                sessions,
                browser_session_id="browser-trip-1",
                platform_account_id="trip1",
                session_port=9221,
            )
            _restore_ebk_browser_session(
                sessions,
                browser_session_id="browser-ebk-2",
                platform_account_id="ebk2",
                session_port=9322,
            )
            asyncio.run(_create_pool_watch(repository))
            client = _client(db_url)

            response = client.delete("/api/v1/browser-sessions/browser-trip-1")

            self.assertEqual(response.status_code, 200)
            group = asyncio.run(
                repository.get_competitor_watch_group_detail(watch_group_key="ops_watch")
            )
            assert group is not None
            self.assertEqual(group["pricing_browser_session_id"], "browser-ebk-2")
            self.assertEqual(group["browser_pool"]["pool_size"], 0)

    def test_cannot_change_pool_member_account_to_ebk_until_removed_from_pool(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repository = _reload_repository(db_url)
            sessions = _reload_browser_sessions()
            _restore_competitor_browser_session(
                sessions,
                browser_session_id="browser-trip-1",
                platform_account_id="trip1",
                session_port=9221,
            )
            asyncio.run(_create_pool_watch(repository, pricing_browser_session_id=None))
            from tests._db_isolation import create_isolated_test_shop

            shop_key = create_isolated_test_shop("竞品池测试店铺")
            client = _client(db_url)

            response = client.patch(
                "/api/v1/browser-sessions/browser-trip-1",
                json={"intended_use": "ebk", "shop_key": shop_key},
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("pool", response.text.lower())

    def test_account_list_shows_pool_usage_without_exposing_secrets(self) -> None:
        with _isolated_sqlite_database() as db_url:
            repository = _reload_repository(db_url)
            sessions = _reload_browser_sessions()
            _restore_competitor_browser_session(
                sessions,
                browser_session_id="browser-trip-1",
                platform_account_id="trip1",
                session_port=9221,
            )
            asyncio.run(_create_pool_watch(repository, pricing_browser_session_id=None))
            client = _client(db_url)

            response = client.get("/api/v1/browser-sessions")

            self.assertEqual(response.status_code, 200)
            account_payload = next(
                item
                for item in response.json()["sessions"]
                if item["browser_session_id"] == "browser-trip-1"
            )
            self.assertIn("competitor_pool_usage", account_payload)
            self.assertEqual(
                account_payload["competitor_pool_usage"][0]["watch_group_key"],
                "ops_watch",
            )
            self.assertEqual(
                account_payload["competitor_pool_usage"][0]["watch_group_name"],
                "Ops watch",
            )
            self.assertNotIn("webhook", str(account_payload).lower())
            self.assertNotIn("oc_secret", str(account_payload))


async def _create_pool_watch(
    repository,
    *,
    pricing_browser_session_id: str | None = "browser-ebk-2",
) -> None:
    types = importlib.import_module("app.domain.competitor_prices.types")
    await _create_active_agent_binding("oc_secret_delivery_group")
    await repository.create_competitor_watch_group(
        types.CompetitorWatchGroupCreate(
            group_key="ops_watch",
            name="Ops watch",
            browser_rotation_mode="pool_round_robin",
            pricing_browser_session_id=pricing_browser_session_id,
            feishu_chat_id="oc_secret_delivery_group",
        )
    )
    await repository.update_competitor_watch_group_browser_pool(
        watch_group_key="ops_watch",
        request=types.CompetitorBrowserPoolUpdate(
            members=[
                types.CompetitorBrowserPoolMemberIn(
                    browser_session_id="browser-trip-1",
                )
            ]
        ),
    )


def _client(db_url: str) -> TestClient:
    _reload_db_session(db_url)
    app = importlib.import_module("app.main")
    return TestClient(importlib.reload(app).create_app())


if __name__ == "__main__":
    unittest.main()
