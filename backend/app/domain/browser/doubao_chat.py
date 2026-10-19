"""豆包 chat 浏览器服务（V6 Step 2.2 Parser 独立模块）。"""

from __future__ import annotations

from app.domain.browser.llm_chat_browser import (
    LlmChatBrowserProfileLockedError,
    LlmChatBrowserService,
    LLM_CHAT_PLATFORM_CONFIGS,
)

DOUBAO_CHAT_URL: str = LLM_CHAT_PLATFORM_CONFIGS["doubao"]["chat_url"]

_DOUBAO_CHAT_SERVICE = LlmChatBrowserService("doubao")


def get_doubao_chat_service() -> LlmChatBrowserService:
    return _DOUBAO_CHAT_SERVICE


__all__ = [
    "DOUBAO_CHAT_URL",
    "LlmChatBrowserProfileLockedError",
    "LlmChatBrowserService",
    "get_doubao_chat_service",
]
