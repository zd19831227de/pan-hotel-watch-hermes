from __future__ import annotations

import os
import unittest

from client.watch_groups import (
    build_watch_group_catalog,
    guard_watch_group_kind,
    normalize_competitor_watch_group_key,
    normalize_ebk_watch_group_key,
    prepare_watch_group_key,
    WATCH_KIND_COMPETITOR,
    WATCH_KIND_EBK,
)


class WatchGroupKindTests(unittest.TestCase):
    def test_ebk_default_key(self) -> None:
        os.environ.pop("WATCH_DEFAULT_EBK_GROUP", None)
        self.assertEqual(normalize_ebk_watch_group_key(None), "ctrip_ebooking_data_center")

    def test_competitor_default_key(self) -> None:
        os.environ.pop("WATCH_DEFAULT_COMPETITOR_GROUP", None)
        self.assertEqual(normalize_competitor_watch_group_key(None), "default_competitor_watch")

    def test_rejects_competitor_key_on_ebk_tools(self) -> None:
        key, error = prepare_watch_group_key("default_competitor_watch", watch_kind=WATCH_KIND_EBK)
        self.assertIsNone(key)
        self.assertIsNotNone(error)
        self.assertEqual(error["watch_kind_expected"], "ebk")

    def test_rejects_ebk_key_on_competitor_tools(self) -> None:
        key, error = prepare_watch_group_key("ctrip_ebooking_data_center", watch_kind=WATCH_KIND_COMPETITOR)
        self.assertIsNone(key)
        self.assertIsNotNone(error)
        self.assertEqual(error["watch_kind_expected"], "competitor")

    def test_legacy_ebk_alias_still_maps(self) -> None:
        self.assertEqual(
            normalize_ebk_watch_group_key("default_data_center_watch"),
            "ctrip_ebooking_data_center",
        )
        self.assertIsNone(
            guard_watch_group_kind("default_data_center_watch", expected_kind=WATCH_KIND_EBK)
        )

    def test_catalog_splits_groups_by_kind(self) -> None:
        catalog = build_watch_group_catalog(
            ebk_groups=[{"group_key": "ctrip_ebooking_data_center", "name": "EBK", "enabled": True}],
            competitor_groups=[{"group_key": "default_competitor_watch", "name": "竞品", "enabled": True}],
        )
        self.assertEqual(catalog["defaults"]["ebk"], "ctrip_ebooking_data_center")
        self.assertEqual(catalog["defaults"]["competitor"], "default_competitor_watch")
        self.assertEqual(catalog["ebk_groups"][0]["watch_kind"], "ebk")
        self.assertEqual(catalog["competitor_groups"][0]["watch_kind"], "competitor")


if __name__ == "__main__":
    unittest.main()
