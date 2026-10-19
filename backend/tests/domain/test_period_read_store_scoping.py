"""周/月/日读取快照的店铺维度（2026-09-16 多店铺日终快照需求）。

- ebk_period_read_snapshots 增列 platform_account_id：多店铺同维度快照并存，
  问答按 chat → 账号 过滤，不再全局互踩；
- fetch_period_read 支持 binding 注入（日终快照按店铺遍历时用）；
- 日终快照轮次遍历全部店铺（此前只读第一个组 = 静里，新店无昨日快照）。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class _FakePage:
    pass


def _binding(account: str, label: str = "") -> dict[str, str]:
    return {
        "platform_account_id": account,
        "browser_session_id": f"browser-{account}",
        "label": label,
    }


class PeriodReadStoreScopingRepositoryTests(unittest.TestCase):
    def test_latest_scoped_by_platform_account(self) -> None:
        """同维度两店铺快照并存：按账号过滤各取各的最新，互不污染。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    payload={"sections": {"store": "静里"}},
                    platform_account_id="JL1700000001",
                )
                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    payload={"sections": {"store": "立瑞"}},
                    platform_account_id="lirui",
                )

                scoped = await repository.get_latest_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    platform_account_id="lirui",
                )
                self.assertEqual(scoped["payload"]["sections"]["store"], "立瑞")
                self.assertEqual(scoped["platform_account_id"], "lirui")

                unscoped = await repository.get_latest_period_read(
                    platform="meituan", data_kind="business", period="day"
                )
                self.assertEqual(unscoped["payload"]["sections"]["store"], "立瑞")

                missing = await repository.get_latest_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    platform_account_id="unknown_acct",
                )
                self.assertIsNone(missing)

            asyncio.run(scenario())


class PeriodReadStoreScopingServiceTests(unittest.TestCase):
    def test_fetch_with_binding_records_platform_account(self) -> None:
        """日终快照按店铺 binding 现读：落库行带 platform_account_id。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                async def fake_reader(page, *, period: str) -> dict:
                    return {
                        "status": "ok",
                        "caliber": "交易分析·按日（前日数据）",
                        "sections": {"经营表现": {"销售额": {"value": "8600"}}},
                    }

                result = await service.fetch_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    page=_FakePage(),
                    reader=fake_reader,
                    binding=_binding("lirui", "云滨立瑞美团 EBK 监控组"),
                )
                self.assertEqual(result["status"], "ok")
                latest = await repository.get_latest_period_read(
                    platform="meituan",
                    data_kind="business",
                    period="day",
                    platform_account_id="lirui",
                )
                self.assertIsNotNone(latest)
                self.assertEqual(latest["platform_account_id"], "lirui")

            asyncio.run(scenario())

    def test_get_period_read_passes_account_scope(self) -> None:
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository, service

                await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    payload={"sections": {"store": "静里"}},
                    platform_account_id="15900000001",
                )
                await repository.append_period_read_snapshot(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    payload={"sections": {"store": "立瑞"}},
                    platform_account_id="liruiota",
                )
                scoped = await service.get_period_read(
                    platform="ctrip",
                    data_kind="business",
                    period="week",
                    platform_account_id="liruiota",
                )
                self.assertIsNotNone(scoped)
                self.assertEqual(scoped["payload"]["sections"]["store"], "立瑞")

            asyncio.run(scenario())


class PeriodReadStoreScopingApiTests(unittest.TestCase):
    def test_get_scopes_by_chat_resolved_account(self) -> None:
        """GET 带 chat_id：按锚点链解析账号过滤快照；未绑定群 404 带指引。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="week",
                    payload={"sections": {"store": "静里"}},
                    platform_account_id="JL1700000001",
                )
                await repository.append_period_read_snapshot(
                    platform="meituan",
                    data_kind="business",
                    period="week",
                    payload={"sections": {"store": "立瑞"}},
                    platform_account_id="lirui",
                )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient
                from unittest.mock import AsyncMock, patch

                client = TestClient(app_module.create_app())
                with patch(
                    "app.api.v1.ebk_period_reads.resolve_order_account_binding",
                    new=AsyncMock(return_value=_binding("lirui")),
                ):
                    got = client.get(
                        "/api/v1/ebk-period-reads",
                        params={
                            "platform": "meituan",
                            "data_kind": "business",
                            "period": "week",
                            "chat_id": "oc_lirui",
                        },
                    )
                self.assertEqual(got.status_code, 200)
                self.assertEqual(
                    got.json()["snapshot"]["payload"]["sections"]["store"], "立瑞"
                )

                with patch(
                    "app.api.v1.ebk_period_reads.resolve_order_account_binding",
                    new=AsyncMock(return_value=None),
                ):
                    missing = client.get(
                        "/api/v1/ebk-period-reads",
                        params={
                            "platform": "meituan",
                            "data_kind": "business",
                            "period": "week",
                            "chat_id": "oc_stranger",
                        },
                    )
                self.assertEqual(missing.status_code, 404)
                self.assertIn("未绑定", missing.json()["detail"])

            asyncio.run(scenario())


    def test_get_date_range_scopes_by_chat_and_unbound_chat_404s(self) -> None:
        """历史范围 GET 带 chat_id：只返回本店铺快照；群未绑定任何平台账号时
        404 带指引，绝不回落成全部店铺数据（多店铺互不串读）。"""
        with _isolated_sqlite_database() as db_url:
            async def scenario() -> None:
                import datetime as dt

                _reload_db(db_url)
                from app.domain.ebk_period_reads import repository

                for account, store in (("JL1700000001", "静里"), ("lirui", "立瑞")):
                    await repository.append_period_read_snapshot(
                        platform="meituan",
                        data_kind="business",
                        period="day",
                        payload={"sections": {"store": store}},
                        platform_account_id=account,
                        business_date=dt.date(2026, 9, 10),
                    )
                app_module = importlib.reload(importlib.import_module("app.main"))
                from fastapi.testclient import TestClient
                from unittest.mock import AsyncMock, patch

                client = TestClient(app_module.create_app())
                with patch(
                    "app.api.v1.ebk_period_reads.resolve_order_account_binding",
                    new=AsyncMock(return_value=_binding("lirui")),
                ):
                    got = client.get(
                        "/api/v1/ebk-period-reads",
                        params={
                            "data_kind": "business",
                            "date_from": "2026-09-08",
                            "date_to": "2026-09-12",
                            "chat_id": "oc_lirui",
                        },
                    )
                self.assertEqual(got.status_code, 200)
                snapshots = got.json()["snapshots"]
                self.assertEqual(
                    [s["payload"]["sections"]["store"] for s in snapshots], ["立瑞"]
                )

                with patch(
                    "app.api.v1.ebk_period_reads.resolve_order_account_binding",
                    new=AsyncMock(return_value=None),
                ):
                    missing = client.get(
                        "/api/v1/ebk-period-reads",
                        params={
                            "data_kind": "business",
                            "date_from": "2026-09-08",
                            "date_to": "2026-09-12",
                            "chat_id": "oc_stranger",
                        },
                    )
                self.assertEqual(missing.status_code, 404)
                self.assertIn("未绑定", missing.json()["detail"])

            asyncio.run(scenario())


class PeriodDaySnapshotMultiStoreTests(unittest.TestCase):
    def test_round_iterates_all_store_bindings(self) -> None:
        """日终快照轮次：每平台每店铺 × (经营, 排名) 全量读取。"""
        from unittest.mock import AsyncMock, patch

        from app.workers.read_only_scheduler import (
            _execute_period_read_day_snapshot_round,
        )

        calls: list[dict] = []

        async def fake_fetch(**kwargs):
            calls.append(kwargs)
            binding = kwargs.get("binding") or {}
            if binding.get("platform_account_id") == "liruiota":
                return {"status": "not_ready"}
            return {"status": "ok"}

        store_bindings = {
            "ctrip": [
                _binding("15900000001", "静里EBK数据中心监控"),
                _binding("liruiota", "云滨立瑞EBK数据中心监控"),
            ],
            "meituan": [_binding("JL1700000001", "静里美团 EBK 监控组")],
            "fliggy": [_binding("hotel1144352", "静里飞猪 EBK 监控组")],
        }

        with patch(
            "app.domain.ebk_period_reads.service.fetch_period_read",
            new=AsyncMock(side_effect=fake_fetch),
        ), patch(
            "app.workers.read_only_scheduler.list_order_account_bindings",
            new=AsyncMock(side_effect=lambda platform: store_bindings[platform]),
        ):
            summary = asyncio.run(_execute_period_read_day_snapshot_round())

        # ctrip 2 店×2 维 + meituan 1 店×2 维 + fliggy 1 店×2 维 = 8 次读取，
        # 每平台每店铺独立注入 binding。
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(call.get("binding") for call in calls))
        self.assertEqual(summary["ok"], 6)
        self.assertEqual(summary["not_ready"], 2)
        self.assertEqual(summary["total"], 8)

        results = summary["platform_results"]
        self.assertEqual(results["ctrip"]["status"], "partial")
        self.assertIn("静里", results["ctrip"]["detail"])
        self.assertIn("立瑞", results["ctrip"]["detail"])
        self.assertEqual(results["meituan"]["status"], "success")
        self.assertEqual(results["fliggy"]["status"], "success")

    def test_run_record_status_uses_total(self) -> None:
        import datetime as dt

        from app.workers.read_only_scheduler import _period_day_snapshot_run_record

        started = dt.datetime(2026, 9, 16, 9, 30, 0)
        base = {"started_at": started, "platform_results": {}, "errors": []}
        self.assertEqual(
            _period_day_snapshot_run_record({**base, "ok": 8, "total": 8})["status"],
            "success",
        )
        self.assertEqual(
            _period_day_snapshot_run_record({**base, "ok": 6, "total": 8})["status"],
            "partial",
        )
        self.assertEqual(
            _period_day_snapshot_run_record({**base, "ok": 0, "total": 8})["status"],
            "failed",
        )


class PeriodReadLegacyMigrationTests(unittest.TestCase):
    def test_backfills_platform_account_from_first_bound_group(self) -> None:
        """存量库：加列 + 按各平台第一个有效绑定组回填 NULL 行。"""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.domain.ebk_period_reads.migrations import ensure_ebk_period_read_schema

        with tempfile.TemporaryDirectory() as tmp:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{(Path(tmp) / 'legacy.db').as_posix()}"
            )

            async def scenario() -> None:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            """
                            CREATE TABLE ebk_period_read_snapshots (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                platform VARCHAR(16) NOT NULL,
                                data_kind VARCHAR(16) NOT NULL,
                                period VARCHAR(8) NOT NULL,
                                status VARCHAR(24) NOT NULL DEFAULT 'ok',
                                caliber VARCHAR(64) NULL,
                                summary VARCHAR(255) NULL,
                                payload_json JSON NULL,
                                source VARCHAR(16) NOT NULL DEFAULT 'manual',
                                business_date DATE NULL,
                                captured_at DATETIME NOT NULL,
                                created_at DATETIME NOT NULL
                            )
                            """
                        )
                    )
                    await conn.execute(
                        text(
                            "CREATE TABLE meituan_ebooking_watch_groups ("
                            "id INTEGER PRIMARY KEY, group_key VARCHAR(64), "
                            "platform_account_id VARCHAR(64))"
                        )
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO meituan_ebooking_watch_groups "
                            "(group_key, platform_account_id) VALUES "
                            "('meituan_b', 'second-acct'), "
                            "('meituan_a', 'legacy-acct'), "
                            "('meituan_c', '__placeholder__')"
                        )
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO ebk_period_read_snapshots "
                            "(platform, data_kind, period, captured_at, created_at) VALUES "
                            "('meituan', 'business', 'day', '2026-09-15 09:30:00', "
                            "'2026-09-15 09:30:00'), "
                            "('fliggy', 'business', 'day', '2026-09-15 09:31:00', "
                            "'2026-09-15 09:31:00')"
                        )
                    )
                    # 跑两遍验证幂等。
                    await ensure_ebk_period_read_schema(conn)
                    await ensure_ebk_period_read_schema(conn)
                    rows = (
                        await conn.execute(
                            text(
                                "SELECT platform, platform_account_id "
                                "FROM ebk_period_read_snapshots ORDER BY platform"
                            )
                        )
                    ).fetchall()
                self.assertEqual(rows[0], ("fliggy", None))
                # 组序第一个有效账号（排除 __ 占位），不是插入序第一个。
                self.assertEqual(rows[1], ("meituan", "legacy-acct"))

            asyncio.run(scenario())
            asyncio.run(engine.dispose())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ebk-period-reads.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb):
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
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


if __name__ == "__main__":
    unittest.main()
