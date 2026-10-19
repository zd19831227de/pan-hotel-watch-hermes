from __future__ import annotations

import asyncio
import unittest


class GlobalPushShopResolutionTests(unittest.TestCase):
    """v2 §3.4 收敛：全局推送平台组解析店铺直连优先，legacy 挂载兜底。

    缺口背景：default_deps._find_mounted_group 原先只按 ctrip_watch_group_key
    挂载链解析美团/飞猪组；店铺直连（组只带 shop_key、无挂载列）时全局推送
    会误报 config_required。收敛后锚点组归属店铺时必须按 shop_key 解析该店
    平台组，锚点未归属时保持 legacy 挂载兜底（行为不变）。
    """

    def _make_anchor_group(self, group_key: str, shop_key: str | None) -> None:
        from app.domain.ctrip_ebooking_watch.repository import (
            DataCenterWatchGroupCreate,
            create_data_center_watch_group,
        )

        asyncio.run(
            create_data_center_watch_group(
                DataCenterWatchGroupCreate(
                    group_key=group_key,
                    name=f"锚点组-{group_key}",
                    shop_key=shop_key,
                )
            )
        )

    def _make_meituan_group(
        self,
        group_key: str,
        *,
        shop_key: str | None = None,
        ctrip_watch_group_key: str | None = None,
    ) -> None:
        from app.domain.meituan_ebooking_watch.repository import (
            MeituanWatchGroupCreate,
            create_meituan_watch_group,
        )

        asyncio.run(
            create_meituan_watch_group(
                MeituanWatchGroupCreate(
                    group_key=group_key,
                    name=f"美团组-{group_key}",
                    shop_key=shop_key,
                    ctrip_watch_group_key=ctrip_watch_group_key,
                )
            )
        )

    def test_shop_direct_group_resolves_when_mount_column_empty(self) -> None:
        """店铺直连组（无挂载列）必须可被解析——这正是被补的缺口。"""
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("直连推送店")
            self._make_anchor_group("gp_anchor_direct", shop_key=shop)
            self._make_meituan_group("gp_mt_direct", shop_key=shop)

            from app.domain.ebk_global_push.service import (
                resolve_mounted_platform_group,
            )

            runtime = asyncio.run(
                resolve_mounted_platform_group("meituan", "gp_anchor_direct")
            )
            self.assertIsNotNone(runtime)
            self.assertEqual(getattr(runtime, "group_key", None), "gp_mt_direct")

    def test_dual_binding_group_resolves_via_shop_path(self) -> None:
        """真实存量形态：组同时带 shop_key 与挂载列（迁移过渡期）→ 店铺路径可解析。

        挂载侧一致性守卫保证「挂他店组」不可能存在，因此这里只验证共存形态。
        """
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("双绑推送店")
            self._make_anchor_group("gp_anchor_dual", shop_key=shop)
            self._make_meituan_group(
                "gp_mt_dual",
                shop_key=shop,
                ctrip_watch_group_key="gp_anchor_dual",
            )

            from app.domain.ebk_global_push.service import (
                resolve_mounted_platform_group,
            )

            runtime = asyncio.run(
                resolve_mounted_platform_group("meituan", "gp_anchor_dual")
            )
            self.assertIsNotNone(runtime)
            self.assertEqual(getattr(runtime, "group_key", None), "gp_mt_dual")

    def test_legacy_mount_fallback_when_anchor_has_no_shop(self) -> None:
        """锚点组未归属店铺时保持旧行为：按挂载列解析。

        v2 创建契约已不允许无店铺挂载组建新，这里用直插构造存量/生产库的
        历史行形态（迁移期兜底正是为它们保留的）。
        """
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            self._make_anchor_group("gp_anchor_legacy", shop_key=None)

            from app.db.session import session_scope
            from app.domain.meituan_ebooking_watch.models import (
                MeituanEbookingWatchGroup,
            )

            async def seed_legacy_mount():
                async with session_scope() as session:
                    session.add(
                        MeituanEbookingWatchGroup(
                            group_key="gp_mt_mounted",
                            name="旧挂载组",
                            platform_account_id="legacy_acct",
                            ctrip_watch_group_key="gp_anchor_legacy",
                        )
                    )

            asyncio.run(seed_legacy_mount())

            from app.domain.ebk_global_push.service import (
                resolve_mounted_platform_group,
            )

            runtime = asyncio.run(
                resolve_mounted_platform_group("meituan", "gp_anchor_legacy")
            )
            self.assertIsNotNone(runtime)
            self.assertEqual(getattr(runtime, "group_key", None), "gp_mt_mounted")

    def test_fliggy_uses_same_shop_direct_resolution(self) -> None:
        """飞猪走同一解析路径（镜像契约）。"""
        from tests._db_isolation import isolated_test_db

        with isolated_test_db():
            from tests._db_isolation import create_isolated_test_shop

            shop = create_isolated_test_shop("飞猪直连店")
            self._make_anchor_group("gp_anchor_fliggy", shop_key=shop)
            from app.domain.fliggy_ebooking_watch.repository import (
                FliggyWatchGroupCreate,
                create_fliggy_watch_group,
            )

            asyncio.run(
                create_fliggy_watch_group(
                    FliggyWatchGroupCreate(
                        group_key="gp_fg_direct",
                        name="飞猪直连组",
                        shop_key=shop,
                    )
                )
            )

            from app.domain.ebk_global_push.service import (
                resolve_mounted_platform_group,
            )

            runtime = asyncio.run(
                resolve_mounted_platform_group("fliggy", "gp_anchor_fliggy")
            )
            self.assertIsNotNone(runtime)
            self.assertEqual(getattr(runtime, "group_key", None), "gp_fg_direct")


if __name__ == "__main__":
    unittest.main()
