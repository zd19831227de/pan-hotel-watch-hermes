"""评价管理「监控组=店铺、组下三子平台」解析层测试。

参照 EBK 挂载模型（commit 31316c9）：携程监控组为店铺锚点；美团/飞猪 EBK
监控组通过 ctrip_watch_group_key 挂在携程组下、各自带浏览器账号、飞书群继承
携程组。本模块锁定：
- resolve_store_browser_session 按 (监控组, 平台) 解析出对应浏览器账号；
- 未挂载/未绑浏览器的子平台返回 None（前端禁用、不静默回落默认端口）；
- list_review_groups 列出全部携程监控组（含未绑浏览器的组，修复「只识别两个」）
  并带三平台挂载状态。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock


def _ctrip_group(group_key, *, browser="ctrip_sess", feishu="oc_group", name="G", shop_key=None):
    return {
        "id": 1,
        "group_key": group_key,
        "name": name,
        "browser_session_id": browser,
        "shop_key": shop_key,
        "feishu_chat_id": feishu,
        "feishu_chat_id_configured": bool(feishu),
        "feishu_chat_id_masked": "oc_***",
        "platform_account_id": "ctrip_acct",
    }


def _meituan_runtime(browser="mt_sess"):
    return SimpleNamespace(
        group_key="mt_g",
        name="美团挂载",
        platform_account_id="mt_acct",
        browser_session_id=browser,
        ctrip_watch_group_key="G1",
    )


def _fliggy_runtime(browser="fl_sess"):
    return SimpleNamespace(
        group_key="fl_g",
        name="飞猪挂载",
        platform_account_id="fl_acct",
        browser_session_id=browser,
        ctrip_watch_group_key="G1",
    )


class StoreResolutionTests(unittest.TestCase):
    def test_resolve_ctrip_uses_group_browser(self) -> None:
        from app.domain.review_management import service as svc

        with mock.patch(
            "app.domain.ctrip_ebooking_watch.repository.list_data_center_watch_group_summaries",
            new=mock.AsyncMock(return_value=[_ctrip_group("G1")]),
        ):
            session, feishu = asyncio.run(
                svc.resolve_store_browser_session("G1", "ctrip")
            )
        self.assertEqual(session, "ctrip_sess")
        self.assertEqual(feishu, "oc_group")

    def test_resolve_meituan_via_anchor(self) -> None:
        from app.domain.review_management import service as svc

        with mock.patch(
            "app.domain.ctrip_ebooking_watch.repository.list_data_center_watch_group_summaries",
            new=mock.AsyncMock(return_value=[_ctrip_group("G1")]),
        ), mock.patch(
            "app.domain.meituan_ebooking_watch.repository.get_meituan_watch_group_runtime_by_ctrip_anchor",
            new=mock.AsyncMock(return_value=_meituan_runtime("mt_sess")),
        ):
            session, feishu = asyncio.run(
                svc.resolve_store_browser_session("G1", "meituan")
            )
        self.assertEqual(session, "mt_sess")
        self.assertEqual(feishu, "oc_group")  # 继承携程组

    def test_resolve_unmounted_subplatform_returns_none(self) -> None:
        from app.domain.review_management import service as svc

        with mock.patch(
            "app.domain.ctrip_ebooking_watch.repository.list_data_center_watch_group_summaries",
            new=mock.AsyncMock(return_value=[_ctrip_group("G1")]),
        ), mock.patch(
            "app.domain.fliggy_ebooking_watch.repository.get_fliggy_watch_group_runtime_by_ctrip_anchor",
            new=mock.AsyncMock(return_value=None),
        ):
            session, feishu = asyncio.run(
                svc.resolve_store_browser_session("G1", "fliggy")
            )
        self.assertIsNone(session)  # 未挂载：不回落默认端口，前端禁用
        self.assertEqual(feishu, "oc_group")

    def test_resolve_ctrip_group_without_browser_returns_none(self) -> None:
        from app.domain.review_management import service as svc

        with mock.patch(
            "app.domain.ctrip_ebooking_watch.repository.list_data_center_watch_group_summaries",
            new=mock.AsyncMock(return_value=[_ctrip_group("G2", browser=None, feishu=None)]),
        ):
            session, feishu = asyncio.run(
                svc.resolve_store_browser_session("G2", "ctrip")
            )
        self.assertIsNone(session)
        self.assertIsNone(feishu)


class ListReviewGroupsTests(unittest.TestCase):
    def test_lists_shops_first_with_legacy_unassigned_ctrip_groups(self) -> None:
        from app.domain.review_management import service as svc

        groups = [
            _ctrip_group("G1", browser="ctrip_sess", feishu="oc_g1", name="本店", shop_key="shop_a"),
            _ctrip_group("G2", browser=None, feishu=None, name="未配置组"),
        ]

        async def fake_ctrip(*, include_secrets=False):
            return list(groups)

        async def fake_list_shops():
            return [
                {
                    "shop_key": "shop_a",
                    "name": "A店铺",
                    "short_name": None,
                    "sort_order": 0,
                }
            ]

        mt_session = SimpleNamespace(
            platform="meituan",
            intended_use="ebk",
            shop_key="shop_a",
            browser_session_id="mt_sess_shop",
            platform_account_id="mt_acct",
            display_name="mt",
        )

        with mock.patch(
            "app.domain.ctrip_ebooking_watch.repository.list_data_center_watch_group_summaries",
            new=mock.AsyncMock(side_effect=fake_ctrip),
        ), mock.patch(
            "app.domain.meituan_ebooking_watch.repository.get_meituan_watch_group_runtime_by_ctrip_anchor",
            new=mock.AsyncMock(return_value=_meituan_runtime("mt_sess")),
        ), mock.patch(
            "app.domain.fliggy_ebooking_watch.repository.get_fliggy_watch_group_runtime_by_ctrip_anchor",
            new=mock.AsyncMock(return_value=None),
        ), mock.patch(
            "app.domain.shops.service.list_shops",
            new=mock.AsyncMock(side_effect=fake_list_shops),
        ), mock.patch(
            "app.domain.browser.sessions.get_browser_session_store"
        ) as fake_store:
            fake_store.return_value.list.return_value = [mt_session]
            result = asyncio.run(svc.list_review_groups())

        # v2 §2：店铺条目在前（group_key=shop_key，账号级平台绑定事实）。
        self.assertEqual([g["group_key"] for g in result], ["shop_a", "G2"])
        shop_item = result[0]
        self.assertEqual(shop_item["name"], "A店铺")
        self.assertEqual(shop_item["ctrip_watch_group_key"], "G1")
        self.assertTrue(shop_item["feishu_chat_id_configured"])
        self.assertFalse(shop_item["platforms"]["ctrip"]["mounted"])
        self.assertTrue(shop_item["platforms"]["meituan"]["mounted"])
        self.assertEqual(
            shop_item["platforms"]["meituan"]["browser_session_id"], "mt_sess_shop"
        )
        self.assertFalse(shop_item["platforms"]["fliggy"]["mounted"])
        # 无店铺归属的旧携程组保留为 legacy 条目（旧挂载语义，不静默丢失）。
        legacy = result[1]
        self.assertIsNone(legacy["shop_key"])
        self.assertIn("未归属店铺", legacy["name"])
        self.assertFalse(legacy["platforms"]["ctrip"]["mounted"])
        self.assertFalse(legacy["platforms"]["fliggy"]["mounted"])


if __name__ == "__main__":
    unittest.main()
