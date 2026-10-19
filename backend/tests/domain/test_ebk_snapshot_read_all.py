from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient


MEITUAN_EXPECTED_READ_ALL_DATASETS = [
    "workbench_home",
    "data_center_home",
    "data_center_trade",
    "data_center_traffic",
    "data_center_service",
    "data_center_peer_ranking",
    "promotion_coupon",
    "comment_manage_home",
    # 2026-09-11 推广通广告卡接入后的读取清单补齐（此前测试常量漏更新致
    # 三用例在 main 上预存失败）。
    "ad_cpc",
]


class EbkSnapshotReadAllApiTests(unittest.TestCase):
    def test_read_all_both_platforms_success_saves_snapshots(self) -> None:
        with _isolated_runtime() as runtime:
            client = runtime["client"]
            ctrip_session = runtime["ctrip_session"]
            meituan_session = runtime["meituan_session"]
            fake_ctrip = _FakeCtripService(status="success")
            fake_meituan = _FakeMeituanService(
                status_by_dataset={
                    dataset: "success" for dataset in MEITUAN_EXPECTED_READ_ALL_DATASETS
                }
            )

            with (
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_ctrip_ebooking_service",
                    return_value=fake_ctrip,
                ),
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_meituan_ebooking_service",
                    return_value=fake_meituan,
                ),
            ):
                response = client.post(
                    "/api/v1/ebk-snapshots/read-all",
                    json={
                        "actor_id": "operator",
                        "ctrip_platform_account_id": "ctrip_read_all",
                        "meituan_platform_account_id": "meituan_read_all",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["results"]["ctrip"]["status"], "success")
            self.assertEqual(payload["results"]["meituan"]["status"], "success")
            self.assertIsNotNone(payload["results"]["ctrip"]["snapshot_id"])
            self.assertIsNotNone(payload["results"]["meituan"]["snapshot_id"])
            self.assertEqual(
                fake_ctrip.calls[0]["browser_session_id"],
                ctrip_session["browser_session_id"],
            )
            self.assertEqual(
                {call["dataset"] for call in fake_meituan.calls},
                set(MEITUAN_EXPECTED_READ_ALL_DATASETS),
            )
            self.assertEqual(
                fake_meituan.calls[0]["browser_session_id"],
                meituan_session["browser_session_id"],
            )

            ctrip_latest = client.get(
                "/api/v1/ebk-snapshots/latest",
                params={
                    "platform": "ctrip",
                    "platform_account_id": "ctrip_read_all",
                },
            )
            meituan_latest = client.get(
                "/api/v1/ebk-snapshots/latest",
                params={
                    "platform": "meituan",
                    "platform_account_id": "meituan_read_all",
                },
            )
            self.assertEqual(ctrip_latest.status_code, 200)
            self.assertEqual(meituan_latest.status_code, 200)
            self.assertEqual(ctrip_latest.json()["status"], "success")
            self.assertEqual(meituan_latest.json()["status"], "success")
            self.assertIn("business_outline", ctrip_latest.json()["payload_json"]["datasets"])
            self.assertIn("data_center_home", meituan_latest.json()["payload_json"]["datasets"])
            self.assertIn("data_center_service", meituan_latest.json()["payload_json"]["datasets"])
            self.assertNotIn("data_center_peer", meituan_latest.json()["payload_json"]["datasets"])
            self.assertNotIn("data_center_marketing", meituan_latest.json()["payload_json"]["datasets"])
            self.assertNotIn("data_center_crowd", meituan_latest.json()["payload_json"]["datasets"])
            self.assertEqual(meituan_latest.json()["dataset_count"], len(MEITUAN_EXPECTED_READ_ALL_DATASETS))


    def test_read_all_can_refresh_only_meituan_snapshot(self) -> None:
        with _isolated_runtime() as runtime:
            client = runtime["client"]
            fake_ctrip = _FakeCtripService(status="success")
            fake_meituan = _FakeMeituanService(
                status_by_dataset={
                    dataset: "success" for dataset in MEITUAN_EXPECTED_READ_ALL_DATASETS
                }
            )

            with (
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_ctrip_ebooking_service",
                    return_value=fake_ctrip,
                ),
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_meituan_ebooking_service",
                    return_value=fake_meituan,
                ),
            ):
                response = client.post(
                    "/api/v1/ebk-snapshots/read-all",
                    json={
                        "actor_id": "operator",
                        "platforms": ["meituan"],
                        "meituan_platform_account_id": "meituan_read_all",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["summary"], "美团 EBK 数据已读取并保存。")
            self.assertNotIn("ctrip", payload["results"])
            self.assertEqual(payload["results"]["meituan"]["status"], "success")
            self.assertEqual(fake_ctrip.calls, [])
            self.assertEqual(
                [call["dataset"] for call in fake_meituan.calls],
                MEITUAN_EXPECTED_READ_ALL_DATASETS,
            )

            meituan_latest = client.get(
                "/api/v1/ebk-snapshots/latest",
                params={
                    "platform": "meituan",
                    "platform_account_id": "meituan_read_all",
                },
            )
            self.assertEqual(meituan_latest.status_code, 200)
            self.assertEqual(meituan_latest.json()["status"], "success")
            self.assertIn("promotion_coupon", meituan_latest.json()["payload_json"]["datasets"])

    def test_read_all_meituan_human_required_returns_partial_and_keeps_ctrip(self) -> None:
        with _isolated_runtime() as runtime:
            client = runtime["client"]
            fake_ctrip = _FakeCtripService(status="success")
            fake_meituan = _FakeMeituanService(
                status_by_dataset={
                    dataset: "human_required" for dataset in MEITUAN_EXPECTED_READ_ALL_DATASETS
                },
                evidence={
                    "cookie": "session=abc",
                    "password": "secret-pass",
                    "profile_path": r"C:\Users\Administrator\.runtime\browser-state\meituan\acc",
                    "verification_code": "123456",
                    "screenshot_path": r"C:\Users\Administrator\Desktop\output\shot.png",
                    "parser": "meituan_data_center_dom_v1",
                },
            )

            with (
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_ctrip_ebooking_service",
                    return_value=fake_ctrip,
                ),
                patch(
                    "app.domain.ebk_snapshots.ebk_snapshot_reader.get_meituan_ebooking_service",
                    return_value=fake_meituan,
                ),
            ):
                response = client.post(
                    "/api/v1/ebk-snapshots/read-all",
                    json={
                        "ctrip_platform_account_id": "ctrip_read_all",
                        "meituan_platform_account_id": "meituan_read_all",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["results"]["ctrip"]["status"], "success")
            self.assertEqual(payload["results"]["meituan"]["status"], "human_required")
            self.assertTrue(fake_ctrip.calls)
            self.assertEqual(len(fake_meituan.calls), len(MEITUAN_EXPECTED_READ_ALL_DATASETS))

            meituan_latest = client.get(
                "/api/v1/ebk-snapshots/latest",
                params={
                    "platform": "meituan",
                    "platform_account_id": "meituan_read_all",
                },
            )
            self.assertEqual(meituan_latest.status_code, 200)
            latest = meituan_latest.json()
            self.assertEqual(latest["status"], "human_required")
            evidence = latest["payload_json"]["evidence"]
            payload_text = str(latest["payload_json"])
            self.assertEqual(evidence["parser"], "meituan_data_center_dom_v1")
            for forbidden in (
                "session=abc",
                "secret-pass",
                "browser-state",
                "123456",
                "shot.png",
            ):
                self.assertNotIn(forbidden, payload_text)
                self.assertNotIn(forbidden, str(payload))


class _FakeCtripService:
    def __init__(self, *, status: str) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    async def read_monitor(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from app.domain.browser.ctrip_ebooking_results import CtripEbookingMonitorResult

        return CtripEbookingMonitorResult(
            status=self.status,
            summary="Ctrip monitor fake",
            platform="ctrip",
            platform_account_id=kwargs["platform_account_id"],
            browser_session_id=kwargs["browser_session_id"],
            data={
                "query_type": "ctrip_ebooking_data_center_monitor",
                "datasets": {
                    "business_outline": {"metrics": {"occupancy_rate": {"value": "72%"}}},
                },
                "monitor": {"occupancy_rate": "72%"},
            },
            evidence={
                "parser_version": "ctrip_ebooking_monitor_v1",
                "dataset_statuses": {"business_outline": self.status},
                "screenshot_paths": [r"C:\Users\Administrator\Desktop\output\ctrip.png"],
                "password_returned": False,
            },
        )


class _FakeMeituanService:
    def __init__(
        self,
        *,
        status_by_dataset: dict[str, str],
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.status_by_dataset = status_by_dataset
        self.evidence = evidence or {"parser": "meituan_data_center_dom_v1"}
        self.calls: list[dict[str, Any]] = []

    async def read_dataset(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        dataset = str(kwargs["dataset"])
        status = self.status_by_dataset.get(dataset, "success")
        return {
            "status": status,
            "summary": f"Meituan {dataset} fake",
            "platform": "meituan",
            "dataset": dataset,
            "platform_account_id": kwargs["platform_account_id"],
            "browser_session_id": kwargs["browser_session_id"],
            "data": {"dataset": dataset, "ok": status == "success"},
            "evidence": dict(self.evidence),
        }


class _isolated_runtime:
    def __enter__(self) -> dict[str, Any]:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._previous = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "DATABASE_AUTO_CREATE_TABLES": os.environ.get("DATABASE_AUTO_CREATE_TABLES"),
            "BROWSER_STATE_DIR": os.environ.get("BROWSER_STATE_DIR"),
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(root / 'ebk-read-all.db').as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        os.environ["BROWSER_STATE_DIR"] = str(root / "browser-state")

        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"],
            pool_pre_ping=True,
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine,
            expire_on_commit=False,
        )
        sessions = importlib.import_module("app.domain.browser.sessions")
        importlib.reload(sessions)
        sessions.reset_browser_session_store()

        from tests._db_isolation import create_isolated_test_shop

        shop_key = create_isolated_test_shop("读取汇总测试店铺")

        main = importlib.import_module("app.main")
        app = importlib.reload(main).create_app()
        client = TestClient(app)

        ctrip = client.post(
            "/api/v1/browser-sessions",
            json={
                "platform_account_id": "ctrip_read_all",
                "display_name": "Ctrip Read All",
                "platform": "ctrip",
                "intended_use": "ebk",
                "shop_key": shop_key,
                "session_port": 9601,
            },
        )
        meituan = client.post(
            "/api/v1/browser-sessions",
            json={
                "platform_account_id": "meituan_read_all",
                "display_name": "Meituan Read All",
                "platform": "meituan",
                "intended_use": "ebk",
                "connection_mode": "unknown",
                "shop_key": shop_key,
                "session_port": 9602,
            },
        )
        assert ctrip.status_code == 200, ctrip.text
        assert meituan.status_code == 200, meituan.text
        return {
            "client": client,
            "ctrip_session": ctrip.json(),
            "meituan_session": meituan.json(),
        }

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        # 复位全局引擎，避免后续测试继承指向已删临时库的 engine（顺序暴露的预存隐患）。
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
