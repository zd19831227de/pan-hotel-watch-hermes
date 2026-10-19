from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class _isolated_env:
    """临时 BROWSER_STATE_DIR + 临时 SQLite（shops 库）。"""

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
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'shops.db').as_posix()}"
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

    @property
    def state_path(self) -> Path:
        return Path(self._tmp.name) / "browser_sessions.json"


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


async def _prepare_shops(names: list[str]) -> dict[str, str]:
    db_session = _reload_db()
    await db_session.ensure_db_ready()
    from app.domain.shops import service as shops_service

    return {
        name: (await shops_service.create_shop({"name": name}))["shop_key"]
        for name in names
    }


class ShopBindingStoreTests(unittest.TestCase):
    def test_create_ebk_requires_shop_key(self) -> None:
        with _isolated_env():
            from app.domain.browser.sessions import BrowserSessionCreate, get_browser_session_store

            with self.assertRaises(ValueError) as ctx:
                get_browser_session_store().create(
                    BrowserSessionCreate(
                        platform_account_id="ctrip_ebk_a",
                        platform="ctrip",
                        intended_use="ebk",
                        session_port=9301,
                    )
                )
            self.assertIn("店铺", str(ctx.exception))

    def test_create_non_ebk_rejects_shop_key(self) -> None:
        with _isolated_env():
            from app.domain.browser.sessions import BrowserSessionCreate, get_browser_session_store

            with self.assertRaises(ValueError) as ctx:
                get_browser_session_store().create(
                    BrowserSessionCreate(
                        platform_account_id="ctrip_public_b",
                        platform="ctrip",
                        intended_use="competitor_price",
                        session_port=9302,
                        shop_key="shop_whatever",
                    )
                )
            self.assertIn("EBK", str(ctx.exception))

    def test_create_ebk_with_shop_key_roundtrips_through_json(self) -> None:
        with _isolated_env() as env:
            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                BrowserSessionStore,
                get_browser_session_store,
            )

            session = get_browser_session_store().create(
                BrowserSessionCreate(
                    platform_account_id="meituan_ebk_a",
                    platform="meituan",
                    intended_use="ebk",
                    session_port=9303,
                    shop_key="shop_roundtrip",
                )
            )
            self.assertEqual(session.shop_key, "shop_roundtrip")

            # 独立实例从盘重读（reset_browser_session_store 会删状态文件，禁用）。
            reloaded = BrowserSessionStore().get(session.browser_session_id)
            self.assertEqual(reloaded.shop_key, "shop_roundtrip")
            raw = json.loads(env.state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                raw["sessions"][session.browser_session_id]["shop_key"],
                "shop_roundtrip",
            )

    def test_old_json_without_shop_key_loads_as_none_and_patch_keeps_it_unset(
        self,
    ) -> None:
        legacy_id = "ctrip_ebk_legacy"
        with _isolated_env() as env:
            env.state_path.parent.mkdir(parents=True, exist_ok=True)
            env.state_path.write_text(
                json.dumps(
                    {
                        "sessions": {
                            legacy_id: {
                                "platform_account_id": legacy_id,
                                "display_name": "旧账号",
                                "platform": "ctrip",
                                "session_port": 9310,
                                "intended_use": "ebk",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            from app.domain.browser.sessions import (
                BrowserSessionPatch,
                BrowserSessionStore,
                get_browser_session_store,
            )

            store = get_browser_session_store()
            self.assertIsNone(store.get(legacy_id).shop_key)

            # 只改展示名的 PATCH 不得要求补 shop_key，也不得丢失其他字段。
            updated = store.update(
                legacy_id, BrowserSessionPatch(display_name="旧账号改名")
            )
            self.assertIsNone(updated.shop_key)
            self.assertEqual(updated.display_name, "旧账号改名")

            self.assertIsNone(BrowserSessionStore().get(legacy_id).shop_key)

    def test_patch_shop_semantics(self) -> None:
        with _isolated_env():
            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                BrowserSessionPatch,
                get_browser_session_store,
            )

            store = get_browser_session_store()
            session = store.create(
                BrowserSessionCreate(
                    platform_account_id="fliggy_ebk_a",
                    platform="fliggy",
                    intended_use="ebk",
                    session_port=9304,
                    shop_key="shop_from",
                )
            )
            moved = store.update(
                session.browser_session_id, BrowserSessionPatch(shop_key="shop_to")
            )
            self.assertEqual(moved.shop_key, "shop_to")

            # EBK 账号不允许显式清空店铺归属。
            with self.assertRaises(ValueError):
                store.update(
                    session.browser_session_id, BrowserSessionPatch(shop_key="")
                )

            # 用途切换为非 EBK 时店铺归属自动清空（竞品账号保持全局）。
            switched = store.update(
                session.browser_session_id,
                BrowserSessionPatch(intended_use="competitor_price"),
            )
            self.assertIsNone(switched.shop_key)

            # 非 EBK 会话不允许再挂店铺。
            with self.assertRaises(ValueError):
                store.update(
                    session.browser_session_id, BrowserSessionPatch(shop_key="shop_x")
                )

    def test_patch_switch_to_ebk_requires_shop(self) -> None:
        with _isolated_env():
            from app.domain.browser.sessions import (
                BrowserSessionCreate,
                BrowserSessionPatch,
                get_browser_session_store,
            )

            store = get_browser_session_store()
            session = store.create(
                BrowserSessionCreate(
                    platform_account_id="llm_chat_a",
                    platform="qwen",
                    intended_use="llm_chat",
                    session_port=9305,
                )
            )
            with self.assertRaises(ValueError):
                store.update(
                    session.browser_session_id, BrowserSessionPatch(intended_use="ebk")
                )
            switched = store.update(
                session.browser_session_id,
                BrowserSessionPatch(intended_use="ebk", shop_key="shop_now"),
            )
            self.assertEqual(switched.shop_key, "shop_now")


class ShopBindingApiAndTreeTests(unittest.TestCase):
    def test_api_validates_shop_existence_and_returns_shop_key(self) -> None:
        with _isolated_env():
            keys = asyncio.run(_prepare_shops(["澜江路泉吉酒店"]))

            from app.main import app

            client = TestClient(app)

            unknown = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_ebk_unknown_shop",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "session_port": 9321,
                    "shop_key": "shop_missing",
                },
            )
            self.assertEqual(unknown.status_code, 422, unknown.text)

            ok = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_ebk_ok",
                    "platform": "ctrip",
                    "intended_use": "ebk",
                    "session_port": 9322,
                    "shop_key": keys["澜江路泉吉酒店"],
                },
            )
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["shop_key"], keys["澜江路泉吉酒店"])

            competitor = client.post(
                "/api/v1/browser-sessions",
                json={
                    "platform_account_id": "ctrip_public_x",
                    "platform": "ctrip",
                    "intended_use": "competitor_price",
                    "session_port": 9323,
                    "shop_key": keys["澜江路泉吉酒店"],
                },
            )
            self.assertEqual(competitor.status_code, 422, competitor.text)

    def test_tree_reflects_real_session_bindings_and_unassigned(self) -> None:
        with _isolated_env() as env:
            keys = asyncio.run(_prepare_shops(["泉吉", "曼馨"]))
            # 旧格式会话（无 shop_key）应计入未分组。
            env.state_path.parent.mkdir(parents=True, exist_ok=True)
            env.state_path.write_text(
                json.dumps(
                    {
                        "sessions": {
                            "legacy_ebk": {
                                "platform_account_id": "legacy_ebk",
                                "display_name": "旧未分组账号",
                                "platform": "ctrip",
                                "session_port": 9330,
                                "intended_use": "ebk",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            from app.main import app

            client = TestClient(app)
            for index, (account, platform, port, shop_name) in enumerate(
                (
                    ("quanji_ctrip", "ctrip", 9331, "泉吉"),
                    ("quanji_meituan", "meituan", 9332, "泉吉"),
                )
            ):
                created = client.post(
                    "/api/v1/browser-sessions",
                    json={
                        "platform_account_id": account,
                        "platform": platform,
                        "intended_use": "ebk",
                        "session_port": port,
                        "shop_key": keys[shop_name],
                    },
                )
                self.assertEqual(created.status_code, 200, created.text)

            tree = client.get("/api/v1/shops/tree")
            self.assertEqual(tree.status_code, 200, tree.text)
            nodes = {
                node["shop_key"]: node for node in tree.json()["shops"]
            }
            quanji = nodes[keys["泉吉"]]
            # v2 §2 契约：三平台同权——无监控组但账号已归属店铺 → 账号级
            # resolved；fliggy 无账号无组 → binding_required。
            self.assertEqual(quanji["bindings"]["ctrip"]["status"], "resolved")
            self.assertEqual(quanji["bindings"]["meituan"]["status"], "resolved")
            self.assertEqual(
                quanji["bindings"]["fliggy"]["status"], "binding_required"
            )
            manxin = nodes[keys["曼馨"]]
            self.assertTrue(
                all(
                    binding["status"] == "binding_required"
                    for binding in manxin["bindings"].values()
                )
            )
            self.assertEqual(tree.json()["unassigned"]["ebk_account_count"], 1)


if __name__ == "__main__":
    unittest.main()
