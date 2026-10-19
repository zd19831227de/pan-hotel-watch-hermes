from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any


class FakeWatchClient:
    def __init__(
        self,
        sessions: list[dict[str, Any]],
        *,
        chat_context: dict[str, Any] | None = None,
        watch_groups: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.sessions = sessions
        self.chat_context = chat_context
        # 平台监控组列表桩：{"meituan": [...], "fliggy": [...]}（锚点兜底用）。
        self.watch_groups = watch_groups or {}
        self.browser_session_calls = 0
        self.chat_context_calls = 0
        self.joint_kwargs: dict[str, Any] | None = None

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if path == "/api/v1/browser-sessions":
            self.browser_session_calls += 1
            return {"sessions": self.sessions}
        if path in (
            "/api/v1/meituan-ebk-watch/watch-groups",
            "/api/v1/fliggy-ebk-watch/watch-groups",
        ):
            platform = "meituan" if "meituan" in path else "fliggy"
            return {"watch_groups": self.watch_groups.get(platform, [])}
        raise AssertionError(path)

    async def get_feishu_chat_context(
        self,
        chat_id: str,
        *,
        store_hint: str | None = None,
    ) -> dict[str, Any]:
        self.chat_context_calls += 1
        if self.chat_context is None:
            raise AssertionError("chat context was not configured")
        return self.chat_context

    async def get_ebk_joint_analysis_context(self, **kwargs: Any) -> dict[str, Any]:
        self.joint_kwargs = kwargs
        return {"status": "success"}

    async def send_operator_feishu_card(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "kwargs": kwargs}


def _session(
    platform: str,
    account_id: str,
    display_name: str,
    *,
    intended_use: str = "ebk",
) -> dict[str, str]:
    return {
        "platform": platform,
        "intended_use": intended_use,
        "platform_account_id": account_id,
        "display_name": display_name,
    }


class EbkAccountResolveTests(unittest.TestCase):
    def test_joint_tool_uses_backend_ota_accounts_without_client_fuzzy_matching(self) -> None:
        """P6：三平台账号以后端 chat-context 的 ota_ebk_accounts 为准。

        客户端品牌词模糊匹配已退役（resolve_ebk_ota_account_ids 删除）；
        店铺→账号的过滤在后端完成（shop_key 精确/锚定兜底）。
        """

        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient(
                [],  # 客户端不再拉 /browser-sessions 做匹配
                chat_context={
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "store_label": "云州澜江路曼馨高客栈澜江路酒店",
                        "ebk_watch_group_key": "ebk_manxin",
                    },
                    "ebk_groups": [
                        {"group_key": "ebk_manxin", "platform_account_id": "manxin-xiecheng"}
                    ],
                    "ota_ebk_accounts": {
                        "ctrip": {"platform_account_id": "manxin-xiecheng"},
                        "meituan": {"platform_account_id": "manxin-meituan"},
                        "fliggy": {"platform_account_id": "manxin-feizhu"},
                    },
                },
            )

            result = await mcp_server._tool_get_ebk_joint_analysis_context(
                client,
                {"chat_id": "oc_manxin", "platforms": ["all"], "response_mode": "text"},
            )

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("status"), "success")
            assert client.joint_kwargs is not None
            self.assertEqual(client.joint_kwargs["ctrip_platform_account_id"], "manxin-xiecheng")
            self.assertEqual(client.joint_kwargs["meituan_platform_account_id"], "manxin-meituan")
            self.assertEqual(client.joint_kwargs["fliggy_platform_account_id"], "manxin-feizhu")
            # 账号解析不再读 /browser-sessions（模糊匹配退役的回归断言）。
            self.assertEqual(client.browser_session_calls, 0)

        asyncio.run(scenario())

    def test_joint_tool_uses_store_label_and_resolves_manxin_brand(self) -> None:
        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient(
                [
                    _session("ctrip", "manxin-xiecheng", "曼馨酒店"),
                    _session("meituan", "manxin-meituan", "曼馨酒店美团"),
                    _session("fliggy", "manxin-feizhu", "曼馨酒店飞猪"),
                    _session("ctrip", "ebk-2", "泉吉酒店"),
                ],
                chat_context={
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "store_label": "云州澜江路曼馨高客栈澜江路酒店",
                        "ebk_watch_group_key": "ebk_manxin",
                    },
                    "ebk_groups": [
                        {
                            "group_key": "ebk_manxin",
                            "platform_account_id": "manxin-xiecheng",
                        }
                    ],
                    "ota_ebk_accounts": {
                        "ctrip": {"platform_account_id": "manxin-xiecheng"},
                        "meituan": {"platform_account_id": "manxin-meituan"},
                        "fliggy": {"platform_account_id": "manxin-feizhu"},
                    },
                },
            )

            result = await mcp_server._tool_get_ebk_joint_analysis_context(
                client,
                {
                    "chat_id": "oc_manxin",
                    "platforms": ["all"],
                    "response_mode": "text",
                },
            )

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("status"), "success")
            assert client.joint_kwargs is not None
            self.assertEqual(client.joint_kwargs["ctrip_platform_account_id"], "manxin-xiecheng")
            self.assertEqual(client.joint_kwargs["meituan_platform_account_id"], "manxin-meituan")
            self.assertEqual(client.joint_kwargs["fliggy_platform_account_id"], "manxin-feizhu")

        asyncio.run(scenario())

    def test_joint_tool_falls_back_to_anchor_mounted_groups(self) -> None:
        """店名匹配不到平台账号时（云滨立瑞命名），按携程锚点挂载组兜底。"""

        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient(
                [
                    _session("ctrip", "liruiota", "立瑞携程ebk"),
                    # 「云滨立瑞」品牌词不含于店名「云州澜江路立瑞酒店」→ 名称解析落空
                    _session("meituan", "lirui", "云滨立瑞美团ebk"),
                    _session("fliggy", "hotel1100001", "云滨立瑞飞猪ebk"),
                ],
                chat_context={
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "store_label": "云州澜江路立瑞酒店",
                        "ebk_watch_group_key": "ebk_lirui",
                    },
                    "ebk_groups": [
                        {
                            "group_key": "ebk_lirui",
                            "platform_account_id": "liruiota",
                        }
                    ],
                    # 后端只给出锚定携程账号；美团/飞猪由锚点挂载组兜底。
                    "ota_ebk_accounts": {
                        "ctrip": {"platform_account_id": "liruiota"},
                        "meituan": None,
                        "fliggy": None,
                    },
                },
                watch_groups={
                    "meituan": [
                        {
                            "group_key": "meituan_jingli",
                            "name": "静里美团 EBK 监控组",
                            "platform_account_id": "JL1700000001",
                            "ctrip_watch_group_key": "ctrip_ebooking_data_center",
                        },
                        {
                            "group_key": "meituan_lirui",
                            "name": "云滨立瑞美团 EBK 监控组",
                            "platform_account_id": "lirui",
                            "ctrip_watch_group_key": "ebk_lirui",
                        },
                    ],
                    "fliggy": [
                        {
                            "group_key": "fliggy_lirui",
                            "name": "云滨立瑞飞猪 EBK 监控组",
                            "platform_account_id": "hotel1100001",
                            "ctrip_watch_group_key": "ebk_lirui",
                        },
                    ],
                },
            )

            result = await mcp_server._tool_get_ebk_joint_analysis_context(
                client,
                {
                    "chat_id": "oc_lirui",
                    "platforms": ["all"],
                    "response_mode": "text",
                },
            )

            payload = json.loads(result[0].text)
            self.assertEqual(payload.get("status"), "success")
            assert client.joint_kwargs is not None
            self.assertEqual(client.joint_kwargs["ctrip_platform_account_id"], "liruiota")
            self.assertEqual(client.joint_kwargs["meituan_platform_account_id"], "lirui")
            self.assertEqual(client.joint_kwargs["fliggy_platform_account_id"], "hotel1100001")

        asyncio.run(scenario())

    def test_joint_tool_fills_missing_ids_but_preserves_explicit_ids(self) -> None:
        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient(
                [
                    _session("ctrip", "auto-ctrip", "Alpha CTRIP"),
                    _session("meituan", "auto-meituan", "Alpha MEITUAN"),
                    _session("fliggy", "auto-fliggy", "Alpha FLIGGY"),
                ],
                chat_context={
                    "binding_mode": "single_store",
                    "resolution": {
                        "status": "resolved",
                        "store_name": "Alpha Hotel",
                        "ebk_watch_group_key": "alpha-ebk",
                    },
                    "ota_ebk_accounts": {
                        "ctrip": {"platform_account_id": "auto-ctrip"},
                        "meituan": {"platform_account_id": "auto-meituan"},
                        "fliggy": {"platform_account_id": "auto-fliggy"},
                    },
                },
            )

            result = await mcp_server._tool_get_ebk_joint_analysis_context(
                client,
                {
                    "chat_id": "oc_alpha",
                    "store_hint": "alpha",
                    "ctrip_platform_account_id": "explicit-ctrip",
                },
            )

            self.assertEqual(json.loads(result[0].text)["status"], "success")
            assert client.joint_kwargs is not None
            self.assertEqual(client.joint_kwargs["ctrip_platform_account_id"], "explicit-ctrip")
            self.assertEqual(client.joint_kwargs["meituan_platform_account_id"], "auto-meituan")
            self.assertEqual(client.joint_kwargs["fliggy_platform_account_id"], "auto-fliggy")
            self.assertEqual(client.joint_kwargs["platforms"], ["ctrip", "meituan", "fliggy"])

        asyncio.run(scenario())

    def test_joint_tool_blocks_ambiguous_store_before_account_or_joint_analysis(self) -> None:
        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient(
                [],
                chat_context={
                    "binding_mode": "multi_store",
                    "resolution": {
                        "status": "ambiguous",
                        "summary": "当前群绑定多家门店，请补充店名。",
                    },
                    "ebk_groups": [
                        {"group_key": "alpha-ebk"},
                        {"group_key": "beta-ebk"},
                    ],
                },
            )

            result = await mcp_server._tool_get_ebk_joint_analysis_context(
                client,
                {"chat_id": "oc_multi", "platforms": ["meituan"]},
            )

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "ambiguous_store")
            self.assertEqual(client.browser_session_calls, 0)
            self.assertIsNone(client.joint_kwargs)

        asyncio.run(scenario())

    def test_joint_tool_requires_chat_before_account_or_joint_analysis(self) -> None:
        async def scenario() -> None:
            try:
                from pan_hotel_mcp import server as mcp_server
            except ModuleNotFoundError as exc:
                raise unittest.SkipTest("mcp package required") from exc

            client = FakeWatchClient([])

            result = await mcp_server._tool_get_ebk_joint_analysis_context(client, {})

            payload = json.loads(result[0].text)
            self.assertEqual(payload["status"], "session_context_required")
            self.assertEqual(client.browser_session_calls, 0)
            self.assertIsNone(client.joint_kwargs)

        asyncio.run(scenario())

    def test_joint_tool_schema_accepts_store_hint_and_fliggy_account(self) -> None:
        try:
            from pan_hotel_mcp import server as mcp_server
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest("mcp package required") from exc

        tools = asyncio.run(mcp_server.list_tools())
        joint = next(tool for tool in tools if tool.name == "get_ebk_joint_analysis_context")
        properties = joint.inputSchema.get("properties", {})

        self.assertIn("store_hint", properties)
        self.assertIn("fliggy_platform_account_id", properties)


if __name__ == "__main__":
    unittest.main()
