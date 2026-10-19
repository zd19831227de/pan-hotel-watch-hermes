from __future__ import annotations

import asyncio
import importlib
import unittest
from datetime import timedelta
from typing import Any

from app.domain.browser.sessions import BrowserSession
from backend.tests.domain.test_competitor_watch_group_config import (
    _isolated_sqlite_database,
    _reload_browser_sessions,
    _reload_repository,
)


class CompetitorBrowserPoolSelectionTests(unittest.TestCase):
    def test_fixed_mode_returns_bound_browser_session(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                group = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        browser_session_id="browser-trip-1",
                        browser_rotation_mode="fixed",
                    )
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )

                self.assertTrue(resolution.ok)
                assert resolution.selection is not None
                self.assertEqual(resolution.selection.browser_session_id, "browser-trip-1")
                self.assertFalse(resolution.selection.evidence()["pool_enabled"])
                self.assertEqual(resolution.selection.evidence()["rotation_mode"], "fixed")

            asyncio.run(scenario())

    def test_pool_round_robin_selects_oldest_available_competitor_session(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                for index in (1, 2, 3):
                    _restore_competitor_session(
                        sessions,
                        browser_session_id=f"browser-trip-{index}",
                        platform_account_id=f"trip{index}",
                        session_port=9220 + index,
                    )
                await _create_pool_group(
                    repository,
                    types,
                    ["browser-trip-1", "browser-trip-2", "browser-trip-3"],
                )
                await _set_member_times(
                    {
                        "browser-trip-1": {"last_selected_minutes_ago": 5},
                        "browser-trip-2": {"last_selected_minutes_ago": 90},
                        "browser-trip-3": {"last_selected_minutes_ago": 30},
                    }
                )
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )

                self.assertTrue(resolution.ok)
                assert resolution.selection is not None
                self.assertEqual(resolution.selection.browser_session_id, "browser-trip-2")
                evidence = resolution.selection.evidence()
                self.assertTrue(evidence["pool_enabled"])
                self.assertEqual(evidence["rotation_mode"], "pool_round_robin")
                self.assertEqual(evidence["pool_size"], 3)
                self.assertEqual(evidence["usable_pool_size"], 3)
                self.assertEqual(evidence["selected_index"], 1)
                self.assertEqual(evidence["source"], "watch_group_pool")

            asyncio.run(scenario())

    def test_pool_skips_cooling_sessions_but_retries_stale_login_state(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                    login_state="logged_out",
                )
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-2",
                    platform_account_id="trip2",
                    session_port=9222,
                    login_state="human_required",
                    human_verification_status="required",
                )
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-3",
                    platform_account_id="trip3",
                    session_port=9223,
                )
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-4",
                    platform_account_id="trip4",
                    session_port=9224,
                )
                await _create_pool_group(
                    repository,
                    types,
                    [
                        "browser-trip-1",
                        "browser-trip-2",
                        "browser-trip-3",
                        "browser-trip-4",
                    ],
                )
                await _set_member_times(
                    {
                        "browser-trip-3": {"cooldown_minutes": 30},
                        "browser-trip-4": {"last_selected_minutes_ago": 15},
                    }
                )
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )

                self.assertTrue(resolution.ok)
                assert resolution.selection is not None
                self.assertEqual(resolution.selection.browser_session_id, "browser-trip-1")
                self.assertEqual(resolution.selection.evidence()["selected_index"], 0)
                self.assertEqual(resolution.selection.evidence()["usable_pool_size"], 3)

            asyncio.run(scenario())

    def test_pool_skips_currently_leased_browser_session(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                for index in (1, 2):
                    _restore_competitor_session(
                        sessions,
                        browser_session_id=f"browser-trip-{index}",
                        platform_account_id=f"trip{index}",
                        session_port=9220 + index,
                    )
                await _create_pool_group(
                    repository,
                    types,
                    ["browser-trip-1", "browser-trip-2"],
                )
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                first = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="worker-a",
                )
                second = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="worker-b",
                )

                self.assertTrue(first.ok)
                self.assertTrue(second.ok)
                assert first.selection is not None
                assert second.selection is not None
                self.assertEqual(first.selection.browser_session_id, "browser-trip-1")
                self.assertEqual(second.selection.browser_session_id, "browser-trip-2")

            asyncio.run(scenario())

    def test_pool_selection_refreshes_browser_session_state_from_storage(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                await _create_pool_group(repository, types, ["browser-trip-1"])
                external_store = sessions.BrowserSessionStore()
                external_store.delete("browser-trip-1")
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )

                self.assertFalse(resolution.ok)
                self.assertEqual(resolution.status, "human_required")
                self.assertEqual(resolution.evidence["usable_pool_size"], 0)

            asyncio.run(scenario())

    def test_pool_returns_human_required_when_all_members_are_unavailable(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                    login_state="logged_out",
                )
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-2",
                    platform_account_id="trip2",
                    session_port=9222,
                    login_state="human_required",
                    human_verification_status="required",
                )
                await _create_pool_group(
                    repository,
                    types,
                    ["browser-trip-1", "browser-trip-2"],
                )
                await _set_member_times(
                    {
                        "browser-trip-1": {"cooldown_minutes": 30},
                        "browser-trip-2": {"cooldown_minutes": 30},
                    }
                )
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )

                self.assertFalse(resolution.ok)
                self.assertEqual(resolution.status, "human_required")
                self.assertTrue(resolution.evidence["pool_enabled"])
                self.assertEqual(resolution.evidence["usable_pool_size"], 0)

            asyncio.run(scenario())

    def test_failed_pool_member_uses_watch_group_cooldown_minutes(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        browser_rotation_mode="pool_round_robin",
                        pool_cooldown_minutes=45,
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
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )
                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="tester",
                )
                self.assertTrue(resolution.ok)
                assert resolution.selection is not None

                pool = importlib.import_module("app.domain.competitor_prices.browser_pool")
                models = importlib.import_module("app.domain.competitor_prices.models")
                db_session = importlib.import_module("app.db.session")
                before = models.local_now()
                await pool.record_competitor_browser_pool_run_result(
                    selection=resolution.selection,
                    status="human_required",
                )

                async with db_session.session_scope() as session:
                    member = await session.scalar(
                        importlib.import_module("sqlalchemy").select(
                            models.CtripCompetitorWatchGroupBrowserPoolMember
                        )
                    )
                assert member is not None
                self.assertIsNotNone(member.cooldown_until)
                assert member.cooldown_until is not None
                cooldown_minutes = (member.cooldown_until - before).total_seconds() / 60
                self.assertAlmostEqual(cooldown_minutes, 45, delta=1)

            asyncio.run(scenario())

    def test_pool_selection_evidence_includes_requested_group_and_pool_metadata(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository, binding, types = _reload_modules(db_url)
                sessions = _reload_browser_sessions()
                _restore_competitor_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                await _create_pool_group(repository, types, ["browser-trip-1"])
                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                resolution = await binding.resolve_competitor_browser_binding_for_group(
                    group=group,
                    actor_id="worker-a",
                )

                self.assertTrue(resolution.ok)
                assert resolution.selection is not None
                evidence = resolution.selection.evidence()
                self.assertEqual(evidence["watch_group_key"], "ops_watch")
                self.assertEqual(evidence["lease_owner"], "worker-a")
                self.assertEqual(evidence["source"], "watch_group_pool")
                self.assertEqual(evidence["pool_size"], 1)
                self.assertEqual(evidence["usable_pool_size"], 1)

            asyncio.run(scenario())


def _reload_modules(db_url: str):
    repository = _reload_repository(db_url)
    binding = importlib.import_module("app.domain.competitor_prices.browser_binding")
    pool = importlib.import_module("app.domain.competitor_prices.browser_pool")
    types = importlib.import_module("app.domain.competitor_prices.types")
    return repository, importlib.reload(binding), importlib.reload(types)


async def _create_pool_group(repository, types, browser_session_ids: list[str]) -> None:
    await repository.create_competitor_watch_group(
        types.CompetitorWatchGroupCreate(
            group_key="ops_watch",
            name="Ops watch",
            browser_rotation_mode="pool_round_robin",
        )
    )
    await repository.update_competitor_watch_group_browser_pool(
        watch_group_key="ops_watch",
        request=types.CompetitorBrowserPoolUpdate(
            members=[
                types.CompetitorBrowserPoolMemberIn(browser_session_id=session_id)
                for session_id in browser_session_ids
            ]
        ),
    )


async def _set_member_times(changes: dict[str, dict[str, int]]) -> None:
    db_session = importlib.import_module("app.db.session")
    models = importlib.import_module("app.domain.competitor_prices.models")
    now = models.local_now()
    async with db_session.session_scope() as session:
        for browser_session_id, values in changes.items():
            member = await session.scalar(
                importlib.import_module("sqlalchemy").select(
                    models.CtripCompetitorWatchGroupBrowserPoolMember
                ).where(
                    models.CtripCompetitorWatchGroupBrowserPoolMember.browser_session_id
                    == browser_session_id
                )
            )
            assert member is not None
            if "last_selected_minutes_ago" in values:
                member.last_selected_at = now - timedelta(
                    minutes=values["last_selected_minutes_ago"]
                )
            if "cooldown_minutes" in values:
                member.cooldown_until = now + timedelta(minutes=values["cooldown_minutes"])


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
                "can_logout": login_state == "logged_in",
                "last_action": "Test session.",
            }
        )
    )


if __name__ == "__main__":
    unittest.main()
