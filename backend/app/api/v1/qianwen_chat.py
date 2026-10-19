"""千问 chat API（V6 Step 2）：open-login / check-login-state / ask-and-extract。"""

from __future__ import annotations

from app.api.v1._llm_chat_api import build_llm_chat_router
from app.domain.browser.qianwen_chat import get_qianwen_chat_service

router = build_llm_chat_router(
    platform="qwen",
    # lambda 在调用时才解析模块全局名，测试才能 patch get_qianwen_chat_service
    service_getter=lambda: get_qianwen_chat_service(),
    display_name="千问",
)
