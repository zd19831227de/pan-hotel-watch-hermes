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
        db_session = importlib.import_module("app.db.session")
        db_session.settings = get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine,
            expire_on_commit=False,
        )
        importlib.reload(db_session)
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


def _prepare_sync(shop_name: str, sessions: list[tuple[str, str, int]]) -> str:
    """同步准备：建店铺 + 注册 EBK 会话（create_isolated_test_shop 内部用
    asyncio.run，必须在事件循环外调用）。"""
    from tests._db_isolation import create_isolated_test_shop

    shop_key = create_isolated_test_shop(shop_name)
    from app.domain.browser.sessions import (
        BrowserSessionCreate,
        get_browser_session_store,
    )

    store = get_browser_session_store()
    for account, platform, port in sessions:
        store.create(
            BrowserSessionCreate(
                platform_account_id=account,
                platform=platform,
                intended_use="ebk",
                shop_key=shop_key,
                session_port=port,
                browser_session_id=f"browser-{account}",
            )
        )
    return shop_key


class ShopResolverTests(unittest.TestCase):
    def test_resolved_when_shop_has_single_platform_account(self) -> None:
        with _isolated_env():
            from app.domain.shops.resolver import resolve_platform_binding

            shop_key = _prepare_sync("泉吉", [("quanji_ctrip", "ctrip", 9371)])
            binding = asyncio.run(resolve_platform_binding(shop_key, "ctrip"))
        self.assertEqual(binding["status"], "resolved")
        self.assertEqual(binding["platform_account_id"], "quanji_ctrip")
        self.assertEqual(binding["browser_session_id"], "browser-quanji_ctrip")

    def test_binding_conflict_when_multiple_accounts_same_platform(self) -> None:
        with _isolated_env():
            from app.domain.shops.resolver import resolve_platform_binding

            shop_key = _prepare_sync(
                "曼馨",
                [("manxin_m_1", "meituan", 9372), ("manxin_m_2", "meituan", 9373)],
            )
            binding = asyncio.run(resolve_platform_binding(shop_key, "meituan"))
        self.assertEqual(binding["status"], "binding_conflict")
        self.assertIsNone(binding["platform_account_id"])

    def test_binding_required_when_no_account_or_unknown_shop(self) -> None:
        with _isolated_env():
            from app.domain.shops.resolver import resolve_platform_binding

            shop_key = _prepare_sync("静里", [("jingli_ctrip", "ctrip", 9374)])
            no_account = asyncio.run(resolve_platform_binding(shop_key, "fliggy"))
            unknown_shop = asyncio.run(
                resolve_platform_binding("shop_missing", "ctrip")
            )
            no_key = asyncio.run(resolve_platform_binding("", "ctrip"))
        for binding in (no_account, unknown_shop, no_key):
            self.assertEqual(binding["status"], "binding_required")
            self.assertIsNone(binding["platform_account_id"])

    def test_invalid_platform_raises(self) -> None:
        with _isolated_env():
            from app.domain.shops.resolver import resolve_platform_binding

            shop_key = _prepare_sync("测试", [("t_ctrip", "ctrip", 9375)])
            with self.assertRaises(ValueError):
                asyncio.run(resolve_platform_binding(shop_key, "douyin"))


if __name__ == "__main__":
    unittest.main()
