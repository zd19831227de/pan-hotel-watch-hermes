from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from app.domain.operator_payload import ARTIFACT_PLACEHOLDER
from app.domain.ctrip_ebooking_watch.repository import (
    DAILY_BRIEF_REQUIRED_DATASET_IDS,
    DataCenterWatchDatasetRuntime,
    DataCenterWatchGroupRuntime,
)
from app.domain.ctrip_ebooking_watch.service import (
    DataCenterWatchService,
    _failure_reason,
    _push_to_feishu,
)
from app.domain.ctrip_ebooking_watch.scheduler import CtripEbookingDataCenterWatchScheduler


class DataCenterWatchServiceTests(unittest.TestCase):
    def test_scheduler_runs_due_daily_brief_and_marks_group_pushed(self) -> None:
        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="澜江路 EBK 数据中心盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url="https://open.feishu.cn/webhook",
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            daily_brief_title="EBK 数据简报",
            daily_brief_push_time="08:30",
            daily_brief_enabled=True,
            datasets=[
                DataCenterWatchDatasetRuntime(
                    dataset_id="business_outline",
                    dataset_label="收益概览",
                    enabled=True,
                    push_enabled=True,
                    sort_order=0,
                )
            ],
        )
        marked: list[str] = []

        async def fake_list_due_data_center_watch_groups() -> list[Any]:
            return []

        async def fake_list_due_daily_brief_groups() -> list[Any]:
            return [group]

        async def fake_mark_daily_brief_group_pushed(*, watch_group_key: str) -> None:
            marked.append(watch_group_key)

        class FakeService:
            async def run_daily_brief(self, **kwargs: Any) -> dict[str, Any]:
                self.kwargs = kwargs
                return {
                    "watch_group_key": kwargs["group"].group_key,
                    "status": "success",
                    "summary": "EBK 数据简报已推送。",
                    "run_kind": "daily_brief",
                    "persistence": {"run_id": 7},
                }

        fake_service = FakeService()

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.scheduler.list_due_data_center_watch_groups",
                fake_list_due_data_center_watch_groups,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.scheduler.list_due_daily_brief_groups",
                fake_list_due_daily_brief_groups,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.scheduler.mark_daily_brief_group_pushed",
                fake_mark_daily_brief_group_pushed,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.scheduler.get_data_center_watch_service",
                lambda: fake_service,
            ),
        ):
            result = asyncio.run(CtripEbookingDataCenterWatchScheduler(tick_seconds=1).tick())

        self.assertEqual(result["due_group_count"], 0)
        self.assertEqual(result["due_daily_brief_count"], 1)
        self.assertEqual(result["daily_brief_results"][0]["run_kind"], "daily_brief")
        self.assertEqual(marked, ["ctrip_ebooking_data_center"])

    def test_finalize_run_persists_datetime_captured_at(self) -> None:
        captured_save_kwargs: dict[str, Any] = {}
        captured_push_kwargs: dict[str, Any] = {}

        async def fake_save_data_center_watch_run(**kwargs: Any) -> dict[str, Any]:
            captured_save_kwargs.update(kwargs)
            if not isinstance(kwargs.get("captured_at"), datetime):
                raise TypeError("captured_at must be datetime")
            return {"status": "success", "run_id": 42}

        async def fake_safe_push_to_feishu(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured_push_kwargs.update(kwargs)
            return {"status": "config_required", "watch_group_key": "ctrip_ebooking_data_center"}

        async def fake_update_data_center_watch_run_push_result(**kwargs: Any) -> None:
            return None

        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="澜江路 EBK 数据中心盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url=None,
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[
                DataCenterWatchDatasetRuntime(
                    dataset_id="business_outline",
                    dataset_label="收益概览",
                    enabled=True,
                    push_enabled=True,
                    sort_order=0,
                )
            ],
        )
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "summary": "读取成功",
                "data": {"dataset": "business_outline"},
                "evidence": {},
                "push_enabled": True,
            }
        ]

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.save_data_center_watch_run",
                fake_save_data_center_watch_run,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service._safe_push_to_feishu",
                fake_safe_push_to_feishu,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.update_data_center_watch_run_push_result",
                fake_update_data_center_watch_run_push_result,
            ),
        ):
            result = asyncio.run(
                DataCenterWatchService()._finalize_run(
                    group=group,
                    browser_session_id="browser-ebk",
                    actor_id="operator",
                    source="api",
                    dataset_results=dataset_results,
                    push_to_feishu=True,
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertIsInstance(captured_save_kwargs["captured_at"], datetime)
        self.assertIsInstance(captured_push_kwargs["captured_at"], datetime)

    def test_finalize_run_returns_sanitized_operator_payload(self) -> None:
        captured_save_kwargs: dict[str, Any] = {}

        async def fake_save_data_center_watch_run(**kwargs: Any) -> dict[str, Any]:
            captured_save_kwargs.update(kwargs)
            return {"status": "success", "run_id": 42}

        async def fake_update_data_center_watch_run_push_result(**kwargs: Any) -> None:
            return None

        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="澜江路 EBK 数据中心盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url=None,
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[
                DataCenterWatchDatasetRuntime(
                    dataset_id="business_outline",
                    dataset_label="收益概览",
                    enabled=True,
                    push_enabled=True,
                    sort_order=0,
                )
            ],
        )
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "summary": "读取成功",
                "data": {"dataset": "business_outline"},
                "evidence": {
                    "screenshot_path": r"C:\Users\Administrator\Desktop\pan-hotel-watch\output\playwright\ebk.png",
                    "profile_path_returned": False,
                    "password_returned": False,
                },
                "push_enabled": True,
            }
        ]

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.save_data_center_watch_run",
                fake_save_data_center_watch_run,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.update_data_center_watch_run_push_result",
                fake_update_data_center_watch_run_push_result,
            ),
        ):
            result = asyncio.run(
                DataCenterWatchService()._finalize_run(
                    group=group,
                    browser_session_id="browser-ebk",
                    actor_id="operator",
                    source="api",
                    dataset_results=dataset_results,
                    push_to_feishu=False,
                )
            )

        self.assertEqual(
            captured_save_kwargs["dataset_results"][0]["evidence"]["screenshot_path"],
            r"C:\Users\Administrator\Desktop\pan-hotel-watch\output\playwright\ebk.png",
        )
        self.assertEqual(
            result["dataset_results"][0]["evidence"]["screenshot_path"],
            ARTIFACT_PLACEHOLDER,
        )
        self.assertIs(result["dataset_results"][0]["evidence"]["profile_path_returned"], False)
        self.assertIs(result["dataset_results"][0]["evidence"]["password_returned"], False)

    def test_failure_reason_falls_back_to_summary_when_evidence_is_empty(self) -> None:
        self.assertEqual(
            _failure_reason(
                "failed",
                {},
                "Expected business fields were not found on the EBK page.",
                {},
            ),
            "页面结构变化，缺少可确认业务字段。",
        )

    def test_push_uses_custom_outer_title_and_original_ebk_card_header(self) -> None:
        captured_push: dict[str, Any] = {}

        async def fake_push_interactive_card_to_feishu_webhook(**kwargs: Any):
            captured_push.update(kwargs)

            class _Result:
                status = "success"
                data: dict[str, Any] = {}

                def model_dump(self, *, mode: str) -> dict[str, Any]:
                    return {
                        "status": "success",
                        "summary": "sent",
                        "data": {"title": kwargs["title"]},
                    }

            return _Result()

        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="携程静里全套房酒店（云州澜江路望川路地铁站店）EBK盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url="https://open.feishu.cn/webhook",
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[],
        )
        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "summary": "读取成功",
                "data": {"dataset": "business_outline"},
                "evidence": {},
                "push_enabled": True,
            }
        ]

        with patch(
            "app.domain.ctrip_ebooking_watch.service.push_interactive_card_to_feishu_webhook",
            fake_push_interactive_card_to_feishu_webhook,
        ):
            result = asyncio.run(
                _push_to_feishu(
                    group,
                    "success",
                    "EBK 数据中心读取成功：成功 1/1。",
                    dataset_results,
                    captured_at="2026-06-18T08:05:23",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(captured_push["title"], group.brief_title)
        self.assertEqual(
            captured_push["card"]["header"]["title"]["content"],
            "携程 EBK 数据中心盯盘",
        )
        self.assertIn("来源", str(captured_push["card"]))
        self.assertIn("结果", str(captured_push["card"]))

    def test_daily_brief_pushes_all_required_dataset_content(self) -> None:
        from app.domain.notifications.feishu import FeishuPushResult

        group = replace(
            _daily_brief_runtime_group(),
            feishu_webhook_url="https://open.feishu.cn/webhook",
        )
        built_cards: list[str] = []
        built_card_dataset_ids: list[tuple[str, ...]] = []

        def fake_build_data_center_watch_push_card(**kwargs: Any) -> list[dict[str, Any]]:
            built_cards.append(str(kwargs.get("title") or ""))
            built_card_dataset_ids.append(
                tuple(
                    str(result.get("dataset_id") or "")
                    for result in kwargs["dataset_results"]
                )
            )
            return [
                {
                    "config": {},
                    "header": {"title": {"content": "携程 EBK 数据中心盯盘"}},
                    "elements": [{"tag": "div", "text": {"content": "旧版内容"}}],
                }
            ]

        async def fake_push_interactive_card_to_feishu_webhook(**kwargs: Any) -> FeishuPushResult:
            self.assertEqual(kwargs["card"]["elements"][0]["text"]["content"], "旧版内容")
            return FeishuPushResult(
                status="success",
                summary="Feishu webhook push completed.",
                data={"msg_type": "interactive"},
            )

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.build_data_center_watch_push_card",
                fake_build_data_center_watch_push_card,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.push_interactive_card_to_feishu_webhook",
                fake_push_interactive_card_to_feishu_webhook,
            ),
        ):
            result = asyncio.run(
                _push_to_feishu(
                    group,
                    "success",
                    "EBK 数据中心读取成功：成功 1/1。",
                    [
                        {
                            "dataset_id": "business_outline",
                            "dataset_label": "收益概览",
                            "status": "success",
                            "data": {"dataset": "business_outline"},
                            "push_enabled": True,
                        },
                        {
                            "dataset_id": "comment_monitor",
                            "dataset_label": "点评分析",
                            "status": "success",
                            "data": {"dataset": "comment_monitor"},
                            "push_enabled": True,
                        },
                        {
                            "dataset_id": "beneficial_data",
                            "dataset_label": "销售数据",
                            "status": "success",
                            "data": {"dataset": "beneficial_data"},
                            "push_enabled": True,
                        },
                        {
                            "dataset_id": "flow_data",
                            "dataset_label": "流量数据",
                            "status": "success",
                            "data": {"dataset": "flow_data"},
                            "push_enabled": True,
                        },
                        {
                            "dataset_id": "competition_profile",
                            "dataset_label": "经营对比",
                            "status": "success",
                            "data": {"dataset": "competition_profile"},
                            "push_enabled": True,
                        },
                    ],
                    run_kind="daily_brief",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["msg_type"], "interactive")
        self.assertEqual(built_cards, [group.brief_title])
        self.assertEqual(
            built_card_dataset_ids,
            [
                (
                    "business_outline",
                    "comment_monitor",
                    "beneficial_data",
                    "flow_data",
                    "competition_profile",
                )
            ],
        )
        self.assertNotIn("card_layout", result["data"])

    def test_run_watch_group_short_circuits_when_browser_session_unregistered(self) -> None:
        """A group whose browser_session_id is not in BrowserSessionStore must
        return a clear human_required result with a Chinese hint, instead of
        surfacing "Unknown browser session: ..." into run status/summary."""
        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title="澜江路 EBK 数据中心盯盘",
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk-missing",
            feishu_webhook_url=None,
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[
                DataCenterWatchDatasetRuntime(
                    dataset_id="business_outline",
                    dataset_label="收益概览",
                    enabled=True,
                    push_enabled=True,
                    sort_order=0,
                )
            ],
        )

        captured_save_kwargs: dict[str, Any] = {}

        async def fake_save_data_center_watch_run(**kwargs: Any) -> dict[str, Any]:
            captured_save_kwargs.update(kwargs)
            return {"status": "human_required", "run_id": 99}

        async def fake_safe_push_to_feishu(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"status": "skipped", "watch_group_key": group.group_key}

        async def fake_update_data_center_watch_run_push_result(**kwargs: Any) -> None:
            return None

        class FakeStore:
            def get(self, browser_session_id: str):  # noqa: ANN001
                raise KeyError(f"Unknown browser session: {browser_session_id}")

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_browser_session_store",
                lambda: FakeStore(),
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.save_data_center_watch_run",
                fake_save_data_center_watch_run,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service._safe_push_to_feishu",
                fake_safe_push_to_feishu,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.update_data_center_watch_run_push_result",
                fake_update_data_center_watch_run_push_result,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_ctrip_ebooking_service"
            ) as fake_ebk_service,
        ):
            result = asyncio.run(
                DataCenterWatchService().run_watch_group(
                    group=group,
                    actor_id="operator",
                    source="api",
                    push_to_feishu=False,
                )
            )

        # The browser read path must NOT have been reached (no read attempt).
        fake_ebk_service.assert_not_called()
        # Status degrades to human_required; the run summary is the generic
        # template (no "Unknown browser session" leak).
        self.assertEqual(result["status"], "human_required")
        self.assertIn("需要人工处理", result["summary"])
        self.assertNotIn("Unknown browser session", result["summary"])
        # Each dataset gets the explicit hint, not the raw KeyError text.
        self.assertEqual(len(result["dataset_results"]), 1)
        ds = result["dataset_results"][0]
        self.assertEqual(ds["status"], "human_required")
        self.assertIn("携程 EBK 浏览器会话未注册", ds["failure_reason"])
        self.assertNotIn("Unknown browser session", ds["failure_reason"])
        # The original (stale) session id is preserved for display-layer tagging.
        self.assertEqual(result["browser_session_id"], "browser-ebk-missing")
        self.assertEqual(captured_save_kwargs["browser_session_id"], "browser-ebk-missing")

    def test_daily_brief_required_datasets_cover_five_datasets(self) -> None:
        self.assertEqual(
            set(DAILY_BRIEF_REQUIRED_DATASET_IDS),
            {
                "business_outline",
                "comment_monitor",
                "beneficial_data",
                "flow_data",
                "competition_profile",
            },
        )

    def test_read_dataset_pyramid_bid_uses_pyramid_read_flow(self) -> None:
        """2026-09-10 需求：金字塔出价作为独立数据集接入盯盘读取。

        必须走只读 read_pyramid_promotions（复用金字塔调价指令的抓取链路），
        并包装成标准数据集结果（promotions 落 data，供推送表格渲染）。
        """
        group = DataCenterWatchGroupRuntime(
            group_key="ctrip_ebooking_data_center",
            name="携程 EBK 数据中心盯盘",
            brief_title=None,
            platform_account_id="ctrip-ebk",
            browser_session_id="browser-ebk",
            feishu_webhook_url=None,
            feishu_chat_id=None,
            timezone="Asia/Shanghai",
            retention_days=14,
            query_interval_minutes=60,
            auto_poll_enabled=False,
            enabled=True,
            last_polled_at=None,
            datasets=[],
        )
        dataset = DataCenterWatchDatasetRuntime(
            dataset_id="pyramid_bid",
            dataset_label="金字塔出价",
            enabled=True,
            push_enabled=True,
            sort_order=10,
        )
        captured_kwargs: dict[str, Any] = {}

        class FakeEbkService:
            async def read_pyramid_promotions(self, **kwargs: Any) -> dict[str, Any]:
                captured_kwargs.update(kwargs)
                return {
                    "status": "success",
                    "summary": "读取到 2 个金字塔推广计划。",
                    "platform_account_id": kwargs["platform_account_id"],
                    "browser_session_id": kwargs["browser_session_id"],
                    "promotions": [
                        {
                            "campaign_name": "周内0-2",
                            "campaign_status": "推广中",
                            "click_bid_cny": 6.5,
                            "daily_budget_cny": 300.0,
                            "today_spend_cny": 45.2,
                        },
                        {
                            "campaign_name": "周末14-16",
                            "campaign_status": "推广中",
                            "click_bid_cny": 5.0,
                            "daily_budget_cny": 400.0,
                            "today_spend_cny": 12.0,
                        },
                    ],
                    "data": {
                        "query_type": "ctrip_pyramid_promotions_read",
                        "captured_at": "2026-09-10T17:00:00+08:00",
                    },
                    "evidence": {"page_state": "list", "row_count": 2, "read_only": True},
                }

        with patch(
            "app.domain.ctrip_ebooking_watch.service.get_ctrip_ebooking_service",
            lambda: FakeEbkService(),
        ):
            result = asyncio.run(
                DataCenterWatchService()._read_dataset(
                    group=group,
                    browser_session_id="browser-ebk",
                    dataset=dataset,
                    actor_id="operator",
                )
            )

        self.assertEqual(captured_kwargs["platform_account_id"], "ctrip-ebk")
        self.assertEqual(captured_kwargs["browser_session_id"], "browser-ebk")
        self.assertEqual(result["dataset_id"], "pyramid_bid")
        self.assertEqual(result["dataset_label"], "金字塔出价")
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["promotions"]), 2)
        self.assertEqual(result["captured_at"], "2026-09-10T17:00:00+08:00")
        self.assertTrue(result["push_enabled"])

    def test_read_dataset_pyramid_bid_failure_maps_status(self) -> None:
        """金字塔页读取 human_required：数据集结果如实降级，不虚构计划。"""
        group = _daily_brief_runtime_group()
        group = replace(group, datasets=[], browser_session_id="browser-ebk")
        dataset = DataCenterWatchDatasetRuntime(
            dataset_id="pyramid_bid",
            dataset_label="金字塔出价",
            enabled=True,
            push_enabled=True,
            sort_order=10,
        )

        class FakeEbkService:
            async def read_pyramid_promotions(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "human_required",
                    "summary": "EBK 需要人工完成登录验证。",
                    "promotions": [],
                    "data": {"captured_at": "2026-09-10T17:00:00+08:00"},
                    "evidence": {"page_state": "login"},
                }

        with patch(
            "app.domain.ctrip_ebooking_watch.service.get_ctrip_ebooking_service",
            lambda: FakeEbkService(),
        ):
            result = asyncio.run(
                DataCenterWatchService()._read_dataset(
                    group=group,
                    browser_session_id="browser-ebk",
                    dataset=dataset,
                    actor_id="operator",
                )
            )

        self.assertEqual(result["status"], "human_required")
        self.assertEqual(result["data"].get("promotions"), [])
        self.assertTrue(result["failure_reason"])

    def test_run_daily_brief_reads_all_required_datasets(self) -> None:
        group = _daily_brief_runtime_group()
        read_dataset_ids: list[str] = []

        async def fake_read(self: DataCenterWatchService, **kwargs: Any) -> dict[str, Any]:
            read_dataset_ids.append(kwargs["dataset"].dataset_id)
            return {
                "dataset_id": kwargs["dataset"].dataset_id,
                "dataset_label": kwargs["dataset"].dataset_label,
                "status": "success",
                "summary": "读取成功",
                "data": {"dataset": kwargs["dataset"].dataset_id},
                "evidence": {},
                "push_enabled": True,
            }

        async def fake_finalize(self: DataCenterWatchService, **kwargs: Any) -> dict[str, Any]:
            return {"status": "success", "run_kind": kwargs["run_kind"]}

        with (
            patch.object(DataCenterWatchService, "_read_dataset", fake_read),
            patch.object(DataCenterWatchService, "_finalize_run", fake_finalize),
            patch(
                "app.domain.ctrip_ebooking_watch.service._browser_session_registered",
                lambda session_id: True,
            ),
        ):
            result = asyncio.run(DataCenterWatchService().run_daily_brief(group=group))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["run_kind"], "daily_brief")
        self.assertEqual(
            set(read_dataset_ids),
            set(DAILY_BRIEF_REQUIRED_DATASET_IDS),
        )

    def test_finalize_run_daily_brief_pushes_daily_card_with_day_over_day(self) -> None:
        group = _daily_brief_runtime_group()
        yesterday = datetime.now() - timedelta(days=1)
        baseline_runs = [
            {
                "run_id": 41,
                "captured_at": yesterday.replace(microsecond=0),
                "data": {
                    "metrics": {
                        "realtime_visitor_rank": {"value": "285"},
                        "psi_service_score": {"value": "4.86"},
                    }
                },
            }
        ]
        captured_card_kwargs: dict[str, Any] = {}

        async def fake_save_data_center_watch_run(**kwargs: Any) -> dict[str, Any]:
            return {"status": "success", "run_id": 42}

        async def fake_update_data_center_watch_run_push_result(**kwargs: Any) -> None:
            return None

        async def fake_get_previous_business_outline_runs(**kwargs: Any) -> list[dict[str, Any]]:
            return baseline_runs

        def fake_build_data_center_watch_push_card(**kwargs: Any) -> dict[str, Any]:
            captured_card_kwargs.update(kwargs)
            return {"config": {}, "header": {}, "elements": []}

        async def fake_push_webhook(**kwargs: Any) -> dict[str, Any]:
            raise AssertionError("webhook push should not happen without webhook_url")

        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "summary": "读取成功",
                "data": {
                    "dataset": "business_outline",
                    "metrics": {
                        "realtime_visitor_rank": {"value": "280", "competitive_rank": "13/21"},
                        "psi_service_score": {"value": "4.91"},
                    },
                },
                "evidence": {},
                "push_enabled": True,
            }
        ]

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.save_data_center_watch_run",
                fake_save_data_center_watch_run,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.update_data_center_watch_run_push_result",
                fake_update_data_center_watch_run_push_result,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_previous_business_outline_runs",
                fake_get_previous_business_outline_runs,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.build_data_center_watch_push_card",
                fake_build_data_center_watch_push_card,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.push_interactive_card_to_feishu_webhook",
                fake_push_webhook,
            ),
            patch(
                "app.domain.page_change_alerts.service.observe_ctrip_dataset_result",
                new=_noop_async,
            ),
        ):
            result = asyncio.run(
                DataCenterWatchService()._finalize_run(
                    group=group,
                    browser_session_id="browser-ebk",
                    actor_id="scheduler",
                    source="scheduler",
                    dataset_results=dataset_results,
                    push_to_feishu=True,
                    push_title="EBK 数据简报",
                    run_kind="daily_brief",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["feishu_push"]["status"], "config_required")
        self.assertNotIn("day_over_day", captured_card_kwargs)

    def test_finalize_run_daily_brief_omits_day_over_day_without_yesterday_run(self) -> None:
        group = _daily_brief_runtime_group()
        captured_card_kwargs: dict[str, Any] = {}

        async def fake_save_data_center_watch_run(**kwargs: Any) -> dict[str, Any]:
            return {"status": "success", "run_id": 43}

        async def fake_update_data_center_watch_run_push_result(**kwargs: Any) -> None:
            return None

        # 只有今天早些时候的运行（不满足“前一日”口径），DOD 必须省略
        async def fake_get_previous_business_outline_runs(**kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "run_id": 40,
                    "captured_at": datetime.now().replace(microsecond=0),
                    "data": {
                        "metrics": {
                            "realtime_visitor_rank": {"value": "285"},
                            "psi_service_score": {"value": "4.86"},
                        }
                    },
                }
            ]

        def fake_build_data_center_watch_push_card(**kwargs: Any) -> dict[str, Any]:
            captured_card_kwargs.update(kwargs)
            return {"config": {}, "header": {}, "elements": []}

        dataset_results = [
            {
                "dataset_id": "business_outline",
                "dataset_label": "收益概览",
                "status": "success",
                "summary": "读取成功",
                "data": {
                    "dataset": "business_outline",
                    "metrics": {
                        "realtime_visitor_rank": {"value": "280"},
                        "psi_service_score": {"value": "4.91"},
                    },
                },
                "evidence": {},
                "push_enabled": True,
            }
        ]

        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.save_data_center_watch_run",
                fake_save_data_center_watch_run,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.update_data_center_watch_run_push_result",
                fake_update_data_center_watch_run_push_result,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_previous_business_outline_runs",
                fake_get_previous_business_outline_runs,
            ),
            patch(
                "app.domain.ctrip_ebooking_watch.service.build_data_center_watch_push_card",
                fake_build_data_center_watch_push_card,
            ),
            patch(
                "app.domain.page_change_alerts.service.observe_ctrip_dataset_result",
                new=_noop_async,
            ),
        ):
            result = asyncio.run(
                DataCenterWatchService()._finalize_run(
                    group=group,
                    browser_session_id="browser-ebk",
                    actor_id="scheduler",
                    source="scheduler",
                    dataset_results=dataset_results,
                    push_to_feishu=True,
                    push_title="EBK 数据简报",
                    run_kind="daily_brief",
                )
            )

        self.assertEqual(result["status"], "success")
        self.assertNotIn("day_over_day", captured_card_kwargs)


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    return None


def _daily_brief_runtime_group() -> DataCenterWatchGroupRuntime:
    return DataCenterWatchGroupRuntime(
        group_key="ctrip_ebooking_data_center",
        name="携程 EBK 数据中心盯盘",
        brief_title="澜江路 EBK 数据中心盯盘",
        platform_account_id="ctrip-ebk",
        browser_session_id="browser-ebk",
        feishu_webhook_url=None,
        feishu_chat_id=None,
        timezone="Asia/Shanghai",
        retention_days=14,
        query_interval_minutes=60,
        auto_poll_enabled=False,
        enabled=True,
        last_polled_at=None,
        daily_brief_title="EBK 数据简报",
        daily_brief_push_time="08:30",
        daily_brief_enabled=True,
        datasets=[
            DataCenterWatchDatasetRuntime(
                dataset_id=dataset_id,
                dataset_label=dataset_id,
                enabled=True,
                push_enabled=True,
                sort_order=index,
            )
            for index, dataset_id in enumerate(DAILY_BRIEF_REQUIRED_DATASET_IDS)
        ],
    )


if __name__ == "__main__":
    unittest.main()
