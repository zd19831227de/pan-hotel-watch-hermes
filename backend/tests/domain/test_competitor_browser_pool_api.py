from __future__ import annotations

import importlib
import unittest

from fastapi.testclient import TestClient

from backend.tests.domain.test_competitor_watch_group_config import (
    _isolated_sqlite_database,
    _reload_browser_sessions,
    _reload_db_session,
    _restore_competitor_browser_session,
    _restore_ebk_browser_session,
)


class CompetitorBrowserPoolApiTests(unittest.TestCase):
    def test_get_watch_group_returns_browser_pool_status(self) -> None:
        with _isolated_sqlite_database() as db_url:
            client = _client(db_url)
            _create_watch_group(client)

            response = client.get(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch/browser-pool"
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["watch_group_key"], "ops_watch")
            self.assertIn("browser_pool", payload)
            self.assertFalse(payload["browser_pool"]["pool_enabled"])

    def test_put_browser_pool_accepts_only_competitor_price_accounts(self) -> None:
        with _isolated_sqlite_database() as db_url:
            sessions = _reload_browser_sessions()
            _restore_competitor_browser_session(
                sessions,
                browser_session_id="browser-trip-1",
                platform_account_id="trip1",
                session_port=9221,
            )
            client = _client(db_url)
            _create_watch_group(client)

            response = client.put(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch/browser-pool",
                json={"members": [{"browser_session_id": "browser-trip-1"}]},
            )

            self.assertEqual(response.status_code, 200)
            pool = response.json()["browser_pool"]
            self.assertEqual(pool["pool_size"], 1)
            self.assertEqual(pool["usable_pool_size"], 1)
            self.assertEqual(pool["members"][0]["browser_session_id"], "browser-trip-1")

    def test_put_browser_pool_rejects_ebk_account(self) -> None:
        with _isolated_sqlite_database() as db_url:
            sessions = _reload_browser_sessions()
            _restore_ebk_browser_session(
                sessions,
                browser_session_id="browser-ebk-1",
                platform_account_id="ebk1",
                session_port=9321,
            )
            client = _client(db_url)
            _create_watch_group(client)

            response = client.put(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch/browser-pool",
                json={"members": [{"browser_session_id": "browser-ebk-1"}]},
            )

            self.assertEqual(response.status_code, 422)

    def test_patch_group_switches_between_fixed_and_pool_modes(self) -> None:
        with _isolated_sqlite_database() as db_url:
            client = _client(db_url)
            _create_watch_group(client)

            pool_response = client.patch(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch",
                json={
                    "browser_rotation_mode": "pool_round_robin",
                    "pool_failover_enabled": True,
                    "pool_cooldown_minutes": 45,
                },
            )
            fixed_response = client.patch(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/ops_watch",
                json={"browser_rotation_mode": "fixed"},
            )

            self.assertEqual(pool_response.status_code, 200)
            self.assertEqual(
                pool_response.json()["watch_group"]["browser_rotation_mode"],
                "pool_round_robin",
            )
            self.assertEqual(pool_response.json()["watch_group"]["pool_cooldown_minutes"], 45)
            self.assertEqual(fixed_response.status_code, 200)
            self.assertEqual(
                fixed_response.json()["watch_group"]["browser_rotation_mode"],
                "fixed",
            )


def _client(db_url: str) -> TestClient:
    _reload_db_session(db_url)
    app = importlib.import_module("app.main")
    return TestClient(importlib.reload(app).create_app())


def _create_watch_group(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ctrip-ebooking/competitor-watch-groups",
        json={"group_key": "ops_watch", "name": "Ops watch"},
    )
    if response.status_code not in {200, 409}:
        raise AssertionError(response.text)


if __name__ == "__main__":
    unittest.main()
