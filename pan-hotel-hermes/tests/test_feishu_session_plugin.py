from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def _load_plugin_module():
    plugin_path = (
        Path(__file__).resolve().parents[1]
        / "hermes_plugin"
        / "pan-hotel-feishu-session"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("pan_hotel_feishu_session", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pan_hotel_feishu_session"] = module
    spec.loader.exec_module(module)
    return module


class FeishuSessionPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = _load_plugin_module()

    def test_pre_llm_prunes_failed_tool_exchange_but_keeps_current_question(self) -> None:
        messages = [
            {"role": "user", "content": "刷新竞对"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old"}]},
            {"role": "tool", "content": '{"status": "failed", "summary": "读取超时"}'},
            {"role": "user", "content": "给我三个平台的 EBK 经营简报"},
        ]

        self.plugin._prune_failed_tool_history(messages)

        self.assertEqual(
            messages,
            [{"role": "user", "content": "给我三个平台的 EBK 经营简报"}],
        )

    def test_prune_failed_tool_exchange_removes_assistant_tool_call_pair(self) -> None:
        messages = [
            {"role": "user", "content": "刷新竞对"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "old", "function": {"name": "run_competitor_watch_once"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "old",
                "content": '{"status":"failed","summary":"读取超时"}',
            },
            {"role": "user", "content": "给我三个平台的 EBK 经营简报"},
        ]

        self.plugin._prune_failed_tool_history(messages)

        self.assertEqual(
            messages,
            [{"role": "user", "content": "给我三个平台的 EBK 经营简报"}],
        )

    def test_pre_llm_injects_authoritative_route_before_first_model_call(self) -> None:
        history = [
            {"role": "tool", "content": '{"status":"failed","summary":"读取超时"}'},
        ]
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_route", "oc_route", "ou_user"),
        ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
            result = self.plugin._on_pre_llm_call(
                session_id="sess_route",
                user_message="给我今天三端简报",
                conversation_history=history,
            )

        self.assertEqual(result.get("route_intent"), "generic_agent")
        self.assertIn("通用运营助手模式", result.get("context") or "")
        self.assertIn("get_ebk_joint_analysis_context", result.get("context") or "")
        self.assertEqual(history, [])

    def test_pre_tool_uses_route_saved_by_pre_llm_not_stale_chat_text(self) -> None:
        # 2026-10-14 全量 LLM 先行：所有问法（含查房价/调价句）都落
        # generic_agent。「保存路由优先于陈旧 chat 文本」仍成立：pre-LLM
        # 保存路由后，即便 USER_TEXT 被换成别的问法，门禁仍按已保存路由
        # 放行 dispatch、拦截特权工具。
        chat_id = "oc_saved_route"
        session_id = "sess_saved_route"
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_saved_route", chat_id, "ou_user"),
        ):
            self.plugin._on_pre_llm_call(
                session_id=session_id,
                user_message="查一下今天携程房价",
                conversation_history=[],
            )
            self.plugin._USER_TEXT_BY_CHAT[chat_id] = "现在竞品多少钱"
            allowed = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_dispatch_watch_feishu_event",
                args={},
                session_id=session_id,
            )
            blocked = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_create_manual_pricing_approval",
                args={},
                session_id=session_id,
            )

        self.assertIsNone(allowed)
        self.assertEqual(blocked.get("action"), "block")

    def test_fallback_assist_route_allows_live_reads_within_budget(self) -> None:
        # 2026-10-09 通用 agent 模式：一切非安全通道问法进 generic_agent，
        # 全目录放行（含 run_* 现读），refresh 参数不钳回；run_* 每轮至多
        # 2 次，第 3 次 block。
        chat_id = "oc_fallback_assist"
        session_id = "sess_fallback_assist"
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_fb", chat_id, "ou_user"),
        ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
            pre = self.plugin._on_pre_llm_call(
                session_id=session_id,
                user_message="帮我盘一下我们酒店最近的整体表现给个结论",
                conversation_history=[],
            )
            self.assertIn("通用", pre.get("context") or "")

            joint_args = {"chat_id": chat_id, "refresh_before_analysis": True}
            joint = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                args=joint_args,
                session_id=session_id,
            )
            self.assertIsNone(joint)
            self.assertTrue(joint_args["refresh_before_analysis"])

            run_tool = "mcp_pan_hotel_watch_run_competitor_watch_once"
            self.assertIsNone(
                self.plugin._on_pre_tool_call(tool_name=run_tool, args={}, session_id=session_id)
            )
            self.assertIsNone(
                self.plugin._on_pre_tool_call(tool_name=run_tool, args={}, session_id=session_id)
            )
            blocked = self.plugin._on_pre_tool_call(tool_name=run_tool, args={}, session_id=session_id)
            self.assertEqual(blocked.get("action"), "block")
            self.assertIn("上限", blocked.get("message") or "")

    def test_live_read_budget_covers_refund_today_and_dataset_reads(self) -> None:
        # 2026-10-08 补全：退款/今日预定 MCP 侧默认 refresh=true、read_ebk_datasets
        # 本身即现读——与 run_* 共享每轮 2 次预算，防无限拉浏览器旁路。
        chat_id = "oc_live_budget"
        session_id = "sess_live_budget"
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_lb", chat_id, "ou_user"),
        ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
            self.plugin._on_pre_llm_call(
                session_id=session_id,
                user_message="帮我盘一下我们酒店最近的整体表现给个结论",
                conversation_history=[],
            )
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_refund_status", args={}, session_id=session_id
                )
            )
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_read_ebk_datasets", args={}, session_id=session_id
                )
            )
            blocked = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_today_reservations",
                args={},
                session_id=session_id,
            )
            self.assertEqual(blocked.get("action"), "block")
            self.assertIn("上限", blocked.get("message") or "")
            # 纯落库只读工具不受预算影响。
            self.assertIsNone(
                self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_latest_prices",
                    args={},
                    session_id=session_id,
                )
            )

    def test_fallback_turn_records_learned_route_after_answer(self) -> None:
        # 探索→记录：通用轮用了数据工具并成功送达回答后，问法+工具集
        # 写入学习库；下一轮同问法携带工具先验（generic_agent 软提示）。
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from client import learned_routes

        chat_id = "oc_learn_record"
        session_id = "sess_learn_record"
        phrase = "帮我盘一下我们酒店最近的整体表现给个结论"
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                learned_routes, "STORE_PATH", Path(tmp) / "learned_routes.json"
            ), mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_lr", chat_id, "ou_user"),
            ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
                self.plugin._on_pre_llm_call(
                    session_id=session_id,
                    user_message=phrase,
                    conversation_history=[],
                )
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(
                        tool_name="mcp_pan_hotel_watch_get_ebk_latest_result",
                        args={},
                        session_id=session_id,
                    )
                )
                self.plugin._on_transform_tool_result(
                    tool_name="mcp_pan_hotel_watch_get_ebk_latest_result",
                    result=json.dumps({"status": "ok", "summary": "经营简报"}),
                    session_id=session_id,
                )
                with mock.patch.object(
                    self.plugin, "_resolve_chat_id", return_value=chat_id
                ), mock.patch.object(self.plugin, "_send_text_as_card", return_value=True):
                    self.plugin._on_transform_llm_output(
                        response_text="近7天整体表现结论…",
                        session_id=session_id,
                        platform="feishu",
                    )
                tools = learned_routes.lookup_learned_tools(phrase)
        self.assertIsNotNone(tools)
        self.assertIn("get_ebk_latest_result", tools or ())

    def test_fallback_turn_without_tools_not_recorded(self) -> None:
        # 纯闲聊兜底回答（没调数据工具）不进学习库。
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from client import learned_routes

        session_id = "sess_learn_skip"
        phrase = "帮我盘一下我们酒店最近的整体表现给个结论"
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                learned_routes, "STORE_PATH", Path(tmp) / "learned_routes.json"
            ), mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_ls", "oc_ls", "ou_ls"),
            ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True):
                self.plugin._on_pre_llm_call(
                    session_id=session_id,
                    user_message=phrase,
                    conversation_history=[],
                )
                with mock.patch.object(
                    self.plugin, "_resolve_chat_id", return_value="oc_ls"
                ), mock.patch.object(self.plugin, "_send_text_as_card", return_value=True):
                    self.plugin._on_transform_llm_output(
                        response_text="直接文字回答，没有工具。",
                        session_id=session_id,
                        platform="feishu",
                    )
                self.assertIsNone(learned_routes.lookup_learned_tools(phrase))


    def test_live_read_budget_env_override_tightens_limit(self) -> None:
        # 2026-10-09：HERMES_LIVE_READ_BUDGET 可调（1~6）。设为 1 时第二次
        # 现读即 block；恢复默认后回到 2 次额度。
        chat_id = "oc_budget_env"
        session_id = "sess_budget_env"
        tool = "mcp_pan_hotel_watch_run_competitor_watch_once"
        try:
            with mock.patch.dict(os.environ, {"HERMES_LIVE_READ_BUDGET": "1"}), mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_be", chat_id, "ou_user"),
            ):
                self.plugin._on_pre_llm_call(
                    session_id=session_id,
                    user_message="刷新竞品看看最新价格",
                    conversation_history=[],
                )
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id)
                )
                blocked = self.plugin._on_pre_tool_call(
                    tool_name=tool, args={}, session_id=session_id
                )
                self.assertEqual(blocked.get("action"), "block")
                self.assertIn("上限", blocked.get("message") or "")
        finally:
            self.plugin._HEAVY_CALLS_BY_SESSION.pop(session_id, None)
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_be2", chat_id, "ou_user"),
        ):
            session_id2 = "sess_budget_env_default"
            self.plugin._on_pre_llm_call(
                session_id=session_id2,
                user_message="刷新竞品看看最新价格",
                conversation_history=[],
            )
            try:
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id2)
                )
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(tool_name=tool, args={}, session_id=session_id2)
                )
                blocked = self.plugin._on_pre_tool_call(
                    tool_name=tool, args={}, session_id=session_id2
                )
                self.assertEqual(blocked.get("action"), "block")
            finally:
                self.plugin._HEAVY_CALLS_BY_SESSION.pop(session_id2, None)

    def test_pre_tool_blocks_when_user_text_missing(self) -> None:
        # 未捕获到用户消息（无路由上下文）时门禁兜底拦截。
        with mock.patch.object(self.plugin, "_session_fields", return_value=("", "", "")):
            blocked = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_run_competitor_watch_once",
                args={},
                session_id="sess_missing_route",
            )

        self.assertEqual(blocked.get("action"), "block")
        self.assertIn("本轮", blocked.get("message") or "")

    def test_pre_tool_does_not_reuse_route_across_message_ids(self) -> None:
        session_id = "sess_message_bound_route"
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_old_route", "oc_route", "ou_user"),
        ):
            self.plugin._on_pre_llm_call(
                session_id=session_id,
                user_message="给我三端经营分析",
                conversation_history=[],
            )

        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_new_route", "oc_route", "ou_user"),
        ):
            blocked = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                args={},
                session_id=session_id,
            )

        self.assertEqual(blocked.get("action"), "block")
        self.assertIn("本轮", blocked.get("message") or "")

    def test_watch_api_base_url_reads_mcp_config(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HERMES_HOME": str(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hermes-home")},
            clear=False,
        ):
            config_dir = Path(os.environ["HERMES_HOME"])
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "config.yaml").write_text(
                "mcp_servers:\n  pan-hotel-watch:\n    env:\n      WATCH_API_BASE_URL: http://127.0.0.1:8100\n",
                encoding="utf-8",
            )
            self.assertEqual(self.plugin._watch_api_base_url(), "http://127.0.0.1:8100")

    def test_send_text_as_card_posts_to_backend_with_service_token(self) -> None:
        with (
            mock.patch.dict(os.environ, {"WATCH_API_SHARED_SECRET": "tok-123"}, clear=False),
            mock.patch("urllib.request.urlopen") as urlopen,
        ):
            urlopen.return_value.__enter__.return_value.status = 200
            sent = self.plugin._send_text_as_card(
                chat_id="oc_group",
                text="运营简报内容",
                title="运营助手",
            )
        self.assertTrue(sent)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertIn("/api/v1/feishu/operator-card-sends", request.full_url)
        self.assertEqual(request.headers.get("X-pan-hotel-service-token"), "tok-123")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["chat_id"], "oc_group")
        self.assertEqual(body["text"], "运营简报内容")

    def test_forward_watch_feishu_event_sends_service_token_header(self) -> None:
        with (
            mock.patch.dict(os.environ, {"WATCH_API_SHARED_SECRET": "tok-123"}, clear=False),
            mock.patch("urllib.request.urlopen") as urlopen,
        ):
            urlopen.return_value.__enter__.return_value.status = 200
            forwarded = self.plugin._forward_watch_feishu_event(
                {"type": "url_verification"}
            )
        self.assertTrue(forwarded)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertIn("/api/v1/feishu/events", request.full_url)
        self.assertEqual(request.headers.get("X-pan-hotel-service-token"), "tok-123")

    def test_send_text_as_card_returns_false_without_service_token(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "WATCH_API_SHARED_SECRET"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            sent = self.plugin._send_text_as_card(
                chat_id="oc_group",
                text="无令牌不应投递",
                title="运营助手",
            )
        self.assertFalse(sent)
        urlopen.assert_not_called()

    def test_transform_llm_output_renders_plain_reply_as_card(self) -> None:
        with (
            mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"),
            mock.patch.object(self.plugin, "_send_text_as_card", return_value=True) as send_mock,
        ):
            result = self.plugin._on_transform_llm_output(
                response_text="你好，这是普通回复。",
                session_id="sess_1",
                platform="feishu",
            )
        send_mock.assert_called_once_with(chat_id="oc_group", text="你好，这是普通回复。")
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_transform_llm_output_falls_back_to_text_when_card_send_fails(self) -> None:
        with (
            mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"),
            mock.patch.object(self.plugin, "_send_text_as_card", return_value=False) as send_mock,
        ):
            result = self.plugin._on_transform_llm_output(
                response_text="你好，这是普通回复。",
                session_id="sess_2",
                platform="feishu",
            )
        send_mock.assert_called_once()
        self.assertIsNone(result)

    def test_transform_llm_output_preserves_media_reply_for_native_delivery(self) -> None:
        # 2026-10-09 生产实证（静里 10:38「前三天竞争圈排名，给一份图表」）：
        # 回复带 MEDIA:<本地PNG> 时若走发卡链路，钩子返回隐形占位符后网关
        # 原生附件投递（base.py extract_media→send_multiple_images）只能在
        # 占位文本上提取，MEDIA 标签被吞、图永远发不出。带真实附件的回复
        # 必须原样放行（返回 None）给原生管线提图上传。
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "jingli_ctrip_competition_rank.png"
            chart.write_bytes(b"\x89PNG\r\n\x1a\n")
            reply = f"📊 前三天竞争圈排名结论如下。\n\nMEDIA:{chart}"
            with (
                mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"),
                mock.patch.object(self.plugin, "_send_text_as_card", return_value=True) as send_mock,
            ):
                result = self.plugin._on_transform_llm_output(
                    response_text=reply,
                    session_id="sess_media_reply",
                    platform="feishu",
                )
        send_mock.assert_not_called()
        self.assertIsNone(result)

    def test_transform_llm_output_still_cards_when_media_path_missing(self) -> None:
        # 附件路径不存在时不得放行：原生管线提取不到文件，MEDIA 行会以裸
        # 文本露出；维持既有发卡口径。
        reply = "图在下方\nMEDIA:C:\\nonexistent_dir\\chart.png"
        with (
            mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"),
            mock.patch.object(self.plugin, "_send_text_as_card", return_value=True) as send_mock,
        ):
            result = self.plugin._on_transform_llm_output(
                response_text=reply,
                session_id="sess_media_missing",
                platform="feishu",
            )
        send_mock.assert_called_once()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_transform_llm_output_sanitizes_markdown_for_native_feishu_text(self) -> None:
        markdown_reply = """## 云州市江汉区近期事件
---
*（外部信息 / 非本店读数）*

**1. 云州国际会展活动**
• 预计带来阶段性客流。

| 日期 | 事件 |
| --- | --- |
| 7月28日 | 展会开幕 |

```text
建议店长提前关注交通变化。
```
"""

        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"):
            result = self.plugin._on_transform_llm_output(
                response_text=markdown_reply,
                session_id="sess_markdown_reply",
                platform="feishu",
            )

        self.assertIsInstance(result, str)
        self.assertIn("云州市江汉区近期事件", result)
        self.assertIn("外部信息 / 非本店读数", result)
        self.assertIn("• 预计带来阶段性客流。", result)
        self.assertIn("日期 · 事件", result)
        self.assertIn("7月28日 · 展会开幕", result)
        self.assertIn("建议店长提前关注交通变化。", result)
        self.assertNotIn("#", result)
        self.assertNotIn("---", result)
        self.assertNotIn("*（", result)
        self.assertNotIn("**", result)
        self.assertNotIn("```", result)
        self.assertNotIn("|", result)

    def test_transform_llm_output_suppresses_disallowed_chat(self) -> None:
        with mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False), mock.patch.object(
            self.plugin,
            "_resolve_chat_id",
            return_value="oc_other",
        ), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text="regular operator reply",
                session_id="sess_disallowed",
                platform="feishu",
            )

        send_mock.assert_not_called()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_transform_llm_output_skips_second_card_when_tool_sent(self) -> None:
        self.plugin._CARD_SENT_BY_SESSION["sess_2"] = True
        try:
            with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
                self.plugin,
                "_send_text_as_card",
                return_value=True,
            ) as send_mock:
                result = self.plugin._on_transform_llm_output(
                    response_text="重复摘要",
                    session_id="sess_2",
                    platform="feishu",
                )
            # 新行为：不再因 card_sent 短路，统一由 _send_text_as_card 决定发卡。
            send_mock.assert_called_once()
            self.assertEqual(result, self.plugin._INVISIBLE_ACK)
        finally:
            self.plugin._CARD_SENT_BY_SESSION.pop("sess_2", None)

    def test_transform_llm_output_suppresses_after_tool_card_even_without_chat_lookup(self) -> None:
        self.plugin._CARD_SENT_BY_SESSION["sess_card_without_chat"] = True
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value=""), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text="不应可见的重复回复",
                session_id="sess_card_without_chat",
                platform="feishu",
            )

        send_mock.assert_not_called()
        # 无 chat_id 时无法发卡；文本经清洗无变化时返回 None（不覆盖原生回复）。
        self.assertIsNone(result)
        self.assertNotIn("sess_card_without_chat", self.plugin._CARD_SENT_BY_SESSION)

    def test_transform_llm_output_suppresses_plain_tool_preamble(self) -> None:
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text="好的，我先查一下日历价配置详情。",
                session_id="sess_preamble",
                platform="feishu",
            )

        send_mock.assert_not_called()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_transform_llm_output_renders_substantive_calendar_status_as_card(self) -> None:
        text = "日历价自动轮询已开启，间隔 30 分钟；上次读取是 2026-07-08 10:31。"
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text=text,
                session_id="sess_status",
                platform="feishu",
            )

        send_mock.assert_called_once_with(chat_id="oc_group", text=text)
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_pre_gateway_dispatch_forwards_card_action_to_watch(self) -> None:
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt-card",
                "event_type": "card.action.trigger",
                "app_id": "cli_source_app",
            },
            "event": {
                "operator": {"operator_id": {"open_id": "ou_operator"}},
                "context": {"open_chat_id": "oc_group"},
                "message_id": "om_card",
                "action": {
                    "value": {
                        "workflow": "pricing_approval",
                        "preview_id": 123,
                        "decision": "approve",
                    }
                },
            },
        }

        event = mock.Mock(raw_message=payload)
        with mock.patch.object(self.plugin, "_forward_watch_feishu_event", return_value=True) as forward_mock:
            result = self.plugin._on_pre_gateway_dispatch(event=event)

        forward_mock.assert_called_once_with(payload)
        self.assertEqual(result, {"action": "skip", "reason": "pan_hotel_watch_feishu_event_forwarded"})

    def test_pre_gateway_dispatch_skips_disallowed_chat(self) -> None:
        payload = {
            "schema": "2.0",
            "header": {"event_id": "evt-msg", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "chat_id": "oc_other",
                    "message_id": "om_msg",
                    "content": json.dumps({"text": "@bot daily report"}),
                },
            },
        }

        event = mock.Mock(raw_message=payload)
        with mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False), mock.patch.object(
            self.plugin,
            "_forward_watch_feishu_event",
            return_value=True,
        ) as forward_mock:
            result = self.plugin._on_pre_gateway_dispatch(event=event)

        forward_mock.assert_not_called()
        self.assertEqual(result, {"action": "skip", "reason": "feishu_chat_not_allowed"})

    def test_strict_routing_denies_chat_when_allowlist_is_empty(self) -> None:
        payload = {
            "schema": "2.0",
            "header": {"event_id": "evt-strict", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "chat_id": "oc_unmapped",
                    "message_id": "om_strict",
                    "content": json.dumps({"text": "@bot 经营简报"}),
                },
            },
        }

        with mock.patch.dict(
            os.environ,
            {
                "FEISHU_STRICT_TENANT_ROUTING": "true",
                "FEISHU_ALLOWED_CHATS": "",
            },
            clear=False,
        ), mock.patch.object(
            self.plugin,
            "_forward_watch_feishu_event",
            return_value=True,
        ) as forward_mock:
            result = self.plugin._on_pre_gateway_dispatch(
                event=mock.Mock(raw_message=payload)
            )

        forward_mock.assert_not_called()
        self.assertEqual(result, {"action": "skip", "reason": "feishu_chat_not_allowed"})

    def test_compatibility_mode_keeps_empty_allowlist_behavior(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "FEISHU_STRICT_TENANT_ROUTING": "false",
                "FEISHU_ALLOWED_CHATS": "",
            },
            clear=False,
        ):
            self.assertTrue(self.plugin._is_allowed_chat_id("oc_legacy"))

    def test_pre_tool_call_does_not_inject_disallowed_chat(self) -> None:
        args: dict[str, str] = {}
        with mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False), mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_msg", "oc_other", "ou_user"),
        ):
            result = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
                args={**args, "user_text": "今天经营简报"},
                session_id="sess_tool",
            )

        self.assertIsNone(result)
        self.assertEqual(args, {})

    def tearDown(self) -> None:
        for key in list(self.plugin._CARD_SENT_BY_SESSION):
            if key.startswith("sess_"):
                self.plugin._CARD_SENT_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_CARD_SENT_MESSAGE_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._CARD_SENT_MESSAGE_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_USER_TEXT_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._USER_TEXT_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_ROUTE_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._ROUTE_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_ROUTE_MESSAGE_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._ROUTE_MESSAGE_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_TOOLS_USED_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._TOOLS_USED_BY_SESSION.pop(key, None)
        for key in list(getattr(self.plugin, "_HEAVY_CALLS_BY_SESSION", {})):
            if key.startswith("sess_"):
                self.plugin._HEAVY_CALLS_BY_SESSION.pop(key, None)

    def test_generic_mode_allows_competitor_reads_and_budgets_run_now(self) -> None:
        # 2026-10-09 通用模式：非刷新话法下 analyze/latest 直接放行（LLM 自主
        # 选工具）；run_* 也放行但受每轮预算控制（第 3 次 block）。
        chat_id = "oc_comp_change"
        self.plugin._USER_TEXT_BY_CHAT[chat_id] = "竞品价格最近变化情况如何"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_comp", chat_id, "ou_user"),
            ):
                allowed_analyze = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_analyze_competitor_price_change",
                    args={},
                    session_id="sess_comp_change",
                )
                allowed_latest = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_latest_prices",
                    args={},
                    session_id="sess_comp_change",
                )
                run_tool = "mcp_pan_hotel_watch_run_competitor_watch_once"
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(tool_name=run_tool, args={}, session_id="sess_comp_change")
                )
                self.assertIsNone(
                    self.plugin._on_pre_tool_call(tool_name=run_tool, args={}, session_id="sess_comp_change")
                )
                blocked_run = self.plugin._on_pre_tool_call(
                    tool_name=run_tool, args={}, session_id="sess_comp_change"
                )
            self.assertIsNone(allowed_analyze)
            self.assertIsNone(allowed_latest)
            self.assertEqual(blocked_run.get("action"), "block")
            self.assertIn("上限", str(blocked_run.get("message") or ""))
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop(chat_id, None)
            self.plugin._HEAVY_CALLS_BY_SESSION.pop("sess_comp_change", None)

    def test_allows_competitor_run_now_when_user_asks_refresh(self) -> None:
        chat_id = "oc_comp_refresh"
        self.plugin._USER_TEXT_BY_CHAT[chat_id] = "刷新竞品"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_refresh", chat_id, "ou_user"),
            ):
                result = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_run_competitor_watch_once",
                    args={},
                    session_id="sess_comp_refresh",
                )
            self.assertIsNone(result)
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop(chat_id, None)

    def test_pre_tool_call_allows_ebk_refresh_and_blocks_execution_tools(self) -> None:
        # 2026-10-09 通用模式：run_ebk 放行（LLM 按需现读），概览等读工具也
        # 放行；执行侧工具（审批卡）仍硬拦；会话注入照常。
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_msg", "oc_group", "ou_user"),
        ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
            self.plugin._on_pre_llm_call(
                session_id="sess_ebk_refresh",
                user_message="刷新 EBK",
                conversation_history=[],
            )
            allowed = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_run_ebk_watch_once",
                args={},
                session_id="sess_ebk_refresh",
            )
            allowed_overview = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
                args={},
                session_id="sess_ebk_refresh",
            )
            blocked = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_create_manual_pricing_approval",
                args={},
                session_id="sess_ebk_refresh",
            )
        self.assertIsNone(allowed)
        self.assertIsNone(allowed_overview)
        self.assertEqual(blocked.get("action"), "block")
        self.assertIn("审批", str(blocked.get("message") or ""))
        inject_mock.assert_called()

    def test_joint_intent_allows_only_joint_analysis_and_context(self) -> None:
        allowed_tools = (
            "mcp_pan_hotel_watch_resolve_feishu_chat_context",
            "mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
        )
        chat_id = "oc_joint"
        self.plugin._USER_TEXT_BY_CHAT[chat_id] = "综合分析今日各平台经营情况"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_joint", chat_id, "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                for tool_name in allowed_tools:
                    with self.subTest(tool_name=tool_name):
                        allowed = self.plugin._on_pre_tool_call(
                            tool_name=tool_name,
                            args={},
                            session_id="sess_joint",
                        )
                        self.assertIsNone(allowed)
                self.assertEqual(inject_mock.call_count, len(allowed_tools))
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop(chat_id, None)

    def test_routing_matrix_allows_non_joint_intents(self) -> None:
        """绑定 / 促销 / 出租率不得被 joint 硬拦误伤。"""
        cases = (
            (
                "本群绑了哪家店",
                "mcp_pan_hotel_watch_resolve_feishu_chat_context",
            ),
            (
                "参加了哪些促销，金字塔今天投放",
                "mcp_pan_hotel_watch_read_ebk_datasets",
            ),
            (
                "现在读一次出租率",
                "mcp_pan_hotel_watch_read_ebk_datasets",
            ),
        )
        for index, (user_text, allowed_tool) in enumerate(cases):
            chat_id = f"oc_allow_{index}"
            self.plugin._USER_TEXT_BY_CHAT[chat_id] = user_text
            try:
                with mock.patch.object(
                    self.plugin,
                    "_session_fields",
                    return_value=(f"om_allow_{index}", chat_id, "ou_user"),
                ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                    allowed = self.plugin._on_pre_tool_call(
                        tool_name=allowed_tool,
                        args={},
                        session_id=f"sess_allow_{index}",
                    )
                    self.assertIsNone(allowed)
                    inject_mock.assert_called_once()
            finally:
                self.plugin._USER_TEXT_BY_CHAT.pop(chat_id, None)

    def test_joint_brief_allows_joint_and_ctrip_datasets_in_generic_mode(self) -> None:
        # 2026-10-09 通用模式：多平台促销/金字塔问法下 joint 与
        # read_ebk_datasets 都放行——目录选择交给 LLM（pre-LLM 简报已注明
        # read_ebk_datasets 是携程监控组口径、多平台用 joint）。
        chat_id = "oc_promo_metric"
        self.plugin._USER_TEXT_BY_CHAT[chat_id] = "综合分析各平台促销效果和广告金字塔"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_promo", chat_id, "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                allowed_datasets = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_read_ebk_datasets",
                    args={},
                    session_id="sess_promo_metric",
                )
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                    args={},
                    session_id="sess_promo_metric",
                )
            self.assertIsNone(allowed_datasets)
            self.assertIsNone(allowed)
            self.assertEqual(inject_mock.call_count, 2)
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop(chat_id, None)

    def test_binding_question_allows_resolve_tool(self) -> None:
        self.plugin._USER_TEXT_BY_CHAT["oc_binding"] = "本群绑了哪家店"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_binding", "oc_binding", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_resolve_feishu_chat_context",
                    args={},
                    session_id="sess_binding",
                )
            self.assertIsNone(allowed)
            inject_mock.assert_called_once()
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_binding", None)

    def test_gateway_message_capture_routes_joint_question_to_minimal_tools(self) -> None:
        user_text = "@曼馨 AI 管家 飞猪和携程经营分析"
        payload = {
            "schema": "2.0",
            "header": {"event_id": "evt-joint", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "chat_id": "oc_manxin",
                    "message_id": "om_joint",
                    "content": json.dumps({"text": user_text}, ensure_ascii=False),
                },
            },
        }

        with mock.patch.object(self.plugin, "_is_watch_feishu_event", return_value=False):
            result = self.plugin._on_pre_gateway_dispatch(event=mock.Mock(raw_message=payload))

        self.assertIsNone(result)
        self.assertEqual(self.plugin._USER_TEXT_BY_CHAT.get("oc_manxin"), user_text)
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_joint", "oc_manxin", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                for tool_name in (
                    "mcp_pan_hotel_watch_resolve_feishu_chat_context",
                    "mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                ):
                    with self.subTest(tool_name=tool_name):
                        allowed = self.plugin._on_pre_tool_call(
                            tool_name=tool_name,
                            args={},
                            session_id="sess_joint_gateway",
                        )
                        self.assertIsNone(allowed)
                self.assertEqual(inject_mock.call_count, 2)
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_manxin", None)

    def _nested_mcp_tool_result(self, inner_payload: dict) -> str:
        return json.dumps({"result": json.dumps(inner_payload, ensure_ascii=False)})

    def test_transform_tool_result_marks_session_for_nested_mcp_adapter_string(self) -> None:
        result = self._nested_mcp_tool_result({"feishu_card_sent": True, "status": "success"})
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_daily_report",
            result=result,
            session_id="sess_nested_true",
        )
        self.assertTrue(self.plugin._CARD_SENT_BY_SESSION.get("sess_nested_true"))

    def test_transform_tool_result_marks_session_for_direct_tool_json(self) -> None:
        result = json.dumps({"feishu_card_sent": True, "status": "success"})
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_direct", "oc_group", "ou_user"),
        ):
            self.plugin._on_transform_tool_result(
                tool_name="mcp_pan_hotel_watch_get_daily_report",
                result=result,
                session_id="sess_direct_true",
            )
        self.assertTrue(self.plugin._CARD_SENT_BY_SESSION.get("sess_direct_true"))

    def test_transform_tool_result_accepts_nested_dict_payload(self) -> None:
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_nested_dict", "oc_group", "ou_user"),
        ):
            self.plugin._on_transform_tool_result(
                tool_name="mcp_pan_hotel_watch_get_daily_report",
                result={"result": {"feishu_card_sent": True, "status": "success"}},
                session_id="sess_nested_dict",
            )

        self.assertTrue(self.plugin._CARD_SENT_BY_SESSION.get("sess_nested_dict"))
        self.assertEqual(
            self.plugin._CARD_SENT_MESSAGE_BY_SESSION.get("sess_nested_dict"),
            "om_nested_dict",
        )

    def test_pre_tool_call_allows_followup_text_read_after_card_sent(self) -> None:
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_same_turn", "oc_group", "ou_user"),
        ):
            self.plugin._on_pre_llm_call(
                session_id="sess_single_card",
                user_message="今天经营简报",
                conversation_history=[],
            )
            self.plugin._on_transform_tool_result(
                tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                result=json.dumps({"feishu_card_sent": True, "status": "success"}),
                session_id="sess_single_card",
            )
            with mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
                    args={},
                    session_id="sess_single_card",
                )

        self.assertIsNone(allowed)
        inject_mock.assert_called_once()

    def test_pre_tool_call_clears_card_lock_for_new_message(self) -> None:
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_old", "oc_group", "ou_user"),
        ):
            self.plugin._on_transform_tool_result(
                tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
                result=json.dumps({"feishu_card_sent": True, "status": "success"}),
                session_id="sess_new_turn",
            )

        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_new", "oc_group", "ou_user"),
        ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject_mock:
            self.plugin._on_pre_llm_call(
                session_id="sess_new_turn",
                user_message="今天经营简报",
                conversation_history=[],
            )
            allowed = self.plugin._on_pre_tool_call(
                tool_name="mcp_pan_hotel_watch_get_operator_hotel_overview",
                args={},
                session_id="sess_new_turn",
            )

        self.assertIsNone(allowed)
        inject_mock.assert_called_once()
        self.assertNotIn("sess_new_turn", self.plugin._CARD_SENT_BY_SESSION)
        self.assertNotIn("sess_new_turn", self.plugin._CARD_SENT_MESSAGE_BY_SESSION)

    def test_transform_tool_result_does_not_mark_session_when_card_not_sent(self) -> None:
        for session_id, payload in (
            ("sess_direct_false", {"feishu_card_sent": False}),
            ("sess_nested_false", {"feishu_card_sent": False}),
        ):
            with self.subTest(session_id=session_id):
                result = (
                    self._nested_mcp_tool_result(payload)
                    if session_id.startswith("sess_nested")
                    else json.dumps(payload)
                )
                self.plugin._on_transform_tool_result(
                    tool_name="mcp_pan_hotel_watch_get_daily_report",
                    result=result,
                    session_id=session_id,
                )
                self.assertNotIn(session_id, self.plugin._CARD_SENT_BY_SESSION)

    def _assert_malformed_tool_result_falls_back_once(
        self,
        *,
        session_id: str,
        tool_result: str,
    ) -> None:
        reply_text = "工具结果解析失败，以下是文字回复。"
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_daily_report",
            result=tool_result,
            session_id=session_id,
        )
        self.assertNotIn(session_id, self.plugin._CARD_SENT_BY_SESSION)
        with mock.patch.object(
            self.plugin,
            "_resolve_chat_id",
            return_value="oc_group",
        ), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text=reply_text,
                session_id=session_id,
                platform="feishu",
            )
        send_mock.assert_called_once_with(chat_id="oc_group", text=reply_text)
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_malformed_outer_tool_json_falls_back_to_one_visible_reply(self) -> None:
        self._assert_malformed_tool_result_falls_back_once(
            session_id="sess_malformed_outer",
            tool_result="{not json",
        )

    def test_malformed_nested_result_json_falls_back_to_one_visible_reply(self) -> None:
        self._assert_malformed_tool_result_falls_back_once(
            session_id="sess_malformed_nested",
            tool_result=json.dumps({"result": "{not json"}),
        )

    def test_transform_tool_result_does_not_accept_other_nested_wrapper(self) -> None:
        session_id = "sess_missing_result_key"
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_daily_report",
            result=json.dumps({"payload": json.dumps({"feishu_card_sent": True})}),
            session_id=session_id,
        )
        self.assertNotIn(session_id, self.plugin._CARD_SENT_BY_SESSION)

    def test_llm_output_after_nested_tool_card_success_returns_invisible_ack(self) -> None:
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_daily_report",
            result=self._nested_mcp_tool_result({"feishu_card_sent": True}),
            session_id="sess_nested_flow",
        )
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text="已通过卡片回复，补充口径即可。",
                session_id="sess_nested_flow",
                platform="feishu",
            )
        # 新行为：工具已发卡后 LLM 文字仍经 _send_text_as_card 发卡，成功后返回 invisible ack。
        send_mock.assert_called_once()
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_llm_output_after_tool_card_failure_falls_back_once(self) -> None:
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_daily_report",
            result=json.dumps({"feishu_card_sent": False}),
            session_id="sess_fallback_once",
        )
        reply_text = "日历价自动轮询已开启，间隔 30 分钟。"
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text=reply_text,
                session_id="sess_fallback_once",
                platform="feishu",
            )
        send_mock.assert_called_once_with(chat_id="oc_group", text=reply_text)
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)

    def test_text_mode_tool_result_is_cardified_by_plugin(self) -> None:
        # Per reply_policy.py, a text-mode tool result ("let the LLM summarize
        # as plain text") still has its LLM summary card-ified by the plugin via
        # _send_text_as_card — response_mode=text never skips card-ifying.
        session_id = "sess_v2_text"
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_read_ebk_datasets",
            result=json.dumps(
                {
                    "response_mode": "text",
                    "reply_mode": "text",
                    "feishu_card_sent": False,
                    "operator_reply_note": "text 模式：未发送飞书卡片。",
                }
            ),
            session_id=session_id,
        )
        self.assertTrue(self.plugin._TEXT_REPLY_BY_SESSION.get(session_id))
        self.assertNotIn(session_id, self.plugin._CARD_SENT_BY_SESSION)
        reply_text = "今日出租率 72%，数据时间 14:30。"
        with mock.patch.object(self.plugin, "_resolve_chat_id", return_value="oc_group"), mock.patch.object(
            self.plugin,
            "_send_text_as_card",
            return_value=True,
        ) as send_mock:
            result = self.plugin._on_transform_llm_output(
                response_text=reply_text,
                session_id=session_id,
                platform="feishu",
            )
        send_mock.assert_called_once_with(chat_id="oc_group", text=reply_text)
        self.assertEqual(result, self.plugin._INVISIBLE_ACK)
        self.assertNotIn(session_id, self.plugin._TEXT_REPLY_BY_SESSION)

    def test_card_sent_blocks_second_explicit_detail_card(self) -> None:
        session_id = "sess_thin_card_block"
        self.plugin._on_transform_tool_result(
            tool_name="mcp_pan_hotel_watch_get_ebk_joint_analysis_context",
            result=json.dumps({"feishu_card_sent": True, "response_mode": "detail"}),
            session_id=session_id,
        )
        self.assertTrue(self.plugin._CARD_SENT_BY_SESSION.get(session_id))
        blocked = self.plugin._on_pre_tool_call(
            tool_name="mcp_pan_hotel_watch_read_ebk_datasets",
            args={"chat_id": "oc_group", "response_mode": "detail"},
            session_id=session_id,
        )
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked.get("action"), "block")

    def test_pre_tool_call_allows_latest_for_amplitude_intent(self) -> None:
        # 2026-10-09 通用模式：涨幅问法下 latest/trend 都放行，
        # 工具选择交给 LLM（简报注明涨幅用 trend）。
        self.plugin._USER_TEXT_BY_CHAT["oc_manxin"] = "竞品最近一天的价格涨幅情况"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_msg", "oc_manxin", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject:
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_latest_prices",
                    args={},
                    session_id="sess_amp",
                )
            self.assertIsNone(allowed)
            inject.assert_called()
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_manxin", None)

    def test_pre_tool_call_allows_calendar_tool_in_generic_mode(self) -> None:
        # 2026-10-09 通用模式：本店房型价格问法不再硬拦日历工具——
        # 目录选择交给 LLM（简报注明本店现价用 get_competitor_latest_prices）。
        self.plugin._USER_TEXT_BY_CHAT["oc_manxin"] = (
            "你读取上一轮的数据，展示今天本店的房型价格"
        )
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_msg", "oc_manxin", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session"):
                blocked = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_calendar_price_latest",
                    args={},
                    session_id="sess_own_room",
                )
            self.assertIsNone(blocked)
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_manxin", None)

    def test_pre_tool_call_allows_competitor_latest_for_own_hotel_room_price(self) -> None:
        self.plugin._USER_TEXT_BY_CHAT["oc_manxin"] = (
            "你读取上一轮的数据，展示今天本店的房型价格"
        )
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_msg", "oc_manxin", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject:
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_latest_prices",
                    args={},
                    session_id="sess_own_room_ok",
                )
            self.assertIsNone(allowed)
            inject.assert_called()
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_manxin", None)

    def test_pre_tool_call_allows_trend_for_amplitude_intent(self) -> None:
        self.plugin._USER_TEXT_BY_CHAT["oc_manxin"] = "竞品最近一天的价格涨幅情况"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_msg", "oc_manxin", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject:
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_price_trend",
                    args={},
                    session_id="sess_amp_ok",
                )
            self.assertIsNone(allowed)
            inject.assert_called()
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_manxin", None)

    def test_inject_feishu_session_adds_user_text(self) -> None:
        self.plugin._USER_TEXT_BY_CHAT["oc_manxin"] = "竞品最近一天的价格涨幅情况"
        args: dict = {}
        with mock.patch.object(
            self.plugin,
            "_session_fields",
            return_value=("om_msg", "oc_manxin", "ou_user"),
        ), mock.patch.object(self.plugin, "_is_allowed_chat_id", return_value=True), mock.patch.object(
            self.plugin, "_write_bridge"
        ):
            self.plugin._inject_feishu_session(args, session_id="sess_inj")
        self.assertEqual(args.get("user_text"), "竞品最近一天的价格涨幅情况")

    def test_pre_tool_call_forces_with_chart_when_user_asks_for_chart(self) -> None:
        # 2026-10-08 晚生产实证（静里 21:14 原句）：问对比曲线图但 LLM 漏传
        # with_chart，转而自绘 PNG 又无上传通道，谎称「图在下方」。硬约束：
        # 消息含图表词 + 调趋势工具 → 插件强制补 with_chart=true 走后端图卡。
        self.plugin._USER_TEXT_BY_CHAT["oc_chart"] = (
            "本地竞对今天一天所有时段的最低价与本地同时段做一个对比曲线图"
        )
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_chart", "oc_chart", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session") as inject:
                args: dict = {"chat_id": "oc_chart"}
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_price_trend",
                    args=args,
                    session_id="sess_chart_force",
                )
            self.assertIsNone(allowed)
            self.assertIs(args.get("with_chart"), True)
            inject.assert_called()
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_chart", None)

    def test_pre_tool_call_keeps_chart_flag_absent_without_chart_word(self) -> None:
        # 没要图的问法不得强插 with_chart（保持文字口径）。
        self.plugin._USER_TEXT_BY_CHAT["oc_nochart"] = "竞品最近一天的价格涨幅情况"
        try:
            with mock.patch.object(
                self.plugin,
                "_session_fields",
                return_value=("om_nochart", "oc_nochart", "ou_user"),
            ), mock.patch.object(self.plugin, "_inject_feishu_session"):
                args: dict = {"chat_id": "oc_nochart"}
                allowed = self.plugin._on_pre_tool_call(
                    tool_name="mcp_pan_hotel_watch_get_competitor_price_trend",
                    args=args,
                    session_id="sess_chart_noforce",
                )
            self.assertIsNone(allowed)
            self.assertNotIn("with_chart", args)
        finally:
            self.plugin._USER_TEXT_BY_CHAT.pop("oc_nochart", None)

    # ── 群资料归档（每日实施数据/文档永久积累）──────────────────────────

    def _receive_event_payload(
        self, *, chat_id: str = "oc_allowed", sender_type: str = "user"
    ) -> dict:
        return {
            "schema": "2.0",
            "header": {"event_id": "evt-arc", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_id": {"open_id": "ou_sender"},
                    "sender_type": sender_type,
                },
                "message": {
                    "chat_id": chat_id,
                    "chat_type": "group",
                    "message_id": "om_arc",
                    "message_type": "text",
                    "create_time": "1760000000",
                    "content": json.dumps(
                        {"text": "10月10日 实施：出租率85%"}, ensure_ascii=False
                    ),
                },
            },
        }

    def test_pre_gateway_dispatch_archives_receive_event(self) -> None:
        event = mock.Mock(raw_message=self._receive_event_payload())
        with (
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            result = self.plugin._on_pre_gateway_dispatch(event=event)
        dispatch_mock.assert_called_once()
        self.assertIsNone(result)

    def test_dispatch_group_message_event_runs_in_worker_thread(self) -> None:
        """归档转发不得阻塞网关事件循环：dispatch 只启动守护线程立即返回。"""
        started: list[str] = []

        class _FakeThread:
            def __init__(self, *, target, args=(), daemon=False):
                self._target = target
                self._args = args
                self.daemon = daemon

            def start(self) -> None:
                started.append("started")
                self._target(*self._args)

        with mock.patch.object(self.plugin.threading, "Thread", _FakeThread):
            self.plugin._dispatch_group_message_event({"some": "payload"})
        self.assertEqual(started, ["started"])

    def test_pre_gateway_dispatch_does_not_archive_card_actions(self) -> None:
        payload = {
            "schema": "2.0",
            "header": {"event_id": "evt-card", "event_type": "card.action.trigger"},
            "event": {
                "operator": {"operator_id": {"open_id": "ou_operator"}},
                "context": {"open_chat_id": "oc_allowed"},
                "action": {"value": {"workflow": "pricing_approval"}},
            },
        }
        event = mock.Mock(raw_message=payload)
        with (
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_archive_group_message_event") as archive_mock,
        ):
            self.plugin._on_pre_gateway_dispatch(event=event)
        archive_mock.assert_not_called()

    def test_pre_gateway_dispatch_does_not_archive_disallowed_chat(self) -> None:
        event = mock.Mock(raw_message=self._receive_event_payload(chat_id="oc_other"))
        with (
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_archive_group_message_event") as archive_mock,
        ):
            self.plugin._on_pre_gateway_dispatch(event=event)
        archive_mock.assert_not_called()

    def test_archive_failure_does_not_break_normal_dispatch(self) -> None:
        event = mock.Mock(raw_message=self._receive_event_payload())
        with (
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(
                self.plugin,
                "_dispatch_group_message_event",
                side_effect=RuntimeError("thread spawn failed"),
            ),
        ):
            result = self.plugin._on_pre_gateway_dispatch(event=event)
        self.assertIsNone(result)

    def test_archive_forward_posts_event_with_service_token(self) -> None:
        self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0
        try:
            with (
                mock.patch.dict(
                    os.environ, {"WATCH_API_SHARED_SECRET": "tok-arc"}, clear=False
                ),
                mock.patch("urllib.request.urlopen") as urlopen,
            ):
                urlopen.return_value.__enter__.return_value.status = 200
                self.plugin._archive_group_message_event(self._receive_event_payload())
            urlopen.assert_called_once()
            request = urlopen.call_args.args[0]
            self.assertIn("/api/v1/group-archive/events", request.full_url)
            self.assertEqual(
                request.headers.get("X-pan-hotel-service-token"), "tok-arc"
            )
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["event"]["message"]["message_id"], "om_arc")
        finally:
            self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0

    def test_archive_forward_skips_app_sender_messages(self) -> None:
        self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0
        try:
            with mock.patch("urllib.request.urlopen") as urlopen:
                self.plugin._archive_group_message_event(
                    self._receive_event_payload(sender_type="app")
                )
            urlopen.assert_not_called()
        finally:
            self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0

    def test_archive_forward_failure_opens_circuit_breaker_and_spools(self) -> None:
        import tempfile
        import time
        import urllib.error

        self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0
        try:
            with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"HERMES_HOME": home}, clear=False
            ), mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.side_effect = urllib.error.URLError("refused")
                self.plugin._archive_group_message_event(self._receive_event_payload())
                urlopen.assert_called_once()
                self.assertGreater(self.plugin._ARCHIVE_FORWARD_FAIL_AT, 0.0)
                # 熔断窗口内不再发请求，但事件必须落本地 spool 兜底（不丢数据）。
                self.plugin._archive_group_message_event(self._receive_event_payload())
                self.assertEqual(urlopen.call_count, 1)
                spool_files = list(self.plugin._archive_spool_dir().glob("*.json"))
                self.assertEqual(len(spool_files), 2)
                # 窗口过期后恢复尝试（spool 里的第二条也重投）。
                self.plugin._ARCHIVE_FORWARD_FAIL_AT = time.monotonic() - 120.0
                self.plugin._archive_group_message_event(self._receive_event_payload())
                self.assertEqual(urlopen.call_count, 2)
        finally:
            self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0

    def test_archive_forward_success_drains_spool(self) -> None:
        import tempfile

        self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0
        try:
            with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"HERMES_HOME": home}, clear=False
            ), mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.status = 200
                spool_dir = self.plugin._archive_spool_dir()
                spool_dir.mkdir(parents=True, exist_ok=True)
                (spool_dir / "0000-om_backlog.json").write_text(
                    json.dumps(self._receive_event_payload()), encoding="utf-8"
                )
                self.plugin._archive_group_message_event(self._receive_event_payload())
                # 当前消息 + spool 积压各一次投递，spool 清空。
                self.assertEqual(urlopen.call_count, 2)
                self.assertEqual(list(spool_dir.glob("*.json")), [])
        finally:
            self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0

    def test_archive_forward_skips_p2p_chats(self) -> None:
        import tempfile

        self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0
        try:
            payload = self._receive_event_payload()
            payload["event"]["message"]["chat_type"] = "p2p"
            with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"HERMES_HOME": home}, clear=False
            ), mock.patch("urllib.request.urlopen") as urlopen:
                self.plugin._archive_group_message_event(payload)
                urlopen.assert_not_called()
                self.assertFalse(self.plugin._archive_spool_dir().exists())
        finally:
            self.plugin._ARCHIVE_FORWARD_FAIL_AT = 0.0

    def test_archive_forward_can_be_disabled_by_env(self) -> None:
        event = mock.Mock(raw_message=self._receive_event_payload())
        with (
            mock.patch.dict(
                os.environ,
                {
                    "FEISHU_ALLOWED_CHATS": "oc_allowed",
                    "FEISHU_GROUP_ARCHIVE_ENABLED": "false",
                },
                clear=False,
            ),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            self.plugin._on_pre_gateway_dispatch(event=event)
        dispatch_mock.assert_not_called()

    # ── 适配器进站口补丁（require_mention 群的裸文件/图片归档）──────────

    @staticmethod
    def _sdk_receive_event(
        *,
        chat_type: str = "group",
        sender_type: str = "user",
        message_type: str = "file",
        content: str = '{"file_key":"fk1","file_name":"10月10日实施.xlsx"}',
    ) -> types.SimpleNamespace:
        # 模拟飞书 SDK P2ImMessageReceiveV1：vars() 可遍历的纯数据对象。
        return types.SimpleNamespace(
            header=types.SimpleNamespace(
                event_type="im.message.receive_v1", event_id="evt-intake"
            ),
            event=types.SimpleNamespace(
                sender=types.SimpleNamespace(
                    sender_id=types.SimpleNamespace(open_id="ou_sender"),
                    sender_type=sender_type,
                ),
                message=types.SimpleNamespace(
                    chat_id="oc_allowed",
                    chat_type=chat_type,
                    message_id="om_intake",
                    message_type=message_type,
                    create_time="1760000000",
                    content=content,
                ),
            ),
        )

    def _make_fake_adapter_module(self) -> tuple[types.ModuleType, dict]:
        calls = {"original": 0}

        class _FakeAdapter:
            async def _handle_message_event_data(self, data: object) -> None:
                calls["original"] += 1

        module = types.ModuleType("plugins.platforms.feishu.adapter")
        module.FeishuAdapter = _FakeAdapter
        return module, calls

    def test_intake_patch_archives_bare_group_file_message(self) -> None:
        # 2026-10-10 生产实证（静里群 10:38-11:03 四份 Excel 全部失踪）：hermes
        # 核心对 require_mention 群在适配器准入层丢弃不带 @机器人 的消息——
        # 文件/图片消息结构上无法携带 @——pre_gateway_dispatch 钩子看不到，
        # 归档必须挂在更早的进站口。补丁只观察：原实现照常调用。
        module, calls = self._make_fake_adapter_module()
        with (
            mock.patch.dict(sys.modules, {"plugins.platforms.feishu.adapter": module}),
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            self.plugin._patch_feishu_adapter_intake()
            adapter = module.FeishuAdapter()
            asyncio.run(adapter._handle_message_event_data(self._sdk_receive_event()))
        dispatch_mock.assert_called_once()
        self.assertEqual(calls["original"], 1)
        payload = dispatch_mock.call_args.args[0]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["event"]["message"]["message_type"], "file")
        self.assertEqual(payload["header"]["event_type"], "im.message.receive_v1")

    def test_intake_patch_ignores_p2p_and_app_senders(self) -> None:
        module, calls = self._make_fake_adapter_module()
        with (
            mock.patch.dict(sys.modules, {"plugins.platforms.feishu.adapter": module}),
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            self.plugin._patch_feishu_adapter_intake()
            adapter = module.FeishuAdapter()
            asyncio.run(
                adapter._handle_message_event_data(
                    self._sdk_receive_event(chat_type="p2p")
                )
            )
            asyncio.run(
                adapter._handle_message_event_data(
                    self._sdk_receive_event(sender_type="app")
                )
            )
        dispatch_mock.assert_not_called()
        self.assertEqual(calls["original"], 2)

    def test_intake_patch_disabled_by_env_and_silent_without_adapter(self) -> None:
        # 开关关闭时不转发；适配器模块不存在（hermes 升级改名）时静默跳过。
        module, calls = self._make_fake_adapter_module()
        with (
            mock.patch.dict(sys.modules, {"plugins.platforms.feishu.adapter": module}),
            mock.patch.dict(
                os.environ, {"FEISHU_GROUP_ARCHIVE_ENABLED": "false"}, clear=False
            ),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            self.plugin._patch_feishu_adapter_intake()
            asyncio.run(
                module.FeishuAdapter()._handle_message_event_data(
                    self._sdk_receive_event()
                )
            )
        dispatch_mock.assert_not_called()
        self.assertEqual(calls["original"], 1)

        with mock.patch.dict(sys.modules, {"plugins.platforms.feishu.adapter": None}):
            self.plugin._patch_feishu_adapter_intake()  # 不得抛错

    def test_intake_patch_is_idempotent(self) -> None:
        # 重复调用不得叠加包装：一次事件只投一次归档、原实现只跑一次。
        module, calls = self._make_fake_adapter_module()
        with (
            mock.patch.dict(sys.modules, {"plugins.platforms.feishu.adapter": module}),
            mock.patch.dict(os.environ, {"FEISHU_ALLOWED_CHATS": "oc_allowed"}, clear=False),
            mock.patch.object(self.plugin, "_dispatch_group_message_event") as dispatch_mock,
        ):
            self.plugin._patch_feishu_adapter_intake()
            self.plugin._patch_feishu_adapter_intake()
            asyncio.run(
                module.FeishuAdapter()._handle_message_event_data(
                    self._sdk_receive_event()
                )
            )
        dispatch_mock.assert_called_once()
        self.assertEqual(calls["original"], 1)


if __name__ == "__main__":
    unittest.main()
