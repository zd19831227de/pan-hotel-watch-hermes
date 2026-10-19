"""豆包/千问 chat API 共享实现（V6 Step 2）。

`doubao_chat.py` / `qianwen_chat.py` 各自挂一个 router，共用这里的
open_login / check_login_state / ask_and_extract 三个端点实现。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.domain.browser.llm_chat_browser import LlmChatBrowserProfileLockedError
from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.operator_payload import sanitize_operator_payload


class LlmChatLoginRequest(BaseModel):
    actor_id: str = "operator"
    browser_session_id: str | None = None
    headless: bool = False


class LlmChatAskRequest(BaseModel):
    actor_id: str = "operator"
    browser_session_id: str | None = None
    headless: bool = False
    prompt: str = Field(min_length=1, max_length=8000)
    max_wait_seconds: float = Field(default=120.0, ge=5.0, le=300.0)


def build_llm_chat_router(
    *,
    platform: str,
    service_getter: Any,
    display_name: str,
) -> APIRouter:
    router = APIRouter(prefix=f"/{platform}-chat", tags=[f"{platform}-chat"])

    def _gate(platform_account_id: str, requested_browser_session_id: str | None) -> str:
        browser_session_id = requested_browser_session_id or (
            get_browser_session_id_for_platform_account(platform_account_id)
        )
        if not browser_session_id:
            raise HTTPException(
                status_code=422,
                detail=f"No browser session is configured for this platform_account_id.",
            )
        try:
            session = get_browser_session_store().get(browser_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if session.platform != platform or session.intended_use != "llm_chat":
            raise HTTPException(
                status_code=422,
                detail=f"{display_name} chat requires a {display_name} llm_chat browser session.",
            )
        if session.platform_account_id != platform_account_id:
            raise HTTPException(
                status_code=422,
                detail="browser_session_id does not match the requested platform_account_id.",
            )
        # 会话级 chat_url 覆盖（F4-B）：导航与页面判断使用运营配置的地址
        try:
            service_getter().apply_chat_url_override(getattr(session, "chat_url", None))
        except Exception:  # noqa: BLE001 - 覆盖失败时回落平台默认 URL
            pass
        return browser_session_id

    @router.post("/sessions/{platform_account_id}/open-login")
    async def open_llm_chat_login(
        platform_account_id: str,
        request: LlmChatLoginRequest,
    ) -> dict[str, Any]:
        browser_session_id = _gate(platform_account_id, request.browser_session_id)
        try:
            result = await service_getter().open_login(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=request.actor_id,
                headless=request.headless,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LlmChatBrowserProfileLockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return sanitize_operator_payload(result)

    @router.post("/sessions/{platform_account_id}/check-login-state")
    async def check_llm_chat_login_state(
        platform_account_id: str,
        request: LlmChatLoginRequest,
    ) -> dict[str, Any]:
        browser_session_id = _gate(platform_account_id, request.browser_session_id)
        try:
            result = await service_getter().check_login_state(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=request.actor_id,
                headless=request.headless,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LlmChatBrowserProfileLockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return sanitize_operator_payload(result)

    @router.post("/sessions/{platform_account_id}/ask-and-extract")
    async def llm_chat_ask_and_extract(
        platform_account_id: str,
        request: LlmChatAskRequest,
    ) -> dict[str, Any]:
        browser_session_id = _gate(platform_account_id, request.browser_session_id)
        try:
            result = await service_getter().ask_and_extract(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
                prompt=request.prompt,
                actor_id=request.actor_id,
                max_wait_seconds=request.max_wait_seconds,
                headless=request.headless,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LlmChatBrowserProfileLockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return sanitize_operator_payload(result)

    return router
