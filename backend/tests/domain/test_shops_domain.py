from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "shops-watch.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        # 恢复全局 engine，避免后续测试继承指向已删临时文件的引擎。
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    return importlib.reload(db_session)


class ShopSchemaTests(unittest.TestCase):
    def test_shops_table_columns_unique_key_and_reentrant_migration(self) -> None:
        with _isolated_sqlite_database():
            async def scenario() -> tuple[set[str], list[dict]]:
                db_session = _reload_db(os.environ["DATABASE_URL"])
                await db_session.ensure_db_ready()
                from sqlalchemy import inspect

                from app.domain.shops.migrations import ensure_shops_schema

                async with db_session.engine.begin() as connection:
                    # 迁移幂等：重复执行不得报错。
                    await ensure_shops_schema(connection)
                    await ensure_shops_schema(connection)

                    def inspect_schema(sync_connection):
                        inspector = inspect(sync_connection)
                        columns = {
                            column["name"]
                            for column in inspector.get_columns("shops")
                        }
                        indexes = [
                            {
                                "name": index["name"],
                                "columns": list(index["column_names"]),
                                "unique": bool(index["unique"]),
                            }
                            for index in inspector.get_indexes("shops")
                        ]
                        return columns, indexes

                    columns, indexes = await connection.run_sync(inspect_schema)
                return columns, indexes

            columns, indexes = asyncio.run(scenario())

        self.assertTrue(
            {
                "id",
                "shop_key",
                "name",
                "short_name",
                "sort_order",
                "created_at",
                "updated_at",
            }.issubset(columns)
        )
        self.assertTrue(
            any(
                index["unique"] and "shop_key" in index["columns"]
                for index in indexes
            ),
            f"shops.shop_key 必须有唯一索引，实际索引：{indexes}",
        )


class ShopServiceCrudTests(unittest.TestCase):
    def test_create_get_update_tree_delete_full_cycle(self) -> None:
        with _isolated_sqlite_database():
            async def scenario():
                db_session = _reload_db(os.environ["DATABASE_URL"])
                await db_session.ensure_db_ready()
                from app.domain.shops import service

                created = await service.create_shop(
                    {"name": "澜江路泉吉酒店", "short_name": "泉吉", "sort_order": 5}
                )
                fetched = await service.get_shop(created["shop_key"])
                updated = await service.update_shop(
                    created["shop_key"],
                    {"name": "澜江路泉吉酒店（云州）", "sort_order": 2},
                )
                tree = await service.list_shop_tree()
                await service.delete_shop(created["shop_key"])
                return created, fetched, updated, tree

            created, fetched, updated, tree = asyncio.run(scenario())

        self.assertTrue(created["shop_key"].startswith("shop_"))
        self.assertEqual(len(created["shop_key"]), len("shop_") + 32)
        self.assertEqual(created["sort_order"], 5)
        self.assertEqual(fetched["name"], "澜江路泉吉酒店")
        self.assertEqual(updated["name"], "澜江路泉吉酒店（云州）")
        self.assertEqual(updated["sort_order"], 2)

        self.assertEqual(len(tree["shops"]), 1)
        node = tree["shops"][0]
        self.assertEqual(node["shop_key"], created["shop_key"])
        self.assertEqual(
            set(node["bindings"]), {"ctrip", "meituan", "fliggy"}
        )
        self.assertTrue(
            all(
                binding["status"] == "binding_required"
                for binding in node["bindings"].values()
            )
        )
        self.assertEqual(node["competitor_group_count"], 0)
        self.assertIn("unassigned", tree)

    def test_duplicate_display_names_allowed_and_sort_default_zero(self) -> None:
        with _isolated_sqlite_database():
            async def scenario():
                _reload_db(os.environ["DATABASE_URL"])
                import app.db.session as db_session

                await db_session.ensure_db_ready()
                from app.domain.shops import service

                first = await service.create_shop({"name": "曼馨酒店"})
                second = await service.create_shop({"name": "曼馨酒店"})
                return first, second

            first, second = asyncio.run(scenario())

        self.assertNotEqual(first["shop_key"], second["shop_key"])
        self.assertEqual(first["sort_order"], 0)
        self.assertEqual(second["sort_order"], 0)

    def test_unknown_shop_raises_not_found(self) -> None:
        with _isolated_sqlite_database():
            async def scenario():
                _reload_db(os.environ["DATABASE_URL"])
                import app.db.session as db_session

                await db_session.ensure_db_ready()
                from app.domain.shops import service

                await service.get_shop("shop_missing")
                await service.update_shop("shop_missing", {"name": "x"})
                await service.delete_shop("shop_missing")

            with self.assertRaises(Exception) as ctx:
                asyncio.run(scenario())

        self.assertIn("ShopNotFound", type(ctx.exception).__name__)


class ShopApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            key: os.environ.get(key)
            for key in ("ENVIRONMENT", "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY")
        }
        os.environ.pop("ENVIRONMENT", None)
        os.environ.pop("PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY", None)
        from app.core.settings import get_settings

        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from app.core.settings import get_settings

        get_settings.cache_clear()

    def test_crud_and_tree_over_http(self) -> None:
        with _isolated_sqlite_database():
            async def prep() -> None:
                db_session = _reload_db(os.environ["DATABASE_URL"])
                await db_session.ensure_db_ready()

            asyncio.run(prep())
            from app.main import app

            client = TestClient(app)

            created = client.post(
                "/api/v1/shops", json={"name": "曼馨酒店", "short_name": "曼馨"}
            )
            self.assertEqual(created.status_code, 200, created.text)
            shop = created.json()["shop"]
            self.assertTrue(shop["shop_key"].startswith("shop_"))

            tree = client.get("/api/v1/shops/tree")
            self.assertEqual(tree.status_code, 200, tree.text)
            self.assertEqual(tree.json()["shops"][0]["shop_key"], shop["shop_key"])

            patched = client.patch(
                f"/api/v1/shops/{shop['shop_key']}", json={"short_name": "曼馨64"}
            )
            self.assertEqual(patched.status_code, 200, patched.text)

            detail = client.get(f"/api/v1/shops/{shop['shop_key']}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["shop"]["short_name"], "曼馨64")

            invalid = client.post("/api/v1/shops", json={"name": ""})
            self.assertEqual(invalid.status_code, 422)

            missing = client.get("/api/v1/shops/shop_missing")
            self.assertEqual(missing.status_code, 404)

    def test_delete_requires_supervisor_key_even_locally(self) -> None:
        with _isolated_sqlite_database():
            async def prep() -> str:
                db_session = _reload_db(os.environ["DATABASE_URL"])
                await db_session.ensure_db_ready()
                from app.domain.shops import service

                shop = await service.create_shop({"name": "静里全套房酒店"})
                return shop["shop_key"]

            shop_key = asyncio.run(prep())
            os.environ["PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"] = "sup-key-123456"
            from app.core.settings import get_settings

            get_settings.cache_clear()
            from app.main import app

            client = TestClient(app)

            no_key = client.delete(f"/api/v1/shops/{shop_key}")
            self.assertEqual(no_key.status_code, 401)

            wrong_key = client.delete(
                f"/api/v1/shops/{shop_key}",
                headers={"X-Pan-Hotel-API-Key": "not-the-key"},
            )
            self.assertEqual(wrong_key.status_code, 401)

            ok = client.delete(
                f"/api/v1/shops/{shop_key}",
                headers={"X-Pan-Hotel-API-Key": "sup-key-123456"},
            )
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertTrue(ok.json()["deleted"])

    def test_create_requires_operator_key_in_production(self) -> None:
        os.environ["ENVIRONMENT"] = "production"
        from app.core.settings import get_settings

        get_settings.cache_clear()
        from app.main import app

        response = TestClient(app).post(
            "/api/v1/shops", json={"name": "未授权店铺"}
        )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
