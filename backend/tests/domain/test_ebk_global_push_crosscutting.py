"""EBK 全局推送横切改动测试：read_trigger 保留键、导出标签、成组裁剪。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.domain.ebk_snapshots import UnifiedEbkSnapshot
from app.domain.exports.ebk_sources import (
    _snapshot_source_label,
    _trim_by_groups,
    _watch_run_source_label,
    merge_export_snapshots,
    watch_runs_to_snapshots,
)
from tests.domain.test_ebk_snapshot_repository import (
    _isolated_sqlite_database,
    _reload_repository,
)


class ReadTriggerGlobalRunIdTests(unittest.TestCase):
    def test_snapshot_repository_persists_global_run_id(self) -> None:
        with _isolated_sqlite_database() as db_url:

            async def scenario() -> None:
                repository = _reload_repository(db_url)
                await repository.save_ebk_snapshot(
                    UnifiedEbkSnapshot(
                        platform="meituan",
                        platform_account_id="mt-account",
                        browser_session_id="private-session",
                        read_at="2026-08-28T12:00:00+08:00",
                        status="success",
                        summary="ok",
                        datasets={"workbench_home": {"status": "success", "data": {}}},
                    ),
                    read_trigger={
                        "actor_id": "ebk_global_push_scheduler",
                        "via": "global_push",
                        "global_run_id": "42",
                        "cookie": "must-not-persist",
                    },
                )
                latest = await repository.get_latest_ebk_snapshot(
                    platform="meituan",
                    platform_account_id="mt-account",
                )
                assert latest is not None
                self.assertEqual(
                    latest["payload_json"]["read_trigger"],
                    {
                        "actor_id": "ebk_global_push_scheduler",
                        "via": "global_push",
                        "global_run_id": "42",
                    },
                )

            asyncio.run(scenario())


class ExportSourceLabelTests(unittest.TestCase):
    def test_watch_run_global_push_label(self):
        self.assertEqual(
            _watch_run_source_label({"source": "global_push", "actor_id": "ebk_global_push_scheduler"}),
            "全局定时推送",
        )

    def test_snapshot_global_push_label(self):
        self.assertEqual(
            _snapshot_source_label(
                {"payload_json": {"read_trigger": {"via": "global_push"}}}
            ),
            "全局定时推送",
        )
        self.assertEqual(
            _snapshot_source_label(
                {"payload_json": {"read_trigger": {"actor_id": "ebk_global_push_scheduler"}}}
            ),
            "全局定时推送",
        )

    def test_unknown_still_unmarked(self):
        self.assertEqual(_watch_run_source_label({"source": "cron"}), "未标记")


class GroupedTrimTests(unittest.TestCase):
    def test_same_global_run_kept_together(self):
        group_a = [
            {"payload_json": {"read_trigger": {"global_run_id": "1"}}, "created_at": "2026-08-28T08:01:00"},
            {"payload_json": {"read_trigger": {"global_run_id": "1"}}, "created_at": "2026-08-28T08:03:00"},
        ]
        group_b = [
            {"payload_json": {"read_trigger": {"global_run_id": "2"}}, "created_at": "2026-08-28T09:00:00"},
        ]
        singles = [
            {"payload_json": {}, "created_at": "2026-08-28T10:00:00"},
            {"payload_json": {}, "created_at": "2026-08-28T11:00:00"},
        ]
        # limit=3：应保留最新 3 组 → group_b + 两个 single；group_a 整组裁掉
        result = _trim_by_groups(group_a + group_b + singles, 3)
        joined = [item.get("payload_json", {}).get("read_trigger", {}).get("global_run_id") for item in result]
        self.assertNotIn("1", joined)
        self.assertIn("2", joined)
        self.assertEqual(len(result), 3)

    def test_limit_zero_returns_empty(self):
        self.assertEqual(_trim_by_groups([{"created_at": "x"}], 0), [])


class ReaderGlobalRunIdPassthroughTests(unittest.TestCase):
    """T2.1：平台读取函数将 global_run_id 直通到 save_ebk_snapshot 的 read_trigger。"""

    def _call_reader(self, reader_name: str, service_getter: str, **extra):
        from app.domain.ebk_snapshots import ebk_snapshot_reader as reader

        captured: dict[str, object] = {}

        class FakeService:
            async def read_dataset(self, **_kwargs):
                return {"status": "success", "data": {}, "evidence": {}}

        async def fake_save(_snapshot, read_trigger=None):
            captured["trigger"] = dict(read_trigger or {})
            return {"id": 55, "dataset_count": 1}

        func = getattr(reader, reader_name)
        with (
            patch.object(reader, service_getter, return_value=FakeService()),
            patch.object(reader, "save_ebk_snapshot", fake_save),
        ):
            result = asyncio.run(
                func(
                    platform_account_id="acc-1",
                    browser_session_id="sess-1",
                    actor_id="ebk_global_push_scheduler",
                    trigger_via="global_push",
                    **extra,
                )
            )
        captured["result"] = result
        return captured

    def test_meituan_reader_persists_global_run_id(self):
        captured = self._call_reader(
            "read_meituan_platform_snapshot",
            "get_meituan_ebooking_service",
            global_run_id="42",
        )
        self.assertEqual(
            captured["trigger"],
            {
                "actor_id": "ebk_global_push_scheduler",
                "via": "global_push",
                "global_run_id": "42",
            },
        )

    def test_fliggy_reader_persists_global_run_id(self):
        captured = self._call_reader(
            "read_fliggy_platform_snapshot",
            "get_fliggy_ebooking_service",
            global_run_id="42",
        )
        self.assertEqual(captured["trigger"].get("global_run_id"), "42")

    def test_global_run_id_omitted_when_absent(self):
        captured = self._call_reader(
            "read_meituan_platform_snapshot",
            "get_meituan_ebooking_service",
        )
        self.assertEqual(
            captured["trigger"],
            {"actor_id": "ebk_global_push_scheduler", "via": "global_push"},
        )


def _ctrip_global_push_run(run_id: int, captured_at: str) -> dict:
    return {
        "run_id": run_id,
        "platform_account_id": "ctrip-account",
        "actor_id": "ebk_global_push_scheduler",
        "source": "global_push",
        "captured_at": captured_at,
        "results": [],
    }


class CtripRunGroupLinkTests(unittest.TestCase):
    """T2.2：携程 watch run 通过 source=global_push + 全局运行表关联进入成组裁剪。"""

    def test_watch_run_adapter_attaches_global_run_link(self):
        adapted = watch_runs_to_snapshots(
            [_ctrip_global_push_run(101, "2026-08-28T08:00:10")],
            global_run_links={"101": "42"},
        )
        trigger = adapted[0]["payload_json"].get("read_trigger", {})
        self.assertEqual(trigger.get("global_run_id"), "42")
        self.assertEqual(adapted[0]["export_source"], "全局定时推送")

    def test_watch_run_adapter_ignores_non_global_push_runs(self):
        run = _ctrip_global_push_run(101, "2026-08-28T08:00:10")
        run["source"] = "api"
        adapted = watch_runs_to_snapshots([run], global_run_links={"101": "42"})
        self.assertNotIn(
            "global_run_id", adapted[0]["payload_json"].get("read_trigger", {})
        )

    def test_merge_keeps_ctrip_run_grouped_with_platform_snapshots(self):
        meituan_same_run = {
            "platform": "meituan",
            "platform_account_id": "mt-account",
            "created_at": "2026-08-28T08:05:00",
            "payload_json": {
                "datasets": {},
                "read_trigger": {"via": "global_push", "global_run_id": "42"},
            },
        }
        newer_single = {
            "platform": "meituan",
            "platform_account_id": "mt-account",
            "created_at": "2026-08-28T09:00:00",
            "payload_json": {"datasets": {}},
        }
        merged = merge_export_snapshots(
            [meituan_same_run, newer_single],
            [_ctrip_global_push_run(101, "2026-08-28T08:00:10")],
            limit=2,
            ctrip_global_run_links={"101": "42"},
        )
        # limit=2 组预算：global_run_id=42 的一组（携程+美团）+ 一个单条，同组不拆散
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["platform"], "ctrip")
        run_ids = [
            item.get("payload_json", {}).get("read_trigger", {}).get("global_run_id")
            for item in merged
        ]
        self.assertEqual(run_ids.count("42"), 2)


class CtripGlobalPushSnapshotPersistTests(unittest.TestCase):
    """数据层基线修复（2026-09-01）：携程全局推送读取后补存 ebk_read_snapshots。

    旧缺陷：携程走 watch-group 链路只落 ctrip_ebooking_watch_runs，
    get_latest_success_ebk_snapshot("ctrip") 永远查不到全局推送数据，
    环比基线只能取到别的来源（如早 8:30 定时读）的旧快照。
    """

    RUN_PAYLOAD = {
        "status": "success",
        "dataset_results": [
            {
                "dataset_id": "business_outline",
                "status": "success",
                "captured_at": "2026-09-01T12:00:10+08:00",
                "data": {
                    "time_dimensions": {
                        "realtime": {
                            "status": "success",
                            "metrics": {
                                "realtime_booking_orders": {
                                    "label": "实时预订订单",
                                    "value": 12,
                                }
                            },
                        }
                    }
                },
            },
            {"dataset_id": "loss_analysis", "status": "success", "data": {}},
        ],
    }

    def _run_read_ctrip(self, run_payload):
        from types import SimpleNamespace

        from app.domain.ebk_global_push.service import default_deps
        from app.domain.ebk_snapshots import ebk_snapshot_reader as reader

        captured: dict[str, object] = {}

        class FakeWatchService:
            async def run_watch_group(self, **kwargs):
                return run_payload

        async def fake_save(snapshot, read_trigger=None, **kwargs):
            captured["snapshot"] = snapshot
            captured["trigger"] = dict(read_trigger or {})
            captured["persist_kwargs"] = dict(kwargs)
            return {"id": 9001, "dataset_count": len(snapshot.datasets)}

        group = SimpleNamespace(
            platform_account_id="ctrip-account", browser_session_id="ctrip-session"
        )
        deps = default_deps()
        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_data_center_watch_service",
                return_value=FakeWatchService(),
            ),
            patch.object(reader, "save_ebk_snapshot", fake_save),
        ):
            result = asyncio.run(deps.read_ctrip(group, global_run_id="42"))
        captured["result"] = result
        return captured

    def test_read_ctrip_persists_snapshot_with_global_push_trigger(self):
        captured = self._run_read_ctrip(self.RUN_PAYLOAD)

        # 返回值保持 run payload 原样（不改变构卡路径）
        self.assertEqual(captured["result"], self.RUN_PAYLOAD)

        snapshot = captured["snapshot"]
        self.assertEqual(snapshot.platform, "ctrip")
        self.assertEqual(snapshot.platform_account_id, "ctrip-account")
        self.assertEqual(snapshot.status, "success")
        self.assertEqual(
            set(snapshot.datasets.keys()), {"business_outline", "loss_analysis"}
        )
        outline = snapshot.datasets["business_outline"]
        self.assertEqual(outline["status"], "success")
        self.assertEqual(
            outline["data"]["time_dimensions"]["realtime"]["metrics"][
                "realtime_booking_orders"
            ]["value"],
            12,
        )
        self.assertEqual(
            captured["trigger"],
            {
                "actor_id": "ebk_global_push_scheduler",
                "via": "global_push",
                "global_run_id": "42",
            },
        )

    def test_persisted_snapshot_feeds_baseline_adapter(self):
        """补存快照序列化后必须能被携程适配器解析（供 _get_previous_metrics 用）。"""
        from app.domain.ebk_global_push.adapters import ctrip_rows_from_run_payload
        from app.domain.ebk_snapshots.types import serialize_unified_ebk_snapshot

        captured = self._run_read_ctrip(self.RUN_PAYLOAD)
        payload_json = serialize_unified_ebk_snapshot(captured["snapshot"])
        rows, _needs = ctrip_rows_from_run_payload(payload_json)
        matched = [
            row for row in rows if row.get("field_key") == "realtime_booking_orders"
        ]
        self.assertTrue(matched)
        self.assertEqual(matched[0].get("value"), 12)

    def test_read_ctrip_save_failure_does_not_break_read(self):
        """补存失败只告警，不阻断本次推送读取。"""
        from types import SimpleNamespace

        from app.domain.ebk_global_push.service import default_deps
        from app.domain.ebk_snapshots import ebk_snapshot_reader as reader

        run_payload = self.RUN_PAYLOAD

        class FakeWatchService:
            async def run_watch_group(self, **kwargs):
                return run_payload

        async def broken_save(_snapshot, read_trigger=None, **_kwargs):
            raise RuntimeError("db down")

        group = SimpleNamespace(
            platform_account_id="ctrip-account", browser_session_id="ctrip-session"
        )
        with (
            patch(
                "app.domain.ctrip_ebooking_watch.service.get_data_center_watch_service",
                return_value=FakeWatchService(),
            ),
            patch.object(reader, "save_ebk_snapshot", broken_save),
        ):
            result = asyncio.run(default_deps().read_ctrip(group, global_run_id="42"))
        self.assertEqual(result, run_payload)


if __name__ == "__main__":
    unittest.main()
