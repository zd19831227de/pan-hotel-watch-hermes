from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import learned_routes


class LearnedRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = mock.patch.object(
            learned_routes, "STORE_PATH", Path(self._tmp.name) / "learned_routes.json"
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_record_then_lookup_roundtrip(self) -> None:
        self.assertTrue(
            learned_routes.record_learned_route("看下我们店的整体表现", ["get_ebk_latest_result"])
        )
        tools = learned_routes.lookup_learned_tools("看下我们店的整体表现")
        self.assertEqual(tools, ("get_ebk_latest_result",))
        self.assertEqual(learned_routes.count_learned_routes(), 1)

    def test_lookup_normalizes_punctuation_whitespace_and_width(self) -> None:
        learned_routes.record_learned_route("看下整体表现", ["get_operator_hotel_overview"])
        for variant in ("看下整体表现。", "看下 整体表现", "看下「整体表现」！", "看下整体表现！"):
            with self.subTest(variant=variant):
                self.assertIsNotNone(learned_routes.lookup_learned_tools(variant), variant)

    def test_lookup_miss_returns_none(self) -> None:
        learned_routes.record_learned_route("看下整体表现", ["get_operator_hotel_overview"])
        self.assertIsNone(learned_routes.lookup_learned_tools("完全不同的问法"))
        # 太短（规范化后 <2 字符）不参与匹配。
        self.assertIsNone(learned_routes.lookup_learned_tools("看"))

    def test_record_repeats_increment_hits_not_entries(self) -> None:
        for _ in range(3):
            learned_routes.record_learned_route("看下整体表现", ["get_ebk_latest_result"])
        self.assertEqual(learned_routes.count_learned_routes(), 1)
        store = json.loads(learned_routes.STORE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(store[learned_routes.normalize_phrase("看下整体表现")]["hits"], 3)

    def test_record_rejects_empty_tools_or_phrase(self) -> None:
        self.assertFalse(learned_routes.record_learned_route("看下整体表现", []))
        self.assertFalse(learned_routes.record_learned_route("", ["get_ebk_latest_result"]))

    def test_disabled_switch_disables_record_and_lookup(self) -> None:
        learned_routes.record_learned_route("看下整体表现", ["get_ebk_latest_result"])
        with mock.patch.dict(os.environ, {"HERMES_LEARNED_ROUTES_DISABLED": "1"}, clear=False):
            self.assertIsNone(learned_routes.lookup_learned_tools("看下整体表现"))
            self.assertFalse(
                learned_routes.record_learned_route("另一个问法", ["get_ebk_latest_result"])
            )

    def test_corrupt_store_behaves_as_empty(self) -> None:
        learned_routes.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        learned_routes.STORE_PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNone(learned_routes.lookup_learned_tools("任意问法"))
        # 损坏后仍可写入（读失败按空库处理，不抛出）。
        self.assertTrue(learned_routes.record_learned_route("新问法", ["get_ebk_latest_result"]))

    def test_eviction_keeps_recent_and_frequent_entries(self) -> None:
        for index in range(learned_routes._MAX_ENTRIES):
            learned_routes.record_learned_route(f"问法{index}", ["get_ebk_latest_result"])
        # 挤出一条高频条目后再写：淘汰的是低频老条目。
        for _ in range(5):
            learned_routes.record_learned_route("问法0", ["get_ebk_latest_result"])
        learned_routes.record_learned_route("新来的问法", ["get_ebk_latest_result"])
        self.assertEqual(learned_routes.count_learned_routes(), learned_routes._MAX_ENTRIES)
        self.assertIsNotNone(learned_routes.lookup_learned_tools("问法0"))
        self.assertIsNotNone(learned_routes.lookup_learned_tools("新来的问法"))


if __name__ == "__main__":
    unittest.main()
