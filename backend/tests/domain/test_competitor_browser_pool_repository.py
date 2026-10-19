from __future__ import annotations

import asyncio
import importlib
import unittest

from backend.tests.domain.test_competitor_watch_group_config import (
    _configured_test_agent,
    _create_active_agent_binding,
    _isolated_sqlite_database,
    _reload_browser_sessions,
    _reload_repository,
    _restore_competitor_browser_session,
    _restore_ebk_browser_session,
)


class CompetitorBrowserPoolRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        agent_config = _configured_test_agent()
        agent_config.start()
        self.addCleanup(agent_config.stop)

    def test_pool_accepts_only_competitor_price_sessions(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                _restore_ebk_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-1",
                    platform_account_id="ebk1",
                    session_port=9321,
                )
                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        browser_rotation_mode="pool_round_robin",
                    )
                )

                with self.assertRaises(ValueError):
                    await repository.update_competitor_watch_group_browser_pool(
                        watch_group_key="ops_watch",
                        request=types.CompetitorBrowserPoolUpdate(
                            members=[
                                types.CompetitorBrowserPoolMemberIn(
                                    browser_session_id="browser-trip-1",
                                ),
                                types.CompetitorBrowserPoolMemberIn(
                                    browser_session_id="browser-ebk-1",
                                ),
                            ]
                        ),
                    )

                payload = await repository.update_competitor_watch_group_browser_pool(
                    watch_group_key="ops_watch",
                    request=types.CompetitorBrowserPoolUpdate(
                        members=[
                            types.CompetitorBrowserPoolMemberIn(
                                browser_session_id="browser-trip-1",
                            ),
                        ]
                    ),
                )

                assert payload is not None
                self.assertEqual(payload["browser_pool"]["pool_size"], 1)
                self.assertEqual(payload["browser_pool"]["usable_pool_size"], 1)
                self.assertEqual(
                    payload["browser_pool"]["members"][0]["browser_session_id"],
                    "browser-trip-1",
                )

            asyncio.run(scenario())

    def test_watch_group_payload_includes_pool_status_without_secrets(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                for index in (1, 2):
                    _restore_competitor_browser_session(
                        sessions,
                        browser_session_id=f"browser-trip-{index}",
                        platform_account_id=f"trip{index}",
                        session_port=9220 + index,
                    )
                _restore_ebk_browser_session(
                    sessions,
                    browser_session_id="browser-ebk-1",
                    platform_account_id="ebk1",
                    session_port=9321,
                    connection_mode="non_direct",
                )
                await _create_active_agent_binding("oc_secret_delivery_group")

                created = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        browser_rotation_mode="pool_round_robin",
                        pricing_browser_session_id="browser-ebk-1",
                        feishu_chat_id="oc_secret_delivery_group",
                    )
                )
                assert created is not None

                await repository.update_competitor_watch_group_browser_pool(
                    watch_group_key="ops_watch",
                    request=types.CompetitorBrowserPoolUpdate(
                        members=[
                            types.CompetitorBrowserPoolMemberIn(
                                browser_session_id="browser-trip-1",
                            ),
                            types.CompetitorBrowserPoolMemberIn(
                                browser_session_id="browser-trip-2",
                            ),
                        ]
                    ),
                )

                group = await repository.get_competitor_watch_group_detail(
                    watch_group_key="ops_watch",
                )

                assert group is not None
                self.assertEqual(group["browser_rotation_mode"], "pool_round_robin")
                self.assertTrue(group["browser_pool"]["pool_enabled"])
                self.assertEqual(group["browser_pool"]["pool_size"], 2)
                self.assertEqual(group["browser_pool"]["usable_pool_size"], 2)
                self.assertEqual(group["pricing_browser_session_id"], "browser-ebk-1")
                self.assertNotIn("pricing_feishu_chat_id", str(group))
                self.assertNotIn("oc_secret_delivery_group", str(group))

            asyncio.run(scenario())

    def test_pool_mode_requires_at_least_one_enabled_pool_member_before_autopoll(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                types = importlib.import_module("app.domain.competitor_prices.types")

                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                    )
                )

                with self.assertRaisesRegex(ValueError, "pool"):
                    await repository.update_competitor_watch_group(
                        watch_group_key="ops_watch",
                        patch=types.CompetitorWatchGroupPatch(
                            browser_rotation_mode="pool_round_robin",
                            auto_poll_enabled=True,
                        ),
                    )

            asyncio.run(scenario())

    def test_fixed_mode_allows_shared_browser_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                first = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_a",
                        name="Ops watch A",
                        browser_session_id="browser-trip-1",
                        browser_rotation_mode="fixed",
                        enabled=True,
                    )
                )
                self.assertIsNotNone(first)

                second = await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch_b",
                        name="Ops watch B",
                        browser_session_id="browser-trip-1",
                        browser_rotation_mode="fixed",
                        enabled=True,
                    )
                )
                self.assertIsNotNone(second)
                self.assertEqual(second["browser_session_id"], "browser-trip-1")

            asyncio.run(scenario())

    def test_delete_competitor_browser_session_removes_pool_membership(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
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
                    )
                )
                await repository.update_competitor_watch_group_browser_pool(
                    watch_group_key="ops_watch",
                    request=types.CompetitorBrowserPoolUpdate(
                        members=[
                            types.CompetitorBrowserPoolMemberIn(
                                browser_session_id="browser-trip-1",
                            ),
                        ]
                    ),
                )

                removed = await repository.clear_competitor_watch_bindings_for_browser_session(
                    platform_account_id="trip1",
                    browser_session_id="browser-trip-1",
                )
                pool = await repository.get_competitor_watch_group_browser_pool(
                    watch_group_key="ops_watch",
                )

                self.assertEqual(removed, 1)
                assert pool is not None
                self.assertEqual(pool["browser_pool"]["pool_size"], 0)

            asyncio.run(scenario())

    def test_pool_mode_resolves_hotels_for_selected_pool_member_account(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        platform_account_id="legacy-fixed-account",
                        browser_rotation_mode="pool_round_robin",
                    )
                )
                await repository.update_competitor_watch_group_browser_pool(
                    watch_group_key="ops_watch",
                    request=types.CompetitorBrowserPoolUpdate(
                        members=[
                            types.CompetitorBrowserPoolMemberIn(
                                browser_session_id="browser-trip-1",
                            ),
                        ]
                    ),
                )
                await repository.upsert_competitor_hotel_config(
                    platform_account_id="legacy-fixed-account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="own",
                        alias="本店",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                        sort_order=0,
                    ),
                )
                await repository.upsert_competitor_hotel_config(
                    platform_account_id="legacy-fixed-account",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="competitor",
                        alias="竞品A",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                        sort_order=10,
                    ),
                )

                configured = await repository.resolve_configured_competitor_targets(
                    platform_account_id="trip1",
                    watch_group_key="ops_watch",
                )

                assert configured is not None
                self.assertIn("hotelId=100001", configured.own_hotel_url)
                self.assertEqual(len(configured.competitor_urls), 1)

            asyncio.run(scenario())

    def test_fixed_mode_still_requires_matching_platform_account_id(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                sessions = _reload_browser_sessions()
                types = importlib.import_module("app.domain.competitor_prices.types")

                _restore_competitor_browser_session(
                    sessions,
                    browser_session_id="browser-trip-1",
                    platform_account_id="trip1",
                    session_port=9221,
                )
                await repository.create_competitor_watch_group(
                    types.CompetitorWatchGroupCreate(
                        group_key="ops_watch",
                        name="Ops watch",
                        browser_session_id="browser-trip-1",
                        browser_rotation_mode="fixed",
                    )
                )
                await repository.upsert_competitor_hotel_config(
                    platform_account_id="trip1",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="own",
                        alias="本店",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
                        sort_order=0,
                    ),
                )
                await repository.upsert_competitor_hotel_config(
                    platform_account_id="trip1",
                    watch_group_key="ops_watch",
                    hotel=types.CompetitorHotelConfigIn(
                        role="competitor",
                        alias="竞品A",
                        detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
                        sort_order=10,
                    ),
                )

                mismatched = await repository.resolve_configured_competitor_targets(
                    platform_account_id="trip-other",
                    watch_group_key="ops_watch",
                )
                matched = await repository.resolve_configured_competitor_targets(
                    platform_account_id="trip1",
                    watch_group_key="ops_watch",
                )

                self.assertIsNone(mismatched)
                assert matched is not None

            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
