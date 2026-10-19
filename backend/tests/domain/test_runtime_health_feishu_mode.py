from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.domain.ops import runtime_health


def _healthy(worker_name: str) -> dict[str, object]:
    return {
        "status": "healthy",
        "process_confirmed": True,
        "last_heartbeat_at": "2026-06-25T08:00:00+00:00",
        "heartbeat_age_seconds": 1,
        "last_error": None,
        "last_tick_summary": {},
    }


def _missing(worker_name: str) -> dict[str, object]:
    return {
        "status": "missing",
        "process_confirmed": False,
        "last_heartbeat_at": None,
        "heartbeat_age_seconds": None,
        "last_error": None,
        "last_tick_summary": {},
    }


class RuntimeHealthFeishuModeTests(unittest.TestCase):
    def test_hermes_mode_feishu_is_managed_external_and_not_attention(self) -> None:
        with (
            mock.patch.object(
                runtime_health,
                "get_worker_heartbeat_status",
                side_effect=lambda name, *a, **k: _healthy(name),
            ),
            mock.patch.object(runtime_health, "_feishu_agent_mode", return_value="hermes"),
        ):
            result = asyncio.run(runtime_health.get_runtime_health())

        feishu = result["modules"]["feishu_listener"]
        self.assertEqual(feishu["freshness_status"], "managed_external")
        self.assertTrue(feishu["process_confirmed"])
        self.assertEqual(feishu.get("managed_by"), "hermes_gateway")
        self.assertNotIn("feishu_listener", result["summary"]["attention_modules"])
        self.assertNotIn("feishu_listener", result["summary"]["stale_or_missing_modules"])
        self.assertEqual(result["summary"]["status"], "fresh")

    def test_hermes_mode_does_not_read_feishu_heartbeat(self) -> None:
        seen: list[str] = []

        def _fake(name: str, *a: object, **k: object) -> dict[str, object]:
            seen.append(name)
            return _healthy(name)

        with (
            mock.patch.object(runtime_health, "get_worker_heartbeat_status", side_effect=_fake),
            mock.patch.object(runtime_health, "_feishu_agent_mode", return_value="hermes"),
        ):
            asyncio.run(runtime_health.get_runtime_health())

        self.assertNotIn("feishu_listener", seen)

    def test_builtin_mode_feishu_missing_still_requires_attention(self) -> None:
        def _fake(name: str, *a: object, **k: object) -> dict[str, object]:
            if name == "feishu_listener":
                return _missing(name)
            return _healthy(name)

        with (
            mock.patch.object(runtime_health, "get_worker_heartbeat_status", side_effect=_fake),
            mock.patch.object(runtime_health, "_feishu_agent_mode", return_value="builtin"),
        ):
            result = asyncio.run(runtime_health.get_runtime_health())

        feishu = result["modules"]["feishu_listener"]
        self.assertEqual(feishu["freshness_status"], "missing")
        self.assertIn("feishu_listener", result["summary"]["attention_modules"])
        self.assertEqual(result["summary"]["status"], "attention_required")

    def test_healthy_heartbeat_with_all_failed_ticks_requires_attention(self) -> None:
        def _fake(name: str, *a: object, **k: object) -> dict[str, object]:
            if name == "competitor_price_scheduler":
                return {
                    "status": "healthy",
                    "process_confirmed": True,
                    "last_heartbeat_at": "2026-06-25T08:00:00+00:00",
                    "heartbeat_age_seconds": 1,
                    "last_error": None,
                    "last_tick_summary": {
                        "last_failed_count": 3,
                        "last_success_count": 0,
                    },
                }
            return _healthy(name)

        with (
            mock.patch.object(runtime_health, "get_worker_heartbeat_status", side_effect=_fake),
            mock.patch.object(runtime_health, "_feishu_agent_mode", return_value="hermes"),
        ):
            result = asyncio.run(runtime_health.get_runtime_health())

        competitor = result["modules"]["competitor_price_scheduler"]
        self.assertEqual(competitor["freshness_status"], "tick_failed")
        self.assertEqual(competitor["heartbeat_status"], "healthy")
        self.assertIn("competitor_price_scheduler", result["summary"]["attention_modules"])
        self.assertEqual(result["summary"]["status"], "attention_required")


if __name__ == "__main__":
    unittest.main()
