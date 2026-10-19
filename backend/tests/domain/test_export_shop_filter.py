from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class _isolated_db:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "DATABASE_AUTO_CREATE_TABLES")
        }
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(Path(self._tmp.name) / 'export.db').as_posix()}"
        )
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        from app.core.settings import get_settings

        get_settings.cache_clear()
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
        asyncio.run(db_session.ensure_db_ready())
        return os.environ["DATABASE_URL"]

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


def _snapshot(platform: str, account: str) -> object:
    from datetime import UTC, datetime

    from app.domain.ebk_snapshots.types import UnifiedEbkSnapshot

    return UnifiedEbkSnapshot(
        platform=platform,
        platform_account_id=account,
        browser_session_id=f"browser-{account}",
        read_at=datetime.now(UTC).isoformat(),
        status="success",
        summary="export filter test",
        datasets={"business_outline": {"status": "success", "data": {}}},
    )


class ExportShopFilterTests(unittest.TestCase):
    def test_snapshots_export_filters_by_shop_key_and_excludes_null_history(self) -> None:
        with _isolated_db():
            async def scenario() -> tuple[list[dict], list[dict]]:
                from app.domain.ebk_snapshots import repository

                for account, shop in (
                    ("acc_a", "shop_export_a"),
                    ("acc_b", "shop_export_b"),
                    ("acc_legacy", None),
                ):
                    await repository.save_ebk_snapshot(
                        _snapshot("ctrip", account),  # type: ignore[arg-type]
                        shop_key=shop,
                    )
                scoped = await repository.list_ebk_snapshots_with_payload_for_export(
                    shop_key="shop_export_a"
                )
                overall = await repository.list_ebk_snapshots_with_payload_for_export()
                return scoped, overall

            scoped, overall = asyncio.run(scenario())

        self.assertEqual(
            [row["platform_account_id"] for row in scoped], ["acc_a"]
        )
        self.assertEqual(len(overall), 3)

    def test_watch_runs_export_filters_by_group_shop(self) -> None:
        with _isolated_db():
            async def scenario() -> tuple[list[dict], list[dict]]:
                from datetime import UTC, datetime

                from app.db.session import session_scope

                from app.domain.ctrip_ebooking_watch.models import (
                    CtripEbookingWatchGroup,
                    CtripEbookingWatchRun,
                )
                from app.domain.ctrip_ebooking_watch.repository import (
                    list_data_center_watch_runs_for_export,
                )

                async with session_scope() as session:
                    group_a = CtripEbookingWatchGroup(
                        group_key="exp_g_a",
                        name="A店锚点",
                        platform_account_id="exp_acc_a",
                        shop_key="shop_export_a",
                    )
                    session.add(group_a)
                    await session.flush()
                    group_free = CtripEbookingWatchGroup(
                        group_key="exp_g_free",
                        name="未归属锚点",
                        platform_account_id="exp_acc_free",
                    )
                    session.add(group_free)
                    await session.flush()
                    captured = datetime.now(UTC).replace(tzinfo=None)
                    for group, account, summary in (
                        (group_a, "exp_acc_a", "a"),
                        (group_free, "exp_acc_free", "free"),
                    ):
                        session.add(
                            CtripEbookingWatchRun(
                                group_id=group.id,
                                platform_account_id=account,
                                browser_session_id=f"browser-{account}",
                                actor_id="test",
                                source="manual",
                                status="success",
                                summary=summary,
                                captured_at=captured,
                            )
                        )
                        await session.flush()
                scoped = await list_data_center_watch_runs_for_export(
                    start_at=captured.replace(hour=0, minute=0, second=0),
                    end_at=None,
                    limit=100,
                    shop_key="shop_export_a",
                )
                overall = await list_data_center_watch_runs_for_export(
                    start_at=captured.replace(hour=0, minute=0, second=0),
                    end_at=None,
                    limit=100,
                )
                return scoped, overall

            scoped, overall = asyncio.run(scenario())

        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(overall), 2)


if __name__ == "__main__":
    unittest.main()
