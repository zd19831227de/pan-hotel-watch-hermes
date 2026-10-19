from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.browser.ctrip_ebooking_read_flows import (
    _dimension_parse_status,
    read_dataset_flow,
)
from app.domain.browser.ctrip_ebooking_session_helpers import _select_dataset_time_dimension
from app.domain.browser.ctrip_ebooking_time_dimensions import (
    assemble_time_dimensioned_snapshot,
    dataset_time_dimension_specs,
    parse_ebk_time_dimension_intent,
    primary_time_dimension_key,
    prepare_time_dimension_snapshot,
    project_dataset_data_for_time_dimension,
    resolve_dataset_status,
    resolve_dual_dimension_status,
)


class EbookingTimeDimensionHelperTests(unittest.TestCase):
    def test_business_outline_has_realtime_and_daily(self) -> None:
        specs = dataset_time_dimension_specs("business_outline")
        self.assertEqual([s.key for s in specs], ["realtime", "daily"])
        self.assertEqual(primary_time_dimension_key("business_outline"), "daily")
        self.assertTrue(all(spec.embedded_section for spec in specs))

    def test_flow_data_uses_current_page_exact_controls_without_text_fallback(self) -> None:
        specs = dataset_time_dimension_specs("flow_data")
        self.assertEqual(
            specs[0].selectors[0],
            "[he-click='datacenter_report_flowdata_now']",
        )
        self.assertEqual(
            specs[1].selectors[0],
            "[he-click='datacenter_report_flowdata_yes']",
        )
        self.assertFalse(specs[0].allow_label_fallback)
        self.assertFalse(specs[1].allow_label_fallback)

    def test_assemble_fills_top_level_from_primary(self) -> None:
        assembled = assemble_time_dimensioned_snapshot(
            dataset_id="business_outline",
            dimensions={
                "realtime": {
                    "status": "success",
                    "structure_ok": True,
                    "metrics": {
                        "realtime_booking_orders": {
                            "key": "realtime_booking_orders",
                            "label": "实时预订订单",
                            "value": "10",
                        }
                    },
                    "tables": [],
                    "rankings": [],
                    "updated_at": "t1",
                },
                "daily": {
                    "status": "success",
                    "structure_ok": True,
                    "metrics": {
                        "occupancy_rate": {
                            "key": "occupancy_rate",
                            "label": "出租率",
                            "value": "30%",
                        }
                    },
                    "tables": [{"name": "daily"}],
                    "rankings": [],
                    "updated_at": "t2",
                },
            },
            page_filter_entries=[
                {"key": "realtime", "selected": True, "method": "text"},
                {"key": "daily", "selected": True, "method": "text"},
            ],
        )
        self.assertEqual(assembled["metrics"]["occupancy_rate"]["value"], "30%")
        self.assertEqual(assembled["tables"], [{"name": "daily"}])
        self.assertEqual(
            assembled["time_dimensions"]["realtime"]["metrics"]["realtime_booking_orders"]["value"],
            "10",
        )
        self.assertEqual(assembled["page_filter"]["time_dimensions"][0]["selected"], True)

    def test_assemble_falls_back_when_configured_primary_failed(self) -> None:
        assembled = assemble_time_dimensioned_snapshot(
            dataset_id="competition_profile",
            dimensions={
                "realtime": {
                    "status": "filter_partial",
                    "structure_ok": False,
                    "metrics": {},
                },
                "yesterday": {
                    "status": "success",
                    "structure_ok": True,
                    "metrics": {"booking_orders": {"value": "12"}},
                },
            },
            page_filter_entries=[],
        )

        self.assertEqual(assembled["primary_time_dimension"], "yesterday")
        self.assertEqual(assembled["metrics"]["booking_orders"]["value"], "12")

    def test_assemble_status_hint_stays_success_when_primary_read_complete(self) -> None:
        assembled = assemble_time_dimensioned_snapshot(
            dataset_id="loss_analysis",
            dimensions={
                "realtime": {
                    "label": "实时",
                    "status": "filter_partial",
                    "structure_ok": False,
                    "metrics": {},
                    "data_quality_notes": ["time_dimension_control_not_found"],
                },
                "yesterday": {
                    "label": "昨日",
                    "status": "success",
                    "structure_ok": True,
                    "metrics": {"lost_orders": {"value": "106个"}},
                },
            },
            page_filter_entries=[
                {"key": "realtime", "selected": False, "method": "not_found"},
                {"key": "yesterday", "selected": True, "method": "selector"},
            ],
        )

        self.assertEqual(assembled["status_hint"], "success")
        self.assertEqual(assembled["primary_time_dimension"], "yesterday")
        self.assertEqual(
            assembled["time_dimensions"]["realtime"]["status"], "filter_partial"
        )

    def test_business_embedded_snapshot_is_partitioned_by_dimension(self) -> None:
        parsed = {
            "structure_ok": True,
            "metrics": {
                "realtime_booking_orders": {"value": "4"},
                "realtime_in_house_room_nights": {"value": "43"},
                "departure_sales_amount": {"value": "19171.84"},
                "departure_room_nights": {"value": "51"},
                "transaction_rate": {"value": "87.93%"},
                "average_selling_price": {"value": "375.92"},
            },
            "metric_groups": {},
        }

        realtime = prepare_time_dimension_snapshot(
            dataset_id="business_outline",
            dimension_key="realtime",
            parsed=parsed,
            selected=True,
        )
        daily = prepare_time_dimension_snapshot(
            dataset_id="business_outline",
            dimension_key="daily",
            parsed=parsed,
            selected=True,
        )

        self.assertEqual(
            set(realtime["metrics"]),
            {"realtime_booking_orders", "realtime_in_house_room_nights"},
        )
        self.assertEqual(
            set(daily["metrics"]),
            {
                "departure_sales_amount",
                "departure_room_nights",
                "transaction_rate",
                "average_selling_price",
            },
        )

    def test_business_outline_partition_keeps_service_overview_metrics(self) -> None:
        """页面实读核验（2026-08-22）：服务概览四指标（PSI/点评分/5分钟回复率/
        收藏数，昨日口径）此前不在任何维度清单里，被双维度拆分清空，导致生产
        简报卡服务概览为空。必须保留在 daily（主）维度。"""
        parsed = {
            "structure_ok": True,
            "metrics": {
                "realtime_visitor_rank": {"value": "273"},
                "psi_service_score": {"value": "4.91"},
                "hotel_review_score": {"value": "4.79"},
                "five_minute_response_rate": {"value": "96.8%"},
                "hotel_favorites": {"value": "21466"},
            },
            "metric_groups": {},
        }

        realtime = prepare_time_dimension_snapshot(
            dataset_id="business_outline",
            dimension_key="realtime",
            parsed=parsed,
            selected=True,
        )
        daily = prepare_time_dimension_snapshot(
            dataset_id="business_outline",
            dimension_key="daily",
            parsed=parsed,
            selected=True,
        )

        self.assertEqual(set(realtime["metrics"]), {"realtime_visitor_rank"})
        self.assertEqual(
            set(daily["metrics"]),
            {
                "psi_service_score",
                "hotel_review_score",
                "five_minute_response_rate",
                "hotel_favorites",
            },
        )

    def test_unselected_dimension_does_not_inherit_visible_page_metrics(self) -> None:
        prepared = prepare_time_dimension_snapshot(
            dataset_id="loss_analysis",
            dimension_key="realtime",
            parsed={
                "structure_ok": True,
                "metrics": {"lost_orders": {"value": "1458"}},
                "tables": [{"name": "loss"}],
                "rankings": [{"rank": 1}],
                "metric_groups": {"core": [{"value": "1458"}]},
                "raw_snippets": ["流失订单量 1458"],
                "data_quality_notes": [],
            },
            selected=False,
        )

        self.assertFalse(prepared["structure_ok"])
        self.assertEqual(prepared["metrics"], {})
        self.assertEqual(prepared["tables"], [])
        self.assertEqual(prepared["rankings"], [])
        self.assertEqual(prepared["raw_snippets"], [])
        self.assertIn("time_dimension_control_not_found", prepared["data_quality_notes"])

    def test_resolve_status_partial_when_one_fails(self) -> None:
        self.assertEqual(resolve_dual_dimension_status(["success", "filter_partial"]), "partial")
        self.assertEqual(resolve_dual_dimension_status(["success", "success"]), "success")
        self.assertEqual(resolve_dual_dimension_status(["human_required"]), "human_required")

    def test_resolve_dataset_status_primary_success_ignores_secondary_filter_partial(self) -> None:
        # Live regression (2026-08-17): loss_analysis pages expose the 昨日
        # control but no 实时 control, so realtime is filter_partial while the
        # primary yesterday read is complete. The dataset must stay success.
        self.assertEqual(
            resolve_dataset_status(
                "loss_analysis",
                {
                    "realtime": {"status": "filter_partial"},
                    "yesterday": {"status": "success"},
                },
            ),
            "success",
        )

    def test_resolve_dataset_status_partial_when_primary_missing_control(self) -> None:
        # If no dimension read succeeded (all controls missing), the dataset
        # must still degrade so operators see the page needs attention.
        self.assertEqual(
            resolve_dataset_status(
                "loss_analysis",
                {
                    "realtime": {"status": "filter_partial"},
                    "yesterday": {"status": "filter_partial"},
                },
            ),
            "partial",
        )

    def test_resolve_dataset_status_partial_when_primary_filter_partial_only(self) -> None:
        # A missing primary control with a healthy secondary stays partial —
        # the primary business data was not captured.
        self.assertEqual(
            resolve_dataset_status(
                "competition_profile",
                {
                    "realtime": {"status": "filter_partial"},
                    "yesterday": {"status": "success"},
                },
            ),
            "partial",
        )

    def test_resolve_dataset_status_not_downgraded_by_data_quality_secondary(self) -> None:
        # Only filter_partial (control absent on page) is exempt when the
        # primary succeeded; a secondary that was selected but parsed partial
        # still surfaces as a data-quality signal.
        self.assertEqual(
            resolve_dataset_status(
                "loss_analysis",
                {
                    "realtime": {"status": "partial"},
                    "yesterday": {"status": "success"},
                },
            ),
            "partial",
        )

    def test_resolve_dataset_status_human_required_wins(self) -> None:
        self.assertEqual(
            resolve_dataset_status(
                "flow_data",
                {
                    "realtime": {"status": "human_required"},
                    "yesterday": {"status": "success"},
                },
            ),
            "human_required",
        )

    def test_parse_intent(self) -> None:
        self.assertEqual(parse_ebk_time_dimension_intent("经营报告实时出租率"), "realtime")
        self.assertEqual(parse_ebk_time_dimension_intent("昨天流量怎么样"), "yesterday")
        self.assertEqual(parse_ebk_time_dimension_intent("按日经营报告"), "daily")
        self.assertIsNone(parse_ebk_time_dimension_intent("经营报告怎么样"))

    def test_project_realtime_only(self) -> None:
        data = {
            "metrics": {"occupancy_rate": {"value": "30%"}},
            "time_dimensions": {
                "realtime": {
                    "label": "实时",
                    "status": "success",
                    "metrics": {
                        "realtime_booking_orders": {
                            "label": "实时预订订单",
                            "value": "10",
                        }
                    },
                    "tables": [],
                    "rankings": [],
                },
                "daily": {
                    "label": "按日",
                    "status": "success",
                    "metrics": {
                        "occupancy_rate": {"label": "出租率", "value": "30%"}
                    },
                    "tables": [],
                    "rankings": [],
                },
            },
        }
        projected = project_dataset_data_for_time_dimension(data, "realtime")
        self.assertIn("realtime_booking_orders", projected["metrics"])
        self.assertNotIn("occupancy_rate", projected["metrics"])
        self.assertEqual(projected["projected_time_dimension"], "realtime")


class EbookingTimeDimensionClickTests(unittest.TestCase):
    def test_select_realtime_clicks_first_matching_selector(self) -> None:
        page = MagicMock()
        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)
        locator.first = locator
        locator.is_visible = AsyncMock(return_value=True)
        locator.is_enabled = AsyncMock(return_value=True)
        locator.click = AsyncMock()
        page.locator = MagicMock(return_value=locator)
        page.wait_for_timeout = AsyncMock()

        async def run() -> dict:
            return await _select_dataset_time_dimension(
                page, dataset_id="business_outline", dimension_key="realtime"
            )

        result = asyncio.run(run())
        self.assertTrue(result["selected"])
        self.assertEqual(result["key"], "realtime")
        self.assertTrue(result["read_only_filter"])

    def test_flow_realtime_does_not_click_unrelated_realtime_text(self) -> None:
        page = MagicMock()
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        locator.first = locator
        page.locator = MagicMock(return_value=locator)
        page.evaluate = AsyncMock(return_value={"selected": True, "method": "label_js"})
        page.wait_for_timeout = AsyncMock()

        async def run() -> dict:
            return await _select_dataset_time_dimension(
                page, dataset_id="flow_data", dimension_key="realtime"
            )

        result = asyncio.run(run())
        self.assertFalse(result["selected"])
        self.assertEqual(result["method"], "not_found")
        page.evaluate.assert_not_awaited()

    def test_exact_control_is_polled_until_react_mounts(self) -> None:
        page = MagicMock()
        exact = MagicMock()
        exact.first = exact
        exact.count = AsyncMock(side_effect=[0, 1])
        exact.is_visible = AsyncMock(return_value=True)
        exact.is_enabled = AsyncMock(return_value=True)
        exact.click = AsyncMock()
        missing = MagicMock()
        missing.first = missing
        missing.count = AsyncMock(return_value=0)
        page.locator = MagicMock(
            side_effect=lambda selector: (
                exact
                if selector == "[he-click='datacenter_report_flowdata_now']"
                else missing
            )
        )
        page.wait_for_timeout = AsyncMock()

        async def run() -> dict:
            return await _select_dataset_time_dimension(
                page, dataset_id="flow_data", dimension_key="realtime"
            )

        result = asyncio.run(run())
        self.assertTrue(result["selected"])
        exact.click.assert_awaited_once()
        self.assertIn(250, [call.args[0] for call in page.wait_for_timeout.await_args_list])


class DimensionParseStatusTests(unittest.TestCase):
    def test_not_authenticated_is_human_required(self) -> None:
        self.assertEqual(
            _dimension_parse_status(
                page_state="login_required",
                parsed={"structure_ok": True},
                selected=True,
            ),
            "human_required",
        )

    def test_not_selected_is_filter_partial(self) -> None:
        self.assertEqual(
            _dimension_parse_status(
                page_state="authenticated",
                parsed={"structure_ok": True},
                selected=False,
            ),
            "filter_partial",
        )

    def test_structure_not_ok_is_page_structure_changed(self) -> None:
        self.assertEqual(
            _dimension_parse_status(
                page_state="authenticated",
                parsed={"structure_ok": False},
                selected=True,
            ),
            "page_structure_changed",
        )

    def test_status_hint_partial_or_needs_review_is_passed_through(self) -> None:
        self.assertEqual(
            _dimension_parse_status(
                page_state="authenticated",
                parsed={"structure_ok": True, "status_hint": "partial"},
                selected=True,
            ),
            "partial",
        )
        self.assertEqual(
            _dimension_parse_status(
                page_state="authenticated",
                parsed={"structure_ok": True, "status_hint": "needs_review"},
                selected=True,
            ),
            "needs_review",
        )

    def test_success_when_selected_and_structure_ok(self) -> None:
        self.assertEqual(
            _dimension_parse_status(
                page_state="authenticated",
                parsed={"structure_ok": True, "status_hint": None},
                selected=True,
            ),
            "success",
        )


class _FakeFlowService:
    async def _capture_screenshot(self, page: object, platform_account_id: str) -> str:
        return "screenshot.png"


class _FakeFlowPage:
    def __init__(self) -> None:
        self.url = (
            "https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true"
        )


class ReadDatasetFlowDualDimensionTests(unittest.TestCase):
    def test_loss_analysis_stays_success_when_realtime_control_missing(self) -> None:
        """Live regression (2026-08-17): the loss-analysis page has the 昨日
        control but no 实时 control. The dataset was downgraded to partial and
        every full watch run reported 9/10 with a failing 流失订单 push card."""
        service = _FakeFlowService()

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=_FakeFlowPage()),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._select_dataset_time_dimension",
                    new=AsyncMock(
                        side_effect=[
                            {
                                "attempted": True,
                                "key": "realtime",
                                "label": "实时",
                                "selected": False,
                                "method": "not_found",
                            },
                            {
                                "attempted": True,
                                "key": "yesterday",
                                "label": "昨日",
                                "selected": True,
                                "method": "selector",
                            },
                        ]
                    ),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(side_effect=["realtime body", "yesterday body"]),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="流失分析"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "authenticated",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    side_effect=[
                        {
                            "structure_ok": True,
                            "status_hint": None,
                            "metrics": {},
                            "tables": [],
                            "rankings": [],
                            "metric_groups": {},
                            "warnings": [],
                            "raw_snippets": [],
                            "data_quality_notes": [],
                            "updated_at": None,
                            "matched_expected_terms": ["流失订单量"],
                        },
                        {
                            "structure_ok": True,
                            "status_hint": None,
                            "metrics": {
                                "lost_orders": {"value": "106个"},
                                "lost_room_nights": {"value": "210个"},
                                "lost_order_amount": {"value": "94,975.32"},
                            },
                            "tables": [{"name": "lost_hotel_top40", "rows": []}],
                            "rankings": [],
                            "metric_groups": {},
                            "warnings": [],
                            "raw_snippets": [],
                            "data_quality_notes": [],
                            "updated_at": "t2",
                            "matched_expected_terms": ["流失订单量"],
                        },
                    ],
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="loss_analysis",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["primary_time_dimension"], "yesterday")
        self.assertEqual(result.data["metrics"]["lost_orders"]["value"], "106个")
        self.assertEqual(
            result.data["time_dimensions"]["realtime"]["status"], "filter_partial"
        )
        self.assertIn(
            "time_dimension_control_not_found",
            result.data["time_dimensions"]["realtime"]["data_quality_notes"],
        )
        self.assertEqual(result.data["time_dimensions"]["yesterday"]["status"], "success")

    def test_business_outline_captures_both_dimensions_with_daily_as_primary(self) -> None:
        service = _FakeFlowService()

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=_FakeFlowPage()),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._select_dataset_time_dimension",
                    new=AsyncMock(
                        side_effect=[
                            {"key": "realtime", "label": "实时", "selected": True},
                            {"key": "daily", "label": "按日", "selected": True},
                        ]
                    ),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(side_effect=["realtime body", "daily body"]),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="经营报告"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "authenticated",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    side_effect=[
                        {
                            "structure_ok": True,
                            "status_hint": None,
                            "metrics": {
                                "realtime_booking_orders": {"value": "1"},
                                "departure_sales_amount": {"value": "999"},
                            },
                            "tables": [],
                            "rankings": [],
                            "metric_groups": {},
                            "warnings": [],
                            "raw_snippets": [],
                            "data_quality_notes": [],
                            "updated_at": "t1",
                            "matched_expected_terms": ["实时"],
                        },
                        {
                            "structure_ok": True,
                            "status_hint": None,
                            "metrics": {
                                "realtime_booking_orders": {"value": "999"},
                                "departure_sales_amount": {"value": "2"},
                            },
                            "tables": [{"name": "daily"}],
                            "rankings": [],
                            "metric_groups": {},
                            "warnings": [],
                            "raw_snippets": [],
                            "data_quality_notes": [],
                            "updated_at": "t2",
                            "matched_expected_terms": ["按日"],
                        },
                    ],
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="business_outline",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["primary_time_dimension"], "daily")
        self.assertEqual(result.data["metrics"]["departure_sales_amount"]["value"], "2")
        self.assertEqual(
            result.data["time_dimensions"]["realtime"]["metrics"]["realtime_booking_orders"]["value"],
            "1",
        )
        self.assertNotIn(
            "departure_sales_amount",
            result.data["time_dimensions"]["realtime"]["metrics"],
        )
        self.assertEqual(len(result.data["page_filter"]["time_dimensions"]), 2)

    def test_human_required_on_first_dimension_breaks_loop(self) -> None:
        service = _FakeFlowService()

        async def run() -> object:
            with (
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows.goto_dataset_page_with_recovery",
                    new=AsyncMock(return_value=_FakeFlowPage()),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._settle_page",
                    new=AsyncMock(),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._dismiss_ebk_read_blocking_popups",
                    new=AsyncMock(return_value={"dismissed": False}),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._select_dataset_time_dimension",
                    new=AsyncMock(
                        side_effect=[{"key": "realtime", "label": "实时", "selected": True}]
                    ),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_visible_text",
                    new=AsyncMock(side_effect=["login page body"]),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._safe_title",
                    new=AsyncMock(return_value="登录"),
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._classify_page_state",
                    new=lambda url, body_text: "login_required",
                ),
                patch(
                    "app.domain.browser.ctrip_ebooking_read_flows._extract_dataset_snapshot",
                    side_effect=[
                        {
                            "structure_ok": False,
                            "status_hint": None,
                            "metrics": {},
                            "tables": [],
                            "rankings": [],
                            "metric_groups": {},
                            "warnings": [],
                            "raw_snippets": [],
                            "data_quality_notes": [],
                            "updated_at": None,
                            "matched_expected_terms": [],
                        }
                    ],
                ),
            ):
                return await read_dataset_flow(
                    service,
                    platform_account_id="ebk-1",
                    browser_session_id="browser-ebk-1",
                    dataset_id="business_outline",
                    filters={},
                    actor_id="tester",
                    push_to_feishu=False,
                )

        result = asyncio.run(run())

        self.assertEqual(result.status, "human_required")
        self.assertEqual(list(result.data["time_dimensions"].keys()), ["realtime"])


class AgentTimeDimensionProjectionTests(unittest.TestCase):
    def test_compact_respects_projected_metrics(self) -> None:
        from app.domain.ai_agent.tools import _compact_ebk_dataset_result_with_data

        item = {
            "dataset_id": "business_outline",
            "dataset_label": "收益概览",
            "status": "success",
            "data": project_dataset_data_for_time_dimension(
                {
                    "dataset_id": "business_outline",
                    "metrics": {"occupancy_rate": {"label": "出租率", "value": "30%"}},
                    "time_dimensions": {
                        "realtime": {
                            "label": "实时",
                            "status": "success",
                            "metrics": {
                                "realtime_booking_orders": {
                                    "key": "realtime_booking_orders",
                                    "label": "实时预订订单",
                                    "value": "10",
                                }
                            },
                        },
                        "daily": {
                            "label": "按日",
                            "status": "success",
                            "metrics": {
                                "occupancy_rate": {
                                    "key": "occupancy_rate",
                                    "label": "出租率",
                                    "value": "30%",
                                }
                            },
                        },
                    },
                },
                "realtime",
            ),
        }
        compact = _compact_ebk_dataset_result_with_data(item)
        self.assertIn("realtime_booking_orders", compact["metrics"])
        self.assertNotIn("occupancy_rate", compact["metrics"])

    def test_compact_missing_dimension_clears_metrics_and_surfaces_status(self) -> None:
        from app.domain.ai_agent.tools import _compact_ebk_dataset_result_with_data

        item = {
            "dataset_id": "flow_data",
            "dataset_label": "流量数据",
            "status": "success",
            "data": project_dataset_data_for_time_dimension(
                {
                    "dataset_id": "flow_data",
                    "metrics": {"visitor_count": {"label": "访客量", "value": "100"}},
                    "time_dimensions": {
                        "yesterday": {
                            "label": "昨日",
                            "status": "success",
                            "metrics": {"visitor_count": {"label": "访客量", "value": "100"}},
                        },
                    },
                },
                "realtime",
            ),
        }
        compact = _compact_ebk_dataset_result_with_data(item)
        self.assertEqual(compact["metrics"], {})
        self.assertEqual(compact["projection_status"], "missing_dimension")
        self.assertEqual(compact["projected_time_dimension"], "realtime")

    def test_compact_unset_includes_both_time_dimensions(self) -> None:
        from app.domain.ai_agent.tools import _compact_ebk_dataset_result_with_data

        item = {
            "dataset_id": "business_outline",
            "dataset_label": "收益概览",
            "status": "success",
            "data": {
                "dataset_id": "business_outline",
                "metrics": {"occupancy_rate": {"key": "occupancy_rate", "label": "出租率", "value": "30%"}},
                "time_dimensions": {
                    "realtime": {
                        "label": "实时",
                        "status": "success",
                        "metrics": {
                            "realtime_booking_orders": {
                                "key": "realtime_booking_orders",
                                "label": "实时预订订单",
                                "value": "10",
                            }
                        },
                    },
                    "daily": {
                        "label": "按日",
                        "status": "success",
                        "metrics": {
                            "occupancy_rate": {
                                "key": "occupancy_rate",
                                "label": "出租率",
                                "value": "30%",
                            }
                        },
                    },
                },
            },
        }
        compact = _compact_ebk_dataset_result_with_data(item)
        self.assertIn("occupancy_rate", compact["metrics"])
        self.assertIn("realtime", compact["time_dimensions"])
        self.assertIn("daily", compact["time_dimensions"])
        self.assertEqual(
            compact["time_dimensions"]["realtime"]["metrics"]["realtime_booking_orders"]["value"],
            "10",
        )
        self.assertNotIn("projected_time_dimension", compact)


class EbkDatasetReadToolArgsTimeDimensionTests(unittest.TestCase):
    def test_defaults_to_none(self) -> None:
        from app.domain.ai_agent.registry import EbkDatasetReadToolArgs

        args = EbkDatasetReadToolArgs()
        self.assertIsNone(args.time_dimension)

    def test_accepts_valid_values(self) -> None:
        from app.domain.ai_agent.registry import EbkDatasetReadToolArgs

        for value in ("realtime", "yesterday", "daily"):
            args = EbkDatasetReadToolArgs(time_dimension=value)
            self.assertEqual(args.time_dimension, value)

    def test_rejects_invalid_value(self) -> None:
        from pydantic import ValidationError

        from app.domain.ai_agent.registry import EbkDatasetReadToolArgs

        with self.assertRaises(ValidationError):
            EbkDatasetReadToolArgs(time_dimension="last_week")


class ReadEbkDatasetsToolTimeDimensionTests(unittest.TestCase):
    def test_read_ebk_datasets_tool_projects_by_requested_time_dimension(self) -> None:
        from typing import Any

        async def scenario() -> None:
            from app.domain.ai_agent import tools

            group = tools.DataCenterWatchGroupRuntime(
                group_key="ctrip_ebooking_data_center",
                name="EBK 数据中心盯盘",
                brief_title=None,
                platform_account_id="ctrip-ebk",
                browser_session_id="browser-ebk",
                feishu_webhook_url=None,
                feishu_chat_id=None,
                timezone="Asia/Shanghai",
                retention_days=30,
                query_interval_minutes=10,
                auto_poll_enabled=True,
                enabled=True,
                last_polled_at=None,
                datasets=[
                    tools.DataCenterWatchDatasetRuntime(
                        dataset_id="business_outline",
                        dataset_label="收益概览",
                        enabled=True,
                        push_enabled=True,
                        sort_order=0,
                    ),
                ],
            )

            async def fake_get_group(*, watch_group_key: str) -> Any:
                return group

            class FakeDataCenterWatchService:
                async def run_watch_group(
                    self,
                    *,
                    group: Any,
                    actor_id: str,
                    source: str,
                    push_to_feishu: bool = True,
                ) -> dict[str, Any]:
                    return {
                        "status": "success",
                        "summary": "EBK 数据中心读取成功",
                        "watch_group_key": group.group_key,
                        "dataset_results": [
                            {
                                "dataset_id": "business_outline",
                                "dataset_label": "收益概览",
                                "status": "success",
                                "data": {
                                    "dataset_id": "business_outline",
                                    "metrics": {
                                        "occupancy_rate": {
                                            "key": "occupancy_rate",
                                            "label": "出租率",
                                            "value": "30%",
                                        }
                                    },
                                    "time_dimensions": {
                                        "realtime": {
                                            "label": "实时",
                                            "status": "success",
                                            "metrics": {
                                                "realtime_booking_orders": {
                                                    "key": "realtime_booking_orders",
                                                    "label": "实时预订订单",
                                                    "value": "10",
                                                }
                                            },
                                        },
                                        "daily": {
                                            "label": "按日",
                                            "status": "success",
                                            "metrics": {
                                                "occupancy_rate": {
                                                    "key": "occupancy_rate",
                                                    "label": "出租率",
                                                    "value": "30%",
                                                }
                                            },
                                        },
                                    },
                                },
                            }
                        ],
                        "feishu_push": {"status": "skipped"},
                    }

            original_get_group = tools.get_data_center_watch_group_runtime
            original_service = tools.DataCenterWatchService
            tools.get_data_center_watch_group_runtime = fake_get_group
            tools.DataCenterWatchService = FakeDataCenterWatchService

            feishu_chat_context = importlib.import_module(
                "app.domain.ops.feishu_chat_context"
            )
            original_build_context = feishu_chat_context.build_feishu_chat_context

            async def fake_build_chat_context(chat_id: str):
                return {
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "ebk_watch_group_key": "ctrip_ebooking_data_center",
                    },
                    "ebk_groups": [{"group_key": "ctrip_ebooking_data_center"}],
                    "competitor_groups": [],
                }

            feishu_chat_context.build_feishu_chat_context = fake_build_chat_context
            try:
                result = await tools.read_ebk_datasets_tool(
                    tools.EbkDatasetReadToolArgs(
                        dataset_ids=["business_outline"],
                        time_dimension="realtime",
                    ),
                    tools.AgentToolCallContext(event_id="evt-1", chat_id="oc_test"),
                )
            finally:
                tools.get_data_center_watch_group_runtime = original_get_group
                tools.DataCenterWatchService = original_service
                feishu_chat_context.build_feishu_chat_context = original_build_context

            dataset_result = result.data["dataset_results"][0]
            self.assertIn("realtime_booking_orders", dataset_result["metrics"])
            self.assertNotIn("occupancy_rate", dataset_result["metrics"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
