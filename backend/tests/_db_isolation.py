"""Shared test helpers for isolated SQLite database sessions.

These helpers exist because the backend uses a *module-level* global engine and
session factory in ``app.db.session`` (``engine`` / ``SessionLocal``).  Domain
tests that need an isolated database reassign those globals to a temp engine via
``_reload_db_session`` / ``_reload_repository``.  If the global state is not
restored afterwards, later tests in the same process inherit a stale engine that
points at a temp directory which has already been deleted — and any real DB call
they make (often un-mocked, e.g. catalog pairing inside
``build_feishu_chat_context``) crashes with
``sqlite3.OperationalError: unable to open database file``.

Call :func:`reset_global_db_session` from every isolation context manager's
``__exit__`` (after restoring the ``DATABASE_URL`` env var, before deleting the
temp dir) to rebuild the global engine/SessionLocal from the now-restored
settings and drop the per-loop readiness cache so the next test starts clean.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def isolated_test_db():
    """临时 SQLite + 浏览器状态目录的隔离上下文（进圈重绑引擎，出圈还原）。

    与各测试文件私有的 _isolated_env 等价；v2 店铺直连起沉淀为共享助手，
    避免每个新用例文件再抄一份。注意：不得 reload settings 模块（会分裂
    get_settings，污染后续鉴权测试）。
    """

    tmp = tempfile.TemporaryDirectory()
    prev = {
        key: os.environ.get(key)
        for key in ("BROWSER_STATE_DIR", "DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
    }
    os.environ["BROWSER_STATE_DIR"] = tmp.name
    os.environ["DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{(Path(tmp.name) / 'isolated.db').as_posix()}"
    )
    os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
    try:
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
        db_session._database_ready_engine = None
        db_session._database_ready_locks.clear()
        yield
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_global_db_session()
        tmp.cleanup()


def reset_global_db_session() -> None:
    """Rebuild ``app.db.session`` globals from the *current* settings.

    This must run *after* the caller has restored ``DATABASE_URL`` /
    ``DATABASE_AUTO_CREATE_TABLES`` to their pre-test values, so the rebuilt
    engine points at the real database again instead of a deleted temp file.
    Any previously-injected temp engine is disposed (best-effort) to release
    file handles before the temp directory is removed.
    """

    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()

    db_session = importlib.import_module("app.db.session")

    # Dispose any temp engine we previously injected so its file handles close
    # before the temp dir cleanup on Windows (which refuses to delete open
    # files).  dispose() is a coroutine; run it on a fresh loop so this stays
    # synchronous-friendly for use from ``__exit__``.
    previous_engine = getattr(db_session, "engine", None)
    if previous_engine is not None:
        import asyncio

        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(previous_engine.dispose())
            finally:
                loop.close()
        except Exception:
            # Best-effort: never let engine teardown mask a test failure.
            pass

    fresh_settings = settings.get_settings()
    db_session.settings = fresh_settings
    db_session.engine = db_session._create_engine(fresh_settings.database_url)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    # Drop the "engine already initialized" memo so the restored engine can be
    # (re)seeded on demand by the next test that needs auto-created tables.
    db_session._database_ready_engine = None
    db_session._database_ready_locks.clear()


def create_isolated_test_shop(name: str = "测试店铺") -> str:
    """在当前（已隔离的）数据库里建一家测试店铺，返回 shop_key。

    P2 起 EBK 浏览器账号必须归属店铺；需要在隔离环境里建 EBK 会话的测试
    用它拿一个合法 shop_key。要求调用方已完成 DATABASE_URL 隔离与引擎重绑
    （或依赖 ensure_db_ready 的自动建库）。
    """
    import asyncio

    db_session = importlib.import_module("app.db.session")
    asyncio.run(db_session.ensure_db_ready())
    from app.domain.shops import service as shops_service

    return asyncio.run(shops_service.create_shop({"name": name}))["shop_key"]
