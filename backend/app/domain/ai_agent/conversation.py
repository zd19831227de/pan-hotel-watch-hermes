from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from app.db.session import SessionLocal
from app.domain.ai_agent.models import AiAgentConversation, AiAgentMessage
from app.domain.competitor_prices.models import local_now

_MAX_CONTENT_LENGTH = 12000
_NON_AUTHORITATIVE_MARKERS = (
    "tool_not_allowed",
    "invalid_arguments",
    "tool_round_limit",
    "config_required",
    "调用失败",
    "执行失败",
    "超时",
)


class AiAgentConversationManager:
    def __init__(self, *, session_factory: Any | None = None) -> None:
        self.session_factory = session_factory or SessionLocal

    async def get_or_create_session(
        self,
        chat_id: str,
        open_id: str | None = None,
    ) -> AiAgentConversation:
        session_id = self._session_id(chat_id=chat_id, open_id=open_id)
        existing = await self._get_session_by_id(session_id)
        if existing is not None:
            await self._touch_session(session_id)
            return existing
        conversation = AiAgentConversation(
            session_id=session_id,
            chat_id=chat_id,
            open_id=open_id,
            created_at=local_now(),
            updated_at=local_now(),
        )
        async with self.session_factory() as session:
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
        return conversation

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None,
        *,
        message_id: str | None = None,
        tool_calls: Any | None = None,
    ) -> AiAgentMessage:
        stored = AiAgentMessage(
            message_id=message_id or str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=_truncate(content),
            tool_calls=_serialize_tool_calls(tool_calls),
            created_at=local_now(),
        )
        async with self.session_factory() as session:
            session.add(stored)
            conversation = await session.get(AiAgentConversation, session_id)
            if conversation is not None:
                conversation.updated_at = local_now()
            await session.commit()
            await session.refresh(stored)
        return stored

    async def get_context(self, session_id: str, *, max_turns: int = 8) -> list[dict[str, Any]]:
        max_messages = max(1, max_turns) * 2
        async with self.session_factory() as session:
            result = await session.execute(
                select(AiAgentMessage)
                .where(AiAgentMessage.session_id == session_id)
                .order_by(desc(AiAgentMessage.created_at))
                .limit(max_messages)
            )
            messages = list(reversed(result.scalars().all()))
        context: list[dict[str, Any]] = []
        for message in messages:
            # Failed tool turns are operational diagnostics, not conversation
            # facts. Keeping them in the next prompt causes the model to copy
            # stale routes and error text into an unrelated answer.
            if message.role == "assistant" and _is_non_authoritative(
                message.content,
                tool_calls=message.tool_calls,
            ):
                continue
            item: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.tool_calls:
                try:
                    item["tool_calls"] = json.loads(message.tool_calls)
                except json.JSONDecodeError:
                    pass
            context.append(item)
        return context

    async def _get_session_by_id(self, session_id: str) -> AiAgentConversation | None:
        async with self.session_factory() as session:
            return await session.get(AiAgentConversation, session_id)

    async def _touch_session(self, session_id: str) -> None:
        async with self.session_factory() as session:
            conversation = await session.get(AiAgentConversation, session_id)
            if conversation is not None:
                conversation.updated_at = local_now()
                await session.commit()

    @staticmethod
    def _session_id(*, chat_id: str, open_id: str | None) -> str:
        normalized_open_id = open_id or "anonymous"
        return f"{chat_id}:{normalized_open_id}"


def _is_non_authoritative(content: str | None, *, tool_calls: str | None = None) -> bool:
    if not tool_calls:
        return False
    text = str(content or "").lower()
    return any(marker.lower() in text for marker in _NON_AUTHORITATIVE_MARKERS)


def _serialize_tool_calls(value: Any | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate(value)
    return _truncate(json.dumps(value, ensure_ascii=False))


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= _MAX_CONTENT_LENGTH:
        return text
    return text[:_MAX_CONTENT_LENGTH] + "...[truncated]"
