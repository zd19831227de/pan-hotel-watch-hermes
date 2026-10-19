from __future__ import annotations

import asyncio
import unittest


class ReviewShopResolutionTests(unittest.TestCase):
    """v2 §2：评价店铺解析 shop-first（店铺=唯一锚点）。"""

    def test_list_review_groups_is_shop_keyed_with_account_level_platforms(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop_key = create_isolated_test_shop("评价店铺A")

            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                get_browser_session_store,
            )

            get_browser_session_store().create(
                BrowserSessionCreate(
                    platform_account_id="rev_meituan_acc",
                    platform="meituan",
                    intended_use="ebk",
                    shop_key=shop_key,
                    session_port=9521,
                )
            )

            from app.domain.review_management import service as review_service

            groups = asyncio.run(review_service.list_review_groups())
            shop_item = next(g for g in groups if g.get("shop_key") == shop_key)
            self.assertEqual(shop_item["group_key"], shop_key)
            self.assertEqual(shop_item["name"], "评价店铺A")
            self.assertFalse(shop_item["platforms"]["ctrip"]["mounted"])
            self.assertTrue(shop_item["platforms"]["meituan"]["mounted"])
            self.assertEqual(
                shop_item["platforms"]["meituan"]["browser_session_id"],
                "browser-rev_meituan_acc",
            )

    def test_resolve_store_browser_session_prefers_shop_key(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop_key = create_isolated_test_shop("解析店铺B")

            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                get_browser_session_store,
            )

            get_browser_session_store().create(
                BrowserSessionCreate(
                    platform_account_id="rev_ctrip_acc",
                    platform="ctrip",
                    intended_use="ebk",
                    shop_key=shop_key,
                    session_port=9221,
                )
            )
            # 店铺携程监控组带飞书群：chat 迁移期继承自它。
            from app.domain.ctrip_ebooking_watch.repository import (
                DataCenterWatchGroupCreate,
                create_data_center_watch_group,
            )

            async def seed_anchor():
                await create_data_center_watch_group(
                    DataCenterWatchGroupCreate(
                        group_key="rev_anchor_g",
                        name="解析店铺B锚点",
                        platform_account_id="rev_ctrip_acc",
                        shop_key=shop_key,
                    )
                )
                # chat 直改行写入：走 API 会触发 Agent 群绑定校验，与本用例无关。
                from app.db.session import session_scope
                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                )
                from sqlalchemy import update

                async with session_scope() as db:
                    await db.execute(
                        update(CtripEbookingWatchGroup)
                        .where(CtripEbookingWatchGroup.group_key == "rev_anchor_g")
                        .values(feishu_chat_id="oc_rev_shop_chat")
                    )

            asyncio.run(seed_anchor())

            from app.domain.review_management import service as review_service

            session_id, chat = asyncio.run(
                review_service.resolve_store_browser_session(shop_key, "ctrip")
            )
            self.assertEqual(session_id, "browser-rev_ctrip_acc")
            self.assertEqual(chat, "oc_rev_shop_chat")
            # 未绑定该平台账号：返回 (None, chat)，不静默回落。
            session_none, _ = asyncio.run(
                review_service.resolve_store_browser_session(shop_key, "fliggy")
            )
            self.assertIsNone(session_none)


if __name__ == "__main__":
    unittest.main()
