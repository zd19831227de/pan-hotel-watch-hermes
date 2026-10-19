from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import client.watch_api as watch_api
from client.operator_summary_contract import CONTRACT_VERSION
from client.watch_api import (
    WatchApiClient,
    build_manual_pricing_feishu_payload,
    enrich_calendar_price_latest_response,
    enrich_ebk_latest_response,
)


class EbkLatestEnrichmentTests(unittest.TestCase):
    def test_enrich_adds_review_summary_from_comment_monitor(self) -> None:
        payload = enrich_ebk_latest_response(
            {
                "watch_group_key": "ctrip_ebooking_data_center",
                "run": {"status": "success", "captured_at": "2026-06-25T09:12:38+00:00"},
                "dataset_results": [
                    {
                        "dataset_id": "comment_monitor",
                        "captured_at": "2026-06-25T09:12:38+00:00",
                        "data": {
                            "comment_monitor": {
                                "review_overview": {
                                    "positive_reviews_yesterday": {"value": "6"},
                                    "negative_reviews_yesterday": {"value": "0"},
                                }
                            }
                        },
                    }
                ],
            }
        )
        review = payload["operator_summary"]["review_summary"]
        self.assertTrue(review["available"])
        self.assertEqual(review["positive_reviews_yesterday"], "6")
        self.assertIn("昨日好评", review["caliber_note"])
        self.assertIn("run_ebk_watch_once", payload["operator_summary"]["usage_note"])


class CalendarPriceEnrichmentTests(unittest.TestCase):
    def test_enrich_calendar_latest_builds_read_only_operator_summary(self) -> None:
        payload = enrich_calendar_price_latest_response(
            {
                "status": "alert",
                "watch_group_key": "default_competitor_watch",
                "future_days": 15,
                "trusted_source": "calendar_dom_cell_price",
                "run": {"id": 10, "status": "alert", "captured_at": "2026-07-06T20:09:13+08:00"},
                "dates": [
                    {
                        "checkin_date": "2026-07-09",
                        "own": {"hotel_alias": "Own hotel", "price_amount_yuan": 411, "status": "success"},
                        "competitors": [
                            {"hotel_alias": "Competitor A", "price_amount_yuan": 398, "status": "success"},
                            {"hotel_alias": "Competitor B", "price_amount_yuan": 420, "status": "success"},
                        ],
                        "alerts": [{"alert_type": "own_price_high", "gap_yuan": 13}],
                    }
                ],
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["watch_kind"], "competitor")
        self.assertEqual(operator["module"], "calendar_price")
        self.assertEqual(operator["legacy_module"], "calendar_price_watch")
        self.assertEqual(operator["alert_count"], 1)
        self.assertEqual(operator["future_days"], 15)
        self.assertIn("run_calendar_price_watch_once", operator["usage_note"])
        self.assertIn("2026-07-09", operator["calendar_rows"][0]["field"])
        self.assertIn("Competitor A", operator["calendar_rows"][0]["value"])
        self.assertEqual(payload["feishu_card_kind"], "calendar_price_latest")
        self.assertIn("feishu_card", payload)
        serialized = str(payload)
        for forbidden in ("pricing_approval", "write_to_ctrip", "auto_execute", "confirm_price_change"):
            self.assertNotIn(forbidden, serialized)


class WatchApiClientProxyTests(unittest.TestCase):
    """本地后端绝不能走系统代理：开了全局代理(如 127.0.0.1:7897)时，
    httpx 默认 trust_env=True 会把 127.0.0.1:8000 也丢给代理 -> 502 Bad Gateway。"""

    def _capture_client_kwargs(
        self, method_name: str, *args: object, **call_kwargs: object
    ) -> dict[str, object]:
        captured: dict[str, object] = {}

        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"ok": True}

        class _FakeAsyncClient:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def get(self, *a: object, **k: object) -> _FakeResponse:
                return _FakeResponse()

            async def post(self, *a: object, **k: object) -> _FakeResponse:
                return _FakeResponse()

            async def patch(self, *a: object, **k: object) -> _FakeResponse:
                return _FakeResponse()

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            await getattr(client, method_name)(*args, **call_kwargs)

        with mock.patch.object(watch_api.httpx, "AsyncClient", _FakeAsyncClient):
            asyncio.run(scenario())
        return captured

    def test_get_json_disables_env_proxy(self) -> None:
        captured = self._capture_client_kwargs("get_json", "/api/v1/health")
        self.assertEqual(captured.get("trust_env"), False)

    def test_post_json_disables_env_proxy(self) -> None:
        captured = self._capture_client_kwargs("post_json", "/api/v1/feishu/events")
        self.assertEqual(captured.get("trust_env"), False)

    def test_headers_include_configured_watch_service_token(self) -> None:
        with mock.patch.dict(
            watch_api.os.environ,
            {"WATCH_API_SHARED_SECRET": "service-secret-123456"},
        ):
            headers = WatchApiClient()._headers()  # noqa: SLF001

        self.assertEqual(
            headers["X-Pan-Hotel-Service-Token"],
            "service-secret-123456",
        )

    def test_patch_json_disables_env_proxy(self) -> None:
        captured = self._capture_client_kwargs(
            "patch_json", "/api/v1/x", body={"enabled": True}
        )
        self.assertEqual(captured.get("trust_env"), False)

    def test_get_refund_status_refresh_triggers_fetch_then_returns_snapshot(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            snapshot_payload = {
                "id": 7,
                "platform": "ctrip",
                "refund_date": "2026-09-04",
                "total_count": 2,
                "by_room_type": {"豪华套房": 1, "家庭双卧套房": 1},
                "by_hour": {"16": 1, "17": 1},
                "peak_hours": [{"hour": "16", "count": 1}, {"hour": "17", "count": 1}],
                "time_caliber": "notify_time",
                "captured_at": "2026-09-04 16:32:00",
            }
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "ok"}}),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshot": snapshot_payload}),
                ) as get_json,
            ):
                payload = await client.get_refund_status(platform="ctrip", refresh=True)
            post_json.assert_awaited_once_with(
                "/api/v1/refunds/fetch",
                body={"platform": "ctrip", "chat_id": None},
                timeout=watch_api.BROWSER_RUN_TIMEOUT,
            )
            get_json.assert_awaited_once_with(
                "/api/v1/refunds", params={"platform": "ctrip"}
            )
            # 小时段分布与口径透传给飞书问答回复。
            self.assertEqual(payload["snapshot"]["by_hour"], {"16": 1, "17": 1})
            self.assertEqual(payload["snapshot"]["peak_hours"][0]["hour"], "16")
            self.assertEqual(payload["snapshot"]["time_caliber"], "notify_time")

        asyncio.run(scenario())

    def test_get_refund_status_no_refresh_skips_fetch(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(client, "post_json", new=mock.AsyncMock()) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": []}),
                ) as get_json,
            ):
                payload = await client.get_refund_status()
            post_json.assert_not_awaited()
            get_json.assert_awaited_once_with("/api/v1/refunds", params=None)
            self.assertEqual(payload, {"snapshots": []})

        asyncio.run(scenario())

    def test_get_refund_status_refresh_without_platform_fetches_all_platforms(self) -> None:
        # 2026-09-05 运营反馈：三平台汇总问法不触发现抓，一直回旧快照。
        # platform 省略 + refresh=True 必须并发触发三平台 fetch（自动拉起浏览器）。
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "ok"}}),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": []}),
                ) as get_json,
            ):
                payload = await client.get_refund_status(refresh=True)
            bodies = [call.kwargs["body"] for call in post_json.await_args_list]
            self.assertEqual(
                sorted(body["platform"] for body in bodies),
                ["ctrip", "fliggy", "meituan"],
            )
            for call in post_json.await_args_list:
                self.assertEqual(call.args[0], "/api/v1/refunds/fetch")
                self.assertEqual(call.kwargs["timeout"], watch_api.BROWSER_RUN_TIMEOUT)
            get_json.assert_awaited_once_with("/api/v1/refunds", params=None)
            self.assertEqual(payload, {"snapshots": []})

        asyncio.run(scenario())

    def test_get_refund_status_refresh_all_platforms_tolerates_fetch_failure(self) -> None:
        # 三平台并发现抓尽力而为：单个平台 fetch 失败不阻断汇总读取，
        # 且把失败平台标到 refresh_fetch_failed 供回复如实说明。
        httpx = watch_api.httpx

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fetch(path: str, *, body: dict, timeout: float):
                if body.get("platform") == "meituan":
                    raise httpx.ConnectError(
                        "connect failed",
                        request=httpx.Request(
                            "POST", "http://127.0.0.1:8000/api/v1/refunds/fetch"
                        ),
                    )
                return {"result": {"status": "ok"}}

            with (
                mock.patch.object(
                    client, "post_json", new=mock.AsyncMock(side_effect=fetch)
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": [{"platform": "ctrip"}]}),
                ) as get_json,
            ):
                payload = await client.get_refund_status(refresh=True)
            self.assertEqual(post_json.await_count, 3)
            get_json.assert_awaited_once_with("/api/v1/refunds", params=None)
            self.assertEqual(payload["refresh_fetch_failed"], ["meituan: ConnectError"])
            self.assertEqual(payload["snapshots"], [{"platform": "ctrip"}])

        asyncio.run(scenario())

    def test_get_refund_status_refresh_all_platforms_marks_non_ok_status(self) -> None:
        # 后端抓取失败返回 HTTP 200 + result.status=not_ready/error；
        # client 不能把「HTTP 成功但抓取失败」当成现抓成功。
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fetch(path: str, *, body: dict, timeout: float):
                if body.get("platform") == "ctrip":
                    return {
                        "result": {
                            "status": "not_ready",
                            "platform": "ctrip",
                            "error": "浏览器未登录",
                        }
                    }
                return {"result": {"status": "ok", "platform": body.get("platform")}}

            with (
                mock.patch.object(
                    client, "post_json", new=mock.AsyncMock(side_effect=fetch)
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": []}),
                ) as get_json,
            ):
                payload = await client.get_refund_status(refresh=True)
            self.assertEqual(post_json.await_count, 3)
            get_json.assert_awaited_once_with("/api/v1/refunds", params=None)
            self.assertEqual(payload["refresh_fetch_failed"], ["ctrip: not_ready"])

        asyncio.run(scenario())

    def test_get_refund_status_404_returns_not_found(self) -> None:
        httpx = watch_api.httpx

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/refunds")
            response = httpx.Response(404, request=request)
            get_json = mock.AsyncMock(
                side_effect=httpx.HTTPStatusError("404", request=request, response=response)
            )
            with mock.patch.object(client, "get_json", new=get_json):
                payload = await client.get_refund_status(platform="fliggy")
            self.assertEqual(payload["status"], "not_found")
            self.assertEqual(payload["platform"], "fliggy")
            self.assertIn("暂无退单记录", payload["summary"])

        asyncio.run(scenario())

    def test_get_today_reservations_refresh_triggers_fetch_then_returns_snapshot(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            snapshot_payload = {
                "id": 9,
                "platform": "ctrip",
                "reservation_date": "2026-09-04",
                "total_count": 3,
                "by_room_type": {"豪华套房": 1, "家庭双卧套房": 2},
                "by_hour": {"16": 2, "17": 1},
                "peak_hours": [{"hour": "16", "count": 2}, {"hour": "17", "count": 1}],
                "time_caliber": "notify_time",
                "captured_at": "2026-09-04 16:32:00",
            }
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "ok"}}),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshot": snapshot_payload}),
                ) as get_json,
            ):
                payload = await client.get_today_reservations(platform="ctrip", refresh=True)
            post_json.assert_awaited_once_with(
                "/api/v1/reservations/fetch",
                body={"platform": "ctrip", "chat_id": None},
                timeout=watch_api.BROWSER_RUN_TIMEOUT,
            )
            get_json.assert_awaited_once_with(
                "/api/v1/reservations", params={"platform": "ctrip"}
            )
            self.assertEqual(payload["snapshot"]["by_hour"], {"16": 2, "17": 1})
            self.assertEqual(payload["snapshot"]["time_caliber"], "notify_time")

        asyncio.run(scenario())

    def test_get_today_reservations_404_returns_not_found(self) -> None:
        httpx = watch_api.httpx

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/reservations")
            response = httpx.Response(404, request=request)
            get_json = mock.AsyncMock(
                side_effect=httpx.HTTPStatusError("404", request=request, response=response)
            )
            with mock.patch.object(client, "get_json", new=get_json):
                payload = await client.get_today_reservations(platform="fliggy")
            self.assertEqual(payload["status"], "not_found")
            self.assertEqual(payload["platform"], "fliggy")
            self.assertIn("暂无今日预定记录", payload["summary"])

        asyncio.run(scenario())

    def test_get_today_reservations_refresh_without_platform_fetches_all_platforms(self) -> None:
        # 同退单：三平台汇总问法 + refresh 必须并发现抓三平台，不再回旧快照。
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "ok"}}),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": []}),
                ) as get_json,
            ):
                payload = await client.get_today_reservations(refresh=True)
            bodies = [call.kwargs["body"] for call in post_json.await_args_list]
            self.assertEqual(
                sorted(body["platform"] for body in bodies),
                ["ctrip", "fliggy", "meituan"],
            )
            for call in post_json.await_args_list:
                self.assertEqual(call.args[0], "/api/v1/reservations/fetch")
            get_json.assert_awaited_once_with("/api/v1/reservations", params=None)
            self.assertEqual(payload, {"snapshots": []})

        asyncio.run(scenario())

    def test_get_today_reservations_refresh_all_platforms_tolerates_fetch_failure(self) -> None:
        httpx = watch_api.httpx

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fetch(path: str, *, body: dict, timeout: float):
                if body.get("platform") == "fliggy":
                    raise httpx.ConnectError(
                        "connect failed",
                        request=httpx.Request(
                            "POST", "http://127.0.0.1:8000/api/v1/reservations/fetch"
                        ),
                    )
                return {"result": {"status": "ok"}}

            with (
                mock.patch.object(
                    client, "post_json", new=mock.AsyncMock(side_effect=fetch)
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": [{"platform": "ctrip"}]}),
                ) as get_json,
            ):
                payload = await client.get_today_reservations(refresh=True)
            self.assertEqual(post_json.await_count, 3)
            get_json.assert_awaited_once_with("/api/v1/reservations", params=None)
            self.assertEqual(payload["refresh_fetch_failed"], ["fliggy: ConnectError"])
            self.assertEqual(payload["snapshots"], [{"platform": "ctrip"}])

        asyncio.run(scenario())

    def test_get_today_reservations_refresh_all_platforms_marks_non_ok_status(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fetch(path: str, *, body: dict, timeout: float):
                if body.get("platform") == "meituan":
                    return {
                        "result": {
                            "status": "error",
                            "platform": "meituan",
                            "error": "页面加载超时",
                        }
                    }
                return {"result": {"status": "ok", "platform": body.get("platform")}}

            with (
                mock.patch.object(
                    client, "post_json", new=mock.AsyncMock(side_effect=fetch)
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshots": []}),
                ) as get_json,
            ):
                payload = await client.get_today_reservations(refresh=True)
            self.assertEqual(post_json.await_count, 3)
            self.assertEqual(payload["refresh_fetch_failed"], ["meituan: error"])

        asyncio.run(scenario())


    def test_competitor_stale_data_suggests_immediate_run(self) -> None:
        from datetime import datetime, timedelta, timezone

        from client.watch_api import enrich_competitor_latest_response

        old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        snapshot = enrich_competitor_latest_response(
            {
                "watch_group_key": "default_competitor_watch",
                "run": {"status": "success", "captured_at": old_time},
                "hotels": [
                    {
                        "hotel_config": {"alias": "竞对A"},
                        "snapshots": {
                            "today": {
                                "rates": [
                                    {
                                        "is_trusted": True,
                                        "room_type": "高级大床房",
                                        "breakfast_text": "无早",
                                        "price_amount_yuan": 420,
                                    }
                                ]
                            }
                        },
                    }
                ],
            }
        )
        operator = snapshot["operator_summary"]
        self.assertTrue(operator["suggest_immediate_run"])
        self.assertIn("run_competitor_watch_once", operator["usage_note"])

    def test_ebk_stale_data_suggests_immediate_run(self) -> None:
        from datetime import datetime, timedelta, timezone

        from client.watch_api import enrich_ebk_latest_response

        old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        snapshot = enrich_ebk_latest_response(
            {
                "watch_group_key": "ctrip_ebooking_data_center",
                "run": {"status": "success", "captured_at": old_time},
                "dataset_results": [
                    {
                        "dataset_id": "comment_monitor",
                        "data": {
                            "comment_monitor": {
                                "review_overview": {
                                    "positive_reviews_yesterday": {"value": "3"},
                                }
                            }
                        },
                    }
                ],
            }
        )
        operator = snapshot["operator_summary"]
        self.assertTrue(operator["suggest_immediate_run"])
        self.assertIn("run_ebk_watch_once", operator["usage_note"])

    def test_run_competitor_watch_once_posts_run_now_and_returns_latest(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={
                            "watch_group_key": "default_competitor_watch",
                            "status": "success",
                            "summary": "读取成功",
                            "feishu_push": {"status": "skipped"},
                        }
                    ),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(
                        return_value={
                            "watch_group_key": "default_competitor_watch",
                            "run": {"status": "success", "captured_at": "2026-06-26T08:00:00+00:00"},
                            "hotels": [],
                        }
                    ),
                ),
            ):
                payload = await client.run_competitor_watch_once("default_competitor_watch", push_to_feishu=False)

            post_json.assert_awaited_once_with(
                "/api/v1/ctrip-ebooking/competitor-watch-groups/default_competitor_watch/run-now",
                params={"push_to_feishu": False},
                timeout=300.0,
            )
            self.assertTrue(payload.get("run_triggered"))
            self.assertEqual(payload["operator_summary"]["source"], "browser_run_now")

        asyncio.run(scenario())

    def test_get_calendar_price_latest_fetches_matrix_and_finalizes_pending_action(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(
                        return_value={
                            "matrix": {
                                "status": "success",
                                "watch_group_key": "default_competitor_watch",
                                "future_days": 15,
                                "trusted_source": "calendar_dom_cell_price",
                                "run": {"status": "success", "captured_at": "2026-07-06T12:00:00+00:00"},
                                "dates": [],
                            }
                        }
                    ),
                ) as get_json,
                mock.patch.object(
                    client,
                    "_finalize_latest_with_pending",
                    new=mock.AsyncMock(side_effect=lambda payload, **kwargs: {**payload, "finalize_kwargs": kwargs}),
                ) as finalize,
            ):
                payload = await client.get_calendar_price_latest(
                    "default_competitor_watch",
                    chat_id="oc_group",
                    operator_id="ou_operator",
                )

            get_json.assert_awaited_once_with(
                "/api/v1/calendar-price-watch/default_competitor_watch/latest",
            )
            finalize.assert_awaited_once()
            self.assertEqual(payload["operator_summary"]["module"], "calendar_price")
            self.assertEqual(payload["operator_summary"]["legacy_module"], "calendar_price_watch")
            self.assertEqual(payload["finalize_kwargs"]["watch_kind"], watch_api.WATCH_KIND_COMPETITOR)
            self.assertEqual(payload["finalize_kwargs"]["watch_group_key"], "default_competitor_watch")

        asyncio.run(scenario())

    def test_get_calendar_price_watch_status_fetches_config_latest_and_scheduler(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None) -> dict:
                if path == "/api/v1/calendar-price-watch/default_competitor_watch/config":
                    return {
                        "config": {
                            "watch_group_key": "default_competitor_watch",
                            "future_days": 15,
                            "auto_poll_enabled": True,
                            "schedule_interval_minutes": 30,
                            "high_gap_threshold_yuan": 10,
                            "low_gap_threshold_yuan": 20,
                            "last_polled_at": "2026-07-08T10:31:00+08:00",
                        }
                    }
                if path == "/api/v1/calendar-price-watch/default_competitor_watch/latest":
                    return {
                        "matrix": {
                            "status": "success",
                            "watch_group_key": "default_competitor_watch",
                            "future_days": 15,
                            "trusted_source": "calendar_dom_cell_price",
                            "run": {
                                "status": "success",
                                "captured_at": "2026-07-08T10:31:00+08:00",
                            },
                            "dates": [],
                        }
                    }
                if path == "/api/v1/calendar-price-watch/scheduler-status":
                    return {"scheduler": {"status": "running", "heartbeat_status": "healthy"}}
                raise AssertionError(f"unexpected GET path: {path}")

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_calendar_price_watch_status(
                    "default_competitor_watch",
                    chat_id="oc_group",
                    operator_id="ou_operator",
                )

            operator = payload["operator_summary"]
            self.assertEqual(operator["module"], "calendar_price_watch")
            self.assertEqual(operator["status_kind"], "calendar_price_watch_status")
            self.assertTrue(operator["auto_poll_enabled"])
            self.assertEqual(operator["schedule_interval_minutes"], 30)
            self.assertEqual(operator["latest_run_status"], "success")
            self.assertEqual(payload["feishu_card_kind"], "calendar_price_status")
            serialized = str(payload)
            for forbidden in (
                "pricing_approval",
                "dry-run",
                "execute",
                "write_to_ctrip",
                "confirm_price_change",
            ):
                self.assertNotIn(forbidden, serialized)

        asyncio.run(scenario())

    def test_run_calendar_price_watch_once_posts_run_now_and_returns_latest_matrix(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={
                            "result": {
                                "watch_group_key": "default_competitor_watch",
                                "status": "alert",
                                "summary": "calendar alert",
                                "future_days": 15,
                                "alert_count": 1,
                                "feishu_push": {"status": "skipped"},
                            }
                        }
                    ),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(
                        return_value={
                            "matrix": {
                                "status": "alert",
                                "watch_group_key": "default_competitor_watch",
                                "future_days": 15,
                                "trusted_source": "calendar_dom_cell_price",
                                "run": {"status": "alert", "captured_at": "2026-07-06T12:00:00+00:00"},
                                "dates": [],
                            }
                        }
                    ),
                ) as get_json,
            ):
                payload = await client.run_calendar_price_watch_once(
                    "default_competitor_watch",
                    push_to_feishu=False,
                    operator_id="ou_operator",
                )

            post_json.assert_awaited_once_with(
                "/api/v1/calendar-price-watch/default_competitor_watch/run-now",
                body={"actor_id": "ou_operator"},
                params={"push_to_feishu": False},
                timeout=300.0,
            )
            get_json.assert_awaited_once_with(
                "/api/v1/calendar-price-watch/default_competitor_watch/latest",
            )
            self.assertTrue(payload.get("run_triggered"))
            self.assertEqual(payload["operator_summary"]["source"], "browser_run_now")
            self.assertEqual(payload["operator_summary"]["module"], "calendar_price")
            self.assertEqual(payload["operator_summary"]["legacy_module"], "calendar_price_watch")

        asyncio.run(scenario())


class EbkDatasetReadEnrichmentTests(unittest.TestCase):
    def test_normalize_maps_promotion_effectiveness_alias(self) -> None:
        self.assertEqual(
            watch_api._normalize_ebk_dataset_id("promotion_effectiveness"),
            "promotion_effect",
        )
        self.assertEqual(
            watch_api._normalize_ebk_dataset_id("已参加促销"),
            "promotion_registered",
        )
        self.assertEqual(
            watch_api._normalize_ebk_dataset_id("流量"),
            "flow_data",
        )

    def test_normalize_maps_pyramid_bid_campaign_aliases(self) -> None:
        # 2026-09-22：金字塔推广计划（计划名=投放时段）并入按需数据集目录。
        for alias in ("金字塔推广计划", "推广计划", "金字塔出价", "金字塔时段", "pyramid_bid", "pyramid_campaigns"):
            self.assertEqual(watch_api._normalize_ebk_dataset_id(alias), "pyramid_bid", alias)
        # 裸「金字塔」仍指数据报告页（ad_pyramid 四组指标），不被劫持。
        self.assertEqual(watch_api._normalize_ebk_dataset_id("金字塔"), "ad_pyramid")
        self.assertIn("pyramid_bid", watch_api.EBK_READABLE_DATASET_IDS)

    def test_expand_pyramid_bid_dataset_ids_appends_for_time_slot_questions(self) -> None:
        # 2026-09-22 实测：组合问法下 agent 只传 ad_pyramid 漏掉 pyramid_bid，
        # 导致回复误称「不提供分时花费」。时段词 + ad_pyramid 双命中时
        # 工具层确定性补齐，不依赖 LLM 记得带。
        expand = watch_api.expand_pyramid_bid_dataset_ids
        self.assertEqual(
            expand(
                ["ad_pyramid"],
                user_text="分析今天金字塔投放的情况，要结合今天每小时的订单产量和金字塔的投放成交",
            ),
            ["ad_pyramid", "pyramid_bid"],
        )
        self.assertEqual(
            expand(["ad_pyramid"], user_text="金字塔各个时段的投放花费"),
            ["ad_pyramid", "pyramid_bid"],
        )
        self.assertEqual(
            expand(["ad_pyramid"], user_text="看下每个计划的投放"),
            ["ad_pyramid", "pyramid_bid"],
        )
        # 无时段词、无金字塔、已带 pyramid_bid、空入参均不改动。
        self.assertEqual(expand(["ad_pyramid"], user_text="金字塔今天花了多少钱"), ["ad_pyramid"])
        self.assertEqual(expand(["ad_pyramid", "pyramid_bid"], user_text="每小时订单产量"), ["ad_pyramid", "pyramid_bid"])
        self.assertEqual(expand(["business_outline"], user_text="每小时订单产量"), ["business_outline"])
        self.assertIsNone(expand(None, user_text="每小时订单产量"))
        self.assertEqual(expand(["ad_pyramid"], user_text=None), ["ad_pyramid"])

    def test_enrich_builds_pyramid_bid_summary_rows(self) -> None:
        payload = watch_api.enrich_ebk_dataset_read_response(
            {
                "status": "success",
                "requested_dataset_ids": ["pyramid_bid"],
                "dataset_results": [
                    {
                        "dataset_id": "pyramid_bid",
                        "dataset_label": "金字塔推广计划",
                        "status": "success",
                        "data": {
                            "promotions": [
                                {
                                    "campaign_name": "周内0-2",
                                    "campaign_status": "推广中",
                                    "click_bid_cny": 6.5,
                                    "today_spend_cny": 45.2,
                                    "today_impressions": 1200,
                                    "today_clicks": 8,
                                },
                                {
                                    "campaign_name": "周末14-16",
                                    "campaign_status": "推广中",
                                    "click_bid_cny": 5.0,
                                    "today_spend_cny": 12.0,
                                },
                            ],
                        },
                    }
                ],
            }
        )
        summary = payload["operator_summary"]["pyramid_bid_summary"]
        self.assertTrue(summary["available"])
        self.assertEqual(summary["campaign_count"], 2)
        self.assertEqual(summary["today_total_spend_cny"], 57.2)
        self.assertEqual(summary["rows"][0]["campaign_name"], "周内0-2")
        self.assertEqual(summary["rows"][0]["today_spend_cny"], 45.2)

    def test_resolve_collects_invalid_dataset_ids(self) -> None:
        resolved, invalid = watch_api._resolve_ebk_read_dataset_ids(
            dataset_ids=["promotion_registered", "not_a_real_dataset"],
            metric_keys=None,
        )
        self.assertEqual(resolved, ["promotion_registered"])
        self.assertEqual(invalid, ["not_a_real_dataset"])

    def test_enrich_ebk_dataset_read_response_builds_promotion_summaries(self) -> None:
        payload = watch_api.enrich_ebk_dataset_read_response(
            {
                "status": "success",
                "requested_dataset_ids": ["promotion_registered", "ad_pyramid"],
                "dataset_results": [
                    {
                        "dataset_id": "promotion_registered",
                        "dataset_label": "已参加促销",
                        "status": "success",
                        "data": {
                            "summary": {"visible_promotion_count": 2},
                            "promotions": [
                                {
                                    "name": "连住优惠",
                                    "promotion_group": "天天特价",
                                    "room_types": "高级大床房",
                                    "promotion_time": "2026-07-01 至 2026-07-31",
                                }
                            ],
                        },
                    },
                    {
                        "dataset_id": "ad_pyramid",
                        "dataset_label": "广告金字塔",
                        "status": "success",
                        "data": {
                            "ad_pyramid": {
                                "groups": [
                                    {
                                        "label": "今日",
                                        "metrics": [
                                            {"label": "总花费(CNY)", "value": "113"},
                                            {"label": "广告投产比(ROAS)", "value": "11.09"},
                                        ],
                                    }
                                ]
                            }
                        },
                    },
                ],
                "operator_summary": {"latest_captured_at": "2026-07-07T03:30:00+08:00"},
            }
        )
        operator = payload["operator_summary"]
        self.assertTrue(operator["promotion_registered_summary"]["available"])
        self.assertEqual(operator["promotion_registered_summary"]["visible_promotion_count"], 2)
        self.assertTrue(operator["ad_pyramid_summary"]["available"])
        self.assertEqual(payload["feishu_card_kind"], "ebk_datasets_read")
        self.assertIn("连住优惠", str(operator["dataset_read_rows"]))

    def test_enrich_multi_dataset_response_sets_analysis_note(self) -> None:
        payload = watch_api.enrich_ebk_dataset_read_response(
            {
                "status": "success",
                "requested_dataset_ids": ["flow_data", "beneficial_data"],
                "dataset_results": [
                    {
                        "dataset_id": "flow_data",
                        "dataset_label": "流量数据",
                        "status": "success",
                        "data": {
                            "metrics": {
                                "visitors": {"label": "访客量", "value": "165", "last_week_same_period": "573"},
                            }
                        },
                    },
                    {
                        "dataset_id": "beneficial_data",
                        "dataset_label": "销售数据",
                        "status": "success",
                        "data": {
                            "metrics": {
                                "booking_orders": {"label": "预订订单数", "value": "17"},
                            }
                        },
                    },
                ],
                "operator_summary": {},
            }
        )
        operator = payload["operator_summary"]
        self.assertIn("multi_dataset_analysis_note", operator)
        self.assertIn("flow_data", operator["dataset_summaries"])
        self.assertIn("beneficial_data", operator["dataset_summaries"])
        self.assertEqual(payload["feishu_card_kind"], "ebk_datasets_read")


class OperatorSummaryV1QualityTests(unittest.TestCase):
    def test_ebk_dataset_read_has_native_v1_contract(self) -> None:
        payload = watch_api.enrich_ebk_dataset_read_response(
            {
                "status": "success",
                "watch_group_key": "ctrip_ebooking_data_center",
                "requested_dataset_ids": ["beneficial_data"],
                "dataset_results": [
                    {
                        "dataset_id": "beneficial_data",
                        "dataset_label": "销售数据",
                        "status": "success",
                        "captured_at": "2026-07-08T12:00:00+08:00",
                        "data": {
                            "metrics": {
                                "occupancy_rate": {"label": "出租率", "value": "82%"},
                                "adr": {"label": "ADR", "value": "420元"},
                            }
                        },
                    }
                ],
                "operator_summary": {
                    "intro": "EBK 数据中心现读完成，出租率 82%。",
                    "latest_captured_at": "2026-07-08T12:00:00+08:00",
                },
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "ebk")
        self.assertEqual(operator["intent"], "immediate_read")
        self.assertEqual(operator["data_time"]["captured_at"], "2026-07-08T12:00:00+08:00")
        self.assertEqual(operator["evidence"][0]["label"], "出租率")
        self.assertEqual(operator["safety_boundary"]["level"], "read_only")
        self.assertEqual(operator["reply_mode"], "card_first")

    def test_competitor_latest_v1_marks_stale_refresh_as_confirmed_action(self) -> None:
        from datetime import datetime, timedelta, timezone

        old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        payload = watch_api.enrich_competitor_latest_response(
            {
                "status": "success",
                "watch_group_key": "default_competitor_watch",
                "run": {"status": "success", "captured_at": old_time},
                "hotels": [
                    {
                        "hotel_config": {"alias": "竞品A"},
                        "snapshots": {
                            "today": {
                                "rates": [
                                    {
                                        "is_trusted": True,
                                        "room_type": "高级大床房",
                                        "breakfast_text": "无早",
                                        "price_amount_yuan": 416,
                                    }
                                ]
                            }
                        },
                    }
                ],
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "competitor")
        self.assertEqual(operator["intent"], "latest_query")
        self.assertEqual(operator["freshness"]["state"], "stale")
        self.assertEqual(operator["next_actions"][0]["tool"], "run_competitor_watch_once")
        self.assertTrue(operator["next_actions"][0]["requires_confirmation"])

    def test_calendar_latest_v1_is_read_only_and_keeps_legacy_module(self) -> None:
        payload = watch_api.enrich_calendar_price_latest_response(
            {
                "status": "success",
                "watch_group_key": "default_competitor_watch",
                "future_days": 15,
                "trusted_source": "calendar_dom_cell_price",
                "run": {"status": "success", "captured_at": "2026-07-08T12:00:00+08:00"},
                "dates": [
                    {
                        "checkin_date": "2026-07-09",
                        "own": {"hotel_alias": "本店", "price_amount_yuan": 411, "status": "success"},
                        "competitors": [
                            {"hotel_alias": "竞品A", "price_amount_yuan": 398, "status": "success"}
                        ],
                    }
                ],
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "calendar_price")
        self.assertEqual(operator["legacy_module"], "calendar_price_watch")
        self.assertEqual(operator["intent"], "latest_query")
        self.assertIn("日历价", operator["conclusion"])
        self.assertEqual(operator["safety_boundary"]["level"], "read_only")
        self.assertIn("pricing_execute", operator["safety_boundary"]["forbidden"])

    def test_price_change_increase_v1_is_alert_only(self) -> None:
        payload = watch_api.enrich_price_change_response(
            {
                "status": "alert",
                "alert_direction": "increase",
                "summary": "竞品价格上涨，仅提醒。",
                "changed_items": [
                    {"hotel_alias": "竞品A", "direction": "increase", "change_amount_yuan": 30}
                ],
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "pricing")
        self.assertEqual(operator["intent"], "analysis")
        self.assertIn("竞品涨价仅提醒", operator["risk"]["flags"])
        self.assertEqual(operator["safety_boundary"]["level"], "read_only")
        self.assertIn("own_hotel_price_increase_suggestion", operator["safety_boundary"]["forbidden"])

    def test_hotel_overview_v1_uses_analysis_module(self) -> None:
        payload = watch_api.enrich_hotel_overview_response(
            {
                "status": "success",
                "checked_at": "2026-07-08T12:00:00+08:00",
                "operator_summary": {
                    "overall_status": "fresh",
                    "overview_rows": [{"field": "EBK", "value": "出租率 82%"}],
                    "usage_note": "只读经营概览。",
                },
            }
        )

        operator = payload["operator_summary"]
        self.assertEqual(operator["contract_version"], CONTRACT_VERSION)
        self.assertEqual(operator["module"], "analysis")
        self.assertEqual(operator["intent"], "analysis")
        self.assertEqual(operator["reply_mode"], "card_first")
        self.assertEqual(operator["evidence"][0]["label"], "EBK")


class ManualPricingPayloadTests(unittest.TestCase):
    def test_builds_watch_compatible_command(self) -> None:
        payload = build_manual_pricing_feishu_payload(
            room_type="高级大床房",
            date_label="today",
            target_price=420,
            chat_id="oc_group",
            message_id="om_1",
            open_id="ou_operator",
        )
        self.assertEqual(payload["header"]["event_type"], "im.message.receive_v1")
        content = payload["event"]["message"]["content"]
        self.assertIn("把今天高级大床房调到420", content)

    def test_specified_checkin_date_synthesizes_iso_command(self) -> None:
        """specified 日期合成 ISO 指令文本，后端 manual_price_command 可解析。"""

        payload = build_manual_pricing_feishu_payload(
            room_type="奢华三卧套房",
            date_label="specified",
            target_price=1013,
            chat_id="oc_group",
            message_id="om_1",
            open_id="ou_operator",
            platform="ctrip",
            checkin_date="2026-09-20",
        )
        content = payload["event"]["message"]["content"]
        self.assertIn("把2026-09-20携程奢华三卧套房调到1013", content)


class EbkRunSourceTests(unittest.TestCase):
    def test_run_ebk_watch_once_marks_hermes_actor(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"status": "failed"}),
                ) as post_json,
                mock.patch.object(client, "get_json", new=mock.AsyncMock()) as get_json,
            ):
                await client.run_ebk_watch_once("ctrip_ebooking_data_center")

            post_json.assert_awaited_once_with(
                "/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center/run-now",
                params={"push_to_feishu": False, "actor_id": "hermes"},
                timeout=300.0,
            )
            get_json.assert_not_awaited()

        asyncio.run(scenario())


class PendingReviewCountTests(unittest.TestCase):

    def test_pending_review_count_passes_chat_id(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(return_value={"pending_count": 2}),
            ) as get_json:
                payload = await client.pending_review_count(
                    platform="ctrip", chat_id="oc_monitor_group"
                )
            get_json.assert_awaited_once_with(
                "/api/v1/reviews/pending-count",
                params={
                    "platform": "ctrip",
                    "chat_id": "oc_monitor_group",
                },
            )
            self.assertEqual(payload["pending_count"], 2)

        asyncio.run(scenario())

    def test_pending_review_count_without_chat_id_keeps_old_params(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                client,
                "get_json",
                new=mock.AsyncMock(return_value={"pending_count": 0}),
            ) as get_json:
                await client.pending_review_count(platform="meituan")
            get_json.assert_awaited_once_with(
                "/api/v1/reviews/pending-count",
                params={"platform": "meituan"},
            )

        asyncio.run(scenario())

    def test_pending_review_count_refresh_triggers_fetch_then_count(self) -> None:
        """飞书「刷新读取评价」：refresh=True 先 POST fetch（按群解析店铺、真实
        浏览器读取）再查数量；自动回复关闭也可手动触发（fetch 与模式无关）。"""

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={"fetch": {"status": "ok", "pending_count": 3}}
                    ),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"pending_count": 3}),
                ) as get_json,
            ):
                payload = await client.pending_review_count(
                    platform="ctrip",
                    chat_id="oc_monitor_group",
                    refresh=True,
                )
            post_json.assert_awaited_once_with(
                "/api/v1/reviews/fetch",
                body={"platform": "ctrip", "chat_id": "oc_monitor_group"},
                timeout=watch_api.BROWSER_RUN_TIMEOUT,
            )
            get_json.assert_awaited_once_with(
                "/api/v1/reviews/pending-count",
                params={"platform": "ctrip", "chat_id": "oc_monitor_group"},
            )
            self.assertEqual(payload["pending_count"], 3)

        asyncio.run(scenario())

    def test_pending_review_count_refresh_failure_returns_fetch_error(self) -> None:
        """fetch 现读失败契约：HTTP 200 + status≠ok 也照常返回数量（读的是最近
        一次抓取记录），并把 fetch 结果附在 payload 里供问答层说明数据时点。"""

        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={"fetch": {"status": "not_ready"}}
                    ),
                ) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"pending_count": 2}),
                ),
            ):
                payload = await client.pending_review_count(
                    platform="fliggy",
                    chat_id="oc_monitor_group",
                    refresh=True,
                )
            self.assertEqual(payload["pending_count"], 2)
            self.assertEqual(payload["refresh_fetch"]["status"], "not_ready")

        asyncio.run(scenario())


class BiDailyShopResolutionTests(unittest.TestCase):
    """BI 日报的 chat→shop 解析：resolution 无 shop_key 时用 EBK 浏览器会话归属兜底。"""

    @staticmethod
    def _context(with_shop_key: bool) -> dict:
        resolution = {"status": "resolved", "reason": "single_pair_auto"}
        if with_shop_key:
            resolution["shop_key"] = "shop_from_resolution"
        return {
            "binding_mode": "single_store",
            "resolution": resolution,
            "resolved_shop_key": resolution.get("shop_key"),
            "ebk_groups": [
                {"group_key": "ctrip_ebooking_data_center", "browser_session_id": "browser-15900000001"},
                {"group_key": "default_ebk_watch", "browser_session_id": "browser-JL1700000001"},
            ],
        }

    def _client_with(self, *, context: dict, sessions: dict | None) -> WatchApiClient:
        client = WatchApiClient(base_url="http://127.0.0.1:8000")

        async def fake_context(chat_id, *, store_hint=None, include_runtime_health=False):
            return context

        async def fake_get_json(path, params=None, timeout=None):
            if path == "/api/v1/browser-sessions":
                return {"sessions": sessions or []}
            assert path == "/api/v1/bi-daily/daily-report", path
            assert params is not None and params.get("shop_key"), params
            return {"shop_key": params["shop_key"], "forward": [], "performance": [],
                    "weekday_profile": [], "quality": {}}

        client.get_feishu_chat_context = fake_context  # type: ignore[method-assign]
        client.get_json = fake_get_json  # type: ignore[method-assign]
        return client

    def test_resolves_shop_key_via_ebk_browser_sessions(self) -> None:
        async def scenario() -> None:
            client = self._client_with(
                context=self._context(with_shop_key=False),
                sessions=[
                    {"browser_session_id": "browser-15900000001", "shop_key": "shop_jingli"},
                    {"browser_session_id": "browser-JL1700000001", "shop_key": "shop_jingli"},
                    {"browser_session_id": "browser-competitor", "shop_key": None},
                ],
            )
            payload = await client.get_bi_daily_report(chat_id="oc_group")
            self.assertEqual(payload["shop_key"], "shop_jingli")

        asyncio.run(scenario())

    def test_resolution_shop_key_takes_priority(self) -> None:
        async def scenario() -> None:
            client = self._client_with(
                context=self._context(with_shop_key=True),
                sessions=[{"browser_session_id": "browser-15900000001", "shop_key": "shop_other"}],
            )
            payload = await client.get_bi_daily_report(chat_id="oc_group")
            self.assertEqual(payload["shop_key"], "shop_from_resolution")

        asyncio.run(scenario())

    def test_conflicting_shop_keys_fail_closed(self) -> None:
        async def scenario() -> None:
            client = self._client_with(
                context=self._context(with_shop_key=False),
                sessions=[
                    {"browser_session_id": "browser-15900000001", "shop_key": "shop_jingli"},
                    {"browser_session_id": "browser-JL1700000001", "shop_key": "shop_lirui"},
                ],
            )
            payload = await client.get_bi_daily_report(chat_id="oc_group")
            self.assertEqual(payload["status"], "shop_binding_missing")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
