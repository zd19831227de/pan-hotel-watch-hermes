from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.domain.ebk_snapshots import (
    UnifiedEbkSnapshot,
    serialize_unified_ebk_snapshot,
)


class UnifiedEbkSnapshotContractTests(unittest.TestCase):
    def test_serialize_ctrip_snapshot_has_required_contract_fields(self) -> None:
        snapshot = UnifiedEbkSnapshot(
            platform="ctrip",
            platform_account_id="ctrip_account_01",
            browser_session_id="bs_ctrip_01",
            read_at=datetime(2026, 7, 10, 9, 20, tzinfo=timezone.utc),
            status="success",
            summary="携程 EBK 经营概览读取成功。",
            datasets={
                "business_outline": {
                    "status": "success",
                    "label": "收益概览",
                    "data": {"metrics": {"occupancy_rate": {"value": "72%"}}},
                }
            },
            evidence={
                "parser": "ctrip_business_outline_dom_v1",
                "page_state": "authenticated",
                "session_port": 9222,
            },
            platform_data={
                "raw_dataset_id": "business_outline",
                "ctrip_query_type": "ctrip_ebooking_dataset",
            },
        )

        payload = serialize_unified_ebk_snapshot(snapshot)

        self.assertEqual(payload["platform"], "ctrip")
        self.assertEqual(payload["platform_account_id"], "ctrip_account_01")
        self.assertEqual(payload["browser_session_id"], "bs_ctrip_01")
        self.assertEqual(payload["read_at"], "2026-07-10T09:20:00+00:00")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["summary"], "携程 EBK 经营概览读取成功。")
        self.assertIn("business_outline", payload["datasets"])
        self.assertEqual(payload["datasets"]["business_outline"]["status"], "success")
        self.assertEqual(
            payload["platform_data"]["ctrip_query_type"],
            "ctrip_ebooking_dataset",
        )
        self.assertEqual(payload["evidence"]["parser"], "ctrip_business_outline_dom_v1")

    def test_serialize_meituan_snapshot_preserves_platform_data(self) -> None:
        snapshot = UnifiedEbkSnapshot(
            platform="meituan",
            platform_account_id="meituan_account_01",
            browser_session_id="bs_meituan_01",
            read_at="2026-07-10T09:35:00+08:00",
            status="partial",
            summary="美团数据中心部分数据集可读。",
            datasets={
                "data_center_home": {
                    "status": "success",
                    "label": "数据中心首页",
                    "data": {
                        "basic_operation_metrics": {
                            "支付订单数": {"value": "7 单"},
                        }
                    },
                },
                "data_center_trade": {
                    "status": "needs_review",
                    "label": "同行分析",
                    "data": {},
                },
            },
            evidence={"parser": "meituan_data_center_dom_v1", "page_state": "authenticated"},
            platform_data={
                "raw_dataset": "data_center_home",
                "meituan_tab_label": "首页",
                "basic_operation_metrics": {
                    "支付订单数": {"value": "7 单", "peer_rank": "7/11"},
                },
            },
        )

        payload = serialize_unified_ebk_snapshot(snapshot)

        self.assertEqual(payload["platform"], "meituan")
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["read_at"], "2026-07-10T09:35:00+08:00")
        self.assertEqual(
            payload["platform_data"]["meituan_tab_label"],
            "首页",
        )
        self.assertEqual(
            payload["datasets"]["data_center_trade"]["status"],
            "needs_review",
        )

    def test_serialize_strips_secrets_from_evidence(self) -> None:
        snapshot = UnifiedEbkSnapshot(
            platform="ctrip",
            platform_account_id="ctrip_account_01",
            browser_session_id="bs_ctrip_01",
            read_at="2026-07-10T10:00:00+08:00",
            status="human_required",
            summary="需要人工登录后重试。",
            datasets={},
            evidence={
                "page_state": "login_required",
                "cookie": "session=abc",
                "password": "secret-pass",
                "profile_path": r"C:\Users\Administrator\.runtime\browser-state\ctrip\acc",
                "verification_code": "123456",
                "screenshot_path": r"C:\Users\Administrator\Desktop\output\shot.png",
                "parser": "ctrip_login_check_v1",
            },
            platform_data={"note": "raw platform payload kept separately"},
        )

        payload = serialize_unified_ebk_snapshot(snapshot)
        evidence = payload["evidence"]

        self.assertEqual(payload["status"], "human_required")
        self.assertEqual(evidence["page_state"], "login_required")
        self.assertEqual(evidence["parser"], "ctrip_login_check_v1")
        self.assertNotEqual(evidence.get("cookie"), "session=abc")
        self.assertNotEqual(evidence.get("password"), "secret-pass")
        self.assertNotEqual(
            evidence.get("profile_path"),
            r"C:\Users\Administrator\.runtime\browser-state\ctrip\acc",
        )
        self.assertNotEqual(evidence.get("verification_code"), "123456")
        self.assertNotEqual(
            evidence.get("screenshot_path"),
            r"C:\Users\Administrator\Desktop\output\shot.png",
        )
        for forbidden in (
            "session=abc",
            "secret-pass",
            "browser-state",
            "123456",
            "shot.png",
        ):
            self.assertNotIn(forbidden, str(evidence))

    def test_rejects_unsupported_platform_and_status(self) -> None:
        # fliggy / meituan are now supported EBK platforms; use a genuinely
        # unsupported platform to exercise the rejection contract.
        with self.assertRaises(ValueError):
            UnifiedEbkSnapshot(
                platform="booking",
                platform_account_id="x",
                browser_session_id="y",
                read_at="2026-07-10T10:00:00+08:00",
                status="success",
                summary="no",
                datasets={},
            )
        with self.assertRaises(ValueError):
            UnifiedEbkSnapshot(
                platform="ctrip",
                platform_account_id="x",
                browser_session_id="y",
                read_at="2026-07-10T10:00:00+08:00",
                status="failed",
                summary="no",
                datasets={},
            )


if __name__ == "__main__":
    unittest.main()
