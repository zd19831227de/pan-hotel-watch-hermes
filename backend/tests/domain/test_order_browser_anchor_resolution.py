"""chat 定位的订单/周月读取账号解析：平台组直绑 → 携程锚点链，不跨店回落。

2026-09-16 立瑞群美团/飞猪问答取到静里账号（或 not_ready）的根因修复配套：
- 监控组未挂 chat 时，应沿「chat → 携程 EBK 组 → 挂载平台组」的推送同款
  链路解析店铺账号；
- chat 给定但整条链解析不到时必须返回 None（not_ready），绝不能回落到
  「全部组的第一个」（跨店读错数据）。
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch


_CTIRP_JINGLI_GROUP = {
    "group_key": "ctrip_ebooking_data_center",
    "name": "静里EBK数据中心监控",
    "platform_account_id": "15900000001",
    "browser_session_id": "browser-15900000001",
}

_CTIRP_LIRUI_GROUP = {
    "group_key": "ebk_1789094672287",
    "name": "云滨立瑞EBK数据中心监控",
    "platform_account_id": "liruiota",
    "browser_session_id": "browser-liruiota",
}

_MEITUAN_GROUPS = [
    {
        "group_key": "meituan_ebooking_watch",
        "name": "静里美团 EBK 监控组",
        "platform_account_id": "JL1700000001",
        "browser_session_id": "browser-JL1700000001",
        "feishu_chat_id": None,
        "ctrip_watch_group_key": "ctrip_ebooking_data_center",
    },
    {
        "group_key": "meituan_ebooking_watch1",
        "name": "云滨立瑞美团 EBK 监控组",
        "platform_account_id": "lirui",
        "browser_session_id": "browser-lirui",
        "feishu_chat_id": None,
        "ctrip_watch_group_key": "ebk_1789094672287",
    },
]

_FLIGGY_GROUPS = [
    {
        "group_key": "fliggy_ebooking_watch",
        "name": "静里飞猪 EBK 监控组",
        "platform_account_id": "hotel1144352",
        "browser_session_id": "browser-hotel1144352",
        "feishu_chat_id": None,
        "ctrip_watch_group_key": "ctrip_ebooking_data_center",
    },
    {
        "group_key": "fliggy_ebooking_watch1",
        "name": "云滨立瑞飞猪 EBK 监控组",
        "platform_account_id": "hotel1100001",
        "browser_session_id": "browser-hotel1100001",
        "feishu_chat_id": None,
        "ctrip_watch_group_key": "ebk_1789094672287",
    },
]


def _patch_repositories(ctrip_by_chat, ctrip_summaries=None):
    """按线上仓储行为打桩：ctrip 按 chat 过滤 + 美团/飞猪全量组列表。"""
    return [
        patch(
            "app.domain.ctrip_ebooking_watch.repository."
            "list_data_center_watch_groups_by_feishu_chat_id",
            new=AsyncMock(return_value=ctrip_by_chat),
        ),
        patch(
            "app.domain.meituan_ebooking_watch.repository.list_meituan_watch_groups",
            new=AsyncMock(return_value=_MEITUAN_GROUPS),
        ),
        patch(
            "app.domain.fliggy_ebooking_watch.repository.list_fliggy_watch_groups",
            new=AsyncMock(return_value=_FLIGGY_GROUPS),
        ),
    ]


class OrderBrowserAnchorResolutionTests(unittest.TestCase):
    def test_chat_scoped_meituan_resolves_via_ctrip_anchor(self) -> None:
        """立瑞群：美团组未挂 chat，但挂载在立瑞携程锚点上 → 解析到立瑞账号。"""
        from app.domain.order_browser import resolve_order_account_binding

        patches = _patch_repositories([_CTIRP_LIRUI_GROUP])
        with patches[0], patches[1], patches[2]:
            binding = asyncio.run(
                resolve_order_account_binding("meituan", chat_id="oc_lirui")
            )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["platform_account_id"], "lirui")

    def test_chat_scoped_fliggy_resolves_via_ctrip_anchor(self) -> None:
        from app.domain.order_browser import resolve_order_account_binding

        patches = _patch_repositories([_CTIRP_LIRUI_GROUP])
        with patches[0], patches[1], patches[2]:
            binding = asyncio.run(
                resolve_order_account_binding("fliggy", chat_id="oc_lirui")
            )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["platform_account_id"], "hotel1100001")

    def test_chat_scoped_ctrip_uses_directly_bound_group(self) -> None:
        from app.domain.order_browser import resolve_order_account_binding

        patches = _patch_repositories([_CTIRP_LIRUI_GROUP])
        with patches[0], patches[1], patches[2]:
            binding = asyncio.run(
                resolve_order_account_binding("ctrip", chat_id="oc_lirui")
            )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["platform_account_id"], "liruiota")

    def test_unmatched_chat_returns_none_instead_of_first_store(self) -> None:
        """群与任何店铺无绑定：返回 None（not_ready），不回落第一个店。"""
        from app.domain.order_browser import resolve_order_account_binding

        patches = _patch_repositories([])
        with patches[0], patches[1], patches[2]:
            for platform in ("ctrip", "meituan", "fliggy"):
                binding = asyncio.run(
                    resolve_order_account_binding(platform, chat_id="oc_stranger")
                )
                self.assertIsNone(binding, platform)

    def test_list_bindings_covers_all_stores_with_labels(self) -> None:
        """无 chat（日终快照/兜底）：按组序返回全部店铺绑定（含组名标签）。"""
        from app.domain.order_browser import list_order_account_bindings

        summaries = [_CTIRP_JINGLI_GROUP, _CTIRP_LIRUI_GROUP]
        patches = _patch_repositories(
            [],
            ctrip_summaries=summaries,
        )
        with patches[0], patches[1], patches[2], patch(
            "app.domain.ctrip_ebooking_watch.repository."
            "list_data_center_watch_group_summaries",
            new=AsyncMock(return_value=summaries),
        ):
            meituan_bindings = asyncio.run(list_order_account_bindings("meituan"))
        self.assertEqual(
            [b["platform_account_id"] for b in meituan_bindings],
            ["JL1700000001", "lirui"],
        )
        self.assertEqual(meituan_bindings[1]["label"], "云滨立瑞美团 EBK 监控组")


if __name__ == "__main__":
    unittest.main()
