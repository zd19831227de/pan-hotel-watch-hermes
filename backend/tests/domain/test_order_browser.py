from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class OrderBrowserBindingTests(unittest.TestCase):
    """订单/退单浏览器会话绑定解析：按平台+chat_id 解析店铺账号绑定。"""

    def setUp(self) -> None:
        # 隔离全局 session store 磁盘源：指向临时空目录，避免真实环境已注册 session
        # 污染「无绑定应返回 None」用例（store.list() 每次会从磁盘 reload）。
        # 注意：不得 reload settings 模块（会分裂 get_settings，污染后续鉴权测试）。
        self._tmp_state = tempfile.TemporaryDirectory()
        self._previous_state_dir = os.environ.get("BROWSER_STATE_DIR")
        os.environ["BROWSER_STATE_DIR"] = self._tmp_state.name
        import app.core.settings as settings_mod

        settings_mod.get_settings.cache_clear()
        from app.domain.browser.sessions import get_browser_session_store

        get_browser_session_store().reset()

    def tearDown(self) -> None:
        if self._previous_state_dir is None:
            os.environ.pop("BROWSER_STATE_DIR", None)
        else:
            os.environ["BROWSER_STATE_DIR"] = self._previous_state_dir
        import app.core.settings as settings_mod

        settings_mod.get_settings.cache_clear()
        from app.domain.browser.sessions import get_browser_session_store

        get_browser_session_store().reset()
        self._tmp_state.cleanup()

    def test_ctrip_binding_uses_chat_bound_enabled_group(self) -> None:
        with _isolated_db() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()

                from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
                from app.db.session import session_scope

                async with session_scope() as session:
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="g_jingli",
                            name="静里携程",
                            platform_account_id="15900000001",
                            browser_session_id="browser-15900000001",
                            feishu_chat_id="oc_jingli",
                            enabled=True,
                        )
                    )
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="g_manxin",
                            name="曼馨携程",
                            platform_account_id="88888",
                            browser_session_id="browser-88888",
                            feishu_chat_id="oc_manxin",
                            enabled=True,
                        )
                    )
                    await session.flush()

                mod = importlib.import_module("app.domain.order_browser")
                binding = await mod.resolve_order_account_binding(
                    "ctrip", chat_id="oc_jingli"
                )
                self.assertIsNotNone(binding)
                self.assertEqual(binding["platform_account_id"], "15900000001")
                self.assertEqual(binding["browser_session_id"], "browser-15900000001")

                binding2 = await mod.resolve_order_account_binding(
                    "ctrip", chat_id="oc_manxin"
                )
                self.assertEqual(binding2["platform_account_id"], "88888")

            asyncio.run(scenario())

    def test_binding_none_when_no_group(self) -> None:
        with _isolated_db() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                mod = importlib.import_module("app.domain.order_browser")
                binding = await mod.resolve_order_account_binding("ctrip", chat_id="oc_none")
                self.assertIsNone(binding)

            asyncio.run(scenario())

    def test_binding_skips_unconfigured_account(self) -> None:
        with _isolated_db() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
                from app.db.session import session_scope

                async with session_scope() as session:
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="g1",
                            name="cleared",
                            platform_account_id="__cleared__",
                            browser_session_id=None,
                            feishu_chat_id="oc_x",
                            enabled=True,
                        )
                    )
                    await session.flush()
                mod = importlib.import_module("app.domain.order_browser")
                binding = await mod.resolve_order_account_binding("ctrip", chat_id="oc_x")
                self.assertIsNone(binding)

            asyncio.run(scenario())

    def test_binding_works_even_when_group_disabled(self) -> None:
        """监控组 enabled=False 只关定时轮询，按需抓取仍应能用其账号绑定。"""
        with _isolated_db() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
                from app.db.session import session_scope

                async with session_scope() as session:
                    session.add(
                        CtripEbookingWatchGroup(
                            group_key="g_disabled",
                            name="停用但绑定有效",
                            platform_account_id="15900000001",
                            browser_session_id="browser-15900000001",
                            feishu_chat_id="oc_j",
                            enabled=False,
                        )
                    )
                    await session.flush()
                mod = importlib.import_module("app.domain.order_browser")
                binding = await mod.resolve_order_account_binding("ctrip", chat_id="oc_j")
                self.assertIsNotNone(binding)
                self.assertEqual(binding["platform_account_id"], "15900000001")

            asyncio.run(scenario())

    def test_binding_falls_back_to_session_store_when_group_account_empty(self) -> None:
        """监控组 platform_account_id 为空（历史数据）时的回落边界（2026-09-16 契约）。

        chat 给定（问答/订单按群定位店铺）：绝不回落 session store 首个账号——
        多店铺下那是跨店猜账号（立瑞串读事故根因），解析不到即 None（not_ready）。
        chat 为空（日终快照/单店铺兜底）：保留回落 session store 的历史语义。
        """
        with _isolated_db() as db_url:
            async def scenario() -> None:
                db_session = _reload_db(db_url)
                await db_session.ensure_db_ready()
                from app.domain.meituan_ebooking_watch.models import MeituanEbookingWatchGroup
                from app.db.session import session_scope

                async with session_scope() as session:
                    session.add(
                        MeituanEbookingWatchGroup(
                            group_key="m1",
                            name="美团监控组",
                            platform_account_id="",  # 历史数据为空
                            browser_session_id=None,
                            feishu_chat_id="oc_j",
                            enabled=False,
                        )
                    )
                    await session.flush()

                # 注册一个美团 session 到 store
                from app.domain.browser.sessions import (
                    BrowserSessionCreate,
                    get_browser_session_store,
                )

                store = get_browser_session_store()
                try:
                    store.create(
                        BrowserSessionCreate(
                            platform_account_id="JL1700000001",
                            platform="meituan",
                            intended_use="ebk",
                            shop_key="shop_order_fallback_test",
                            session_port=9521,
                            browser_session_id="browser-JL1700000001",
                        )
                    )
                except ValueError:
                    pass

                mod = importlib.import_module("app.domain.order_browser")
                # chat 定位：组账号为空即 None，不跨店回落 session store。
                binding = await mod.resolve_order_account_binding("meituan", chat_id="oc_j")
                self.assertIsNone(binding)
                # 无 chat（日终快照/历史单店铺）：回落 session store 语义保留。
                legacy = await mod.resolve_order_account_binding("meituan")
                self.assertIsNotNone(legacy)
                self.assertEqual(legacy["platform_account_id"], "JL1700000001")
                self.assertEqual(legacy["browser_session_id"], "browser-JL1700000001")

            asyncio.run(scenario())

    def test_multi_session_no_chat_returns_none_and_list_enumerates_all(self) -> None:
        """P3：chat 为空 + 多账号时不再取第一个（跨店猜测）；遍历接口返回全部。"""
        with _isolated_db() as db_url:
            def prepare() -> None:
                db_session = _reload_db(db_url)

                asyncio.run(db_session.ensure_db_ready())
                from app.domain.browser.sessions import (
                    BrowserSessionCreate,
                    get_browser_session_store,
                )

                store = get_browser_session_store()
                for account, port in (("m_shop_a", 9531), ("m_shop_b", 9532)):
                    store.create(
                        BrowserSessionCreate(
                            platform_account_id=account,
                            platform="meituan",
                            intended_use="ebk",
                            shop_key=f"shop_{account}",
                            session_port=port,
                            browser_session_id=f"browser-{account}",
                        )
                    )

            prepare()

            async def scenario() -> tuple[object, list[dict]]:
                mod = importlib.import_module("app.domain.order_browser")
                single = await mod.resolve_order_account_binding("meituan")
                listed = await mod.list_order_account_bindings("meituan")
                return single, listed

            single, listed = asyncio.run(scenario())
            self.assertIsNone(single)
            self.assertEqual(
                sorted(item["platform_account_id"] for item in listed),
                ["m_shop_a", "m_shop_b"],
            )


class OrderBrowserPortTests(unittest.TestCase):
    """ä¸å¹³å°é»è®¤ç«¯å£è§£æã"""

    def test_default_ports(self) -> None:
        mod = importlib.import_module("app.domain.order_browser")
        self.assertEqual(mod._default_port_for_platform("ctrip"), 9221)
        self.assertEqual(mod._default_port_for_platform("meituan"), 9521)
        self.assertEqual(mod._default_port_for_platform("fliggy"), 9531)


class _isolated_db:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "order-browser.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        return os.environ["DATABASE_URL"]

    def __exit__(self, *exc) -> None:
        # 还原 DATABASE_URL 并复位全局引擎：此前缺这一步，后续用例会继承
        # 指向已删临时库的 engine / 环境变量（顺序暴露的预存污染源）。
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db(db_url: str):
    # 不得 reload settings 模块：那会造出第二个 get_settings，其余模块仍握着
    # 旧函数及其脏缓存（supervisor 鉴权 401 的根因）。只清缓存+重绑引擎。
    import app.core.settings as settings_mod
    import app.db.session as db_session

    settings_mod.get_settings.cache_clear()
    db_session.settings = settings_mod.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    importlib.reload(db_session)
    return db_session


if __name__ == "__main__":
    unittest.main()
