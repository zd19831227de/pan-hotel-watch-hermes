from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from app.domain.ai_agent.registry import AgentToolSpec
from app.domain.config.service import LlmRuntimeConfig, get_runtime_config

logger = logging.getLogger("app.domain.ai_agent.llm")

# V5 Step 1 - per-model timeout inside the fallback chain. A stuck primary
# yields to the next configured model instead of blocking the whole agent turn.
LLM_FALLBACK_TIMEOUT_SECONDS = 30


class AgentLlmConfigError(RuntimeError):
    pass


class AgentLlmToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentLlmResponse(BaseModel):
    content: str | None = None
    tool_calls: list[AgentLlmToolCall] = Field(default_factory=list)


class AgentLlmClient(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[AgentToolSpec],
    ) -> AgentLlmResponse:
        ...


class OpenAiCompatibleAgentLlmClient:
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[AgentToolSpec],
    ) -> AgentLlmResponse:
        runtime_config = get_runtime_config()
        chain = await runtime_config.llm_runtime_chain()
        if not chain:
            raise AgentLlmConfigError("No usable LLM model is configured.")
        return await chat_with_fallback_chain(
            chain,
            messages=messages,
            tools=tools,
            timeout_seconds=45 if len(chain) == 1 else LLM_FALLBACK_TIMEOUT_SECONDS,
        )


async def chat_with_fallback_chain(
    chain: list[LlmRuntimeConfig],
    *,
    messages: list[dict[str, Any]],
    tools: list[AgentToolSpec],
    timeout_seconds: float = LLM_FALLBACK_TIMEOUT_SECONDS,
) -> AgentLlmResponse:
    """Call each runtime in priority order until one succeeds.

    On timeout, transport error, or an HTTP error response, log a secret-free
    fallback note and try the next runtime. If every runtime fails, raise
    :class:`AgentLlmConfigError` with a clear message (never the api_key).
    """

    if not chain:
        raise AgentLlmConfigError("未配置任何可用的大模型。")

    payload = {
        "messages": messages,
        "tools": [_tool_spec_to_openai_tool(spec) for spec in tools],
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    last_error: Exception | None = None
    for index, runtime in enumerate(chain):
        payload["model"] = runtime.model
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    _chat_completions_endpoint(runtime.base_url),
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError) as exc:
            elapsed = time.monotonic() - started
            last_error = exc
            _log_fallback(runtime, index, chain, elapsed, exc)
            continue
        return _parse_chat_completion_response(_safe_json(response))

    raise AgentLlmConfigError(
        "所有配置的大模型均不可用：" + type(last_error).__name__ if last_error else "未知错误"
    )


def _log_fallback(
    runtime: LlmRuntimeConfig,
    index: int,
    chain: list[LlmRuntimeConfig],
    elapsed: float,
    exc: Exception,
) -> None:
    """Log a secret-free note that one model failed and which model is next.

    Only provider/model/host/error-type/elapsed are logged — never the
    api_key, never the raw exception text (which some SDKs decorate with the
    request URL, but never the bearer token; still, we stay explicit).
    """

    next_runtime = chain[index + 1] if index + 1 < len(chain) else None
    next_label = (
        f"{next_runtime.provider}/{next_runtime.model}" if next_runtime else "（无更多备选）"
    )
    logger.warning(
        "LLM fallback: 模型 %s/%s 调用失败（%s），耗时 %.2fs，回退到 %s",
        runtime.provider,
        runtime.model,
        type(exc).__name__,
        elapsed,
        next_label,
    )


def _tool_spec_to_openai_tool(spec: AgentToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": _tool_description(spec),
            "parameters": spec.parameters,
        },
    }


def _tool_description(spec: AgentToolSpec) -> str:
    safety = "；".join(spec.safety_notes)
    if not safety:
        return spec.description
    return f"{spec.description}\n安全边界：{safety}"


def _parse_chat_completion_response(payload: dict[str, Any]) -> AgentLlmResponse:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return AgentLlmResponse(content="")
    first = choices[0]
    if not isinstance(first, dict):
        return AgentLlmResponse(content="")
    message = first.get("message")
    if not isinstance(message, dict):
        return AgentLlmResponse(content="")
    return AgentLlmResponse(
        content=_optional_text(message.get("content")),
        tool_calls=_parse_tool_calls(message.get("tool_calls")),
    )


def _parse_tool_calls(value: Any) -> list[AgentLlmToolCall]:
    if not isinstance(value, list):
        return []
    calls: list[AgentLlmToolCall] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        calls.append(
            AgentLlmToolCall(
                id=str(item.get("id") or f"tool_call_{index}"),
                name=name,
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    return calls


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chat_completions_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
