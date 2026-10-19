from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any
from unittest import mock

module = importlib.import_module("app.domain.browser.llm_chat_browser")


class ClassifyLlmChatPageStateTests(unittest.TestCase):
    def test_human_markers_win(self) -> None:
        state = module.classify_llm_chat_page_state(
            "https://www.doubao.com/chat/", "请完成人机验证后继续"
        )
        self.assertEqual(state, "human_required")

    def test_login_markers(self) -> None:
        state = module.classify_llm_chat_page_state(
            "https://www.doubao.com/chat/", "扫码登录 手机号登录"
        )
        self.assertEqual(state, "login")

    def test_input_box_means_authenticated(self) -> None:
        state = module.classify_llm_chat_page_state(
            "https://www.doubao.com/chat/", "你好我是豆包 发送", has_input=True
        )
        self.assertEqual(state, "authenticated")

    def test_no_input_no_text_is_human_required(self) -> None:
        self.assertEqual(
            module.classify_llm_chat_page_state("about:blank", "", has_input=None),
            "human_required",
        )


class ExtractAnswerTextTests(unittest.TestCase):
    def test_strips_known_prefix(self) -> None:
        before = "你好我是豆包\n有什么可以帮你"
        after = before + "\n携程EBK报名活动的入口在数据中心-营销推广。\n报名需提前1天。"
        answer = module.extract_answer_text(before, after)
        self.assertIn("营销推广", answer)
        self.assertNotIn("你好我是豆包", answer)

    def test_empty_after_text(self) -> None:
        self.assertEqual(module.extract_answer_text("a", ""), "")

    def test_tail_fallback_when_prefix_rewritten(self) -> None:
        answer = module.extract_answer_text("x" * 10, "y" * 400)
        self.assertTrue(answer)

    def test_strips_submitted_prompt_echo_before_answer(self) -> None:
        before = "你好我是豆包\n发送"
        after = before + "\n用户\n原始房价是 420 元，请提炼入住规则\n助手\n入住需提前一天预订。"
        answer = module.extract_answer_text(
            before,
            after,
            prompt="原始房价是 420 元，请提炼入住规则",
        )
        self.assertEqual(answer, "入住需提前一天预订。")


# ---------------------------------------------------------------------------
# ask_and_extract with duck-typed fake page
# ---------------------------------------------------------------------------


class _FakeLocator:
    def __init__(self, *, supports_fill: bool = True) -> None:
        self.supports_fill = supports_fill
        self.filled: list[str] = []
        self.pressed: list[str] = []
        self.typed: list[str] = []

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return 1

    async def fill(self, value: str, timeout: int = 5000) -> None:
        if not self.supports_fill:
            raise RuntimeError("not fillable")
        self.filled.append(value)

    async def click(self, timeout: int = 2000) -> None:
        return None

    async def type(self, value: str, delay: int = 10) -> None:  # noqa: A002
        self.typed.append(value)

    async def press(self, key: str, timeout: int = 2000) -> None:
        self.pressed.append(key)


class _MissingInputLocator:
    @property
    def first(self) -> "_MissingInputLocator":
        return self

    async def count(self) -> int:
        return 0


class _FakePage:
    def __init__(
        self,
        *,
        url: str = "https://www.doubao.com/chat/",
        body_sequence: list[str] | None = None,
        input_locator: Any = None,
    ) -> None:
        self.url = url
        self._body_sequence = list(body_sequence or [])
        self._bodies_read = 0
        self.input_locator = input_locator if input_locator is not None else _FakeLocator()
        self.goto_calls: list[str] = []
        self.keyboard = self

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls.append(url)
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        await asyncio.sleep(0)

    async def press(self, key: str) -> None:
        return None

    def locator(self, selector: str) -> Any:
        if "body" in selector:
            return self
        return self.input_locator

    async def inner_text(self, timeout: int = 3000) -> str:
        index = min(self._bodies_read, len(self._body_sequence) - 1)
        self._bodies_read += 1
        return self._body_sequence[index] if self._body_sequence else ""

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        return None


def _service(platform: str = "doubao") -> Any:
    service = module.LlmChatBrowserService(platform)
    return service


def _patch_store(**kwargs: Any) -> mock._patch:  # type: ignore[name-defined]
    session = type(
        "Session",
        (),
        {"session_port": kwargs.get("session_port", 9600)},
    )()

    class _Store:
        def get(self, sid: str) -> Any:
            return session

        def mark_authenticated(self, sid: str, actor: str, action: str = "") -> None:
            return None

        def mark_login_window_opened(self, sid: str, actor: str, action: str = "") -> None:
            return None

    return mock.patch.object(module, "get_browser_session_store", return_value=_Store())


class Run258RegressionTests(unittest.TestCase):
    def test_run_258_shape_strips_short_prompt_echo_and_ui_tail(self) -> None:
        answer = module.extract_answer_text(
            "old message",
            "\u603b\u7ed3\u4e00\u4e0b\u5185\u5bb9\n\u5185\u5bb9\u5982\u4e0b\uff1a\n\u9152\u5e97\u8fd0\u8425\u89c4\u5219\uff1a\u63d0\u524d 14 \u5929\u9884\u8ba2\u53ef\u514d\u8d39\u53d6\u6d88\uff1b\u5165\u4f4f\u65f6\u95f4\u4e0b\u5348 3 \u70b9\uff0c\u9000\u623f\u65f6\u95f4\u4e2d\u5348 12 \u70b9\u3002\n\u5df2\u5b8c\u6210\n\u9152\u5e97\u8fd0\u8425\u89c4\u5219\u603b\u7ed3\uff1a\n\u53d6\u6d88\u653f\u7b56\uff1a\u63d0\u524d 14 \u5929\u9884\u8ba2\u53ef\u514d\u8d39\u53d6\u6d88\u3002\n\u5de5\u4f5c\u4efb\u52a1\n\u4f01\u4e1a\u77e5\u8bc6\n\u66f4\u591a\u6280\u80fd\n\u8fde\u63a5\u5668\n\u8c46\u5305 2.1 Turbo\n\u9ad8",
            prompt="\u603b\u7ed3\u4e00\u4e0b\u5185\u5bb9",
        )
        self.assertIn("\u9152\u5e97\u8fd0\u8425\u89c4\u5219\u603b\u7ed3", answer)
        self.assertIn("\u53d6\u6d88\u653f\u7b56", answer)
        self.assertNotIn("\u5185\u5bb9\u5982\u4e0b", answer)
        self.assertNotIn("\u5de5\u4f5c\u4efb\u52a1", answer)
        self.assertNotIn("\u8c46\u5305 2.1 Turbo", answer)


class AskAndExtractTests(unittest.TestCase):
    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def test_success_extracts_streamed_answer(self) -> None:
        page = _FakePage(
            body_sequence=[
                "你好，我是豆包 发送",
                "你好，我是豆包 发送 部分回答",
                "你好，我是豆包 发送 携程活动报名规则：需在数据中心-营销推广中报名。",
                "你好，我是豆包 发送 携程活动报名规则：需在数据中心-营销推广中报名。",
            ]
        )
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="携程EBK活动报名规则是什么",
                    max_wait_seconds=3,
                    poll_interval_seconds=0.01,
                    stable_polls=2,
                )
            )
        self.assertEqual(result["status"], "success")
        self.assertIn("营销推广", result["answer_text"])
        self.assertTrue(page.input_locator.filled)
        self.assertIn("Enter", page.input_locator.pressed)

    def test_success_does_not_ingest_prompt_echo_as_answer(self) -> None:
        page = _FakePage(
            body_sequence=[
                "你好，我是豆包 发送",
                "你好，我是豆包 发送 用户 原始房价是 420 元，请提炼入住规则",
                "你好，我是豆包 发送 用户 原始房价是 420 元，请提炼入住规则 助手 入住需提前一天预订。",
                "你好，我是豆包 发送 用户 原始房价是 420 元，请提炼入住规则 助手 入住需提前一天预订。",
            ]
        )
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="原始房价是 420 元，请提炼入住规则",
                    max_wait_seconds=3,
                    poll_interval_seconds=0.01,
                    stable_polls=2,
                )
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["answer_text"], "入住需提前一天预订。")

    def test_human_required_when_login_marker(self) -> None:
        page = _FakePage(body_sequence=["扫码登录 手机号登录"])
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="测试",
                    max_wait_seconds=1,
                    poll_interval_seconds=0.01,
                )
            )
        self.assertEqual(result["status"], "human_required")
        self.assertTrue(result["manual_login_required"])

    def test_parse_failed_when_answer_never_appears(self) -> None:
        page = _FakePage(body_sequence=["你好，我是豆包 发送"] * 5)
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="测试",
                    max_wait_seconds=0.2,
                    poll_interval_seconds=0.05,
                )
            )
        self.assertEqual(result["status"], "parse_failed")

    def test_run_255_ui_and_thinking_text_is_not_a_success(self) -> None:
        page = _FakePage(
            body_sequence=[
                "\u5f20\u56fd\u884c\n\u65b0\u7ad9\u53f0\n\u4e91\u7535\u8111\n\u4e0b\u8f7d\u7535\u8111",
                "\u5f20\u56fd\u884c\n\u65b0\u7ad9\u53f0\n\u4e91\u7535\u8111\n\u4e0b\u8f7d\u7535\u8111\n\u4f60\u662f\u9152\u5e97\u8fd0\u8425\u77e5\u8bc6\u52a9\u624b\u3002\u8bf7\u9605\u8bfb\u4e0b\u9762\u7684\u5185\u5bb9\uff0c\u63d0\u53d6\u7a33\u5b9a\u77e5\u8bc6\u70b9\u3002\n\u5185\u5bb9\u5982\u4e0b\uff1a\n\u8fc7\u9891\n\u6b63\u5728\u7406\u89e3\u4efb\u52a1\u8981\u6c42\n\u5de5\u4f5c\u4efb\u52a1\n\u4f01\u4e1a\u77e5\u8bc6\n\u66f4\u591a\u6280\u80fd\n\u8fde\u63a5\u5668\n\u8c46\u5305 2.1 Turbo\n\u9ad8",
            ] * 4
        )
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="\u9152\u5e97\u8fd0\u8425\u77e5\u8bc6\u63d0\u53d6",
                    max_wait_seconds=0.2,
                    poll_interval_seconds=0.01,
                )
            )
        self.assertEqual(result["status"], "parse_failed")

    def test_parse_failed_when_input_missing(self) -> None:
        page = _FakePage(
            body_sequence=["你好，我是豆包"],
            input_locator=_MissingInputLocator(),
        )
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="测试",
                    max_wait_seconds=1,
                    poll_interval_seconds=0.01,
                )
            )
        self.assertEqual(result["status"], "parse_failed")
        self.assertIn("输入框", result["summary"])

    def test_empty_prompt_rejected_before_browser(self) -> None:
        service = _service()
        result = asyncio.run(
            service.ask_and_extract(
                platform_account_id="a",
                browser_session_id="b",
                prompt="  ",
            )
        )
        self.assertEqual(result["status"], "parse_failed")

    def test_contenteditable_fallback_types_when_fill_fails(self) -> None:
        locator = _FakeLocator(supports_fill=False)
        page = _FakePage(
            body_sequence=[
                "你好我是豆包",
                "平台规则A：报名需提前1天。",
                "平台规则A：报名需提前1天。",
            ],
            input_locator=locator,
        )
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            result = asyncio.run(
                service.ask_and_extract(
                    platform_account_id="doubao_acc",
                    browser_session_id="browser-doubao_acc",
                    prompt="提问",
                    max_wait_seconds=2,
                    poll_interval_seconds=0.01,
                )
            )
        self.assertEqual(locator.typed, ["提问"])
        self.assertEqual(result["status"], "success")

    def test_navigates_to_chat_url_when_off_platform(self) -> None:
        page = _FakePage(url="https://example.com/other", body_sequence=["你好我是豆包 发送"])
        service = _service("qwen")
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            asyncio.run(
                service.ask_and_extract(
                    platform_account_id="qwen_acc",
                    browser_session_id="browser-qwen_acc",
                    prompt="测试",
                    max_wait_seconds=0.2,
                    poll_interval_seconds=0.05,
                )
            )
        self.assertEqual(page.goto_calls, [service.chat_url])


class LoginStateTests(unittest.TestCase):
    def test_open_login_reports_human_required_when_not_logged_in(self) -> None:
        page = _FakePage(body_sequence=["扫码登录"])
        service = _service()
        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            with _patch_store():
                result = asyncio.run(
                    service.open_login(
                        platform_account_id="doubao_acc",
                        browser_session_id="browser-doubao_acc",
                    )
                )
        self.assertEqual(result["status"], "human_required")
        self.assertEqual(result["evidence"]["page_state"], "login")

    def test_open_login_success_when_input_present(self) -> None:
        page = _FakePage(body_sequence=["你好，我是豆包 发送"])
        service = _service()
        mark_calls: list[tuple[str, str]] = []

        class _Store:
            def get(self, sid: str) -> Any:
                return type("S", (), {"session_port": 9600})()

            def mark_authenticated(self, sid: str, actor: str, action: str = "") -> None:
                mark_calls.append(("authenticated", sid))

            def mark_login_window_opened(self, sid: str, actor: str, action: str = "") -> None:
                mark_calls.append(("opened", sid))

        with mock.patch.object(service, "_get_or_create_page", new=mock.AsyncMock(return_value=page)):
            with mock.patch.object(module, "get_browser_session_store", return_value=_Store()):
                result = asyncio.run(
                    service.open_login(
                        platform_account_id="doubao_acc",
                        browser_session_id="browser-doubao_acc",
                    )
                )
        self.assertEqual(result["status"], "success")
        self.assertEqual(mark_calls, [("authenticated", "browser-doubao_acc")])


class PlatformModuleTests(unittest.TestCase):
    def test_doubao_and_qianwen_services_are_singletons(self) -> None:
        doubao = importlib.import_module("app.domain.browser.doubao_chat")
        qianwen = importlib.import_module("app.domain.browser.qianwen_chat")
        self.assertIs(doubao.get_doubao_chat_service(), doubao.get_doubao_chat_service())
        self.assertIs(qianwen.get_qianwen_chat_service(), qianwen.get_qianwen_chat_service())
        self.assertEqual(doubao.get_doubao_chat_service().platform, "doubao")
        self.assertEqual(qianwen.get_qianwen_chat_service().platform, "qwen")
        self.assertIn("doubao.com", doubao.DOUBAO_CHAT_URL)
        self.assertIn("qianwen.ai", qianwen.QIANWEN_CHAT_URL)

    def test_unsupported_platform_rejected(self) -> None:
        with self.assertRaises(ValueError):
            module.LlmChatBrowserService("unknown_platform")


class ChatUrlOverrideTests(unittest.TestCase):
    """F4-B — 会话级 chat_url 覆盖：导航地址与 host 判断同步更新。"""

    def test_override_updates_url_and_host_markers(self) -> None:
        from app.domain.browser.llm_chat_browser import LlmChatBrowserService

        service = LlmChatBrowserService("doubao")
        default_url = service.chat_url
        self.assertIn("doubao.com", default_url)

        service.apply_chat_url_override("https://chat.example.com/entry")
        self.assertEqual(service.chat_url, "https://chat.example.com/entry")
        self.assertTrue(service._is_chat_url("https://chat.example.com/some/page"))
        # 默认平台域名仍被接受（已打开的默认页不误判）
        self.assertTrue(service._is_chat_url("https://www.doubao.com/chat/"))

    def test_override_ignores_blank_or_invalid_values(self) -> None:
        from app.domain.browser.llm_chat_browser import LlmChatBrowserService

        service = LlmChatBrowserService("qwen")
        default_url = service.chat_url
        for bad in (None, "", "   ", "ftp://x", "not-a-url"):
            service.apply_chat_url_override(bad)
            self.assertEqual(service.chat_url, default_url)


if __name__ == "__main__":
    unittest.main()
