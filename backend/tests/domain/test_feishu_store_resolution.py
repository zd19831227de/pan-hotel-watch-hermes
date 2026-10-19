from __future__ import annotations

import unittest

from app.domain.ops import feishu_store_resolution as resolution


class FeishuStoreResolutionTests(unittest.TestCase):
    def test_build_store_catalog_pairs_by_pricing_account(self) -> None:
        catalog = resolution.build_store_catalog(
            ebk_groups=[
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
            ],
            competitor_groups=[
                {
                    "group_key": "watch_jingli",
                    "name": "静里竞品",
                    "platform_account_id": "trip-jingli",
                    "pricing_platform_account_id": "ebk-jingli",
                    "own_hotels": ["云州静里"],
                    "competitor_hotels": ["竞对A"],
                },
                {
                    "group_key": "watch_quanji",
                    "name": "澜江路泉吉竞品",
                    "platform_account_id": "trip-quanji",
                    "pricing_platform_account_id": "ebk-quanji",
                    "own_hotels": ["澜江路泉吉"],
                    "competitor_hotels": ["竞对B"],
                },
            ],
        )
        self.assertEqual(len(catalog), 2)
        self.assertTrue(all(item["pair_complete"] for item in catalog))

    def test_single_ebk_and_competitor_pair_without_pricing_account(self) -> None:
        catalog = resolution.build_store_catalog(
            ebk_groups=[
                {
                    "group_key": "ebk_1782812373623",
                    "name": "泉吉高空江景 EBK 数据中心",
                    "platform_account_id": "ebk-1",
                }
            ],
            competitor_groups=[
                {
                    "group_key": "default_competitor_watch",
                    "name": "澜江路泉吉竞品价格",
                    "pricing_platform_account_id": None,
                    "own_hotels": ["泉吉云州澜江路步行街高空江景酒店"],
                    "competitor_hotels": ["云滩江景酒店"],
                }
            ],
        )

        self.assertEqual(len(catalog), 1)
        self.assertTrue(catalog[0]["pair_complete"])
        self.assertEqual(catalog[0]["pair_source"], "single_group_context")
        self.assertEqual(catalog[0]["ebk_watch_group_key"], "ebk_1782812373623")
        self.assertEqual(catalog[0]["competitor_watch_group_key"], "default_competitor_watch")

        result = resolution.resolve_store_selection(store_catalog=catalog, store_hint="泉吉")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["competitor_watch_group_key"], "default_competitor_watch")

    def test_multi_store_without_pricing_account_still_requires_specific_hint(self) -> None:
        catalog = resolution.build_store_catalog(
            ebk_groups=[
                {"group_key": "ebk_jingli", "name": "静里 EBK", "platform_account_id": "ebk-jingli"},
                {"group_key": "ebk_quanji", "name": "泉吉 EBK", "platform_account_id": "ebk-quanji"},
            ],
            competitor_groups=[
                {
                    "group_key": "watch_jingli",
                    "name": "静里竞品",
                    "pricing_platform_account_id": None,
                    "own_hotels": ["静里酒店"],
                },
                {
                    "group_key": "watch_quanji",
                    "name": "泉吉竞品",
                    "pricing_platform_account_id": None,
                    "own_hotels": ["泉吉酒店"],
                },
            ],
        )

        result = resolution.resolve_store_selection(store_catalog=catalog)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["reason"], "multi_store_requires_hint")

    def test_single_store_auto_resolve(self) -> None:
        catalog = [
            {
                "store_label": "澜江路泉吉",
                "ebk_watch_group_key": "ebk_quanji",
                "competitor_watch_group_key": "watch_quanji",
                "pair_complete": True,
            }
        ]
        result = resolution.resolve_store_selection(store_catalog=catalog)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["reason"], "single_pair_auto")
        self.assertEqual(result["competitor_watch_group_key"], "watch_quanji")

    def test_multi_store_requires_hint(self) -> None:
        catalog = [
            {
                "store_label": "云州静里",
                "ebk_watch_group_key": "ebk_jingli",
                "competitor_watch_group_key": "watch_jingli",
                "pair_complete": True,
            },
            {
                "store_label": "澜江路泉吉",
                "ebk_watch_group_key": "ebk_quanji",
                "competitor_watch_group_key": "watch_quanji",
                "pair_complete": True,
            },
        ]
        result = resolution.resolve_store_selection(store_catalog=catalog)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["reason"], "multi_store_requires_hint")

    def test_ebk_only_catalog_allows_single_group_auto_resolve(self) -> None:
        catalog = resolution.build_store_catalog(
            ebk_groups=[
                {
                    "group_key": "ctrip_ebooking_data_center",
                    "name": "静里 EBK 数据中心",
                    "platform_account_id": "ebk-jingli",
                }
            ],
            competitor_groups=[],
        )
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["ebk_watch_group_key"], "ctrip_ebooking_data_center")
        self.assertIsNone(catalog[0]["competitor_watch_group_key"])

        result = resolution.resolve_store_selection(store_catalog=catalog)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["reason"], "single_group_auto")
        self.assertEqual(result["ebk_watch_group_key"], "ctrip_ebooking_data_center")

    def test_name_match_resolves_store(self) -> None:
        catalog = [
            {
                "store_label": "云州静里",
                "ebk_watch_group_key": "ebk_jingli",
                "competitor_watch_group_key": "watch_jingli",
                "pair_complete": True,
                "own_hotels": ["云州静里"],
            },
            {
                "store_label": "澜江路泉吉",
                "ebk_watch_group_key": "ebk_quanji",
                "competitor_watch_group_key": "watch_quanji",
                "pair_complete": True,
                "own_hotels": ["澜江路泉吉"],
            },
        ]
        result = resolution.resolve_store_selection(store_catalog=catalog, store_hint="静里")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["competitor_watch_group_key"], "watch_jingli")

    def test_name_match_resolves_incomplete_pair_when_ebk_not_chat_bound(self) -> None:
        catalog = [
            {
                "store_label": "泉吉云州澜江路步行街高空江景酒店",
                "ebk_watch_group_key": None,
                "competitor_watch_group_key": "default_competitor_watch",
                "competitor_group_name": "澜江路泉吉酒店",
                "pair_complete": False,
                "own_hotels": ["泉吉云州澜江路步行街高空江景酒店"],
            },
            {
                "store_label": "静里全套房酒店（云州澜江路望川路地铁站店）",
                "ebk_watch_group_key": "ctrip_ebooking_data_center",
                "competitor_watch_group_key": "watch_1782109266413",
                "competitor_group_name": "静里全套房酒店（云州澜江路望川路地铁站店）",
                "pair_complete": False,
                "own_hotels": ["静里全套房酒店（云州澜江路望川路地铁站店）"],
            },
        ]
        result = resolution.resolve_store_selection(store_catalog=catalog, store_hint="静里")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["reason"], "name_match")
        self.assertEqual(result["competitor_watch_group_key"], "watch_1782109266413")
        self.assertEqual(result["ebk_watch_group_key"], "ctrip_ebooking_data_center")

    def test_name_not_found_lists_store_catalog_candidates_when_no_complete_pairs(self) -> None:
        catalog = [
            {
                "store_label": "静里全套房酒店（云州澜江路望川路地铁站店）",
                "ebk_watch_group_key": None,
                "competitor_watch_group_key": "watch_1782109266413",
                "pair_complete": False,
                "own_hotels": ["静里全套房酒店（云州澜江路望川路地铁站店）"],
            },
        ]
        result = resolution.resolve_store_selection(store_catalog=catalog, store_hint="泉吉")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["reason"], "name_not_found")
        self.assertEqual(len(result["store_candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
