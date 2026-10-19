from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.domain.ai_agent.conversation import AiAgentConversationManager
from app.domain.ai_agent.llm import (
    AgentLlmClient,
    AgentLlmConfigError,
    OpenAiCompatibleAgentLlmClient,
)
from app.domain.ai_agent.registry import (
    AgentToolCallContext,
    AgentToolRegistry,
    get_default_agent_tool_registry,
)


class AiAgentRunResult(BaseModel):
    status: str
    summary: str
    final_output: str | None = None
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


class AiAgentRunner:
    def __init__(
        self,
        *,
        registry: AgentToolRegistry | None = None,
        llm_client: AgentLlmClient | None = None,
        conversation_manager: AiAgentConversationManager | None = None,
        max_context_turns: int = 8,
        max_tool_rounds: int = 3,
    ) -> None:
        self.registry = registry or get_default_agent_tool_registry()
        self.llm_client = llm_client or OpenAiCompatibleAgentLlmClient()
        self.conversation_manager = conversation_manager
        self.max_context_turns = max(1, max_context_turns)
        self.max_tool_rounds = max(1, max_tool_rounds)

    async def run(
        self,
        user_message: str,
        *,
        context: AgentToolCallContext,
    ) -> AiAgentRunResult:
        session_id, context_messages, conversation_error = await self._load_conversation_context(
            context=context,
        )
        messages = [{"role": "system", "content": _system_prompt()}]
        messages.extend(context_messages)
        messages.append({"role": "user", "content": user_message.strip()})
        if session_id:
            conversation_error = conversation_error or await self._record_conversation_message(
                session_id=session_id,
                role="user",
                content=user_message.strip(),
                message_id=context.message_id,
            )
        tool_results: list[dict[str, Any]] = []
        tools = _tool_specs_for_message(user_message, self.registry.tool_specs())

        for round_index in range(self.max_tool_rounds + 1):
            try:
                response = await self.llm_client.chat(messages=messages, tools=tools)
            except AgentLlmConfigError:
                return AiAgentRunResult(
                    status="config_required",
                    summary="大模型未配置，暂时无法使用飞书智能体。",
                    evidence=_base_evidence(
                        tool_invoked=False,
                        tool_round_count=len(tool_results),
                        conversation_enabled=bool(session_id),
                        conversation_error=conversation_error,
                    ),
                    next_action="在模型配置中填写 provider、base_url、model 和 API key 后重试。",
                )
            except httpx.HTTPError as exc:
                return AiAgentRunResult(
                    status="failed",
                    summary="大模型请求失败，系统没有执行高风险操作。",
                    evidence={
                        **_base_evidence(
                            tool_invoked=bool(tool_results),
                            tool_round_count=len(tool_results),
                            conversation_enabled=bool(session_id),
                            conversation_error=conversation_error,
                        ),
                        "error_type": type(exc).__name__,
                    },
                    next_action="请检查模型连接配置或稍后重试。",
                )

            if not response.tool_calls:
                final_output = response.content or "我没有拿到可用回复。"
                if session_id:
                    conversation_error = conversation_error or await self._record_conversation_message(
                        session_id=session_id,
                        role="assistant",
                        content=final_output,
                    )
                return AiAgentRunResult(
                    status="success",
                    summary=final_output,
                    final_output=final_output,
                    tool_results=tool_results,
                    evidence=_base_evidence(
                        tool_invoked=bool(tool_results),
                        tool_round_count=len(tool_results),
                        conversation_enabled=bool(session_id),
                        conversation_error=conversation_error,
                    ),
                )

            if round_index >= self.max_tool_rounds:
                return AiAgentRunResult(
                    status="tool_round_limit",
                    summary="AI Agent 工具调用轮数已达到上限，已安全停止。",
                    tool_results=tool_results,
                    evidence=_base_evidence(
                        tool_invoked=bool(tool_results),
                        tool_round_count=len(tool_results),
                        conversation_enabled=bool(session_id),
                        conversation_error=conversation_error,
                    ),
                    next_action="请缩小问题范围后重试。",
                )

            messages.append(_assistant_tool_call_message(response.tool_calls))
            for call in response.tool_calls:
                result = await self.registry.execute_tool(
                    call.name,
                    call.arguments,
                    context=context,
                )
                result_payload = result.model_dump(mode="json")
                tool_results.append(
                    {
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "status": result.status,
                        "summary": result.summary,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result_payload, ensure_ascii=False),
                    }
                )
            if session_id and response.tool_calls:
                conversation_error = conversation_error or await self._record_conversation_message(
                    session_id=session_id,
                    role="assistant",
                    content=_tool_call_summary_text(tool_results),
                    tool_calls=[
                        {
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in response.tool_calls
                    ],
                )

        return AiAgentRunResult(
            status="tool_round_limit",
            summary="AI Agent 工具调用轮数已达到上限，已安全停止。",
            tool_results=tool_results,
            evidence=_base_evidence(
                tool_invoked=bool(tool_results),
                tool_round_count=len(tool_results),
                conversation_enabled=bool(session_id),
                conversation_error=conversation_error,
            ),
            next_action="请缩小问题范围后重试。",
        )

    async def _load_conversation_context(
        self,
        *,
        context: AgentToolCallContext,
    ) -> tuple[str | None, list[dict[str, Any]], str | None]:
        if not context.chat_id:
            return None, [], None
        manager = self.conversation_manager or AiAgentConversationManager()
        try:
            session = await manager.get_or_create_session(
                chat_id=context.chat_id,
                open_id=context.open_id,
            )
            self.conversation_manager = manager
            history = await manager.get_context(
                session.session_id,
                max_turns=self.max_context_turns,
            )
            return session.session_id, history, None
        except Exception as exc:
            return None, [], type(exc).__name__

    async def _record_conversation_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str | None,
        message_id: str | None = None,
        tool_calls: Any | None = None,
    ) -> str | None:
        if self.conversation_manager is None:
            return None
        try:
            await self.conversation_manager.add_message(
                session_id,
                role,
                content,
                message_id=message_id,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            return type(exc).__name__
        return None


def _assistant_tool_call_message(tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ],
    }


def _system_prompt() -> str:
    return (
        "你是 Pan Hotel Watch 的飞书智能助手。"
        "你只能通过后端提供的白名单工具查询或触发系统能力。"
        "不要编造酒店业务数据；没有数据时明确说明暂无数据。"
        "不要泄露 webhook、cookie、token、密码、浏览器 profile、本地路径或截图路径。"
        "不要直接执行 OTA 写价、dry-run 或 execute。调价相关请求只能创建飞书审批卡，"
        "并且必须由人工在飞书卡片中审批。"
        "用户询问 EBK 经营报告、出租率、收益概览、销售数据等具体指标时，优先使用 read_ebk_datasets，"
        "从工具返回的 requested_metrics 或 dataset_results 中直接给出答案，不要让用户再去飞书卡片查看。"
        "只有用户明确要求“推送飞书、盯盘推送、读取并推送、跑一次盯盘”时，才使用 run_ebk_watch_once。"
        "回复必须使用简洁中文，面向酒店运营人员。"
    ) + (
        "用户询问日历价、未来15天价格、未来房价或日历价格监测时，先使用 get_calendar_price_latest 读取持久化结果；"
        "用户询问日历价状态、自动轮询、轮询间隔、配置、上次读取或调度时，使用 get_calendar_price_watch_status；"
        "只有用户明确说最新、刷新、立即读、现读或跑一次日历价时，才使用 run_calendar_price_watch_once；"
        "日历价只读，不创建调价审批、dry-run 或 execute。"
    )


def _tool_specs_for_message(user_message: str, tools: list[Any]) -> list[Any]:
    text = user_message.strip().lower()
    filtered = tools
    if _looks_like_ebk_metric_question(text) and not _looks_like_feishu_push_request(text):
        filtered = [tool for tool in filtered if getattr(tool, "name", None) != "run_ebk_watch_once"]
    if _looks_like_calendar_status_question(text):
        return [
            tool
            for tool in filtered
            if getattr(tool, "name", None)
            not in {"get_calendar_price_latest", "run_calendar_price_watch_once"}
        ]
    if _looks_like_calendar_price_question(text) and not _looks_like_calendar_refresh_request(text):
        filtered = [tool for tool in filtered if getattr(tool, "name", None) != "run_calendar_price_watch_once"]
    return filtered


def _looks_like_ebk_metric_question(text: str) -> bool:
    if "ebk" not in text and "经营报告" not in text and "数据中心" not in text:
        return False
    metric_terms = (
        "出租率",
        "入住率",
        "经营报告",
        "收益概览",
        "销售数据",
        "平均卖价",
        "平均房价",
        "成交率",
        "告诉我",
        "多少",
    )
    return any(term in text for term in metric_terms)


def _looks_like_feishu_push_request(text: str) -> bool:
    push_terms = (
        "推送",
        "飞书卡片",
        "发到飞书",
        "发送飞书",
        "盯盘推送",
        "读取并推送",
        "跑一次盯盘",
        "执行盯盘",
    )
    return any(term in text for term in push_terms)


def _looks_like_calendar_price_question(text: str) -> bool:
    terms = (
        "日历价",
        "日历价格",
        "未来15天",
        "未来十五天",
        "未来房价",
        "未来价格",
        "calendar price",
    )
    return any(term in text for term in terms)


def _looks_like_calendar_status_question(text: str) -> bool:
    if not _looks_like_calendar_price_question(text):
        return False
    status_terms = (
        "状态",
        "轮询",
        "间隔",
        "自动",
        "配置",
        "上次",
        "调度",
    )
    return any(term in text for term in status_terms)


def _looks_like_calendar_refresh_request(text: str) -> bool:
    refresh_terms = (
        "最新",
        "刷新",
        "立即读",
        "现读",
        "跑一次",
        "读一遍",
        "重新读",
        "现在读",
    )
    return any(term in text for term in refresh_terms)


def _tool_call_summary_text(tool_results: list[dict[str, Any]]) -> str:
    if not tool_results:
        return "AI Agent 调用了工具，但没有返回摘要。"
    latest = tool_results[-5:]
    lines = ["AI Agent 工具调用摘要："]
    for item in latest:
        lines.append(
            f"- {item.get('tool_name')}: {item.get('status')}，{item.get('summary')}"
        )
    return "\n".join(lines)


def _base_evidence(
    *,
    tool_invoked: bool,
    tool_round_count: int,
    conversation_enabled: bool = False,
    conversation_error: str | None = None,
) -> dict[str, Any]:
    return {
        "tool_invoked": tool_invoked,
        "tool_round_count": tool_round_count,
        "conversation_enabled": conversation_enabled,
        "conversation_error": conversation_error,
        "ota_write_invoked": False,
        "browser_dom_operated_by_agent": False,
        "secrets_returned": False,
    }


async def run_default_ai_agent(
    user_message: str,
    *,
    context: AgentToolCallContext,
) -> AiAgentRunResult:
    return await AiAgentRunner().run(user_message, context=context)
