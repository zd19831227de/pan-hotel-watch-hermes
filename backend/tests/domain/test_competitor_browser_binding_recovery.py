from __future__ import annotations

import asyncio
import importlib
import unittest

from app.domain.browser.sessions import BrowserSession
from backend.tests.domain.test_competitor_watch_group_config import (
    _isolated_sqlite_database,
    _reload_browser_sessions,
    _reload_repository,
)


def _restore_competitor_session(
    sessions,
    *,
    browser_session_id: str,
    platform_account_id: str,
    session_port: int,
    login_state: str = "logged_in",
    human_verification_status: str = "none",
) -> None:
    sessions.get_browser_session_store().restore(
        BrowserSession.model_validate(
            {
                "browser_session_id": browser_session_id,
                "platform_account_id": platform_account_id,
                "display_name": platform_account_id,
                "platform": "ctrip",
                "session_port": session_port,
                "intended_use": "competitor_price",
                "login_state": login_state,
                "human_verification_status": human_verification_status,
                "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                "takeover_url": f"/browser-control?session={browser_session_id}",
                "manual_takeover_url": (
                    f"/browser-control?session={browser_session_id}&mode=manual"
                ),
                "can_takeover": True,
                "can_logout": True,
                "last_action": "Test session.",
            }
        )
    )


class CompetitorBrowserBindingRecoveryTests(unittest.TestCase):
    def test_fixed_binding_retries_when_login_state_is_stale(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                binding = importlib.import_module(
                    "app.domain.competitor_prices.browser_binding"
                )
                binding = importlib.reload(binding)
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                    login_state="human_required",
                    human_verification_status="required",
                )
                group = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="竞品监控组",
                        browser_session_id="browser-public-1",
                    )
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="scheduler",
                )

                self.assertTrue(resolution.ok)
                assert resolution.selection is not None
                self.assertEqual(
                    resolution.selection.browser_session_id,
                    "browser-public-1",
                )
                self.assertTrue(resolution.evidence.get("login_state_retry_deferred"))
                self.assertEqual(resolution.evidence.get("login_state"), "human_required")

            asyncio.run(scenario())

    def test_enabled_watch_groups_can_share_browser_session(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-public-1",
                    platform_account_id="ctrip-public-1",
                    session_port=9321,
                )

                first = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_a",
                        name="竞品监控组 A",
                        browser_session_id="browser-public-1",
                        enabled=True,
                    )
                )
                second = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_b",
                        name="竞品监控组 B",
                        browser_session_id="browser-public-1",
                        enabled=True,
                    )
                )

                self.assertEqual(first["browser_session_id"], "browser-public-1")
                self.assertEqual(second["browser_session_id"], "browser-public-1")

            asyncio.run(scenario())


class PublicCompetitorSessionSyncTests(unittest.TestCase):
    def _restore_session(
        self,
        store,
        *,
        browser_session_id: str,
        platform_account_id: str,
        session_port: int,
        login_state: str = "logged_in",
        human_verification_status: str = "none",
    ) -> None:
        store.restore(
            BrowserSession.model_validate(
                {
                    "browser_session_id": browser_session_id,
                    "platform_account_id": platform_account_id,
                    "display_name": platform_account_id,
                    "platform": "ctrip",
                    "session_port": session_port,
                    "intended_use": "competitor_price",
                    "login_state": login_state,
                    "human_verification_status": human_verification_status,
                    "last_heartbeat_at": "2026-06-17T00:00:00+00:00",
                    "takeover_url": f"/browser-control?session={browser_session_id}",
                    "manual_takeover_url": (
                        f"/browser-control?session={browser_session_id}&mode=manual"
                    ),
                    "can_takeover": True,
                    "can_logout": True,
                    "last_action": "Test session.",
                }
            )
        )

    def test_no_trusted_dom_prices_does_not_persist_human_required(self) -> None:
        reader = importlib.import_module("app.domain.browser.ctrip_public_price_reader")
        sessions = importlib.import_module("app.domain.browser.sessions")
        sessions.reset_browser_session_store()
        store = sessions.get_browser_session_store()
        self._restore_session(
            store,
            browser_session_id="browser-public-1",
            platform_account_id="ctrip-public-1",
            session_port=9321,
            login_state="human_required",
            human_verification_status="required",
        )

        reader._sync_public_competitor_browser_session_state(
            browser_session_id="browser-public-1",
            actor_id="scheduler",
            trusted_dom_rate_count=0,
            status="needs_review",
            page_states={"own:today": "authenticated"},
        )

        session = store.get("browser-public-1")
        self.assertEqual(session.login_state, "logged_in")
        self.assertEqual(session.human_verification_status, "none")

    def test_login_required_page_state_persists_human_required(self) -> None:
        reader = importlib.import_module("app.domain.browser.ctrip_public_price_reader")
        sessions = importlib.import_module("app.domain.browser.sessions")
        sessions.reset_browser_session_store()
        store = sessions.get_browser_session_store()
        self._restore_session(
            store,
            browser_session_id="browser-public-2",
            platform_account_id="ctrip-public-2",
            session_port=9322,
        )

        reader._sync_public_competitor_browser_session_state(
            browser_session_id="browser-public-2",
            actor_id="scheduler",
            trusted_dom_rate_count=0,
            status="human_required",
            page_states={"own:today": "login_required"},
        )

        session = store.get("browser-public-2")
        self.assertEqual(session.login_state, "human_required")
        self.assertEqual(session.human_required_reason, "login_required")

