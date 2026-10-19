from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.domain.ops import feishu_chat_context


async def _build_single_store_context_payload() -> dict:
    with (
        mock.patch.object(
            feishu_chat_context,
            "list_data_center_watch_groups_by_feishu_chat_id",
            new=mock.AsyncMock(
                return_value=[
                    {
                        "group_key": "ebk_quanji",
                        "name": "澜江路泉吉 EBK",
                        "platform_account_id": "ebk-quanji",
                        "browser_session_id": "browser-ebk-2",
                    }
                ]
            ),
        ),
        mock.patch.object(
            feishu_chat_context,
            "list_competitor_watch_groups_by_feishu_chat_id",
            new=mock.AsyncMock(
                return_value=[
                    {
                        "group_key": "watch_quanji",
                        "name": "澜江路泉吉竞品",
                        "platform_account_id": "trip-quanji",
                        "pricing_platform_account_id": "ebk-quanji",
                    }
                ]
            ),
        ),
        mock.patch.object(
            feishu_chat_context,
            "_collect_enabled_hotel_labels",
            new=mock.AsyncMock(return_value=(["澜江路泉吉"], ["竞对A"])),
        ),
        mock.patch.object(
            feishu_chat_context,
            "get_pricing_browser_session_for_feishu_chat_id",
            return_value=mock.Mock(
                display_name="云州泉吉EBK",
                browser_session_id="browser-ebk-2",
                platform_account_id="ebk-quanji",
            ),
        ),
    ):
        return await feishu_chat_context.build_feishu_chat_context("oc_test")


class FeishuChatContextTests(unittest.TestCase):
    def test_single_store_resolves_without_hint(self) -> None:
        async def scenario() -> None:
            payload = await _build_single_store_context_payload()

            self.assertTrue(payload["chat_id_configured"])
            self.assertEqual(payload["binding_mode"], "single_store")
            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["competitor_watch_group_key"], "watch_quanji")
            self.assertEqual(payload["ebk_watch_group_key"], "ebk_quanji")
            self.assertEqual(payload["competitor_data_source"], "chat_bound")

        asyncio.run(scenario())

    def test_single_store_context_output_contract_contains_resolver_fields(self) -> None:
        payload = asyncio.run(_build_single_store_context_payload())

        self.assertEqual(payload["ebk_groups"][0]["group_key"], "ebk_quanji")
        self.assertEqual(
            payload["competitor_groups"][0]["group_key"],
            "watch_quanji",
        )
        resolution = payload["resolution"]
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(resolution["ebk_watch_group_key"], "ebk_quanji")
        self.assertEqual(
            resolution["competitor_watch_group_key"],
            "watch_quanji",
        )

    def test_single_group_context_pairs_read_only_store_without_pricing_account(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ebk_1782812373623",
                                "name": "泉吉高空江景 EBK 数据中心",
                                "platform_account_id": "ebk-1",
                                "browser_session_id": "browser-ebk-1",
                                "auto_poll_enabled": False,
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "default_competitor_watch",
                                "name": "澜江路泉吉竞品价格",
                                "platform_account_id": "ctrip-public",
                                "pricing_platform_account_id": None,
                                "pricing_account_configured": False,
                                "auto_poll_enabled": True,
                                "latest_run": {
                                    "status": "success",
                                    "captured_at": "2026-07-08T10:31:00+08:00",
                                },
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(
                        return_value=(
                            ["泉吉云州澜江路步行街高空江景酒店"],
                            ["云滩江景酒店"],
                        )
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_group_summaries",
                    new=mock.AsyncMock(return_value=[]),
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context(
                    "oc_quanji",
                    store_hint="泉吉",
                )

            self.assertEqual(payload["binding_mode"], "single_store")
            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["competitor_watch_group_key"], "default_competitor_watch")
            self.assertEqual(payload["ebk_watch_group_key"], "ebk_1782812373623")
            self.assertIsNone(payload["pricing_platform_account_id"])
            self.assertIn(
                "调价 EBK",
                str(payload["operator_summary"]["pricing_reference_policy_rows"]),
            )
            self.assertIn("只读查询", payload["operator_summary"]["usage_note"])

        asyncio.run(scenario())

    def test_multi_store_ambiguous_without_hint(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ebk_jingli",
                                "name": "静里 EBK",
                                "platform_account_id": "ebk-jingli",
                            },
                            {
                                "group_key": "ebk_quanji",
                                "name": "澜江路泉吉 EBK",
                                "platform_account_id": "ebk-quanji",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "watch_jingli",
                                "name": "静里竞品",
                                "platform_account_id": "trip-jingli",
                                "pricing_platform_account_id": "ebk-jingli",
                            },
                            {
                                "group_key": "watch_quanji",
                                "name": "澜江路泉吉竞品",
                                "platform_account_id": "trip-quanji",
                                "pricing_platform_account_id": "ebk-quanji",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(
                        side_effect=[
                            (["云州静里"], ["竞对A"]),
                            (["澜江路泉吉"], ["竞对B"]),
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_test")

            self.assertEqual(payload["binding_mode"], "multi_store")
            self.assertEqual(payload["resolution"]["status"], "ambiguous")
            self.assertIsNone(payload["competitor_watch_group_key"])
            self.assertIn("store_selection_rows", payload["operator_summary"])

        asyncio.run(scenario())

    def test_multi_store_resolves_with_store_hint_when_ebk_not_chat_bound(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_group_summaries",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ctrip_ebooking_data_center",
                                "name": "程静里全套房酒店数据中心盯盘",
                                "platform_account_id": "ebk-2",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "default_competitor_watch",
                                "name": "澜江路泉吉酒店",
                                "platform_account_id": "trip-1",
                                "pricing_platform_account_id": None,
                                "pricing_browser_session_id": None,
                            },
                            {
                                "group_key": "watch_1782109266413",
                                "name": "静里全套房酒店（云州澜江路望川路地铁站店）",
                                "platform_account_id": "trip-2",
                                "pricing_platform_account_id": "ebk-2",
                                "pricing_browser_session_id": "browser-ebk-2",
                                "pricing_account_configured": True,
                                "pricing_connection_mode": "non_direct",
                                "pricing_can_auto_write_price": True,
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(
                        side_effect=[
                            (["泉吉云州澜江路步行街高空江景酒店"], ["竞对A"]),
                            (
                                ["静里全套房酒店（云州澜江路望川路地铁站店）"],
                                ["竞对B"],
                            ),
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=mock.Mock(
                        display_name="澜江路泉吉 EBK",
                        browser_session_id="browser-ebk-1",
                        platform_account_id="ebk-1",
                    ),
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context(
                    "oc_test",
                    store_hint="静里",
                )

            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["competitor_watch_group_key"], "watch_1782109266413")
            self.assertEqual(payload["ebk_watch_group_key"], "ctrip_ebooking_data_center")
            self.assertEqual(payload["pricing_browser_session_id"], "browser-ebk-2")
            self.assertEqual(payload["pricing_platform_account_id"], "ebk-2")
            self.assertIn("ebk-2", payload["operator_summary"]["overview_rows"][1]["value"])

        asyncio.run(scenario())

    def test_multi_store_resolves_with_store_hint(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ebk_jingli",
                                "name": "静里 EBK",
                                "platform_account_id": "ebk-jingli",
                            },
                            {
                                "group_key": "ebk_quanji",
                                "name": "澜江路泉吉 EBK",
                                "platform_account_id": "ebk-quanji",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "watch_jingli",
                                "name": "静里竞品",
                                "platform_account_id": "trip-jingli",
                                "pricing_platform_account_id": "ebk-jingli",
                            },
                            {
                                "group_key": "watch_quanji",
                                "name": "澜江路泉吉竞品",
                                "platform_account_id": "trip-quanji",
                                "pricing_platform_account_id": "ebk-quanji",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(
                        side_effect=[
                            (["云州静里"], ["竞对A"]),
                            (["澜江路泉吉"], ["竞对B"]),
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context(
                    "oc_test",
                    store_hint="静里",
                )

            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["competitor_watch_group_key"], "watch_jingli")
            self.assertEqual(payload["own_hotels"], ["云州静里"])

        asyncio.run(scenario())

    def test_unbound_chat_returns_config_required(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_unknown")

            self.assertFalse(payload["chat_id_configured"])
            self.assertEqual(payload["binding_mode"], "unbound")
            self.assertEqual(payload["competitor_data_source"], "none")
            self.assertEqual(payload["status"], "config_required")
            operator = payload["operator_summary"]
            self.assertIn("运营问答上下文群未绑定", operator["intro"])
            self.assertIn("EBK 数据中心监控组", operator["routing_hints"][0])
            self.assertIn("竞品监控组", operator["routing_hints"][0])
            self.assertIn("不要去改 EBK 账号上的手动调价指令群", operator["routing_hints"][1])
            self.assertNotIn("本群未绑定监控组", " ".join(operator["routing_hints"]))
            self.assertNotIn("配置 feishu_chat_id", " ".join(operator["routing_hints"]))

        asyncio.run(scenario())

    def test_ebk_only_chat_binding_resolves_data_center_group(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ctrip_ebooking_data_center",
                                "name": "静里 EBK 数据中心",
                                "platform_account_id": "ebk-jingli",
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_group_summaries",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_ebk_only")

            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["chat_id_configured"])
            self.assertEqual(payload["binding_mode"], "single_store")
            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["ebk_watch_group_key"], "ctrip_ebooking_data_center")
            self.assertIsNone(payload["competitor_watch_group_key"])
            self.assertNotIn("1 组 EBK + 1 组竞品", payload["operator_summary"]["intro"])

        asyncio.run(scenario())

    def test_ebk_bound_chat_pairs_competitor_group_by_pricing_account(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ctrip_ebooking_data_center",
                                "name": "静里 EBK 数据中心",
                                "platform_account_id": "ebk-jingli",
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_group_summaries",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "watch_jingli",
                                "name": "静里竞品",
                                "platform_account_id": "trip-jingli",
                                "pricing_platform_account_id": "ebk-jingli",
                                "pricing_browser_session_id": "browser-ebk-jingli",
                            },
                            {
                                "group_key": "watch_other",
                                "name": "其他竞品",
                                "platform_account_id": "trip-other",
                                "pricing_platform_account_id": "ebk-other",
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(return_value=(["云州静里"], ["竞对A"])),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_browser_session_display_name",
                    return_value="静里 EBK 调价账号",
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_ebk_ops")

            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["chat_id_configured"])
            self.assertEqual(payload["resolution"]["status"], "resolved")
            self.assertEqual(payload["ebk_watch_group_key"], "ctrip_ebooking_data_center")
            self.assertEqual(payload["competitor_watch_group_key"], "watch_jingli")
            self.assertEqual(payload["pricing_platform_account_id"], "ebk-jingli")
            self.assertEqual(payload["pricing_browser_session_id"], "browser-ebk-jingli")
            self.assertEqual(payload["competitor_data_source"], "paired_by_pricing_account")

        asyncio.run(scenario())

    def test_empty_chat_id(self) -> None:
        async def scenario() -> None:
            payload = await feishu_chat_context.build_feishu_chat_context(None)
            self.assertFalse(payload["chat_id_configured"])
            self.assertEqual(payload["binding_mode"], "unbound")

        asyncio.run(scenario())

    def test_competitor_group_includes_pricing_reference_policy(self) -> None:
        async def scenario() -> None:
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(return_value=[]),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "watch_jingli",
                                "name": "静里竞品",
                                "platform_account_id": "trip-jingli",
                                "pricing_platform_account_id": "ebk-jingli",
                                "pricing_account_configured": True,
                                "pricing_connection_mode": "non_direct",
                                "pricing_can_auto_write_price": True,
                                "pricing_write_policy_message": "非直连账号可在 Feishu 审批通过后继续调价流程",
                            },
                            {
                                "group_key": "watch_quanji",
                                "name": "泉吉竞品",
                                "platform_account_id": "trip-quanji",
                                "pricing_account_configured": False,
                            },
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(return_value=(["本店"], ["竞对A"])),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_test")

            policy_rows = payload["operator_summary"]["pricing_reference_policy_rows"]
            self.assertEqual(len(policy_rows), 2)
            self.assertIn("降价参考卡", policy_rows[0]["field"])
            self.assertIn("非直连", policy_rows[0]["value"])
            self.assertIn("未配置调价 EBK", policy_rows[1]["value"])

        asyncio.run(scenario())

    def test_ota_ebk_accounts_resolve_meituan_fliggy_when_only_ctrip_watch_group_bound(
        self,
    ) -> None:
        """Feishu EBK watch group is Ctrip-only; brand-matched browser EBK still counts."""

        async def scenario() -> None:
            store = mock.Mock()
            store.list.return_value = [
                mock.Mock(
                    browser_session_id="browser-manxin-xiecheng",
                    platform_account_id="manxin-xiecheng",
                    display_name="曼馨酒店",
                    platform="ctrip",
                    intended_use="ebk",
                ),
                mock.Mock(
                    browser_session_id="browser-manxin-meituan",
                    platform_account_id="manxin-meituan",
                    display_name="曼馨酒店美团",
                    platform="meituan",
                    intended_use="ebk",
                ),
                mock.Mock(
                    browser_session_id="browser-manxin-feizhu",
                    platform_account_id="manxin-feizhu",
                    display_name="曼馨酒店飞猪",
                    platform="fliggy",
                    intended_use="ebk",
                ),
                mock.Mock(
                    browser_session_id="browser-jingli-meituan",
                    platform_account_id="jingli-meituan",
                    display_name="静里美团账号",
                    platform="meituan",
                    intended_use="ebk",
                ),
            ]
            with (
                mock.patch.object(
                    feishu_chat_context,
                    "list_data_center_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "ebk_manxin",
                                "name": "曼馨 EBK 数据中心",
                                "platform_account_id": "manxin-xiecheng",
                                "browser_session_id": "browser-manxin-xiecheng",
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "list_competitor_watch_groups_by_feishu_chat_id",
                    new=mock.AsyncMock(
                        return_value=[
                            {
                                "group_key": "watch_manxin",
                                "name": "曼馨竞品",
                                "platform_account_id": "trip-1",
                                "pricing_platform_account_id": "manxin-xiecheng",
                            }
                        ]
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "_collect_enabled_hotel_labels",
                    new=mock.AsyncMock(
                        return_value=(
                            ["云州澜江路步行街高空江景曼馨酒店"],
                            ["竞对A"],
                        )
                    ),
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_pricing_browser_session_for_feishu_chat_id",
                    return_value=None,
                ),
                mock.patch.object(
                    feishu_chat_context,
                    "get_browser_session_store",
                    return_value=store,
                ),
            ):
                payload = await feishu_chat_context.build_feishu_chat_context("oc_manxin")

            ota = payload["ota_ebk_accounts"]
            self.assertEqual(ota["ctrip"]["platform_account_id"], "manxin-xiecheng")
            self.assertEqual(ota["meituan"]["platform_account_id"], "manxin-meituan")
            self.assertEqual(ota["fliggy"]["platform_account_id"], "manxin-feizhu")
            overview = {
                row["field"]: row["value"]
                for row in payload["operator_summary"]["overview_rows"]
            }
            self.assertIn("曼馨酒店美团", overview.get("美团 EBK 账号", ""))
            self.assertIn("曼馨酒店飞猪", overview.get("飞猪 EBK 账号", ""))
            hints = "\n".join(payload["operator_summary"]["routing_hints"])
            self.assertIn("禁止", hints)
            self.assertIn("未配置美团", hints)
            self.assertIn("get_ebk_joint_analysis_context", hints)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
