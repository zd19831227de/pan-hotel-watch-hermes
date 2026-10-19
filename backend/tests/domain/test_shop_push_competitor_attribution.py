from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class _isolated_env:
    def __enter__(self) -> "_isolated_env":
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
        }
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'p5.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        from app.core.settings import get_settings

        get_settings.cache_clear()
        self._rebind()
        return self

    @staticmethod
    def _rebind() -> None:
        import app.db.session as db_session
        from app.core.settings import get_settings

        db_session.settings = get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine,
            expire_on_commit=False,
        )
        importlib.reload(db_session)
        asyncio.run(db_session.ensure_db_ready())

    def __exit__(self, *args) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from app.core.settings import get_settings

        get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()

    def make_shop(self, name: str) -> str:
        from tests._db_isolation import create_isolated_test_shop

        return create_isolated_test_shop(name)


class ShopPushCompetitorAttributionTests(unittest.TestCase):
    def test_migration_dict_covers_both_dialect_branches(self) -> None:
        """真实库迁移踩坑复盘：shop_key 只进了 mysql 分支，sqlite 真实库漏补列
        （测试走 create_all 建表测不出 ALTER 字典缺口）。两个分支都必须含新列。"""
        from app.domain.competitor_prices.migrations import _watch_group_columns

        for dialect in ("sqlite", "mysql", "mariadb"):
            self.assertIn(
                "shop_key",
                _watch_group_columns(dialect),
                f"{dialect} 分支缺 shop_key 列定义",
            )

    def test_competitor_group_shop_attribution_and_tree_count(self) -> None:
        with _isolated_env() as env:
            shop = env.make_shop("竞品归属店")

            async def scenario() -> dict:
                from app.domain.competitor_prices.repository import (
                    CompetitorWatchGroupCreate,
                    CompetitorWatchGroupPatch,
                    create_competitor_watch_group,
                    update_competitor_watch_group,
                )
                from app.domain.shops import service as shops_service

                created = await create_competitor_watch_group(
                    CompetitorWatchGroupCreate(
                        group_key="comp_g1",
                        name="竞品组一",
                        platform_account_id="comp_acc",
                        shop_key=shop,
                    )
                )
                assert created is not None
                missing = None
                try:
                    await create_competitor_watch_group(
                        CompetitorWatchGroupCreate(
                            group_key="comp_g2",
                            name="竞品组二",
                            platform_account_id="comp_acc2",
                            shop_key="shop_missing",
                        )
                    )
                except ValueError as exc:
                    missing = str(exc)
                patched = await update_competitor_watch_group(
                    watch_group_key="comp_g1", patch=CompetitorWatchGroupPatch(shop_key="")
                )
                assert patched is not None
                again = await update_competitor_watch_group(
                    watch_group_key="comp_g1",
                    patch=CompetitorWatchGroupPatch(shop_key=shop),
                )
                assert again is not None
                # 第二个归属同店的竞品组：允许（竞品组是全局对象，shop 只是归属维度）。
                second = await create_competitor_watch_group(
                    CompetitorWatchGroupCreate(
                        group_key="comp_g3",
                        name="竞品组三",
                        platform_account_id="comp_acc3",
                        shop_key=shop,
                    )
                )
                assert second is not None
                tree = await shops_service.list_shop_tree()
                node = next(n for n in tree["shops"] if n["shop_key"] == shop)
                return {
                    "created": created["shop_key"],
                    "missing": missing,
                    "cleared": patched["shop_key"],
                    "restored": again["shop_key"],
                    "count": node["competitor_group_count"],
                    "keys": sorted(node["competitor_group_keys"]),
                }

            result = asyncio.run(scenario())

        self.assertEqual(result["created"], shop)
        self.assertIn("店铺不存在", result["missing"] or "")
        self.assertIsNone(result["cleared"])
        self.assertEqual(result["restored"], shop)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["keys"], ["comp_g1", "comp_g3"])

    def test_push_config_derives_shop_key_from_anchor(self) -> None:
        with _isolated_env() as env:
            shop_a = env.make_shop("推送甲店")

            async def scenario() -> dict:
                from app.domain.ctrip_ebooking_watch.repository import (
                    DataCenterWatchGroupCreate,
                    DataCenterWatchGroupPatch,
                    create_data_center_watch_group,
                    update_data_center_watch_group,
                )
                from app.domain.ebk_global_push.repository import (
                    EbkGlobalPushConfigCreate,
                    EbkGlobalPushConfigPatch,
                    create_global_push_config,
                    list_global_push_configs,
                    update_global_push_config,
                )

                anchor = await create_data_center_watch_group(
                    DataCenterWatchGroupCreate(
                        group_key="push_anchor",
                        name="推送锚点",
                        platform_account_id="push_acc",
                        shop_key=shop_a,
                    )
                )
                assert anchor is not None
                created = await create_global_push_config(
                    EbkGlobalPushConfigCreate(
                        ctrip_watch_group_key="push_anchor",
                        name="推送配置",
                        enabled_platforms=["ctrip"],
                        schedule_mode="fixed_slots",
                        push_times=["09:00"],
                    )
                )
                assert created is not None

                # 锚点解绑店铺后，任意字段更新都会重新派生 shop_key（权威源=锚点组）。
                await update_data_center_watch_group(
                    watch_group_key="push_anchor",
                    patch=DataCenterWatchGroupPatch(shop_key=""),
                )
                updated = await update_global_push_config(
                    created["id"], EbkGlobalPushConfigPatch(name="推送配置改名")
                )
                assert updated is not None

                other = await create_data_center_watch_group(
                    DataCenterWatchGroupCreate(
                        group_key="push_anchor_free",
                        name="无店铺锚点",
                        platform_account_id="push_acc2",
                    )
                )
                assert other is not None
                free = await create_global_push_config(
                    EbkGlobalPushConfigCreate(
                        ctrip_watch_group_key="push_anchor_free",
                        name="无店铺推送",
                        enabled_platforms=["ctrip"],
                        schedule_mode="fixed_slots",
                        push_times=["09:00"],
                    )
                )
                assert free is not None

                scoped = await list_global_push_configs(shop_key=shop_a)
                return {
                    "created": created.get("shop_key"),
                    "after_unbind": updated.get("shop_key"),
                    "free": free.get("shop_key"),
                    "scoped_ids": sorted(item["ctrip_watch_group_key"] for item in scoped),
                }

            result = asyncio.run(scenario())

        self.assertEqual(result["created"], shop_a)
        self.assertIsNone(result["after_unbind"])
        self.assertIsNone(result["free"])
        self.assertEqual(result["scoped_ids"], [])


if __name__ == "__main__":
    unittest.main()
