from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.domain.ai_agent.registry import (
    AgentToolCallContext,
    WatchControlToolArgs,
    WatchGroupToolArgs,
    get_default_agent_tool_registry,
)
from app.domain.ai_agent.tools import (
    _resolve_authorized_platform_watch_group_key,
    _set_platform_watch_enabled,
)


def _chat_context(ctrip_keys: list[str]) -> dict[str, Any]:
    return {
        "ebk_groups": [{"group_key": key} for key in ctrip_keys],
        "resolution": {"status": "resolved", "ebk_watch_group_key": ctrip_keys[0]}
        if ctrip_keys
        else {},
    }


def _platform_runtime(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "group_key": "mt_store_a",
        "name": "门店A美团监控",
        "platform_account_id": "mt-acc-1",
        "browser_session_id": "mt-session-1",
        "ctrip_watch_group_key": "ctrip_store_a",
        "enabled": True,
        "auto_poll_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PlatformWatchAuthorizationTests(unittest.TestCase):
    """平台监控组授权：按当前群绑定的携程组锚点解析（设计 §4-⑤）。"""

    def test_resolves_single_anchored_schedule_for_chat(self) -> None:
        async def run() -> None:
            meituan_repo = importlib.import_module(
                "app.domain.meituan_ebooking_watch.repository"
            )

            async def fake_by_anchor(*, ctrip_watch_group_key: str):
                if ctrip_watch_group_key == "ctrip_store_a":
                    return _platform_runtime()
                return None

            with (
                patch(
                    "app.domain.ops.feishu_chat_context.build_feishu_chat_context",
                    new=_fake_chat_context(["ctrip_store_a"]),
                ),
                patch.object(
                    meituan_repo,
                    "get_meituan_watch_group_runtime_by_ctrip_anchor",
                    fake_by_anchor,
                ),
            ):
                key, blocked = await _resolve_authorized_platform_watch_group_key(
                    context=AgentToolCallContext(event_id="evt", chat_id="oc_a"),
                    platform="meituan",
                    explicit_key=None,
                    tool_name="run_ebk_watch_once",
                )
            assert blocked is None
            assert key == "mt_store_a"

        asyncio.run(run())

    def test_explicit_key_outside_chat_anchor_is_rejected(self) -> None:
        async def run() -> None:
            meituan_repo = importlib.import_module(
                "app.domain.meituan_ebooking_watch.repository"
            )

            async def fake_get(*, watch_group_key: str):
                return _platform_runtime(ctrip_watch_group_key="ctrip_other")

            with (
                patch(
                    "app.domain.ops.feishu_chat_context.build_feishu_chat_context",
                    new=_fake_chat_context(["ctrip_store_a"]),
                ),
                patch.object(
                    meituan_repo, "get_meituan_watch_group_runtime", fake_get
                ),
            ):
                key, blocked = await _resolve_authorized_platform_watch_group_key(
                    context=AgentToolCallContext(event_id="evt", chat_id="oc_a"),
                    platform="meituan",
                    explicit_key="mt_store_b",
                    tool_name="run_ebk_watch_once",
                )
            assert key is None
            assert blocked is not None
            assert blocked.status == "watch_group_not_allowed"

        asyncio.run(run())


def _fake_chat_context(ctrip_keys: list[str]):
    async def fake_build(chat_id: str) -> dict[str, Any]:
        return _chat_context(ctrip_keys)

    return fake_build


class PlatformEbkToolTests(unittest.TestCase):
    """run_ebk_watch_once / get_ebk_latest_result / pause_resume 的平台分支。"""

    def setUp(self) -> None:
        from unittest import mock

        config_service = importlib.import_module("app.domain.config.service")
        self._patches = [
            patch(
                "app.domain.ops.feishu_chat_context.build_feishu_chat_context",
                new=_fake_chat_context(["ctrip_store_a"]),
            ),
            mock.patch.object(
                config_service,
                "resolve_feishu_role_for_chat",
                new=mock.AsyncMock(return_value="operator"),
            ),
        ]

    def _apply(self):
        for item in self._patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in self._patches])

    def test_run_ebk_watch_once_platform_meituan_invokes_platform_scheduler(self) -> None:
        self._apply()
        meituan_repo = importlib.import_module(
            "app.domain.meituan_ebooking_watch.repository"
        )
        scheduler_module = importlib.import_module(
            "app.domain.meituan_ebooking_watch.scheduler"
        )
        calls: list[str] = []

        async def fake_by_anchor(*, ctrip_watch_group_key: str):
            return _platform_runtime() if ctrip_watch_group_key == "ctrip_store_a" else None

        class FakeScheduler:
            def __init__(self, *, tick_seconds: int) -> None:
                calls.append("init")

            async def run_watch_group_now(self, *, watch_group_key: str, **kwargs):
                calls.append(watch_group_key)
                return {
                    "status": "success",
                    "summary": "美团 EBK 已读取并保存。",
                    "watch_group_key": watch_group_key,
                    "snapshot_id": 7,
                    "dataset_count": 4,
                    "feishu_push": {
                        "status": "success",
                        "feishu_webhook_url": "https://open.feishu.cn/hook/secret",
                    },
                }

        with (
            patch.object(
                meituan_repo,
                "get_meituan_watch_group_runtime_by_ctrip_anchor",
                fake_by_anchor,
            ),
            patch.object(
                scheduler_module, "MeituanEbookingWatchScheduler", FakeScheduler
            ),
        ):
            result = asyncio.run(
                get_default_agent_tool_registry().execute_tool(
                    "run_ebk_watch_once",
                    {"platform": "meituan"},
                    context=AgentToolCallContext(
                        event_id="evt-mt", chat_id="oc_a"
                    ),
                )
            )
        self.assertEqual(result.status, "success")
        self.assertEqual(calls, ["init", "mt_store_a"])
        self.assertEqual(result.data["platform"], "meituan")
        self.assertEqual(result.data["watch_group_key"], "mt_store_a")
        serialized = str(result.model_dump(mode="json"))
        self.assertNotIn("secret", serialized)
        self.assertTrue(result.evidence["scheduler_invoked"])
        self.assertFalse(result.evidence["ota_write_invoked"])

    def test_get_ebk_latest_result_platform_fliggy_reads_snapshot(self) -> None:
        self._apply()
        fliggy_repo = importlib.import_module(
            "app.domain.fliggy_ebooking_watch.repository"
        )
        snapshots_repo = importlib.import_module(
            "app.domain.ebk_snapshots.repository"
        )

        async def fake_by_anchor(*, ctrip_watch_group_key: str):
            if ctrip_watch_group_key != "ctrip_store_a":
                return None
            return SimpleNamespace(
                group_key="fl_store_a",
                name="门店A飞猪监控",
                platform_account_id="fl-acc-1",
            )

        async def fake_get_runtime(*, watch_group_key: str):
            return SimpleNamespace(
                group_key="fl_store_a",
                platform_account_id="fl-acc-1",
                ctrip_watch_group_key="ctrip_store_a",
            )

        async def fake_latest(*, platform: str, platform_account_id: str):
            return {
                "id": 11,
                "status": "success",
                "dataset_count": 3,
                "created_at": "2026-08-25T10:00:00+00:00",
                "payload_json": {"summary": "飞猪 EBK 已读取。"},
            }

        with (
            patch.object(
                fliggy_repo,
                "get_fliggy_watch_group_runtime_by_ctrip_anchor",
                fake_by_anchor,
            ),
            patch.object(
                fliggy_repo, "get_fliggy_watch_group_runtime", fake_get_runtime
            ),
            patch.object(snapshots_repo, "get_latest_ebk_snapshot", fake_latest),
        ):
            result = asyncio.run(
                get_default_agent_tool_registry().execute_tool(
                    "get_ebk_latest_result",
                    {"platform": "fliggy"},
                    context=AgentToolCallContext(
                        event_id="evt-fl", chat_id="oc_a"
                    ),
                )
            )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["platform"], "fliggy")
        self.assertEqual(result.data["snapshot_id"], 11)
        self.assertIn("飞猪", result.summary)

    def test_pause_watch_meituan_ebk_updates_platform_group(self) -> None:
        self._apply()
        meituan_repo = importlib.import_module(
            "app.domain.meituan_ebooking_watch.repository"
        )

        async def fake_by_anchor(*, ctrip_watch_group_key: str):
            return _platform_runtime() if ctrip_watch_group_key == "ctrip_store_a" else None

        updates: list[dict[str, Any]] = []

        async def fake_update(*, watch_group_key: str, patch: Any):
            updates.append(
                {
                    "watch_group_key": watch_group_key,
                    "enabled": patch.enabled,
                    "auto_poll_enabled": patch.auto_poll_enabled,
                }
            )
            return {
                "group_key": watch_group_key,
                "name": "门店A美团监控",
                "enabled": patch.enabled,
                "auto_poll_enabled": patch.auto_poll_enabled,
            }

        with (
            patch.object(
                meituan_repo,
                "get_meituan_watch_group_runtime_by_ctrip_anchor",
                fake_by_anchor,
            ),
            patch.object(meituan_repo, "update_meituan_watch_group", fake_update),
        ):
            result = asyncio.run(
                _set_platform_watch_enabled(
                    arguments=WatchControlToolArgs(watch_kind="meituan_ebk"),
                    context=AgentToolCallContext(event_id="evt-p", chat_id="oc_a"),
                    enabled=False,
                    auto_poll_enabled=False,
                    action_label="暂停",
                    tool_name="pause_watch",
                )
            )
        self.assertEqual(result.status, "success")
        self.assertEqual(updates, [
            {
                "watch_group_key": "mt_store_a",
                "enabled": False,
                "auto_poll_enabled": False,
            }
        ])

    def test_registry_accepts_platform_and_watch_kind_args(self) -> None:
        # 参数模型可解析新字段，且 registry 覆盖到平台分支。
        args = WatchGroupToolArgs(platform="meituan")
        self.assertEqual(args.platform, "meituan")
        control = WatchControlToolArgs(watch_kind="fliggy_ebk")
        self.assertEqual(control.watch_kind, "fliggy_ebk")


if __name__ == "__main__":
    unittest.main()
