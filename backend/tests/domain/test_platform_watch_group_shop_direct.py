from __future__ import annotations

import asyncio
import unittest


class PlatformWatchGroupShopDirectTests(unittest.TestCase):
    """v2 §2：美团/飞猪监控组店铺直连契约（新建不再先配携程组）。"""

    def test_create_meituan_group_with_shop_key_only(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("直连美团店")

            from app.domain.meituan_ebooking_watch.repository import (
                MeituanWatchGroupCreate,
                create_meituan_watch_group,
            )

            async def scenario():
                return await create_meituan_watch_group(
                    MeituanWatchGroupCreate(
                        group_key="mt_direct_g",
                        name="美团直连组",
                        shop_key=shop,
                    )
                )

            group = asyncio.run(scenario())
            self.assertIsNotNone(group)
            self.assertEqual(group["shop_key"], shop)
            self.assertIsNone(group["ctrip_watch_group_key"])

    def test_create_meituan_group_requires_shop_or_legacy_anchor(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from app.domain.meituan_ebooking_watch.repository import (
                MeituanWatchGroupCreate,
                create_meituan_watch_group,
            )

            async def scenario():
                return await create_meituan_watch_group(
                    MeituanWatchGroupCreate(
                        group_key="mt_orphan_g",
                        name="无归属组",
                    )
                )

            with self.assertRaises(ValueError) as ctx:
                asyncio.run(scenario())
            self.assertIn("必须归属一个店铺", str(ctx.exception))

    def test_legacy_anchor_derives_shop_key(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("派生店铺")

            from app.domain.ctrip_ebooking_watch.repository import (
                DataCenterWatchGroupCreate,
                create_data_center_watch_group,
            )
            from app.domain.fliggy_ebooking_watch.repository import (
                FliggyWatchGroupCreate,
                create_fliggy_watch_group,
            )

            async def scenario():
                await create_data_center_watch_group(
                    DataCenterWatchGroupCreate(
                        group_key="anchor_for_derive",
                        name="锚点组",
                        platform_account_id="anchor_acc_derive",
                        shop_key=shop,
                    )
                )
                return await create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="fl_derive_g",
                        name="飞猪挂载组",
                        ctrip_watch_group_key="anchor_for_derive",
                    )
                )

            group = asyncio.run(scenario())
            self.assertEqual(group["shop_key"], shop)
            self.assertEqual(group["ctrip_watch_group_key"], "anchor_for_derive")

    def test_explicit_shop_conflicting_with_anchor_shop_rejected(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop_a = create_isolated_test_shop("冲突店A")
            shop_b = create_isolated_test_shop("冲突店B")

            from app.domain.ctrip_ebooking_watch.repository import (
                DataCenterWatchGroupCreate,
                create_data_center_watch_group,
            )
            from app.domain.meituan_ebooking_watch.repository import (
                MeituanWatchGroupCreate,
                create_meituan_watch_group,
            )

            async def scenario():
                await create_data_center_watch_group(
                    DataCenterWatchGroupCreate(
                        group_key="anchor_conflict",
                        name="锚点组",
                        platform_account_id="anchor_acc_conflict",
                        shop_key=shop_a,
                    )
                )
                return await create_meituan_watch_group(
                    MeituanWatchGroupCreate(
                        group_key="mt_conflict_g",
                        name="冲突组",
                        shop_key=shop_b,
                        ctrip_watch_group_key="anchor_conflict",
                    )
                )

            with self.assertRaises(ValueError) as ctx:
                asyncio.run(scenario())
            self.assertIn("不一致", str(ctx.exception))

    def test_duplicate_shop_per_platform_rejected(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("唯一店铺")

            from app.domain.meituan_ebooking_watch.repository import (
                MeituanWatchGroupCreate,
                MeituanWatchGroupPatch,
                create_meituan_watch_group,
                update_meituan_watch_group,
            )

            async def scenario():
                first = await create_meituan_watch_group(
                    MeituanWatchGroupCreate(
                        group_key="mt_dup_first",
                        name="第一个",
                        shop_key=shop,
                    )
                )
                with self.assertRaises(ValueError) as inner:
                    await create_meituan_watch_group(
                        MeituanWatchGroupCreate(
                            group_key="mt_dup_second",
                            name="第二个",
                            shop_key=shop,
                        )
                    )
                self.assertIn("每店最多一个", str(inner.exception))
                moved = await update_meituan_watch_group(
                    watch_group_key="mt_dup_first",
                    patch=MeituanWatchGroupPatch(shop_key=""),
                )
                return first, moved

            first, moved = asyncio.run(scenario())
            self.assertEqual(first["shop_key"], shop)
            self.assertIsNone(moved["shop_key"])

    def test_delivery_prefers_own_webhook(self) -> None:
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from app.domain.fliggy_ebooking_watch.repository import (
                FliggyWatchGroupRuntime,
            )
            from app.domain.fliggy_ebooking_watch.service import _load_anchor_delivery

            runtime = FliggyWatchGroupRuntime(
                group_key="fl_own_webhook",
                name="自有webhook组",
                brief_title=None,
                platform_account_id="acc",
                browser_session_id=None,
                shop_key="shop_x",
                ctrip_watch_group_key=None,
                feishu_chat_id=None,
                feishu_webhook_url="https://example.invalid/hook",
                timezone="Asia/Shanghai",
                schedule_mode="fixed_slots",
                schedule_start_time="06:00",
                schedule_end_time="01:00",
                schedule_interval_minutes=120,
                auto_poll_enabled=True,
                enabled=True,
                last_polled_at=None,
            )

            store_name, webhook, blocked = asyncio.run(_load_anchor_delivery(runtime))
            self.assertEqual(webhook, "https://example.invalid/hook")
            self.assertEqual(blocked, {})


if __name__ == "__main__":
    unittest.main()
