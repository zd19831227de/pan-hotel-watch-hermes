from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import httpx

import client.watch_api as watch_api
from client.watch_api import WatchApiClient

_SNAPSHOT = {
    "id": 11,
    "platform": "ctrip",
    "data_kind": "business",
    "period": "week",
    "status": "ok",
    "caliber": "预订销售·按周（自然周，较去年同期）",
    "payload": {"sections": {"预订": {"预订订单数": {"value": "369"}}}},
    "captured_at": "2026-09-07 15:00:00",
}


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/ebk-period-reads")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class EbkPeriodReadClientTests(unittest.TestCase):
    def test_fresh_snapshot_skips_fetch(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(client, "post_json", new=mock.AsyncMock()) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(return_value={"snapshot": _SNAPSHOT}),
                ) as get_json,
            ):
                payload = await client.get_ebk_period_read(
                    platform="ctrip", data_kind="business", period="week"
                )
            post_json.assert_not_awaited()
            get_json.assert_awaited_once_with(
                "/api/v1/ebk-period-reads",
                params={"platform": "ctrip", "data_kind": "business", "period": "week"},
            )
            self.assertEqual(payload["snapshot"]["caliber"], "预订销售·按周（自然周，较去年同期）")

        asyncio.run(scenario())

    def test_stale_snapshot_triggers_fetch_then_rereads(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            get_side_effect = [
                _http_status_error(404),  # 首读：无当日快照 → 过期
                {"snapshot": _SNAPSHOT},  # 重读
            ]
            get_calls: list[dict | None] = []

            async def fake_get_json(path, params=None):
                get_calls.append(params)
                result = get_side_effect.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "ok"}}),
                ) as post_json,
                mock.patch.object(client, "get_json", new=fake_get_json),
            ):
                payload = await client.get_ebk_period_read(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    chat_id="oc_test",
                )
            post_json.assert_awaited_once_with(
                "/api/v1/ebk-period-reads/fetch",
                body={
                    "platform": "ctrip",
                    "data_kind": "business",
                    "period": "week",
                    "chat_id": "oc_test",
                },
                timeout=watch_api.BROWSER_RUN_TIMEOUT,
            )
            self.assertEqual(len(get_calls), 2)
            self.assertEqual(payload["snapshot"]["payload"]["sections"]["预订"]["预订订单数"]["value"], "369")

        asyncio.run(scenario())

    def test_fetch_failure_surfaces_refresh_fetch_failed(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            get_side_effect = [
                _http_status_error(404),  # 过期
                _http_status_error(404),  # 重读仍无
            ]

            async def fake_get_json(path, params=None):
                result = get_side_effect.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(return_value={"result": {"status": "error", "reason": "boom"}}),
                ),
                mock.patch.object(client, "get_json", new=fake_get_json),
            ):
                payload = await client.get_ebk_period_read(
                    platform="ctrip", data_kind="ranking", period="month"
                )
            self.assertEqual(payload["status"], "not_found")
            self.assertEqual(payload["refresh_fetch_failed"], ["ctrip: error（boom）"])

        asyncio.run(scenario())

    def test_all_platforms_fetch_only_stale_ones(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            snapshots = {
                "ctrip": {"snapshot": {**_SNAPSHOT, "platform": "ctrip"}},
                "meituan": {"snapshot": {**_SNAPSHOT, "platform": "meituan"}},
                "fliggy": {"snapshot": {**_SNAPSHOT, "platform": "fliggy"}},
            }
            fetched: list[str] = []

            async def fake_get_json(path, params=None):
                platform = (params or {}).get("platform")
                if platform in ("meituan",) and platform not in fetched:
                    raise _http_status_error(404)  # meituan 首读过期
                if isinstance(platform, str):
                    return snapshots[platform]
                return {"snapshots": []}

            async def fake_post_json(path, body=None, timeout=None):
                fetched.append(body["platform"])
                return {"result": {"status": "ok"}}

            with (
                mock.patch.object(client, "post_json", new=fake_post_json),
                mock.patch.object(client, "get_json", new=fake_get_json),
            ):
                payload = await client.get_ebk_period_read(
                    data_kind="business", period="week"
                )
            # 只对过期的 meituan 触发 fetch，其余平台直接用当日快照。
            self.assertEqual(fetched, ["meituan"])
            self.assertEqual(
                sorted(s["platform"] for s in payload["snapshots"]),
                ["ctrip", "fliggy", "meituan"],
            )
            self.assertNotIn("refresh_fetch_failed", payload)

        asyncio.run(scenario())

    def test_invalid_kind_or_period_returns_invalid_request(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(client, "get_json", new=mock.AsyncMock()):
                payload = await client.get_ebk_period_read(
                    platform="ctrip", data_kind="sales", period="week"
                )
            self.assertEqual(payload["status"], "invalid_request")

            payload = await client.get_ebk_period_read(
                platform="ota", data_kind="business", period="week"
            )
            self.assertEqual(payload["status"], "invalid_request")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
