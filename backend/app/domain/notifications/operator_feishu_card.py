from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.feishu_qa_cards import build_qa_reply_card
from app.domain.notifications.feishu_client import FeishuBotClient

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_INTERNAL_TEXT_RE = re.compile(r"\b(?:mcp_[\w-]*|operator_summary|skill_view)\b", re.IGNORECASE)
# 泄漏过滤（AGENTS.md 原则 3）：内部标记、绝对路径、API key、token、webhook 地址。
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s]+")
_UNIX_PATH_RE = re.compile(r"(?<![\w])/+(?:usr|home|var|etc|opt|tmp|root)/[^\s]+")
_API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)
_WEBHOOK_URL_RE = re.compile(
    r"open\.feishu\.cn/open-apis/bot/hook/[A-Za-z0-9\-]+|https?://[^\s]*webhook[^\s]*",
    re.IGNORECASE,
)
_LEAK_LINE_RES = (
    _INTERNAL_TEXT_RE,
    _WINDOWS_PATH_RE,
    _UNIX_PATH_RE,
    _API_KEY_RE,
    _BEARER_TOKEN_RE,
    _WEBHOOK_URL_RE,
)


def _sanitize_conversation_reply_text(text: str) -> str:
    """清洗问答回复文本：放行 markdown 结构，仅拦截泄漏内容。

    - 放行：``#`` 标题、``|`` 表格、``-`` 列表、``**`` 加粗、```` `` ```` 代码段；
    - 拦截：JSON 片段（单行/多行/````` ```json `````围栏）、内部标记
      （mcp_* / operator_summary / skill_view）、绝对路径、API key、
      Bearer token、webhook 地址。
    """
    cleaned_lines: list[str] = []
    json_depth = 0
    in_json_fence = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_json_fence:
            if line == "```":
                in_json_fence = False
            continue
        if line.lower() == "```json":
            in_json_fence = True
            continue
        if json_depth > 0:
            json_depth += line.count("{") - line.count("}")
            if json_depth < 0:
                json_depth = 0
            continue
        if line.startswith("{"):
            # 单行 JSON 直接丢弃；多行 JSON 进入深度跟踪，直到括号闭合
            json_depth = max(0, line.count("{") - line.count("}"))
            continue
        if any(pattern.search(line) for pattern in _LEAK_LINE_RES):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def build_conversation_reply_card(
    text: str,
    *,
    title: str = "运营助手",
    status: str = "success",
) -> dict[str, Any]:
    """简报式对话卡片：按内容自动选择模板渲染（V7 Step 0）。"""
    body = _sanitize_conversation_reply_text(str(text or "")) or "（无内容）"
    return build_qa_reply_card(body, title=title, status=status, now=datetime.now(_SHANGHAI))


async def deliver_operator_briefing_card(
    *,
    chat_id: str,
    card: dict[str, Any],
    fallback_text: str | None = None,
    store_hint: str | None = None,
) -> dict[str, Any]:
    """由飞书应用机器人在当前 chat_id 群发送交互卡片。

    Hermes 运营回复**禁止**走竞品监控组 webhook，避免卡片误推到其他群。
    ``store_hint`` 仅保留 API 兼容，不参与投递路由。
    """
    _ = store_hint
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return {
            "status": "config_required",
            "summary": "缺少飞书群 chat_id，无法发送机器人卡片。",
            "next_action": "请确认 Hermes 会话已绑定当前飞书群。",
        }

    result = await FeishuBotClient(chat_id=normalized_chat_id).send_interactive_card(
        normalized_chat_id,
        card,
        fallback_text=fallback_text,
    )
    sanitized = dict(result)
    sanitized["delivery"] = "app_bot"
    sanitized.pop("evidence", None)
    return sanitized


async def deliver_operator_text_as_card(
    *,
    chat_id: str,
    text: str,
    title: str | None = None,
    fallback_text: str | None = None,
    store_hint: str | None = None,
    status: str = "success",
) -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return {
            "status": "config_required",
            "summary": "缺少可推送的正文内容。",
        }
    card = build_conversation_reply_card(
        normalized_text,
        title=title or "运营助手",
        status=status,
    )
    return await deliver_operator_briefing_card(
        chat_id=chat_id,
        card=card,
        fallback_text=fallback_text or normalized_text,
        store_hint=store_hint,
    )
