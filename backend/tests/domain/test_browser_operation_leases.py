from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class _isolated_db:
    def __enter__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._prev = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
        }
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'leases.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        self._reload()

    @staticmethod
    def _reload() -> None:
        from app.core import settings as settings_mod
        from app.db import session as db_session

        settings_mod.get_settings.cache_clear()
        importlib.reload(db_session)
        asyncio.run(db_session.ensure_db_ready())

    def __exit__(self, *args) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from app.core import settings as settings_mod

        settings_mod.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


class BrowserOperationLeaseTests(unittest.TestCase):
    def test_exclusive_conflict_and_different_keys_independent(self) -> None:
        async def scenario() -> None:
            from app.domain.browser.operation_leases import (
                LeaseConflict,
                acquire_operation_lease,
                release_operation_lease,
            )

            first = await acquire_operation_lease(
                "browser_session:s1", holder="worker-a", wait_seconds=0.2
            )
            try:
                with self.assertRaises(LeaseConflict):
                    await acquire_operation_lease(
                        "browser_session:s1", holder="worker-b", wait_seconds=0.2
                    )
                # 不同浏览器互不影响。
                second = await acquire_operation_lease(
                    "browser_session:s2", holder="worker-b", wait_seconds=0.2
                )
                await release_operation_lease(second)
            finally:
                await release_operation_lease(first)
            # 释放后可立即重取。
            again = await acquire_operation_lease(
                "browser_session:s1", holder="worker-c", wait_seconds=0.2
            )
            await release_operation_lease(again)

        asyncio.run(scenario())

    def test_expired_lease_takeover_and_token_guard(self) -> None:
        async def scenario() -> None:
            from app.domain.browser.operation_leases import (
                LeaseLost,
                acquire_operation_lease,
                release_operation_lease,
                renew_operation_lease,
            )

            stale = await acquire_operation_lease(
                "browser_session:stale", holder="crashed", ttl_seconds=0.01
            )
            await asyncio.sleep(0.1)
            fresh = await acquire_operation_lease(
                "browser_session:stale", holder="worker-new", wait_seconds=0.1
            )
            # 旧持有方的续期/释放不得影响新持有方。
            with self.assertRaises(LeaseLost):
                await renew_operation_lease(stale, ttl_seconds=60)
            await release_operation_lease(stale)  # 无异常但不删新租约
            await renew_operation_lease(fresh, ttl_seconds=60)
            await release_operation_lease(fresh)

        asyncio.run(scenario())

    def test_storage_unavailable_fails_closed(self) -> None:
        async def scenario() -> None:
            from app.core import settings as settings_mod
            from app.db import session as db_session

            from app.domain.browser.operation_leases import (
                LeaseUnavailable,
                acquire_operation_lease,
            )

            settings_mod.get_settings.cache_clear()
            db_session.settings = settings_mod.get_settings()
            db_session.engine = db_session.create_async_engine(
                "sqlite+aiosqlite:///Z:/definitely/missing/dir/leases.db",
                pool_pre_ping=True,
            )
            db_session.SessionLocal = db_session.async_sessionmaker(
                db_session.engine,
                expire_on_commit=False,
            )
            db_session._database_ready_engine = None
            with self.assertRaises(LeaseUnavailable):
                await acquire_operation_lease(
                    "browser_session:x", holder="worker", wait_seconds=0.1
                )

        with _isolated_db():
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
