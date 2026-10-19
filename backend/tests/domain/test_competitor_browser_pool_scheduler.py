from __future__ import annotations

import asyncio
import importlib
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch


class CompetitorBrowserPoolSchedulerTests(unittest.TestCase):
    def test_scheduler_uses_pool_selected_browser_session_for_read(self) -> None:
        calls: dict[str, Any] = {}

        async def scenario() -> dict[str, Any]:
            scheduler = _reload_scheduler()
            service = _FakeService(calls=calls)
            with _patched_scheduler_dependencies(
                scheduler,
                service=service,
                binding=_binding("trip3", "browser-trip3"),
            ):
                return await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )

        result = asyncio.run(scenario())

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls["read"]["platform_account_id"], "trip3")
        self.assertEqual(calls["read"]["browser_session_id"], "browser-trip3")

    def test_manual_read_also_enters_browser_profile_gate(self) -> None:
        async def scenario() -> Mock:
            scheduler = _reload_scheduler()

            @asynccontextmanager
            async def fake_gate(*args: Any, **kwargs: Any):
                yield

            gate = Mock(side_effect=fake_gate)
            with (
                _patched_scheduler_dependencies(
                    scheduler,
                    binding=_binding("trip3", "browser-trip3"),
                ),
                patch.object(scheduler, "competitor_profile_poll", gate),
            ):
                await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            return gate

        gate = asyncio.run(scenario())

        gate.assert_called_once_with("trip3", "browser-trip3", enabled=True)

    def test_scheduler_releases_pool_lease_when_cancelled_waiting_for_profile_gate(self) -> None:
        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            gate_started = asyncio.Event()
            gate_never_opens = asyncio.Event()

            @asynccontextmanager
            async def blocking_gate(*args: Any, **kwargs: Any):
                gate_started.set()
                await gate_never_opens.wait()
                yield

            with (
                _patched_scheduler_dependencies(
                    scheduler,
                    binding=_binding("trip3", "browser-trip3"),
                    release=release,
                ),
                patch.object(scheduler, "competitor_profile_poll", blocking_gate),
            ):
                task = asyncio.create_task(
                    scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                        _pool_group(),
                        push_to_feishu=False,
                        actor_id="operator",
                        source="api",
                    )
                )
                await asyncio.wait_for(gate_started.wait(), timeout=0.2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return release

        release = asyncio.run(scenario())

        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_records_pool_success_and_releases_lease(self) -> None:
        async def scenario() -> tuple[AsyncMock, AsyncMock]:
            scheduler = _reload_scheduler()
            record = AsyncMock()
            release = AsyncMock()
            with _patched_scheduler_dependencies(
                scheduler,
                binding=_binding("trip3", "browser-trip3"),
                record=record,
                release=release,
            ):
                await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            return record, release

        record, release = asyncio.run(scenario())

        record.assert_awaited_once()
        self.assertEqual(record.await_args.kwargs["selection"].browser_session_id, "browser-trip3")
        self.assertEqual(record.await_args.kwargs["status"], "success")
        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_releases_pool_lease_when_read_raises(self) -> None:
        class ExplodingService:
            async def read_competitor_prices(self, **kwargs: Any) -> Any:
                raise RuntimeError("ctrip read crashed")

        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            with _patched_scheduler_dependencies(
                scheduler,
                service=ExplodingService(),
                binding=_binding("trip3", "browser-trip3"),
                release=release,
            ):
                with self.assertRaisesRegex(RuntimeError, "ctrip read crashed"):
                    await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                        _pool_group(),
                        push_to_feishu=False,
                        actor_id="operator",
                        source="api",
                    )
            return release

        release = asyncio.run(scenario())
        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_times_out_stuck_read_and_releases_pool_lease(self) -> None:
        class HangingService:
            async def read_competitor_prices(self, **kwargs: Any) -> Any:
                await asyncio.Event().wait()

        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            with (
                _patched_scheduler_dependencies(
                    scheduler,
                    service=HangingService(),
                    binding=_binding("trip3", "browser-trip3"),
                    release=release,
                ),
                patch.object(
                    scheduler,
                    "COMPETITOR_PRICE_READ_TIMEOUT_SECONDS",
                    0.01,
                    create=True,
                ),
            ):
                with self.assertRaisesRegex(TimeoutError, "timed out"):
                    await asyncio.wait_for(
                        scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                            _pool_group(),
                            push_to_feishu=False,
                            actor_id="operator",
                            source="api",
                        ),
                        timeout=0.2,
                    )
            return release

        release = asyncio.run(scenario())
        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_releases_pool_lease_when_persistence_raises(self) -> None:
        async def exploding_save(**kwargs: Any) -> Any:
            raise RuntimeError("save crashed")

        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            with _patched_scheduler_dependencies(
                scheduler,
                binding=_binding("trip3", "browser-trip3"),
                save=AsyncMock(side_effect=exploding_save),
                release=release,
            ):
                with self.assertRaisesRegex(RuntimeError, "save crashed"):
                    await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                        _pool_group(),
                        push_to_feishu=False,
                        actor_id="operator",
                        source="api",
                    )
            return release

        release = asyncio.run(scenario())
        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_releases_pool_lease_when_target_resolution_raises(self) -> None:
        async def exploding_configured(**kwargs: Any) -> Any:
            raise RuntimeError("target config crashed")

        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            with _patched_scheduler_dependencies(
                scheduler,
                binding=_binding("trip3", "browser-trip3"),
                configured=AsyncMock(side_effect=exploding_configured),
                release=release,
            ):
                with self.assertRaisesRegex(RuntimeError, "target config crashed"):
                    await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                        _pool_group(),
                        push_to_feishu=False,
                        actor_id="operator",
                        source="api",
                    )
            return release

        release = asyncio.run(scenario())
        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_marks_failed_member_cooldown_and_fails_over_once(self) -> None:
        calls: list[dict[str, Any]] = []

        async def scenario() -> dict[str, Any]:
            scheduler = _reload_scheduler()
            first_service_result = _read_result(status="human_required")
            second_service_result = _read_result(status="success")
            service = _FakeService(
                calls={"reads": calls},
                results=[first_service_result, second_service_result],
            )
            record = AsyncMock()
            release = AsyncMock()
            resolver = AsyncMock(
                side_effect=[
                    _binding("trip1", "browser-trip1"),
                    _binding("trip2", "browser-trip2"),
                ]
            )
            with _patched_scheduler_dependencies(
                scheduler,
                service=service,
                binding=resolver,
                record=record,
                release=release,
            ):
                return await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(pool_failover_enabled=True),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )

        result = asyncio.run(scenario())

        self.assertEqual(result["status"], "success")
        self.assertEqual([item["browser_session_id"] for item in calls], ["browser-trip1", "browser-trip2"])

    def test_scheduler_enters_profile_gate_for_each_failover_selection(self) -> None:
        gate_calls: list[tuple[str, str, bool]] = []

        @asynccontextmanager
        async def fake_gate(
            platform_account_id: str,
            browser_session_id: str,
            *,
            enabled: bool,
        ):
            gate_calls.append((platform_account_id, browser_session_id, enabled))
            yield

        async def scenario() -> None:
            scheduler = _reload_scheduler()
            service = _FakeService(
                results=[
                    _read_result(status="human_required"),
                    _read_result(status="success"),
                ]
            )
            resolver = AsyncMock(
                side_effect=[
                    _binding("trip1", "browser-trip1"),
                    _binding("trip2", "browser-trip2"),
                ]
            )
            with (
                _patched_scheduler_dependencies(
                    scheduler,
                    service=service,
                    binding=resolver,
                ),
                patch.object(scheduler, "competitor_profile_poll", fake_gate),
            ):
                await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(pool_failover_enabled=True),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )

        asyncio.run(scenario())

        self.assertEqual(
            gate_calls,
            [
                ("trip1", "browser-trip1", True),
                ("trip2", "browser-trip2", True),
            ],
        )

    def test_scheduler_releases_pool_lease_when_pool_result_recording_raises(self) -> None:
        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            release = AsyncMock()
            record = AsyncMock(side_effect=RuntimeError("pool result save crashed"))
            with _patched_scheduler_dependencies(
                scheduler,
                binding=_binding("trip3", "browser-trip3"),
                record=record,
                release=release,
            ):
                with self.assertRaisesRegex(RuntimeError, "pool result save crashed"):
                    await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                        _pool_group(),
                        push_to_feishu=False,
                        actor_id="operator",
                        source="api",
                    )
            return release

        release = asyncio.run(scenario())

        release.assert_awaited_once()
        self.assertEqual(release.await_args.kwargs["selection"].browser_session_id, "browser-trip3")

    def test_scheduler_does_not_advance_last_polled_when_pool_has_no_available_account(self) -> None:
        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            mark_polled = AsyncMock()
            with _patched_scheduler_dependencies(
                scheduler,
                binding=SimpleNamespace(
                    ok=False,
                    status="human_required",
                    summary="no account",
                    selection=None,
                    evidence={"pool_enabled": True},
                    next_action="login",
                ),
                mark_polled=mark_polled,
            ):
                result = await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            self.assertEqual(result["status"], "human_required")
            return mark_polled

        mark_polled = asyncio.run(scenario())
        mark_polled.assert_not_called()

    def test_run_persistence_records_actual_selected_browser_session(self) -> None:
        save = AsyncMock(return_value=_persistence(run_id=42))

        async def scenario() -> AsyncMock:
            scheduler = _reload_scheduler()
            with _patched_scheduler_dependencies(
                scheduler,
                binding=_binding("trip3", "browser-trip3"),
                save=save,
            ):
                await scheduler.CompetitorPriceScheduler(tick_seconds=1)._poll_group(
                    _pool_group(),
                    push_to_feishu=False,
                    actor_id="operator",
                    source="api",
                )
            return save

        save = asyncio.run(scenario())

        save.assert_awaited_once()
        self.assertEqual(save.await_args.kwargs["platform_account_id"], "trip3")
        self.assertEqual(save.await_args.kwargs["browser_session_id"], "browser-trip3")


class _FakeService:
    def __init__(
        self,
        *,
        calls: dict[str, Any] | None = None,
        results: list[Any] | None = None,
    ) -> None:
        self.calls = calls if calls is not None else {}
        self.results = list(results or [_read_result(status="success")])

    async def read_competitor_prices(self, **kwargs: Any) -> Any:
        if "reads" in self.calls:
            self.calls["reads"].append(kwargs)
        else:
            self.calls["read"] = kwargs
        return self.results.pop(0)


def _reload_scheduler():
    scheduler = importlib.import_module("app.domain.competitor_prices.scheduler")
    return importlib.reload(scheduler)


def _pool_group(*, pool_failover_enabled: bool = False) -> dict[str, Any]:
    return {
        "group_key": "ops_watch",
        "platform_account_id": "__pool__",
        "browser_session_id": None,
        "browser_rotation_mode": "pool_round_robin",
        "pool_failover_enabled": pool_failover_enabled,
    }


def _binding(platform_account_id: str, browser_session_id: str) -> Any:
    selection = SimpleNamespace(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        source="watch_group_pool",
        pool_enabled=True,
        evidence=lambda: {
            "source": "watch_group_pool",
            "pool_enabled": True,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
        },
    )
    return SimpleNamespace(
        ok=True,
        status="success",
        summary="ok",
        selection=selection,
        evidence={"browser_selection": selection.evidence()},
        next_action=None,
    )


def _read_result(*, status: str) -> Any:
    return SimpleNamespace(
        status=status,
        summary=status,
        data={"snapshots": []},
        evidence={"trusted_dom_rate_count": 1},
    )


def _persistence(*, run_id: int | None) -> Any:
    return SimpleNamespace(
        run_id=run_id,
        model_dump=lambda mode: {"status": "success", "run_id": run_id},
    )


def _patched_scheduler_dependencies(
    scheduler,
    *,
    binding: Any,
    service: _FakeService | None = None,
    record: AsyncMock | None = None,
    release: AsyncMock | None = None,
    save: AsyncMock | None = None,
    configured: AsyncMock | None = None,
    mark_polled: AsyncMock | None = None,
):
    service = service or _FakeService()
    return patch.multiple(
        scheduler,
        get_latest_competitor_price_run_meta=AsyncMock(return_value=None),
        resolve_competitor_browser_binding_for_group=(
            binding if isinstance(binding, AsyncMock) else AsyncMock(return_value=binding)
        ),
        resolve_configured_competitor_targets=configured
        or AsyncMock(
            return_value=SimpleNamespace(
                own_hotel_url="https://hotels.ctrip.com/hotels/detail/?hotelId=1",
                competitor_urls=["https://hotels.ctrip.com/hotels/detail/?hotelId=2"],
                own_hotel_alias="Own",
                competitor_aliases=["Comp"],
            )
        ),
        get_ctrip_ebooking_service=Mock(return_value=service),
        save_ctrip_competitor_price_result=save or AsyncMock(return_value=_persistence(run_id=42)),
        update_competitor_price_run_push_result=AsyncMock(),
        _evaluate_price_change_alert_after_run=AsyncMock(
            return_value={"status": "skipped", "summary": "test"}
        ),
        record_competitor_browser_pool_run_result=record or AsyncMock(),
        release_competitor_browser_pool_lease=release or AsyncMock(),
        mark_competitor_watch_group_polled=mark_polled or AsyncMock(),
        create=True,
    )


if __name__ == "__main__":
    unittest.main()
