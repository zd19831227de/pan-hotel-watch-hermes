import asyncio
import unittest

from client.feishu_store_resolver import resolve_overview_watch_groups, resolve_watch_group_keys


class FakeContextClient:
    def __init__(self, context: dict):
        self.context = context

    async def get_feishu_chat_context(self, chat_id: str, *, store_hint: str | None = None) -> dict:
        return self.context


class FakeFallbackClient(FakeContextClient):
    def __init__(self, context: dict, configuration: dict):
        super().__init__(context)
        self.configuration = configuration
        self.configuration_calls = 0

    async def get_operator_watch_configuration(self) -> dict:
        self.configuration_calls += 1
        return self.configuration


class FeishuStoreResolverTests(unittest.TestCase):
    def test_explicit_key_outside_current_chat_is_rejected(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "ebk_watch_group_key": "ebk_group_a",
                    },
                    "ebk_groups": [{"group_key": "ebk_group_a"}],
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group_a",
                watch_kind="ebk",
                explicit_key="ebk_group_b",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "watch_group_not_allowed")

        asyncio.run(scenario())

    def test_competitor_explicit_key_outside_current_chat_is_rejected(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "competitor_watch_group_key": "competitor_group_a",
                    },
                    "competitor_groups": [{"group_key": "competitor_group_a"}],
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group_a",
                watch_kind="competitor",
                explicit_key="competitor_group_b",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "watch_group_not_allowed")

        asyncio.run(scenario())

    def test_overview_explicit_key_outside_current_chat_is_rejected(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "ebk_watch_group_key": "ebk_group_a",
                        "competitor_watch_group_key": "competitor_group_a",
                    },
                    "ebk_groups": [{"group_key": "ebk_group_a"}],
                    "competitor_groups": [{"group_key": "competitor_group_a"}],
                }
            )

            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_group_a",
                ebk_watch_group_key="ebk_group_b",
            )

            self.assertEqual(keys, (None, None))
            assert blocked is not None
            self.assertEqual(blocked["status"], "watch_group_not_allowed")

        asyncio.run(scenario())

    def test_overview_accepts_authorized_explicit_key_for_ambiguous_chat(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {"status": "ambiguous"},
                    "ebk_groups": [
                        {"group_key": "ebk_group_a"},
                        {"group_key": "ebk_group_b"},
                    ],
                    "competitor_groups": [
                        {"group_key": "competitor_group_a"},
                        {"group_key": "competitor_group_b"},
                    ],
                }
            )

            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_group_a",
                ebk_watch_group_key="ebk_group_a",
            )

            self.assertEqual(keys, ("ebk_group_a", None))
            self.assertIsNone(blocked)

        asyncio.run(scenario())

    def test_explicit_key_without_chat_id_still_requires_session_context(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient({})

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id=None,
                watch_kind="competitor",
                explicit_key="competitor_group_a",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "session_context_required")

        asyncio.run(scenario())

    def test_resolved_store_without_ebk_binding_blocks_default_fallback(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "resolved",
                        "match_reason": "name_match",
                        "store_name": "Jingli Hotel",
                        "ebk_watch_group_key": None,
                        "competitor_watch_group_key": "watch_1782109266413",
                    },
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group",
                store_hint="Jingli",
                watch_kind="ebk",
            )

            self.assertIsNone(key)
            self.assertIsNotNone(blocked)
            assert blocked is not None
            self.assertEqual(blocked["status"], "config_required")
            self.assertEqual(blocked["watch_kind"], "ebk")

        asyncio.run(scenario())

    def test_resolved_store_without_competitor_binding_blocks_default_fallback(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "resolved",
                        "match_reason": "name_match",
                        "store_name": "Jingli Hotel",
                        "ebk_watch_group_key": "ctrip_ebooking_data_center",
                        "competitor_watch_group_key": None,
                    },
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group",
                store_hint="Jingli",
                watch_kind="competitor",
            )

            self.assertIsNone(key)
            self.assertIsNotNone(blocked)
            assert blocked is not None
            self.assertEqual(blocked["status"], "config_required")
            self.assertEqual(blocked["watch_kind"], "competitor")

        asyncio.run(scenario())

    def test_missing_chat_id_returns_session_context_required(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient({})
            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id=None,
                watch_kind="ebk",
            )
            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "session_context_required")
            self.assertIn("运营问答上下文群", blocked["summary"])
            self.assertIn("不是价格变化提醒群", blocked["operator_summary"]["intro"])
            self.assertIn("不是手动调价指令群", blocked["operator_summary"]["intro"])

        asyncio.run(scenario())

    def test_missing_watch_group_binding_names_operations_context_group(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "unbound",
                    "resolution": {
                        "status": "resolved",
                        "store_name": "Jingli Hotel",
                        "ebk_watch_group_key": None,
                        "competitor_watch_group_key": "watch_1782109266413",
                    },
                }
            )
            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group",
                store_hint="Jingli",
                watch_kind="ebk",
            )
            self.assertIsNone(key)
            assert blocked is not None
            self.assertIn("运营问答上下文群", blocked["summary"])
            self.assertIn("价格变化提醒群", blocked["operator_summary"]["routing_hints"][0])
            self.assertIn("手动调价指令群", blocked["operator_summary"]["routing_hints"][1])

        asyncio.run(scenario())

    def test_ebk_tool_uses_single_bound_ebk_when_multistore_hint_is_missing(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "ambiguous",
                        "summary": "请说明是静里还是澜江路泉吉。",
                    },
                    "ebk_groups": [
                        {
                            "group_key": "ctrip_ebooking_data_center",
                            "name": "静里 EBK",
                        }
                    ],
                    "competitor_groups": [
                        {"group_key": "default_competitor_watch", "name": "泉吉竞品"},
                        {"group_key": "watch_1782109266413", "name": "静里竞品"},
                    ],
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group",
                store_hint=None,
                watch_kind="ebk",
            )

            self.assertIsNone(blocked)
            self.assertEqual(key, "ctrip_ebooking_data_center")

        asyncio.run(scenario())

    def test_competitor_tool_still_blocks_when_multistore_hint_is_missing(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "ambiguous",
                        "summary": "请说明是静里还是澜江路泉吉。",
                    },
                    "ebk_groups": [
                        {"group_key": "ctrip_ebooking_data_center", "name": "静里 EBK"}
                    ],
                    "competitor_groups": [
                        {"group_key": "default_competitor_watch", "name": "泉吉竞品"},
                        {"group_key": "watch_1782109266413", "name": "静里竞品"},
                    ],
                }
            )

            key, blocked = await resolve_watch_group_keys(
                client,
                chat_id="oc_group",
                store_hint=None,
                watch_kind="competitor",
            )

            self.assertIsNone(key)
            assert blocked is not None
            self.assertEqual(blocked["status"], "ambiguous_store")

        asyncio.run(scenario())

    def test_overview_resolves_both_keys_for_store(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "resolved",
                        "store_label": "静里",
                        "ebk_watch_group_key": "ctrip_ebooking_data_center",
                        "competitor_watch_group_key": "watch_1782109266413",
                    },
                }
            )
            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_group",
                store_hint="静里",
            )
            self.assertIsNone(blocked)
            self.assertEqual(keys, ("ctrip_ebooking_data_center", "watch_1782109266413"))

        asyncio.run(scenario())

    def test_overview_allows_ebk_only_context_without_competitor_default(self) -> None:
        async def scenario() -> None:
            client = FakeContextClient(
                {
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "store_label": "静里",
                        "ebk_watch_group_key": "ctrip_ebooking_data_center",
                        "competitor_watch_group_key": None,
                    },
                }
            )
            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_group",
                store_hint="静里",
            )

            self.assertIsNone(blocked)
            self.assertEqual(keys, ("ctrip_ebooking_data_center", None))

        asyncio.run(scenario())

    def test_unbound_group_with_unique_store_hint_requires_configuration(self) -> None:
        async def scenario() -> None:
            client = FakeFallbackClient(
                {
                    "binding_mode": "unbound",
                    "resolution": {"status": "unbound"},
                    "operator_summary": {"intro": "本群未绑定。"},
                },
                {
                    "operator_summary": {
                        "store_catalog": [
                            {
                                "store_name": "静里全套房酒店",
                                "store_aliases": ["静里"],
                                "ebk_watch_group_key": "ctrip_ebooking_data_center",
                                "competitor_watch_group_key": "watch_1782109266413",
                            }
                        ]
                    }
                },
            )

            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_unbound",
                store_hint="静里",
            )

            self.assertEqual(keys, (None, None))
            assert blocked is not None
            self.assertEqual(blocked["status"], "config_required")
            self.assertEqual(client.configuration_calls, 0)

        asyncio.run(scenario())

    def test_unbound_group_without_store_hint_still_blocks(self) -> None:
        async def scenario() -> None:
            client = FakeFallbackClient(
                {
                    "binding_mode": "unbound",
                    "resolution": {"status": "unbound"},
                },
                {
                    "operator_summary": {
                        "store_catalog": [
                            {
                                "store_name": "静里全套房酒店",
                                "store_aliases": ["静里"],
                                "ebk_watch_group_key": "ctrip_ebooking_data_center",
                                "competitor_watch_group_key": "watch_1782109266413",
                            }
                        ]
                    }
                },
            )

            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_unbound",
                store_hint=None,
            )

            self.assertEqual(keys, (None, None))
            assert blocked is not None
            self.assertEqual(blocked["status"], "config_required")
            self.assertIn("运营问答上下文群", blocked["summary"])
            self.assertIn("EBK 数据中心监控组", blocked["operator_summary"]["routing_hints"][0])
            self.assertIn("竞品监控组", blocked["operator_summary"]["routing_hints"][0])
            self.assertIn("手动调价指令群", blocked["operator_summary"]["routing_hints"][1])

        asyncio.run(scenario())

    def test_unbound_group_with_ambiguous_store_hint_blocks(self) -> None:
        async def scenario() -> None:
            client = FakeFallbackClient(
                {
                    "binding_mode": "unbound",
                    "resolution": {"status": "unbound"},
                },
                {
                    "operator_summary": {
                        "store_catalog": [
                            {
                                "store_name": "静里一店",
                                "store_aliases": ["静里"],
                                "ebk_watch_group_key": "ebk_1",
                                "competitor_watch_group_key": "comp_1",
                            },
                            {
                                "store_name": "静里二店",
                                "store_aliases": ["静里"],
                                "ebk_watch_group_key": "ebk_2",
                                "competitor_watch_group_key": "comp_2",
                            },
                        ]
                    }
                },
            )

            keys, blocked = await resolve_overview_watch_groups(
                client,
                chat_id="oc_unbound",
                store_hint="静里",
            )

            self.assertEqual(keys, (None, None))
            assert blocked is not None
            self.assertEqual(blocked["status"], "config_required")
            self.assertEqual(client.configuration_calls, 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
