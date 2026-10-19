from __future__ import annotations

import unittest

from client.feishu_cards import attach_feishu_card
from client.operator_watch_config import (
    attach_chat_binding_to_payload,
    attach_runtime_health_to_payload,
    build_chat_binding_summary,
    build_operator_watch_configuration,
)


class OperatorWatchConfigTests(unittest.TestCase):
    def test_defaults_note_routes_group_identity_to_chat_context(self) -> None:
        payload = build_operator_watch_configuration(
            ebk_groups=[],
            competitor_groups=[],
            competitor_hotels_by_key={},
            browser_sessions=[],
        )

        note = str(payload["operator_summary"].get("defaults_note") or "")
        self.assertIn("resolve_feishu_chat_context", note)
        self.assertIn("全局配置总览", note)
        self.assertNotIn("必须读本工具", note)

    def test_build_configuration_includes_ebk_account_and_competitor_hotels(self) -> None:
        payload = build_operator_watch_configuration(
            ebk_groups=[
                {
                    "group_key": "ctrip_ebooking_data_center",
                    "name": "Ctrip EBK 数据中心盯盘",
                    "platform_account_id": "ebk-2",
                    "browser_session_id": "browser-ebk-2",
                    "enabled": True,
                    "auto_poll_enabled": False,
                    "datasets": [
                        {"dataset_id": "comment_monitor", "dataset_label": "点评监控", "enabled": True},
                    ],
                }
            ],
            competitor_groups=[
                {
                    "group_key": "default_competitor_watch",
                    "name": "澜江路泉吉酒店",
                    "platform_account_id": "trip-1",
                    "browser_session_id": "browser-trip-1",
                    "browser_rotation_mode": "pool_round_robin",
                    "browser_pool": {
                        "pool_enabled": True,
                        "pool_size": 3,
                        "usable_pool_size": 2,
                        "members": [{"browser_session_id": "browser-trip-1"}],
                    },
                    "pricing_browser_session_id": "browser-ebk-2",
                    "feishu_chat_id": "oc_secret_delivery_group",
                    "enabled": True,
                    "auto_poll_enabled": True,
                }
            ],
            competitor_hotels_by_key={
                "default_competitor_watch": [
                    {
                        "role": "own",
                        "alias": "本店公开页",
                        "hotel_name": "本店公开页",
                        "enabled": True,
                        "ctrip_hotel_id": "122575330",
                    },
                    {
                        "role": "competitor",
                        "alias": "竞对A",
                        "hotel_name": "竞对A",
                        "enabled": True,
                        "ctrip_hotel_id": "133146885",
                    },
                ]
            },
            browser_sessions=[
                {
                    "browser_session_id": "browser-ebk-2",
                    "display_name": "云州澜江路泉吉EBK",
                    "platform_account_id": "ebk-2",
                    "intended_use": "ebk",
                },
                {
                    "browser_session_id": "browser-trip-1",
                    "display_name": "zd读价账号",
                    "platform_account_id": "trip-1",
                    "intended_use": "competitor_price",
                },
            ],
        )
        operator = payload["operator_summary"]
        self.assertEqual(operator["ebk_sections"][0]["ebk_account_display_name"], "云州澜江路泉吉EBK")
        self.assertEqual(operator["competitor_sections"][0]["own_hotels"], ["本店公开页"])
        self.assertEqual(operator["competitor_sections"][0]["competitor_hotels"], ["竞对A"])
        self.assertEqual(operator["competitor_sections"][0]["browser_rotation_mode"], "pool_round_robin")
        self.assertEqual(operator["competitor_sections"][0]["browser_pool"]["usable_pool_size"], 2)
        self.assertEqual(operator["competitor_sections"][0]["pricing_browser_session_id"], "browser-ebk-2")
        self.assertNotIn("oc_secret_delivery_group", str(operator))
        fields = {row["field"] for row in operator["overview_rows"]}
        self.assertTrue(any("EBK 后台" in field for field in fields))
        self.assertTrue(any("竞对酒店" in field for field in fields))

    def test_attach_watch_configuration_card(self) -> None:
        payload = attach_feishu_card(
            {
                "operator_summary": {
                    "intro": "配置总览",
                    "overview_rows": [{"field": "EBK 后台", "value": "测试酒店EBK"}],
                }
            },
            "watch_configuration",
        )
        self.assertEqual(payload["feishu_card_kind"], "watch_configuration")
        self.assertEqual(payload["feishu_card"]["header"]["title"]["content"], "盯盘配置总览")

    def test_build_chat_binding_summary_resolved(self) -> None:
        binding = build_chat_binding_summary(
            {
                "chat_id_configured": True,
                "binding_mode": "single_store",
                "resolution": {
                    "status": "resolved",
                    "store_label": "泉吉云州澜江路步行街高空江景酒店",
                    "ebk_watch_group_key": "ebk_1782812373623",
                    "competitor_watch_group_key": "default_competitor_watch",
                },
                "operator_summary": {
                    "overview_rows": [{"field": "EBK 后台账号", "value": "云州泉吉EBK"}],
                },
            }
        )
        self.assertEqual(binding["chat_binding_status"], "resolved")
        self.assertIn("泉吉云州澜江路", binding["chat_binding_intro"])

    def test_attach_chat_binding_and_runtime_to_configuration(self) -> None:
        base = build_operator_watch_configuration(
            ebk_groups=[],
            competitor_groups=[],
            competitor_hotels_by_key={},
            browser_sessions=[],
        )
        binding = build_chat_binding_summary(
            {
                "chat_id_configured": True,
                "binding_mode": "single_store",
                "resolution": {
                    "status": "resolved",
                    "store_label": "泉吉云州澜江路步行街高空江景酒店",
                    "ebk_watch_group_key": "ebk_1782812373623",
                    "competitor_watch_group_key": "default_competitor_watch",
                },
                "operator_summary": {"overview_rows": []},
            }
        )
        payload = attach_chat_binding_to_payload(base, binding)
        payload = attach_runtime_health_to_payload(
            payload,
            {
                "operator_summary": {
                    "overall_status": "fresh",
                    "module_rows": [{"field": "竞品轮询", "value": "fresh"}],
                }
            },
        )
        operator = payload["operator_summary"]
        self.assertEqual(operator["chat_binding_status"], "resolved")
        self.assertEqual(operator["runtime_status"], "fresh")
        self.assertIn("禁止再说本群未绑定", operator["routing_hints"][0])
        self.assertIn("系统运行状态", operator["intro"])


if __name__ == "__main__":
    unittest.main()
