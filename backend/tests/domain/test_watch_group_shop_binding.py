from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path


class _isolated_env:
    def __enter__(self) -> "_isolated_env":
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in (
                "BROWSER_STATE_DIR",
                "DATABASE_URL",
                "DATABASE_AUTO_CREATE_TABLES",
            )
        }
        os.environ["BROWSER_STATE_DIR"] = self._tmp.name
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'groups.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        from app.core.settings import get_settings

        get_settings.cache_clear()
        # 进圈即重绑全局引擎到临时库：否则会沿用上一用例的引擎（甚至写真实库）。
        db_session = _reload_db()
        db_session.settings = get_settings()
        return self

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


def _reload_db():
    import importlib

    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(
        os.environ["DATABASE_URL"], pool_pre_ping=True
    )
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


def _make_shop(name: str) -> str:
    from tests._db_isolation import create_isolated_test_shop

    return create_isolated_test_shop(name)


async def _create_group(group_key: str, *, shop_key: str | None = None, account: str = "acc_g", browser_session_id: str | None = None):
    from app.domain.ctrip_ebooking_watch.repository import (
        DataCenterWatchGroupCreate,
        create_data_center_watch_group,
    )

    return await create_data_center_watch_group(
        DataCenterWatchGroupCreate(
            group_key=group_key,
            name=group_key,
            platform_account_id=account,
            browser_session_id=browser_session_id,
            shop_key=shop_key,
        )
    )


class WatchGroupShopBindingTests(unittest.TestCase):
    def test_legacy_group_table_gets_shop_key_column_and_unique_index(self) -> None:
        with _isolated_env():
            async def scenario() -> tuple[set[str], list[dict]]:
                db_session = _reload_db()
                async with db_session.engine.begin() as connection:
                    from sqlalchemy import inspect, text

                    # 造旧结构表（无 shop_key）后由 ensure 幂等补列 + 建唯一索引。
                    await connection.execute(
                        text(
                            """
                            CREATE TABLE ctrip_ebooking_watch_groups (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                group_key VARCHAR(64) NOT NULL,
                                name VARCHAR(128) NOT NULL,
                                platform_account_id VARCHAR(128) NOT NULL,
                                timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
                                retention_days INTEGER NOT NULL DEFAULT 14,
                                query_interval_minutes INTEGER NOT NULL DEFAULT 60,
                                auto_poll_enabled BOOLEAN NOT NULL DEFAULT 0,
                                daily_brief_push_time VARCHAR(5) NOT NULL DEFAULT '08:30',
                                enabled BOOLEAN NOT NULL DEFAULT 1,
                                created_at DATETIME NOT NULL,
                                updated_at DATETIME NOT NULL
                            )
                            """
                        )
                    )
                    from app.domain.ctrip_ebooking_watch.migrations import (
                        ensure_data_center_watch_schema,
                    )

                    await ensure_data_center_watch_schema(connection)
                    await ensure_data_center_watch_schema(connection)

                    def inspect_table(sync_connection):
                        inspector = inspect(sync_connection)
                        columns = {
                            column["name"]
                            for column in inspector.get_columns(
                                "ctrip_ebooking_watch_groups"
                            )
                        }
                        indexes = [
                            {
                                "name": index["name"],
                                "columns": list(index["column_names"]),
                                "unique": bool(index["unique"]),
                            }
                            for index in inspector.get_indexes(
                                "ctrip_ebooking_watch_groups"
                            )
                        ]
                        return columns, indexes

                    return await connection.run_sync(inspect_table)

            columns, indexes = asyncio.run(scenario())

        self.assertIn("shop_key", columns)
        self.assertTrue(
            any(
                index["unique"]
                and "shop_key" in index["columns"]
                for index in indexes
            ),
            f"shop_key 需唯一索引，实际：{indexes}",
        )

    def test_create_validates_existence_conflict_and_account_consistency(self) -> None:
        with _isolated_env():
            _reload_db()
            import asyncio as _asyncio

            _asyncio.run(_reload_db().ensure_db_ready())
            shop_a = _make_shop("泉吉")
            shop_b = _make_shop("曼馨")

            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                get_browser_session_store,
            )

            store = get_browser_session_store()
            session_a = store.create(
                BrowserSessionCreate(
                    platform_account_id="acc_in_shop_a",
                    platform="ctrip",
                    intended_use="ebk",
                    shop_key=shop_a,
                    session_port=9381,
                    browser_session_id="browser-acc_in_shop_a",
                )
            )

            async def scenario() -> None:
                # 一致绑定：组直接绑定归属 shop_a 的会话（账号归属=组归属）。
                first = await _create_group(
                    "g1",
                    shop_key=shop_a,
                    browser_session_id=session_a.browser_session_id,
                )
                assert first is not None
                self.assertEqual(first["shop_key"], shop_a)
                self.assertEqual(first["platform_account_id"], "acc_in_shop_a")

                with self.assertRaises(ValueError) as ctx_dup:
                    await _create_group("g2", shop_key=shop_a, account="acc_g2")
                self.assertIn("已绑定", str(ctx_dup.exception))

                with self.assertRaises(ValueError) as ctx_missing:
                    await _create_group("g3", shop_key="shop_missing")
                self.assertIn("店铺不存在", str(ctx_missing.exception))

                with self.assertRaises(ValueError) as ctx_mismatch:
                    await _create_group(
                        "g4",
                        shop_key=shop_b,
                        browser_session_id=session_a.browser_session_id,
                    )
                self.assertIn("不一致", str(ctx_mismatch.exception))

            asyncio.run(scenario())

    def test_patch_moves_conflicts_and_clears(self) -> None:
        with _isolated_env():
            _reload_db()
            import asyncio as _asyncio

            _asyncio.run(_reload_db().ensure_db_ready())
            shop_a = _make_shop("甲店")
            shop_b = _make_shop("乙店")

            async def scenario() -> None:
                from app.domain.ctrip_ebooking_watch.repository import (
                    DataCenterWatchGroupPatch,
                    update_data_center_watch_group,
                )

                await _create_group("ga", shop_key=shop_a, account="acc_a")
                await _create_group("gb", shop_key=shop_b, account="acc_b")

                with self.assertRaises(ValueError):
                    await update_data_center_watch_group(
                        watch_group_key="ga",
                        patch=DataCenterWatchGroupPatch(shop_key=shop_b),
                    )

                cleared = await update_data_center_watch_group(
                    watch_group_key="ga",
                    patch=DataCenterWatchGroupPatch(shop_key=""),
                )
                assert cleared is not None
                self.assertIsNone(cleared["shop_key"])

                moved = await update_data_center_watch_group(
                    watch_group_key="gb",
                    patch=DataCenterWatchGroupPatch(shop_key=shop_a),
                )
                assert moved is not None
                self.assertEqual(moved["shop_key"], shop_a)

            asyncio.run(scenario())

    def test_tree_reports_group_level_bindings(self) -> None:
        with _isolated_env():
            _reload_db()
            import asyncio as _asyncio

            _asyncio.run(_reload_db().ensure_db_ready())
            shop = _make_shop("树测试店")

            async def scenario() -> dict:
                from app.db.session import session_scope

                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                )
                from app.domain.meituan_ebooking_watch.models import (
                    MeituanEbookingWatchGroup,
                )
                from app.domain.shops import service as shops_service

                async with session_scope() as session:
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="anchor_g",
                            name="主携程组",
                            platform_account_id="anchor_acc",
                            shop_key=shop,
                        )
                    )
                    # v2 §2：美团组按自身 shop_key 直连；纯挂载（无 shop_key）
                    # 的旧组不进店铺节点，归未分组区。
                    session.add(
                        MeituanEbookingWatchGroup(
                            group_key="m_direct",
                            name="美团直连组",
                            platform_account_id="m_acc",
                            shop_key=shop,
                            enabled=True,
                        )
                    )
                    session.add(
                        MeituanEbookingWatchGroup(
                            group_key="m_mount_legacy",
                            name="美团旧挂载组",
                            platform_account_id="m_acc2",
                            ctrip_watch_group_key="anchor_g",
                            enabled=True,
                        )
                    )
                    await session.flush()
                tree = await shops_service.list_shop_tree()
                return tree

            tree = asyncio.run(scenario())
            node = next(n for n in tree["shops"] if n["shop_key"] == shop)
            self.assertEqual(node["bindings"]["ctrip"]["status"], "resolved")
            self.assertEqual(node["bindings"]["ctrip"]["group_key"], "anchor_g")
            self.assertEqual(node["bindings"]["ctrip"]["platform_account_id"], "anchor_acc")
            self.assertEqual(node["bindings"]["meituan"]["status"], "resolved")
            self.assertEqual(node["bindings"]["meituan"]["group_key"], "m_direct")
            self.assertNotIn("mounted", node["bindings"]["meituan"])
            self.assertEqual(node["bindings"]["fliggy"]["status"], "binding_required")
            unassigned_keys = tree["unassigned"]["watch_group_keys"]
            self.assertIn("m_mount_legacy", unassigned_keys)
            self.assertNotIn("m_direct", unassigned_keys)

    def test_delete_shop_blocked_by_group_and_account_references(self) -> None:
        with _isolated_env():
            _reload_db()
            import asyncio as _asyncio

            _asyncio.run(_reload_db().ensure_db_ready())
            shop = _make_shop("引用测试店")

            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                get_browser_session_store,
            )

            get_browser_session_store().create(
                BrowserSessionCreate(
                    platform_account_id="acc_ref",
                    platform="ctrip",
                    intended_use="ebk",
                    shop_key=shop,
                    session_port=9382,
                    browser_session_id="browser-acc_ref",
                )
            )

            async def scenario() -> list[str]:
                from app.db.session import session_scope

                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                )
                from app.domain.shops import service as shops_service

                async with session_scope() as session:
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="ref_g",
                            name="引用组",
                            platform_account_id="ref_acc",
                            shop_key=shop,
                        )
                    )
                    await session.flush()
                return await shops_service.collect_shop_references(shop)

            references = asyncio.run(scenario())
        self.assertTrue(any("ref_g" in ref for ref in references))
        self.assertTrue(any("acc_ref" in ref for ref in references))


if __name__ == "__main__":
    unittest.main()
