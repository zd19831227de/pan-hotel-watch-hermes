from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import httpx

import client.watch_api as watch_api
from client.watch_api import WatchApiClient


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def _load_mcp_server():
    try:
        from pan_hotel_mcp import server as mcp_server

        return mcp_server
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest("mcp package required") from exc


class HistoricalRefundReservationClientTests(unittest.TestCase):
    def test_refund_historical_range_skips_browser_fetch(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(client, "post_json", new=mock.AsyncMock()) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(
                        return_value={
                            "date_range": {"from": "2026-09-01", "to": "2026-09-07"},
                            "total_count": 12,
                            "snapshots": [],
                            "by_room_type": {},
                        }
                    ),
                ) as get_json,
            ):
                payload = await client.get_refund_status(
                    platform="meituan",
                    refresh=True,  # 即使请求刷新，历史范围也跳过抓取
                    date_from="2026-09-01",
                    date_to="2026-09-07",
                )
            post_json.assert_not_awaited()
            get_json.assert_awaited_once()
            path, kwargs = get_json.await_args.args[0], get_json.await_args.kwargs
            self.assertEqual(path, "/api/v1/refunds")
            self.assertEqual(
                kwargs["params"],
                {
                    "platform": "meituan",
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-07",
                },
            )
            self.assertEqual(payload["total_count"], 12)

        asyncio.run(scenario())

    def test_reservation_historical_range_skips_browser_fetch(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(client, "post_json", new=mock.AsyncMock()) as post_json,
                mock.patch.object(
                    client,
                    "get_json",
                    new=mock.AsyncMock(
                        return_value={"total_count": 5, "snapshots": [], "by_room_type": {}}
                    ),
                ),
            ):
                payload = await client.get_today_reservations(
                    platform="ctrip",
                    refresh=True,
                    date_from="2026-09-01",
                    date_to="2026-09-01",
                )
            post_json.assert_not_awaited()
            self.assertEqual(payload["total_count"], 5)

        asyncio.run(scenario())

    def test_ebk_period_read_custom_range_triggers_live_fetch(self) -> None:
        """自定义历史区间 = 平台页自定义时间现读（2026-09-16 起不再读落库快照）。"""
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={
                            "result": {
                                "status": "ok",
                                "platform": "ctrip",
                                "date_range": {"from": "2026-09-01", "to": "2026-09-07"},
                                "caliber": "预订销售·自定义 2026-09-01~2026-09-07（较去年同期）",
                                "summary": "已读取携程自定义区间经营指标。",
                                "sections": {"预订": {"预订订单数": {"value": "369"}}},
                            }
                        }
                    ),
                ) as post_json,
                mock.patch.object(client, "get_json", new=mock.AsyncMock()) as get_json,
            ):
                payload = await client.get_ebk_period_read(
                    platform="ctrip",
                    data_kind="business",
                    date_from="2026-09-01",
                    date_to="2026-09-07",
                )
            get_json.assert_not_awaited()
            post_json.assert_awaited_once_with(
                "/api/v1/ebk-period-reads/fetch",
                body={
                    "platform": "ctrip",
                    "data_kind": "business",
                    "period": "week",
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-07",
                },
                timeout=watch_api.BROWSER_RUN_TIMEOUT,
            )
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["range_mode"], "custom")
            self.assertEqual(payload["date_range"], {"from": "2026-09-01", "to": "2026-09-07"})
            self.assertEqual(payload["results"][0]["sections"]["预订"]["预订订单数"]["value"], "369")

        asyncio.run(scenario())

    def test_ebk_period_read_custom_range_unsupported_platform_keeps_reason(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with (
                mock.patch.object(
                    client,
                    "post_json",
                    new=mock.AsyncMock(
                        return_value={
                            "result": {
                                "status": "not_available",
                                "platform": "fliggy",
                                "reason": "飞猪经营数据页仅提供 今日/昨日/近7天/近30天 与诊断报告周报口径，无自定义日期区间。",
                            }
                        }
                    ),
                ),
                mock.patch.object(client, "get_json", new=mock.AsyncMock()),
            ):
                payload = await client.get_ebk_period_read(
                    platform="fliggy",
                    data_kind="business",
                    date_from="2026-09-01",
                    date_to="2026-09-07",
                )
            self.assertEqual(payload["status"], "not_available")
            self.assertIn("飞猪", payload["results"][0]["reason"])

        asyncio.run(scenario())

    def test_range_preset_last_week_resolves_dates_and_falls_back_for_preset_only_platforms(self) -> None:
        """「上周」预设：携程经营走自定义区间现读；美团排名（无自定义控件）回落近7天预设现读。"""
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            with mock.patch.object(
                watch_api, "_beijing_today", new=mock.Mock(return_value=__import__("datetime").date(2026, 9, 16))
            ):
                payload = await client.get_ebk_period_read(
                    data_kind="business",
                    period="week",
                    range_preset="last_week",
                )
            self.assertEqual(
                payload["date_range"], {"from": "2026-09-07", "to": "2026-09-13"}
            )
            self.assertEqual(payload["range_mode"], "preset_last_week")

        asyncio.run(scenario())

    def test_range_preset_falls_back_to_platform_preset_without_dates(self) -> None:
        """美团排名无自定义控件：「上周」回落近7天预设现读（POST 不带日期）再读快照。"""
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            post_bodies: list[dict] = []

            async def fake_post_json(path, body=None, timeout=None):
                post_bodies.append(body)
                return {"result": {"status": "ok"}}

            async def fake_get_json(path, params=None):
                return {
                    "snapshot": {
                        "id": 21,
                        "platform": "meituan",
                        "caliber": "同行分析·近7天（滚动7天，四榜口径）",
                        "payload": {"sections": {"四榜": {"入住榜": "第4"}}},
                    }
                }

            with (
                mock.patch.object(client, "post_json", new=fake_post_json),
                mock.patch.object(client, "get_json", new=fake_get_json),
            ):
                payload = await client.get_ebk_period_read(
                    platform="meituan",
                    data_kind="ranking",
                    period="week",
                    range_preset="last_week",
                )
            self.assertEqual(len(post_bodies), 1)
            self.assertNotIn("date_from", post_bodies[0])
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["results"][0]["range_fallback"])
            self.assertEqual(
                payload["results"][0]["caliber"], "同行分析·近7天（滚动7天，四榜口径）"
            )

        asyncio.run(scenario())

    def test_range_preset_ctrip_ranking_uses_native_period(self) -> None:
        """携程排名上月预设：走页面原生「上月」单选钮（自定义日历仅最近约33天）。"""
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            post_bodies: list[dict] = []

            async def fake_post_json(path, body=None, timeout=None):
                post_bodies.append(body)
                return {"result": {"status": "ok"}}

            async def fake_get_json(path, params=None):
                return {
                    "snapshot": {
                        "platform": "ctrip",
                        "caliber": "竞争排名·上月（页面四榜口径）",
                        "payload": {"sections": {"rankings": {"销售排名_预订订单量排名": "4"}}},
                    }
                }

            with (
                mock.patch.object(client, "post_json", new=fake_post_json),
                mock.patch.object(client, "get_json", new=fake_get_json),
            ):
                payload = await client.get_ebk_period_read(
                    platform="ctrip",
                    data_kind="ranking",
                    period="month",
                    range_preset="last_month",
                )
            self.assertEqual(len(post_bodies), 1)
            self.assertNotIn("date_from", post_bodies[0])
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["results"][0]["range_fallback"])
            self.assertEqual(payload["results"][0]["caliber"], "竞争排名·上月（页面四榜口径）")

        asyncio.run(scenario())

    def test_range_preset_fliggy_business_uses_custom_range(self) -> None:
        """飞猪经营「上周」预设走自定义区间（2026-09-17 实测页面有 ant-picker-range），POST 带日期不回落。"""
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            post_bodies: list[dict] = []

            async def fake_post_json(path, body=None, timeout=None):
                post_bodies.append(body)
                return {
                    "result": {
                        "status": "ok",
                        "caliber": "经营数据·自定义 2026-09-07~2026-09-13",
                        "sections": {"在店数据": {"在店间夜": {"value": "27间夜"}}},
                    }
                }

            with (
                mock.patch.object(client, "post_json", new=fake_post_json),
                mock.patch.object(client, "get_json", new=mock.AsyncMock()),
                mock.patch.object(
                    watch_api,
                    "_beijing_today",
                    new=mock.Mock(return_value=__import__("datetime").date(2026, 9, 16)),
                ),
            ):
                payload = await client.get_ebk_period_read(
                    platform="fliggy",
                    data_kind="business",
                    period="week",
                    range_preset="last_week",
                )
            self.assertEqual(len(post_bodies), 1)
            self.assertEqual(post_bodies[0].get("date_from"), "2026-09-07")
            self.assertEqual(post_bodies[0].get("date_to"), "2026-09-13")
            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["results"][0].get("range_fallback"))

        asyncio.run(scenario())

    def test_range_preset_dates_math(self) -> None:
        import datetime as dt

        with mock.patch.object(
            watch_api, "_beijing_today", new=mock.Mock(return_value=dt.date(2026, 9, 16))
        ):
            self.assertEqual(
                watch_api._range_preset_dates("last_week"), ("2026-09-07", "2026-09-13")
            )
            self.assertEqual(
                watch_api._range_preset_dates("last_month"), ("2026-08-01", "2026-08-31")
            )
        # 月末/月初边界：10月1日问上月 → 9月整月；问上周 → 9月21~27。
        with mock.patch.object(
            watch_api, "_beijing_today", new=mock.Mock(return_value=dt.date(2026, 10, 1))
        ):
            self.assertEqual(
                watch_api._range_preset_dates("last_month"), ("2026-09-01", "2026-09-30")
            )
            self.assertEqual(
                watch_api._range_preset_dates("last_week"), ("2026-09-21", "2026-09-27")
            )


class McpHistoricalQuerySchemaTests(unittest.TestCase):
    """接入历史范围的工具均暴露 date_from/date_to 可选参数。

    竞品价格趋势/日历价不接入历史范围（保持每日实时读取推送口径）。
    """

    EXPECTED_TOOLS = {
        "get_refund_status",
        "get_today_reservations",
        "get_ebk_period_read",
    }

    def test_tools_expose_date_range_params(self) -> None:
        mcp_server = _load_mcp_server()
        tools = asyncio.run(mcp_server.list_tools())
        by_name = {t.name: t for t in tools}
        for name in self.EXPECTED_TOOLS:
            self.assertIn(name, by_name, name)
            props = by_name[name].inputSchema.get("properties", {})
            self.assertIn("date_from", props, name)
            self.assertIn("date_to", props, name)
        # 竞品/日历价工具不带 date 参数（additionalProperties=False，传了会被拒）。
        for name in ("get_competitor_price_trend", "get_calendar_price_latest"):
            props = by_name[name].inputSchema.get("properties", {})
            self.assertNotIn("date_from", props, name)
            self.assertNotIn("date_to", props, name)


if __name__ == "__main__":
    unittest.main()
