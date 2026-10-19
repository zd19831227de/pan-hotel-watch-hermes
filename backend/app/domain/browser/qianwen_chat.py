"""千问 chat 浏览器服务（V6 Step 2.2 Parser 独立模块）。"""

from __future__ import annotations

from app.domain.browser.llm_chat_browser import (
    LlmChatBrowserProfileLockedError,
    LlmChatBrowserService,
    LLM_CHAT_PLATFORM_CONFIGS,
)

QIANWEN_CHAT_URL: str = LLM_CHAT_PLATFORM_CONFIGS["qwen"]["chat_url"]

_QIANWEN_CHAT_SERVICE = LlmChatBrowserService("qwen")


def get_qianwen_chat_service() -> LlmChatBrowserService:
    return _QIANWEN_CHAT_SERVICE


__all__ = [
    "QIANWEN_CHAT_URL",
    "LlmChatBrowserProfileLockedError",
    "LlmChatBrowserService",
    "get_qianwen_chat_service",
]
