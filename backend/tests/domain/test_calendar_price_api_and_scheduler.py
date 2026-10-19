from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.tests._service_auth import service_token_headers


class CalendarPriceApiAndSchedulerTests(unittest.TestCase):
    def test_manual_run_reads_configured_hotels_for_future_days_and_saves_alerts(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            read_calls: list[dict[str, Any]] = []

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                read_calls.append(kwargs)
                return {
                    "status": "success",
                    "summary": "read 2 trusted calendar prices",
                    "points": [
                        {
                            "checkin_date": "2026-07-08",
                            "price_amount_yuan": 420 if kwargs["target"]["role"] == "own" else 410,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                            "source_text": "sample",
                        },
                        {
                            "checkin_date": "2026-07-09",
                            "price_amount_yuan": (
                                360
                                if kwargs["target"]["role"] == "own"
                                else 390
                                if kwargs["target"]["label"] == "Comp A"
                                else 400
                            ),
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                            "source_text": "sample",
                        },
                    ],
                    "page_state": "ready",
                    "review_reason": None,
                }

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=AsyncMock(return_value=_browser_binding()),
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler._wait_between_calendar_hotel_reads",
                    AsyncMock(),
                    create=True,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=False,
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["status"], "alert")
            self.assertEqual(result["future_days"], 15)
            self.assertEqual(result["alert_count"], 2)
            self.assertNotIn("pricing_approval_auto_flow", result)
            self.assertNotIn("screenshot_path", str(result))
            self.assertGreaterEqual(len(read_calls), 2)

    def test_manual_run_uses_calendar_browser_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            read_calls: list[dict[str, Any]] = []

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                read_calls.append(kwargs)
                return {
                    "status": "success",
                    "summary": "ok",
                    "points": [
                        {
                            "checkin_date": "2026-07-08",
                            "price_amount_yuan": 420,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                        },
                    ],
                }

            selection = SimpleNamespace(
                platform_account_id="ctrip-calendar-account",
                browser_session_id="browser-calendar-account",
                evidence=lambda: {
                    "source": "calendar_price_watch_config",
                    "pool_enabled": False,
                    "platform_account_id": "ctrip-calendar-account",
                    "browser_session_id": "browser-calendar-account",
                },
            )
            resolver = AsyncMock(return_value=_browser_binding(selection=selection))

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=resolver,
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler._wait_between_calendar_hotel_reads",
                    AsyncMock(),
                    create=True,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=False,
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["status"], "success")
            resolver.assert_awaited_once()
            self.assertTrue(read_calls)
            self.assertEqual(read_calls[0]["platform_account_id"], "ctrip-calendar-account")
            self.assertEqual(read_calls[0]["browser_session_id"], "browser-calendar-account")

    def test_manual_run_returns_config_required_without_calendar_browser_binding(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            scheduler = _reload_scheduler_module()
            result = asyncio.run(
                scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                    "ops_watch",
                    push_to_feishu=False,
                    actor_id="test",
                    source="test",
                )
            )
            self.assertEqual(result["status"], "config_required")
            self.assertIn("独立", result["summary"])

    def test_config_inherits_competitor_schedule_window_and_feishu_robot_state(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                types = importlib.import_module("app.domain.competitor_prices.types")
                await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        schedule_start_time="08:15",
                        schedule_end_time="22:45",
                        schedule_mode="fixed_slots",
                        feishu_webhook_url="https://open.feishu.cn/calendar-secret",
                    ),
                )

            asyncio.run(seed())
            response = TestClient(_reload_app()).get("/api/v1/calendar-price-watch/ops_watch/config")
            self.assertEqual(response.status_code, 200)
            config = response.json()["config"]
            self.assertEqual(config["schedule_start_time"], "08:15")
            self.assertEqual(config["schedule_end_time"], "22:45")
            self.assertEqual(config["schedule_source"], "competitor_watch_group")
            self.assertEqual(config["browser_binding_source"], "calendar_price_watch_config")
            self.assertFalse(config["browser_session_configured"])
            self.assertTrue(config["feishu_webhook_configured"])
            self.assertNotIn("calendar-secret", str(config))

    def test_due_calendar_configs_respect_competitor_schedule_window(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                types = importlib.import_module("app.domain.competitor_prices.types")
                await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        schedule_mode="fixed_slots",
                        schedule_start_time="09:00",
                        schedule_end_time="10:00",
                        schedule_interval_minutes=30,
                    ),
                )
                calendar_repository = _reload_calendar_repository(db_url)
                calendar_types = importlib.import_module("app.domain.calendar_price_watch.types")
                await calendar_repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=calendar_types.CalendarPriceWatchConfigPatch(
                        auto_poll_enabled=True,
                        schedule_interval_minutes=30,
                    ),
                )

                before_window = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 8, 30),
                )
                self.assertEqual(before_window, [])

                in_window = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 9, 0),
                )
                self.assertEqual(len(in_window), 1)
                self.assertEqual(in_window[0]["schedule_start_time"], "09:00")
                self.assertEqual(in_window[0]["schedule_end_time"], "10:00")
                self.assertEqual(in_window[0]["schedule_mode"], "fixed_slots")
                self.assertTrue(in_window[0]["schedule_state"]["in_window"])
                self.assertTrue(in_window[0]["schedule_state"]["due"])

                await calendar_repository.mark_calendar_price_watch_polled(
                    watch_group_key="ops_watch",
                    captured_at=datetime(2026, 7, 8, 9, 5),
                )
                not_due_same_slot = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 9, 15),
                )
                self.assertEqual(not_due_same_slot, [])

                due_next_slot = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 9, 30),
                )
                self.assertEqual(len(due_next_slot), 1)

            asyncio.run(scenario())

    def test_due_calendar_configs_use_last_run_interval_mode_from_competitor_group(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                types = importlib.import_module("app.domain.competitor_prices.types")
                await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        schedule_mode="last_run_interval",
                        schedule_start_time="06:00",
                        schedule_end_time="23:00",
                        schedule_interval_minutes=120,
                    ),
                )
                calendar_repository = _reload_calendar_repository(db_url)
                calendar_types = importlib.import_module("app.domain.calendar_price_watch.types")
                await calendar_repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=calendar_types.CalendarPriceWatchConfigPatch(
                        auto_poll_enabled=True,
                        schedule_interval_minutes=60,
                    ),
                )

                due_now = datetime(2026, 7, 8, 10, 0)
                due_groups = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=due_now,
                )
                self.assertEqual(len(due_groups), 1)
                self.assertEqual(due_groups[0]["schedule_mode"], "last_run_interval")

                recent = due_now - timedelta(minutes=10)
                await calendar_repository.mark_calendar_price_watch_polled(
                    watch_group_key="ops_watch",
                    captured_at=recent,
                )
                not_due = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=due_now,
                )
                self.assertEqual(not_due, [])

            asyncio.run(scenario())

    def test_manual_run_waits_between_calendar_hotel_reads(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            read_calls: list[str] = []
            delay_calls: list[str] = []

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                read_calls.append(kwargs["target"]["label"])
                return {
                    "status": "success",
                    "summary": "ok",
                    "points": [
                        {
                            "checkin_date": "2026-07-08",
                            "price_amount_yuan": 420,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                        },
                    ],
                }

            async def fake_wait() -> None:
                delay_calls.append("wait")

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=AsyncMock(return_value=_browser_binding()),
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler._wait_between_calendar_hotel_reads",
                    fake_wait,
                    create=True,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=False,
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(read_calls, ["Own", "Comp A", "Comp B"])
            self.assertEqual(delay_calls, ["wait", "wait"])

    def test_human_required_read_persists_review_points_without_fake_prices(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

                async def fake_read(**kwargs: Any) -> dict[str, Any]:
                    return {
                        "status": "human_required",
                        "summary": "login required",
                        "points": [],
                        "page_state": "login_required",
                        "review_reason": "login_required",
                    }

                scheduler = _reload_scheduler_module()
                with (
                    patch(
                        "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                        new=AsyncMock(return_value=_browser_binding()),
                        create=True,
                    ),
                    patch(
                        "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                        return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                    ),
                ):
                    result = await scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=False,
                        actor_id="test",
                        source="test",
                    )

                self.assertEqual(result["status"], "human_required")
                matrix = await calendar_repository.get_latest_calendar_price_matrix(
                    watch_group_key="ops_watch",
                )
                assert matrix is not None
                for date_row in matrix["dates"]:
                    self.assertIsNone(date_row["own"]["price_amount_yuan"])
                    self.assertIn(date_row["own"]["status"], {"needs_review", "human_required"})

            asyncio.run(scenario())

    def test_calendar_alert_push_falls_back_to_mobile_webhook_when_legacy_empty(self) -> None:
        # 生产 2026-10-09：静里组 legacy feishu_webhook_url 为空、竞品推送走
        # feishu_mobile_webhook_url，日历价预警必须与竞品推送同地址。
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                types = importlib.import_module("app.domain.competitor_prices.types")
                await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        feishu_webhook_url="",
                        feishu_mobile_webhook_url="https://open.feishu.cn/mobile-hook",
                    ),
                )
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            push_calls: list[dict[str, Any]] = []

            async def fake_push(**kwargs: Any) -> SimpleNamespace:
                push_calls.append(kwargs)
                return SimpleNamespace(
                    status="success",
                    summary="ok",
                    model_dump=lambda mode: {"status": "success"},
                )

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "success",
                    "summary": "ok",
                    "points": [
                        {
                            "checkin_date": "2026-07-08",
                            "price_amount_yuan": 420 if kwargs["target"]["role"] == "own" else 410,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                        },
                    ],
                }

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=AsyncMock(return_value=_browser_binding()),
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.push_interactive_card_to_feishu_webhook",
                    fake_push,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=True,
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["status"], "alert")
            self.assertEqual(result["feishu_push"]["status"], "success")
            self.assertEqual(len(push_calls), 1)
            self.assertEqual(push_calls[0]["webhook_url"], "https://open.feishu.cn/mobile-hook")

    def test_successful_alert_run_pushes_warning_card_without_pricing_flow(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            push_calls: list[dict[str, Any]] = []

            async def fake_push(**kwargs: Any) -> SimpleNamespace:
                push_calls.append(kwargs)
                return SimpleNamespace(
                    status="success",
                    summary="ok",
                    model_dump=lambda mode: {"status": "success"},
                )

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "success",
                    "summary": "ok",
                    "points": [
                        {
                            "checkin_date": "2026-07-08",
                            "price_amount_yuan": 420 if kwargs["target"]["role"] == "own" else 410,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                        },
                    ],
                }

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=AsyncMock(return_value=_browser_binding()),
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.push_interactive_card_to_feishu_webhook",
                    fake_push,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_watch_group_now(
                        "ops_watch",
                        push_to_feishu=True,
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["status"], "alert")
            self.assertEqual(len(push_calls), 1)
            card_text = str(push_calls[0].get("card") or push_calls[0])
            for forbidden in [
                "confirm_price_change",
                "write_to_ctrip",
                "auto_execute",
                "decision=approve",
                "approve",
                "reject",
            ]:
                self.assertNotIn(forbidden, card_text)

    def test_scheduled_tick_only_runs_enabled_calendar_configs_that_are_due(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                types = importlib.import_module("app.domain.competitor_prices.types")
                await repository.update_competitor_watch_group(
                    watch_group_key="ops_watch",
                    patch=types.CompetitorWatchGroupPatch(
                        schedule_mode="fixed_slots",
                        schedule_start_time="06:00",
                        schedule_end_time="23:00",
                        schedule_interval_minutes=120,
                    ),
                )
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.update_calendar_price_watch_config(
                    watch_group_key="ops_watch",
                    patch=importlib.import_module(
                        "app.domain.calendar_price_watch.types"
                    ).CalendarPriceWatchConfigPatch(
                        auto_poll_enabled=True,
                        schedule_interval_minutes=60,
                    ),
                )
                due_now = datetime(2026, 7, 8, 10, 0)
                due_groups = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=due_now,
                )
                self.assertEqual(len(due_groups), 1)

                await calendar_repository.mark_calendar_price_watch_polled(
                    watch_group_key="ops_watch",
                    captured_at=datetime(2026, 7, 8, 10, 2),
                )
                not_due = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 10, 30),
                )
                self.assertEqual(len(not_due), 0)

                due_next_slot = await calendar_repository.list_due_calendar_price_watch_configs(
                    now=datetime(2026, 7, 8, 11, 0),
                )
                self.assertEqual(len(due_next_slot), 1)

            asyncio.run(scenario())

    def test_tick_polls_backs_off_configs_that_fail_before_persisting(self) -> None:
        """2026-10-08 巡检 F4：落库前失败（如浏览器账号不存在）不推进
        last_polled_at，配置永远 due → 每分钟重试刷日志。失败后 60 分钟
        退避；成功（有 run_id）清退避。"""
        from app.domain.calendar_price_watch.scheduler import (
            CalendarPriceWatchScheduler,
        )

        scheduler = CalendarPriceWatchScheduler()
        calls: list[str] = []

        async def failing_run(watch_group_key, **kwargs):
            calls.append(watch_group_key)
            return {
                "watch_group_key": watch_group_key,
                "status": "binding_required",
                "summary": "浏览器账号不存在：browser-trip-1",
            }

        scheduler.run_watch_group_now = failing_run
        config = {"watch_group_key": "ops_watch"}

        async def due_configs(**kwargs):
            return [dict(config)]

        async def no_briefs(**kwargs):
            return []

        with patch(
            "app.domain.calendar_price_watch.scheduler.list_due_calendar_price_watch_configs",
            new=due_configs,
        ), patch(
            "app.domain.calendar_price_watch.scheduler.list_due_matrix_brief_configs",
            new=no_briefs,
        ):
            first = asyncio.run(scheduler._tick_polls())
            second = asyncio.run(scheduler._tick_polls())

        self.assertEqual(first["due_group_count"], 1)
        # 第二轮 tick：退避期内不再重试该组。
        self.assertEqual(calls, ["ops_watch"])
        self.assertEqual(second["due_group_count"], 1)

        # 退避过期后恢复重试。
        scheduler._poll_failure_backoff_until["ops_watch"] = datetime.now(UTC) - timedelta(
            minutes=1
        )
        with patch(
            "app.domain.calendar_price_watch.scheduler.list_due_calendar_price_watch_configs",
            new=due_configs,
        ), patch(
            "app.domain.calendar_price_watch.scheduler.list_due_matrix_brief_configs",
            new=no_briefs,
        ):
            asyncio.run(scheduler._tick_polls())
        self.assertEqual(calls, ["ops_watch", "ops_watch"])

    def test_scheduler_heartbeat_reports_last_due_group_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            heartbeat_path = Path(directory) / "calendar_scheduler.json"
            status_module = importlib.import_module(
                "app.domain.calendar_price_watch.scheduler_status"
            )
            now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
            status_module.write_calendar_price_scheduler_heartbeat(
                "tick_finished",
                tick_result={"due_group_count": 2, "results": []},
                path=heartbeat_path,
                now=now,
            )
            status = status_module.get_calendar_price_scheduler_status(
                path=heartbeat_path,
                now=now + timedelta(seconds=30),
            )
            self.assertEqual(status["last_due_group_count"], 2)

    def test_get_config_returns_calendar_defaults(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)

            asyncio.run(seed())
            response = TestClient(_reload_app()).get("/api/v1/calendar-price-watch/ops_watch/config")
            self.assertEqual(response.status_code, 200)
            config = response.json()["config"]
            self.assertEqual(config["future_days"], 15)
            self.assertEqual(config["high_gap_threshold_yuan"], 10)
            self.assertEqual(config["low_gap_threshold_yuan"], 20)

    def test_patch_config_accepts_30_days_and_rejects_31(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)

            asyncio.run(seed())
            client = TestClient(_reload_app())
            ok = client.patch(
                "/api/v1/calendar-price-watch/ops_watch/config",
                json={"future_days": 30},
            )
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["config"]["future_days"], 30)

            bad = client.patch(
                "/api/v1/calendar-price-watch/ops_watch/config",
                json={"future_days": 31},
            )
            self.assertEqual(bad.status_code, 422)

    def test_run_now_endpoint_returns_alert_matrix_without_execution_actions(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)

            asyncio.run(seed())

            async def fake_run(watch_group_key: str, **kwargs: Any) -> dict[str, Any]:
                return {
                    "watch_group_key": watch_group_key,
                    "status": "alert",
                    "summary": "calendar alert",
                    "future_days": 15,
                    "alert_count": 1,
                    "alerts": [
                        {
                            "alert_type": "own_price_high",
                            "checkin_date": "2026-07-08",
                            "gap_yuan": 10,
                        }
                    ],
                }

            with patch("app.api.v1.calendar_price_watch.CalendarPriceWatchScheduler") as scheduler_cls:
                scheduler_cls.return_value.run_watch_group_now = fake_run
                response = TestClient(_reload_app()).post(
                    "/api/v1/calendar-price-watch/ops_watch/run-now?push_to_feishu=false",
                    headers=service_token_headers(),
                )
            self.assertEqual(response.status_code, 200)
            payload = response.json()["result"]
            self.assertEqual(payload["status"], "alert")
            serialized = str(payload)
            for forbidden in ["confirm_price_change", "write_to_ctrip", "auto_execute", "pricing_approval"]:
                self.assertNotIn(forbidden, serialized)

    def test_latest_endpoint_returns_404_before_first_run(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            response = TestClient(_reload_app()).get("/api/v1/calendar-price-watch/ops_watch/latest")
            self.assertEqual(response.status_code, 404)

    def test_matrix_brief_run_reads_and_pushes_blue_card_without_alerts(self) -> None:
        with _isolated_sqlite_database() as db_url:
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

            async def seed() -> None:
                repository = _reload_competitor_repository(db_url)
                await _seed_watch_group(repository)
                calendar_repository = _reload_calendar_repository(db_url)
                await calendar_repository.get_calendar_price_watch_config(watch_group_key="ops_watch")

            asyncio.run(seed())
            push_calls: list[dict[str, Any]] = []

            async def fake_push(**kwargs: Any) -> SimpleNamespace:
                push_calls.append(kwargs)
                return SimpleNamespace(
                    status="success",
                    summary="ok",
                    model_dump=lambda mode: {"status": "success"},
                )

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "success",
                    "summary": "ok",
                    "points": [
                        {
                            "checkin_date": today,
                            "price_amount_yuan": 400 if kwargs["target"]["role"] == "own" else 405,
                            "currency": "CNY",
                            "source": "calendar_dom_cell_price",
                            "is_trusted": True,
                        },
                    ],
                }

            scheduler = _reload_scheduler_module()
            with (
                patch(
                    "app.domain.calendar_price_watch.scheduler.resolve_calendar_browser_binding_for_config",
                    new=AsyncMock(return_value=_browser_binding()),
                    create=True,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.get_ctrip_ebooking_service",
                    return_value=SimpleNamespace(read_public_calendar_prices=fake_read),
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler.push_interactive_card_to_feishu_webhook",
                    fake_push,
                ),
                patch(
                    "app.domain.calendar_price_watch.scheduler._wait_between_calendar_hotel_reads",
                    AsyncMock(),
                    create=True,
                ),
            ):
                result = asyncio.run(
                    scheduler.CalendarPriceWatchScheduler().run_matrix_brief_now(
                        "ops_watch",
                        actor_id="test",
                        source="test",
                    )
                )

            self.assertEqual(result["push_kind"], "matrix_brief")
            self.assertEqual(result["status"], "success")
            self.assertGreaterEqual(len(push_calls), 1)
            card = push_calls[0].get("card") or {}
            self.assertEqual(card.get("header", {}).get("template"), "blue")
            self.assertTrue(
                any(element.get("tag") == "table" for element in card.get("elements", []))
            )
            self.assertIn("左右滑动查看", str(card))
            self.assertNotIn("collapsible_panel", str(card))


async def _seed_watch_group(repository) -> None:
    types = importlib.import_module("app.domain.competitor_prices.types")
    await repository.create_competitor_watch_group(
        types.CompetitorWatchGroupCreate(
            group_key="ops_watch",
            name="Ops competitor watch",
            platform_account_id="ctrip_public_account",
            feishu_webhook_url="https://open.feishu.cn/secret",
        )
    )
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="own",
            alias="Own",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100001",
            sort_order=0,
        ),
    )
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="competitor",
            alias="Comp A",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100002",
            sort_order=10,
        ),
    )
    await repository.upsert_competitor_hotel_config(
        platform_account_id="ctrip_public_account",
        watch_group_key="ops_watch",
        hotel=types.CompetitorHotelConfigIn(
            role="competitor",
            alias="Comp B",
            detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=100003",
            sort_order=20,
        ),
    )


def _browser_binding(selection: Any | None = None) -> SimpleNamespace:
    if selection is None:
        selection = SimpleNamespace(
            platform_account_id="ctrip-public",
            browser_session_id="browser-public",
            evidence=lambda: {},
        )
    return SimpleNamespace(
        ok=True,
        status="success",
        summary="ok",
        selection=selection,
        evidence={"browser_selection": selection.evidence()},
        next_action=None,
    )


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "calendar-api.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        # Restore the global app.db.session engine so later tests in this
        # process do not inherit a stale engine pointing at the temp file we
        # are about to delete. Must run before the temp dir cleanup.
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db_session(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return db_session


def _reload_competitor_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.competitor_prices.repository")
    return importlib.reload(repository)


def _reload_calendar_repository(db_url: str):
    _reload_db_session(db_url)
    repository = importlib.import_module("app.domain.calendar_price_watch.repository")
    return importlib.reload(repository)


def _reload_scheduler_module():
    _reload_calendar_repository(os.environ["DATABASE_URL"])
    scheduler = importlib.import_module("app.domain.calendar_price_watch.scheduler")
    return importlib.reload(scheduler)


def _reload_app():
    _reload_db_session(os.environ.get("DATABASE_URL", ""))
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


if __name__ == "__main__":
    unittest.main()
