from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest import mock

from client.watch_api import WatchApiClient


def _ctrip_snapshot_row() -> dict[str, Any]:
    return {
        "id": 11,
        "platform": "ctrip",
        "platform_account_id": "ctrip_account_01",
        "browser_session_id": "bs_ctrip_01",
        "status": "success",
        "dataset_count": 1,
        "created_at": "2026-07-10T09:20:00",
        "payload_json": {
            "platform": "ctrip",
            "platform_account_id": "ctrip_account_01",
            "browser_session_id": "bs_ctrip_01",
            "read_at": "2026-07-10T09:20:00+08:00",
            "status": "success",
            "summary": "携程数据中心读取成功",
            "datasets": {
                "business_outline": {
                    "status": "success",
                    "data": {"metrics": {"occupancy_rate": {"value": "72%"}}},
                }
            },
            "evidence": {
                "parser_version": "ctrip_ebooking_monitor_v1",
                "cookie": "session=should-not-leak",
                "password": "secret-pass",
                "profile_path": r"C:\Users\Administrator\.runtime\browser-state\ctrip",
                "verification_code": "123456",
                "screenshot_path": r"C:\Users\Administrator\Desktop\output\ctrip.png",
            },
            "platform_data": {"monitor": {"occupancy_rate": "72%"}},
        },
    }


def _fliggy_snapshot_row() -> dict[str, Any]:
    return {
        "id": 12,
        "platform": "fliggy",
        "platform_account_id": "fliggy",
        "browser_session_id": "browser-fliggy",
        "status": "success",
        "payload_json": {
            "platform": "fliggy",
            "platform_account_id": "fliggy",
            "read_at": "2026-07-16T01:30:00+00:00",
            "status": "success",
            "datasets": {
                "home_v1": {
                    "status": "success",
                    "data": {
                        "hotel_name": "静里公寓酒店",
                        "today_data": {},
                        "yesterday_data": {"销售额": "962.5元", "预订订单量": "3"},
                    },
                },
                "diagnose_report": {
                    "status": "success",
                    "data": {
                        "guest_review_score": "4.8分",
                        "review_extras": {"新增差评数": "暂无"},
                        "mci_index": "3.25分",
                        "trade_overview": {"今日在店": "2间夜"},
                    },
                },
                "operating_data": {
                    "status": "success",
                    "data": {
                        "in_house_data": {"在店间夜": "2间夜"},
                        "booking_data": {"预订订单": "3单"},
                        "flow_data": {"列表页曝光人数": "52", "预订转化率": "7.8%"},
                    },
                },
                "promotion_agent": {
                    "status": "success",
                    "data": {
                        "joined_activities": [
                            {
                                "name": "分场景运营活动",
                                "activity_type": "分场景运营活动",
                                "room_product": "39个",
                                "valid_period": "2026-05-30至2027-05-29",
                                "strength": "立减5%",
                                "status": "进行中",
                            }
                        ]
                    },
                },
            },
        },
    }


def _meituan_snapshot_row() -> dict[str, Any]:
    return {
        "id": 22,
        "platform": "meituan",
        "platform_account_id": "meituan_account_01",
        "browser_session_id": "bs_meituan_01",
        "status": "success",
        "dataset_count": 7,
        "created_at": "2026-07-10T09:35:00",
        "payload_json": {
            "platform": "meituan",
            "platform_account_id": "meituan_account_01",
            "browser_session_id": "bs_meituan_01",
            "read_at": "2026-07-10T09:35:00+08:00",
            "status": "success",
            "summary": "美团 EBK 已读出",
            "datasets": {
                "workbench_home": {
                    "status": "success",
                    "data": {
                        "realtime_data": {"支付订单数": {"value": "3"}},
                        "yesterday_data": {"支付订单数": {"value": "7"}},
                        "hos_score": {"value": "4.56"},
                    },
                },
                "data_center_home": {
                    "status": "success",
                    "data": {
                        "basic_operation_metrics": {
                            "销售额": {"value": "2780.00 元"},
                        }
                    },
                },
                "data_center_trade": {
                    "status": "success",
                    "data": {
                        "tab_label": "交易分析",
                        "business_performance": {
                            "销售额": {"value": "2780.00 元"},
                            "支付订单数": {"value": "7单"},
                        },
                        "metrics": {
                            "满房率": {"value": "56.92%"},
                        },
                    },
                },
                "data_center_traffic": {
                    "status": "success",
                    "data": {
                        "tab_label": "流量分析",
                        "traffic_trend": {
                            "曝光量": {"value": "1542"},
                            "浏览人数": {"value": "270"},
                        },
                        "loss_analysis": {
                            "流失订单量": {"value": "3"},
                            "流失间夜量": {"value": "5"},
                            "流失订单金额": {"value": "228.15 元"},
                        },
                    },
                },
                "data_center_service": {
                    "status": "success",
                    "data": {
                        "tab_label": "服务分析",
                        "service_metrics": {
                            "服务分": {"value": "4.9"},
                            "评价分": {"value": "4.8"},
                            "信息分": {"value": "4.8"},
                        },
                    },
                },
                "promotion_coupon": {
                    "status": "success",
                    "data": {
                        "coupon_activities": [
                            {
                                "activity_name": "美团神券",
                                "status": "报名成功",
                                "activity_metrics": {"曝光量": {"value": "120"}},
                            }
                        ]
                    },
                },
                "comment_manage_home": {
                    "status": "success",
                    "data": {
                        "review_score": "4.7",
                        "review_total": "128",
                        "review_status": {
                            "好评率": "96%",
                            "待回复评价": "2",
                        },
                        "review_dimensions": {
                            "服务": "4.8",
                        },
                        "review_periods": {
                            "30天": {
                                "新增有效评价数": "33条",
                                "差评率": "6.1%",
                            },
                        },
                        "pending_reply_count": "2",
                    },
                },
            },
            "evidence": {
                "parser": "meituan_data_center_dom_v1",
                "cookie": "mt=should-not-leak",
            },
            "platform_data": {},
        },
    }


class EbkSnapshotHermesClientTests(unittest.TestCase):
    def test_get_latest_ebk_snapshot_calls_backend_latest_api(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                captured["path"] = path
                captured["params"] = params
                return _ctrip_snapshot_row()

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_latest_ebk_snapshot(
                    platform="ctrip",
                    platform_account_id="ctrip_account_01",
                )

            self.assertEqual(captured["path"], "/api/v1/ebk-snapshots/latest")
            self.assertEqual(
                captured["params"],
                {"platform": "ctrip", "platform_account_id": "ctrip_account_01"},
            )
            self.assertEqual(payload["platform"], "ctrip")
            self.assertEqual(payload["status"], "success")
            self.assertNotIn("session=should-not-leak", json.dumps(payload, ensure_ascii=False))

        asyncio.run(scenario())

    def test_get_latest_ebk_snapshot_allows_fliggy(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                captured["path"] = path
                captured["params"] = params
                return _fliggy_snapshot_row()

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_latest_ebk_snapshot(
                    platform="fliggy",
                    platform_account_id="fliggy",
                )

            self.assertEqual(captured["path"], "/api/v1/ebk-snapshots/latest")
            self.assertEqual(
                captured["params"],
                {"platform": "fliggy", "platform_account_id": "fliggy"},
            )
            self.assertEqual(payload["platform"], "fliggy")
            self.assertEqual(payload["status"], "success")

        asyncio.run(scenario())

    def test_resolve_platforms_all_includes_fliggy(self) -> None:
        from client.watch_api import _resolve_ebk_analysis_platforms

        requested = _resolve_ebk_analysis_platforms(
            platforms=["all"],
            ctrip_platform_account_id="ctrip_account_01",
            meituan_platform_account_id="meituan_account_01",
            fliggy_platform_account_id="fliggy",
        )

        self.assertEqual(requested["platforms"], ["ctrip", "meituan", "fliggy"])
        self.assertEqual(requested["analysis_mode"], "joint")
        self.assertEqual(requested["fliggy_platform_account_id"], "fliggy")

    def test_get_ebk_joint_analysis_context_groups_ctrip_and_meituan(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                self.assertEqual(path, "/api/v1/ebk-snapshots/latest")
                assert params is not None
                if params["platform"] == "ctrip":
                    return _ctrip_snapshot_row()
                if params["platform"] == "meituan":
                    return _meituan_snapshot_row()
                raise AssertionError(params)

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_ebk_joint_analysis_context(
                    ctrip_platform_account_id="ctrip_account_01",
                    meituan_platform_account_id="meituan_account_01",
                )

            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["analysis_mode"], "joint")
            self.assertEqual(payload["source"], "hermes_ebk_joint_analysis")
            operator = payload["operator_summary"]
            self.assertIn("ctrip", operator["platforms"])
            self.assertIn("meituan", operator["platforms"])
            self.assertIn("fliggy", operator["platforms"])
            self.assertEqual(operator["platforms"]["ctrip"]["status"], "available")
            self.assertEqual(operator["platforms"]["meituan"]["status"], "available")
            self.assertEqual(operator["platforms"]["fliggy"]["status"], "missing")
            self.assertIn("72%", str(operator["ctrip_ebk"]))
            self.assertEqual(operator["meituan_workbench"]["hos_score"]["value"], "4.56")
            self.assertEqual(
                operator["meituan_promotion_coupon"]["activities"][0]["activity_name"],
                "美团神券",
            )
            self.assertEqual(operator["meituan_reviews"]["review_score"], "4.7")
            self.assertEqual(operator["meituan_reviews"]["review_status"]["好评率"], "96%")
            self.assertEqual(operator["meituan_reviews"]["review_periods"]["30天"]["新增有效评价数"], "33条")
            self.assertIn("meituan_data_center", operator)
            self.assertEqual(operator["meituan_data_center"]["tabs"]["data_center_trade"]["label"], "交易分析")
            self.assertEqual(
                operator["meituan_data_center"]["tabs"]["data_center_trade"]["metrics"]["销售额"]["value"],
                "2780.00 元",
            )
            self.assertEqual(
                operator["meituan_data_center"]["tabs"]["data_center_traffic"]["metrics"]["曝光量"]["value"],
                "1542",
            )
            self.assertEqual(
                operator["meituan_data_center"]["tabs"]["data_center_service"]["metrics"]["服务分"]["value"],
                "4.9",
            )
            self.assertIn("ctrip", operator["read_times"])
            self.assertIn("meituan", operator["read_times"])
            self.assertTrue(any("飞猪" in item for item in operator["missing_data"]))
            self.assertNotIn("fliggy_home", operator)
            self.assertIn("只读分析", operator["usage_note"])
            self.assertNotIn("调价执行", operator["usage_note"])
            serialized = json.dumps(payload, ensure_ascii=False)
            for forbidden in (
                "session=should-not-leak",
                "secret-pass",
                "browser-state",
                "123456",
                "ctrip.png",
                "mt=should-not-leak",
                "write_to_ctrip",
                "auto_execute",
                "confirm_price_change",
                "立即调价",
            ):
                self.assertNotIn(forbidden, serialized)

        asyncio.run(scenario())

    def test_joint_analysis_can_refresh_snapshots_before_analysis(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            calls: list[dict[str, Any]] = []

            async def fake_post_json(path: str, *, body: dict | None = None, params=None, timeout=None):
                calls.append({"method": "POST", "path": path, "body": body})
                self.assertEqual(path, "/api/v1/ebk-snapshots/read-all")
                return {
                    "status": "success",
                    "results": {
                        "ctrip": {
                            "status": "success",
                            "snapshot_id": 11,
                            "payload": _ctrip_snapshot_row()["payload_json"],
                        },
                        "meituan": {
                            "status": "success",
                            "snapshot_id": 22,
                            "payload": _meituan_snapshot_row()["payload_json"],
                        },
                    },
                }

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                calls.append({"method": "GET", "path": path, "params": params})
                raise AssertionError("refresh mode should not need latest snapshot GET when read-all returned payloads")

            with (
                mock.patch.object(client, "post_json", side_effect=fake_post_json),
                mock.patch.object(client, "get_json", side_effect=fake_get_json),
            ):
                payload = await client.get_ebk_joint_analysis_context(
                    ctrip_platform_account_id="ctrip_account_01",
                    meituan_platform_account_id="meituan_account_01",
                    refresh_before_analysis=True,
                )

            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["analysis_mode"], "joint")
            self.assertEqual(calls[0]["method"], "POST")
            self.assertEqual(
                calls[0]["body"],
                {
                    "actor_id": "hermes",
                    "ctrip_platform_account_id": "ctrip_account_01",
                    "meituan_platform_account_id": "meituan_account_01",
                    "fliggy_platform_account_id": None,
                    "platforms": ["ctrip", "meituan"],
                    "headless": False,
                    "trigger_via": "bot_analysis",
                },
            )
            operator = payload["operator_summary"]
            self.assertEqual(operator["platforms"]["ctrip"]["status"], "available")
            self.assertEqual(operator["platforms"]["meituan"]["status"], "available")
            self.assertEqual(operator["platforms"]["fliggy"]["status"], "missing")
            self.assertEqual(operator["data_source"], "immediate_read")

        asyncio.run(scenario())

    def test_joint_analysis_refresh_passes_fliggy_account(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            captured: dict[str, Any] = {}

            async def fake_post_json(path: str, *, body: dict | None = None, params=None, timeout=None):
                captured["path"] = path
                captured["body"] = body
                return {
                    "status": "success",
                    "results": {
                        "ctrip": {
                            "status": "success",
                            "snapshot_id": 11,
                            "payload": _ctrip_snapshot_row()["payload_json"],
                        },
                        "meituan": {
                            "status": "success",
                            "snapshot_id": 22,
                            "payload": _meituan_snapshot_row()["payload_json"],
                        },
                        "fliggy": {
                            "status": "success",
                            "snapshot_id": 12,
                            "payload": _fliggy_snapshot_row()["payload_json"],
                        },
                    },
                }

            with mock.patch.object(client, "post_json", side_effect=fake_post_json):
                payload = await client.get_ebk_joint_analysis_context(
                    ctrip_platform_account_id="ctrip_account_01",
                    meituan_platform_account_id="meituan_account_01",
                    fliggy_platform_account_id="fliggy",
                    platforms=["all"],
                    refresh_before_analysis=True,
                )

            self.assertEqual(captured["path"], "/api/v1/ebk-snapshots/read-all")
            self.assertEqual(captured["body"]["platforms"], ["ctrip", "meituan", "fliggy"])
            self.assertEqual(captured["body"]["fliggy_platform_account_id"], "fliggy")
            self.assertEqual(
                payload["operator_summary"]["platforms"]["fliggy"]["status"],
                "available",
            )
            self.assertIn("fliggy_home", payload["operator_summary"])

        asyncio.run(scenario())

    def test_refresh_marks_requested_fliggy_account_missing(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_post_json(path: str, *, body: dict | None = None, params=None, timeout=None):
                return {"status": "partial", "results": {}}

            with mock.patch.object(client, "post_json", side_effect=fake_post_json):
                payload = await client.get_ebk_joint_analysis_context(
                    ctrip_platform_account_id="ctrip_account_01",
                    meituan_platform_account_id="meituan_account_01",
                    platforms=["all"],
                    refresh_before_analysis=True,
                )

            self.assertEqual(
                payload["operator_summary"]["platforms"]["fliggy"]["reason"],
                "missing_platform_account_id",
            )

        asyncio.run(scenario())

    def test_get_ebk_joint_analysis_context_supports_single_platform(self) -> None:
        async def scenario() -> None:
            client = WatchApiClient(base_url="http://127.0.0.1:8000")
            calls: list[dict[str, Any]] = []

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                calls.append({"path": path, "params": params})
                return _meituan_snapshot_row()

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_ebk_joint_analysis_context(
                    meituan_platform_account_id="meituan_account_01",
                    platforms=["meituan"],
                )

            self.assertEqual(payload["analysis_mode"], "meituan")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["params"]["platform"], "meituan")
            operator = payload["operator_summary"]
            self.assertEqual(set(operator["platforms"]), {"meituan"})
            self.assertEqual(operator["platforms"]["meituan"]["status"], "available")
            self.assertIn("meituan_workbench", operator)
            self.assertNotIn("ctrip_ebk", operator)

        asyncio.run(scenario())

    def test_joint_analysis_marks_missing_platform_without_failing(self) -> None:
        async def scenario() -> None:
            import httpx

            client = WatchApiClient(base_url="http://127.0.0.1:8000")

            async def fake_get_json(path: str, *, params: dict | None = None, timeout=None):
                assert params is not None
                if params["platform"] == "ctrip":
                    return _ctrip_snapshot_row()
                request = httpx.Request("GET", "http://127.0.0.1:8000/api/v1/ebk-snapshots/latest")
                response = httpx.Response(404, request=request, json={"detail": "No EBK snapshot found."})
                raise httpx.HTTPStatusError("missing", request=request, response=response)

            with mock.patch.object(client, "get_json", side_effect=fake_get_json):
                payload = await client.get_ebk_joint_analysis_context(
                    ctrip_platform_account_id="ctrip_account_01",
                    meituan_platform_account_id="meituan_account_01",
                )

            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["analysis_mode"], "joint")
            operator = payload["operator_summary"]
            self.assertEqual(operator["platforms"]["ctrip"]["status"], "available")
            self.assertEqual(operator["platforms"]["meituan"]["status"], "missing")
            self.assertEqual(operator["platforms"]["fliggy"]["status"], "missing")
            self.assertTrue(any("美团" in item for item in operator["missing_data"]))
            self.assertTrue(any("飞猪" in item for item in operator["missing_data"]))

        asyncio.run(scenario())


class EbkJointAnalysisContextTests(unittest.TestCase):
    def test_sparse_joint_still_has_finding_action_and_caliber_note(self) -> None:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        ctrip = {
            "status": "success",
            "created_at": "2026-07-22T09:00:00+08:00",
            "payload_json": {
                "status": "success",
                "read_at": "2026-07-22T09:00:00+08:00",
                "datasets": {},
                "platform_data": {"monitor": {}},
            },
        }
        meituan = {
            "status": "success",
            "created_at": "2026-07-22T09:01:00+08:00",
            "payload_json": {
                "status": "success",
                "read_at": "2026-07-22T09:01:00+08:00",
                "datasets": {
                    key: {"status": "success", "data": {}}
                    for key in (
                        "workbench_home",
                        "promotion_coupon",
                        "comment_manage_home",
                        "data_center_home",
                        "data_center_trade",
                        "data_center_traffic",
                        "data_center_service",
                    )
                },
            },
        }
        fliggy = {
            "status": "success",
            "created_at": "2026-07-22T09:02:00+08:00",
            "payload_json": {
                "status": "success",
                "read_at": "2026-07-22T09:02:00+08:00",
                "datasets": {
                    key: {"status": "success", "data": {}}
                    for key in ("home_v1", "diagnose_report", "operating_data", "promotion_agent")
                },
            },
        }

        payload = build_ebk_joint_analysis_context(
            ctrip_snapshot=ctrip,
            meituan_snapshot=meituan,
            fliggy_snapshot=fliggy,
            analysis_mode="joint",
        )

        operator = payload["operator_summary"]
        self.assertTrue(str(operator.get("conclusion") or "").strip())
        self.assertTrue(operator.get("findings"))
        self.assertTrue(operator.get("actions"))
        self.assertIn("字段定义可能不同", str(operator.get("comparison_caliber_note") or ""))

    def test_build_joint_context_includes_fliggy_sections(self) -> None:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        payload = build_ebk_joint_analysis_context(
            ctrip_snapshot=_ctrip_snapshot_row(),
            meituan_snapshot=_meituan_snapshot_row(),
            fliggy_snapshot=_fliggy_snapshot_row(),
            analysis_mode="joint",
        )
        operator = payload["operator_summary"]
        self.assertEqual(operator["platforms"]["fliggy"]["status"], "available")
        self.assertEqual(operator["fliggy_home"]["yesterday_data"]["销售额"], "962.5元")
        self.assertEqual(operator["fliggy_diagnose"]["guest_review_score"], "4.8分")
        self.assertEqual(operator["fliggy_operating"]["flow_data"]["列表页曝光人数"], "52")
        self.assertEqual(operator["fliggy_promotion"]["joined_activities"][0]["name"], "分场景运营活动")
        self.assertTrue(str(operator.get("conclusion") or "").strip())
        self.assertIsInstance(operator.get("findings"), list)
        self.assertGreaterEqual(len(operator["findings"]), 1)
        findings_text = str(operator["findings"])
        self.assertIn("页面昨日金额字段", findings_text)
        self.assertNotIn("销售额最高", findings_text)
        self.assertNotIn("可用于联合分析汇报", str(payload.get("summary") or ""))

    def test_build_fliggy_only_mode(self) -> None:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        payload = build_ebk_joint_analysis_context(
            fliggy_snapshot=_fliggy_snapshot_row(),
            analysis_mode="fliggy",
        )
        self.assertEqual(payload["analysis_mode"], "fliggy")
        self.assertEqual(set(payload["operator_summary"]["platforms"]), {"fliggy"})

    def test_joint_marks_missing_fliggy_without_inventing(self) -> None:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        payload = build_ebk_joint_analysis_context(
            meituan_snapshot=_meituan_snapshot_row(),
            fliggy_snapshot=None,
            fliggy_error="no_snapshot",
            analysis_mode="joint",
        )
        self.assertEqual(payload["operator_summary"]["platforms"]["fliggy"]["status"], "missing")
        self.assertTrue(any("飞猪" in item for item in payload["operator_summary"]["missing_data"]))
        self.assertNotIn("fliggy_home", payload["operator_summary"])

    def test_bare_joint_without_fliggy_kwargs_marks_fliggy_missing(self) -> None:
        from client.ebk_snapshot_analysis import build_ebk_joint_analysis_context

        payload = build_ebk_joint_analysis_context(
            ctrip_snapshot=_ctrip_snapshot_row(),
            meituan_snapshot=_meituan_snapshot_row(),
            analysis_mode="joint",
        )
        operator = payload["operator_summary"]
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(operator["platforms"]["fliggy"]["status"], "missing")
        self.assertEqual(operator["platforms"]["fliggy"]["reason"], "no_snapshot")
        self.assertTrue(any("飞猪 EBK 最新 snapshot 缺失" in item for item in operator["missing_data"]))
        self.assertNotIn("fliggy_home", operator)
        self.assertNotIn("fliggy_diagnose", operator)
        self.assertNotIn("fliggy_operating", operator)
        self.assertNotIn("fliggy_promotion", operator)


class EbkSnapshotHermesToolContractTests(unittest.TestCase):
    def test_mcp_exposes_snapshot_analysis_tools(self) -> None:
        try:
            from pan_hotel_mcp import server as mcp_server
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest("mcp package required") from exc

        tools = asyncio.run(mcp_server.list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("get_latest_ebk_snapshot", names)
        self.assertIn("get_ebk_joint_analysis_context", names)
        joint = next(tool for tool in tools if tool.name == "get_ebk_joint_analysis_context")
        props = joint.inputSchema.get("properties", {})
        self.assertIn("ctrip_platform_account_id", props)
        self.assertIn("meituan_platform_account_id", props)
        self.assertIn("platforms", props)
        self.assertIn("refresh_before_analysis", props)
        self.assertIn("open_id", props)
        self.assertIn("chat_id", props)
        description = str(joint.description or "")
        self.assertIn("只读", description)
        self.assertIn("可读取已保存 snapshot，也可立即触发 EBK 读取", props["refresh_before_analysis"]["description"])
        self.assertNotIn("调价执行", description)


if __name__ == "__main__":
    unittest.main()
