"""豆包/千问 LLM chat 浏览器服务（V6 Step 2.1/2.2）。

与 EBK 模式同构的「加模块」实现：
- 绑定 chat URL 直开（如 https://www.doubao.com/chat/），打开即为聊天界面；
- Playwright 只做三件事：找输入框、填问题、等流式输出稳定后读答案；
- 登录态持久化：profile 目录 ``.runtime/browser-state/<platform>-chat/<账号>/``；
- OS Chrome 子进程 + CDP 复用 + 跨进程启动锁（与 meituan EBK 同一套路）；
- 验证码/扫码/风控一律 ``human_required``，不绕过（AGENTS.md 原则 4）。

Parser 逻辑（输入框定位、流式稳定、答案提取）独立在本模块，平台改版时
只需调整这里的 selector 常量，不影响主系统。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.domain.browser.sessions import get_browser_session_store

logger = logging.getLogger(__name__)

try:  # Playwright 是运行时依赖；测试环境允许无浏览器导入本模块做纯逻辑测试。
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
    from playwright.async_api import Error as PlaywrightError
except Exception:  # pragma: no cover - 仅缺依赖时降级
    async_playwright = None  # type: ignore[assignment]
    PlaywrightError = Exception  # type: ignore[assignment]
    Playwright = None  # type: ignore[assignment]
    Browser = None  # type: ignore[assignment]
    BrowserContext = None  # type: ignore[assignment]
    Page = None  # type: ignore[assignment]


class LlmChatBrowserProfileLockedError(RuntimeError):
    """chat 浏览器 profile 被其他进程占用（对应 HTTP 409）。"""


# ---------------------------------------------------------------------------
# 平台配置（新增平台 = 加一项配置 + 薄封装模块）
# ---------------------------------------------------------------------------

LLM_CHAT_PLATFORM_CONFIGS: dict[str, dict[str, Any]] = {
    "doubao": {
        "chat_url": "https://www.doubao.com/chat/",
        "profile_dir_name": "doubao-chat",
        "host_markers": ("doubao.com",),
        "display_name": "豆包",
        "assistant_message_selectors": (
            "[data-message-author-role='assistant']",
            "[data-role='assistant']",
            "[class*='assistant']",
        ),
    },
    "qwen": {
        "chat_url": "https://chat.qianwen.ai/",
        "profile_dir_name": "qwen-chat",
        "host_markers": ("qianwen.ai", "tongyi.com"),
        "display_name": "千问",
        "assistant_message_selectors": (
            "[data-message-author-role='assistant']",
            "[data-role='assistant']",
            "[class*='assistant']",
        ),
    },
}

# DOM 依赖最小化：输入框候选 selector（平台改版时只改这里）
_CHAT_INPUT_SELECTORS = (
    "textarea:visible",
    "[contenteditable='true']:visible",
    "div[contenteditable='true']",
)

_HUMAN_MARKERS = (
    "验证码",
    "人机验证",
    "安全验证",
    "风控",
    "滑块",
    "请按住滑块",
    "拖动滑块",
)
_ANSWER_STATUS_MARKERS = (
    "正在理解任务要求",
    "正在思考",
    "思考中",
    "加载中",
    "请求过于频繁",
    "已完成",
)
_UI_TAIL_MARKERS = (
    "工作任务",
    "企业知识",
    "更多技能",
    "连接器",
    "豆包 2.1 Turbo",
    "千问",
)
_LOGIN_MARKERS = (
    "扫码登录",
    "手机号登录",
    "验证码登录",
    "登录后可同步",
    "请登录",
)


def _has_login_markers(body_text: str) -> bool:
    return any(marker in str(body_text or "") for marker in _LOGIN_MARKERS)


def classify_llm_chat_page_state(url: str, body_text: str, *, has_input: bool | None = None) -> str:
    """chat 页面状态分类：authenticated / login / human_required。"""
    text = str(body_text or "")
    if any(marker in text for marker in _HUMAN_MARKERS):
        return "human_required"
    if any(marker in text for marker in _LOGIN_MARKERS):
        return "login"
    if has_input is True:
        return "authenticated"
    if has_input is False:
        return "login"
    return "human_required" if not text.strip() else "login"


def normalize_stream_stability_text(text: str) -> str:
    """流式稳定判断用的归一化：压空白、去数字动画类噪声（如倒计时）。"""
    collapsed = re.sub(r"\s+", " ", str(text or "").strip())
    return collapsed


def _strip_prompt_echo(candidate: str, prompt: str | None) -> str:
    """Remove the submitted user message and role label from chat-page text."""
    text = str(candidate or "").replace("\xa0", " ").strip()
    normalized_text = re.sub(r"\s+", " ", text).strip()
    normalized_prompt = re.sub(r"\s+", " ", str(prompt or "").replace("\xa0", " ")).strip()
    if normalized_prompt:
        prompt_index = normalized_text.rfind(normalized_prompt)
        if prompt_index >= 0:
            normalized_text = normalized_text[prompt_index + len(normalized_prompt) :].lstrip(" \t\r\n:：")
    text = normalized_text
    content_marker = re.search(r"(?:内容如下|以下内容)\s*[:：]", text)
    if content_marker:
        text = text[content_marker.end() :].lstrip()
    for marker in ("助手", "Assistant", "assistant", "AI"):
        if text.startswith(marker):
            text = text[len(marker) :].lstrip(" \t\r\n:：")
            break
    return text.strip()


def _clean_extracted_answer(candidate: str, prompt: str | None) -> str:
    text = _strip_prompt_echo(candidate, prompt)
    tail_positions = [text.find(marker) for marker in _UI_TAIL_MARKERS if text.find(marker) >= 0]
    if tail_positions:
        text = text[: min(tail_positions)].rstrip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line in _UI_TAIL_MARKERS:
            lines = lines[:index]
            break
    text = "\n".join(lines).strip()
    if "已完成" in text:
        text = text.rsplit("已完成", 1)[-1].strip()
    return text


def _is_complete_answer(text: str, *, minimum_length: int = 5) -> bool:
    cleaned = _clean_extracted_answer(text, None)
    meaningful_lines = [
        line
        for line in cleaned.splitlines()
        if line.strip()
        and line.strip() not in _ANSWER_STATUS_MARKERS
        and line.strip() not in _UI_TAIL_MARKERS
    ]
    meaningful = "".join(meaningful_lines).strip()
    if len(meaningful) < minimum_length:
        return False
    return not any(marker in meaningful for marker in ("正在理解任务要求", "正在思考", "请求过于频繁"))


def extract_answer_text(
    before_text: str,
    after_text: str,
    *,
    prompt: str | None = None,
) -> str:
    """从对话全文中提取新出现的回答部分。

    策略：截掉提交前已有内容的最长公共前缀，再去掉开头的提问回显；
    若页面整体重排导致前缀不可用，则退化为取尾部文本。
    """
    before = str(before_text or "")
    after = str(after_text or "")
    if not after.strip():
        return ""
    prefix_len = 0
    max_compare = min(len(before), len(after))
    for index in range(max_compare):
        if before[index] != after[index]:
            break
        prefix_len = index + 1
    candidate = _clean_extracted_answer(after[prefix_len:], prompt)
    if len(candidate) < 20 and len(after) > 200:
        candidate = _clean_extracted_answer(after[-2000:], prompt)
    return candidate.strip()


# ---------------------------------------------------------------------------
# 浏览器服务
# ---------------------------------------------------------------------------


class _ChatProfileLaunchLock:
    """与 EBK 相同的跨进程 profile 启动锁（O_CREAT|O_EXCL 锁文件，120s 过期）。"""

    _STALE_AFTER_SECONDS = 120

    def __init__(self, profile_dir: Path) -> None:
        self.path = profile_dir / ".pan_hotel_watch_launch.lock"
        self._handle: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self._handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._handle, str(os.getpid()).encode("ascii", errors="ignore"))
                return True
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    with suppress(FileNotFoundError):
                        self.path.unlink()
                    continue
                return False
        return False

    def release(self) -> None:
        if self._handle is not None:
            with suppress(OSError):
                os.close(self._handle)
            self._handle = None
        with suppress(FileNotFoundError):
            self.path.unlink()

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self._STALE_AFTER_SECONDS
        except OSError:
            return False


def _resolve_installed_chrome_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


async def _wait_for_cdp_ready(session_port: int, *, timeout_seconds: float = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{session_port}/json/version"
    while time.monotonic() < deadline:
        if await asyncio.to_thread(_probe_cdp_version, url):
            return True
        await asyncio.sleep(0.25)
    return False


def _probe_cdp_version(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - local CDP only
            return int(getattr(response, "status", 200) or 200) == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _is_stale_playwright_page_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "target closed" in message
        or "browser has been closed" in message
        or "context or browser has been closed" in message
        or "connection closed" in message
    )


def _is_profile_singleton_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "processsingleton" in message
        or "lock file can not be created" in message
        or "profile directory" in message
    )


class LlmChatBrowserService:
    """单平台 chat 浏览器服务（doubao/qwen 各一个实例）。"""

    def __init__(self, platform: str) -> None:
        config = LLM_CHAT_PLATFORM_CONFIGS.get(platform)
        if config is None:
            raise ValueError(f"unsupported llm chat platform: {platform}")
        self.platform = platform
        self.chat_url: str = config["chat_url"]
        self._default_chat_url: str = config["chat_url"]
        self.display_name: str = config["display_name"]
        self._host_markers: tuple[str, ...] = tuple(config["host_markers"])
        self._profile_dir_name: str = config["profile_dir_name"]
        self._assistant_message_selectors: tuple[str, ...] = tuple(
            config.get("assistant_message_selectors", ())
        )
        self._playwright: Any = None
        self._cdp_browsers: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._page_locks: dict[str, asyncio.Lock] = {}
        self._os_chrome_processes: dict[str, subprocess.Popen[Any]] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def apply_chat_url_override(self, chat_url: str | None) -> None:
        """应用会话级 chat_url 覆盖（F4-B）。

        值为空/非法时保持平台默认；合法 http(s) 时覆盖导航地址，并把该 host
        并入 host_markers，使"当前是否在 chat 页"的判断接受自定义域名。
        """
        cleaned = str(chat_url or "").strip()
        if not cleaned or not cleaned.lower().startswith(("http://", "https://")):
            return
        self.chat_url = cleaned
        try:
            from urllib.parse import urlparse

            host = urlparse(cleaned).hostname or ""
        except ValueError:
            host = ""
        if host and host not in self._host_markers:
            self._host_markers = (*self._host_markers, host)

    async def open_login(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str = "operator",
        headless: bool = False,
    ) -> dict[str, Any]:
        del headless  # chat 登录始终使用可见 OS Chrome 窗口
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        if not self._is_chat_url(page.url):
            await self._goto_chat(page)
        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=True,
        )

    async def check_login_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str = "operator",
        headless: bool = False,
    ) -> dict[str, Any]:
        del headless
        self._pop_cached_page(platform_account_id, browser_session_id)
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        return await self._sync_login_state_from_page(
            page=page,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            opened_login_window=False,
        )

    async def ask_and_extract(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
        prompt: str,
        actor_id: str = "operator",
        max_wait_seconds: float = 120.0,
        poll_interval_seconds: float = 1.5,
        stable_polls: int = 2,
        headless: bool = False,
    ) -> dict[str, Any]:
        """输入问题 → 等流式输出稳定 → 提取回答文本。"""
        del headless
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            return self._failed_payload(
                platform_account_id,
                browser_session_id,
                "parse_failed",
                "缺少有效的提问内容。",
            )
        page = await self._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        if not self._is_chat_url(page.url):
            await self._goto_chat(page)

        body_text = await self._safe_body_text(page)
        has_input = await self._has_chat_input(page)
        page_state = classify_llm_chat_page_state(page.url, body_text, has_input=has_input)
        # 仅登录/风控标记才打断为 human_required；无输入框但无登录标记更可能是
        # 页面改版（parse_failed），交给 _submit_prompt 的结果判定。
        if page_state == "human_required" or (page_state == "login" and _has_login_markers(body_text)):
            return self._human_required_payload(
                platform_account_id, browser_session_id, page_state, page.url
            )

        before_text = body_text
        before_assistant_messages = await self._read_assistant_messages(page)
        submitted = await self._submit_prompt(page, normalized_prompt)
        if not submitted:
            return self._failed_payload(
                platform_account_id,
                browser_session_id,
                "parse_failed",
                "未找到可用的聊天输入框，页面可能已改版。",
            )

        after_assistant_messages = None
        if before_assistant_messages is not None:
            after_assistant_messages = await self._wait_assistant_messages_stable(
                page,
                before_messages=before_assistant_messages,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                stable_polls=stable_polls,
            )
        if after_assistant_messages:
            answer_text = _clean_extracted_answer(after_assistant_messages[-1], normalized_prompt)
        else:
            after_text = await self._wait_streaming_stable(
                page,
                before_text=before_text,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                stable_polls=stable_polls,
            )
            answer_text = extract_answer_text(before_text, after_text, prompt=normalized_prompt)
        if not answer_text or not _is_complete_answer(answer_text):
            return {
                **self._failed_payload(
                    platform_account_id,
                    browser_session_id,
                    "parse_failed",
                    "未能从页面提取到有效回答（输出为空或未稳定），本轮跳过。",
                ),
                "answer_text": "",
            }
        return {
            "status": "success",
            "summary": f"{self.display_name} chat 回答提取成功（{len(answer_text)} 字）。",
            "platform": self.platform,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "prompt": normalized_prompt[:200],
            "answer_text": answer_text,
            "evidence": {
                "browser_mode": "os_chrome_cdp",
                "page_state": "authenticated",
                "stream_wait_seconds_cap": max_wait_seconds,
                "profile_path_returned": False,
                "password_returned": False,
                "automation_bypass": False,
            },
            "next_action": "回答文本已提取，可继续蒸馏或展示给运营。",
        }

    async def close(self) -> None:
        await self._reset_playwright_driver()

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------

    async def _sync_login_state_from_page(
        self,
        *,
        page: Any,
        platform_account_id: str,
        browser_session_id: str,
        actor_id: str,
        opened_login_window: bool,
    ) -> dict[str, Any]:
        body_text = await self._safe_body_text(page)
        has_input = await self._has_chat_input(page)
        page_state = classify_llm_chat_page_state(page.url, body_text, has_input=has_input)
        login_complete = page_state == "authenticated"
        session = get_browser_session_store().get(browser_session_id)

        if login_complete:
            get_browser_session_store().mark_authenticated(
                browser_session_id,
                actor_id,
                action=f"{self.display_name} chat login confirmed from opened browser session.",
            )
            summary = f"{self.display_name} chat 登录态可用。"
            next_action = "可以直接发起提问/蒸馏任务。"
        else:
            if opened_login_window:
                with suppress(KeyError):
                    get_browser_session_store().mark_login_window_opened(
                        browser_session_id,
                        actor_id,
                        action=f"{self.display_name} chat login window opened; finish login manually if required.",
                    )
                summary = f"{self.display_name} chat 页面已打开，请人工完成登录。"
                next_action = f"在 Chrome 中完成 {self.display_name} 登录后，点击「确认登录态」。"
            else:
                summary = f"{self.display_name} chat 尚未登录或会话已失效。"
                next_action = f"打开 {self.display_name} 登录窗口完成登录后再试。"

        return {
            "status": "success" if login_complete else "human_required",
            "summary": summary,
            "platform": self.platform,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "login_url": self.chat_url,
            "manual_login_required": not login_complete,
            "evidence": {
                "session_port": session.session_port,
                "profile_path_returned": False,
                "password_returned": False,
                "password_stored": False,
                "automation_bypass": False,
                "browser_mode": "os_chrome_cdp",
                "current_url": page.url,
                "page_state": page_state,
            },
            "next_action": next_action,
        }

    # ------------------------------------------------------------------
    # Parser：输入框 / 提交 / 流式稳定（平台改版只需调整这里的 selector）
    # ------------------------------------------------------------------

    async def _has_chat_input(self, page: Any) -> bool:
        return await self._locate_chat_input(page) is not None

    async def _locate_chat_input(self, page: Any) -> Any:
        for selector in _CHAT_INPUT_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    return locator.first
            except Exception:  # noqa: BLE001 - selector 尝试失败继续下一个
                continue
        return None

    async def _submit_prompt(self, page: Any, prompt: str) -> bool:
        locator = await self._locate_chat_input(page)
        if locator is None:
            return False
        try:
            await locator.fill(prompt, timeout=5_000)
        except Exception:  # noqa: BLE001 - contenteditable 可能不支持 fill，退化为 click+type
            try:
                await locator.click(timeout=2_000)
                await locator.type(prompt, delay=10)
            except Exception:  # noqa: BLE001
                return False
        try:
            await locator.press("Enter", timeout=2_000)
        except Exception:  # noqa: BLE001
            with suppress(Exception):
                await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        return True

    async def _wait_streaming_stable(
        self,
        page: Any,
        *,
        before_text: str,
        max_wait_seconds: float,
        poll_interval_seconds: float,
        stable_polls: int,
    ) -> str:
        best_text = str(before_text or "")
        last_normalized = normalize_stream_stability_text(best_text)
        stable_count = 0
        deadline = time.monotonic() + max(1.0, max_wait_seconds)
        while time.monotonic() < deadline:
            await page.wait_for_timeout(int(poll_interval_seconds * 1000))
            text = await self._safe_body_text(page)
            normalized = normalize_stream_stability_text(text)
            if normalized and len(text) >= len(best_text):
                best_text = text
            if normalized and normalized == last_normalized and len(normalized) > len(
                normalize_stream_stability_text(before_text or "")
            ):
                stable_count += 1
                if stable_count >= max(1, stable_polls):
                    break
            else:
                stable_count = 0
                last_normalized = normalized
        return best_text

    async def _read_assistant_messages(self, page: Any) -> list[str] | None:
        messages: list[str] = []
        supported = False
        for selector in self._assistant_message_selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for index in range(count):
                    text = await locator.nth(index).inner_text(timeout=3_000)
                    supported = True
                    if str(text or "").strip():
                        messages.append(str(text).strip())
                if messages:
                    return messages
            except Exception:  # noqa: BLE001 - DOM selectors are platform/version dependent
                continue
        return messages if supported else None

    async def _wait_assistant_messages_stable(
        self,
        page: Any,
        *,
        before_messages: list[str] | None,
        max_wait_seconds: float,
        poll_interval_seconds: float,
        stable_polls: int,
    ) -> list[str] | None:
        if before_messages is None:
            return None
        best: list[str] = []
        previous: tuple[str, ...] | None = None
        stable_count = 0
        deadline = time.monotonic() + max(1.0, max_wait_seconds)
        while time.monotonic() < deadline:
            await page.wait_for_timeout(int(poll_interval_seconds * 1000))
            current = await self._read_assistant_messages(page)
            if len(current) > len(before_messages):
                best = current
            signature = tuple(current[-1:])
            if best and signature == previous:
                stable_count += 1
                if stable_count >= max(1, stable_polls):
                    return best
            else:
                stable_count = 0
                previous = signature
        return best

    async def _safe_body_text(self, page: Any) -> str:
        with suppress(Exception):
            return await page.locator("body").inner_text(timeout=3_000)
        return ""

    def _is_chat_url(self, url: str) -> bool:
        try:
            host = urlparse(url or "").netloc.lower()
        except Exception:
            return False
        return any(marker in host for marker in self._host_markers)

    async def _goto_chat(self, page: Any) -> None:
        await page.goto(self.chat_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(2_000)

    # ------------------------------------------------------------------
    # 页面获取：缓存页 → CDP 复用 → OS Chrome 启动（跨进程锁）
    # ------------------------------------------------------------------

    def _cache_key(self, *, platform_account_id: str, browser_session_id: str) -> str:
        return f"{platform_account_id}:{browser_session_id}:{self._profile_dir_name}"

    def _pop_cached_page(self, platform_account_id: str, browser_session_id: str) -> None:
        self._pages.pop(
            self._cache_key(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            ),
            None,
        )

    def _profile_dir(self, platform_account_id: str) -> Path:
        settings = get_settings()
        return (
            Path(settings.browser_state_dir) / self._profile_dir_name / platform_account_id
        ).resolve()

    async def _get_or_create_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Any:
        cache_key = self._cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        async with self._lock_for(cache_key):
            return await self._get_or_create_page_unlocked(
                platform_account_id=platform_account_id,
                browser_session_id=browser_session_id,
            )

    async def _get_or_create_page_unlocked(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> Any:
        cache_key = self._cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        existing = self._pages.get(cache_key)
        if existing:
            try:
                if not existing.is_closed():
                    return existing
            except Exception as exc:
                if _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                else:
                    raise

        session = get_browser_session_store().get(browser_session_id)
        profile_dir = self._profile_dir(platform_account_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            playwright = await self._get_playwright()
            launch_lock: _ChatProfileLaunchLock | None = None
            try:
                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                )
                if cdp_page is not None:
                    return cdp_page

                launch_lock = _ChatProfileLaunchLock(profile_dir)
                if not launch_lock.acquire():
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page is not None:
                        return cdp_page
                    raise LlmChatBrowserProfileLockedError(
                        f"{self.display_name} chat 浏览器账号正被其他任务占用，请稍后重试或关闭占用窗口。"
                    )

                chrome_path = _resolve_installed_chrome_path()
                if chrome_path is None:
                    launch_lock.release()
                    raise RuntimeError(
                        "未找到本机 Google Chrome。请安装 Chrome 后重试，或先用带调试端口的 Chrome 打开 chat 页面。"
                    )
                args = [
                    str(chrome_path),
                    f"--remote-debugging-port={session.session_port}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    self.chat_url,
                ]
                process = subprocess.Popen(  # noqa: S603 - operator-owned local Chrome launch
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._os_chrome_processes[cache_key] = process
                ready = await _wait_for_cdp_ready(session.session_port, timeout_seconds=20)
                launch_lock.release()
                launch_lock = None
                if not ready:
                    raise RuntimeError("本机 Chrome 已启动，但调试端口尚未就绪，请稍后重试。")

                cdp_page = await self._connect_existing_debug_page(
                    playwright=playwright,
                    platform_account_id=platform_account_id,
                    browser_session_id=browser_session_id,
                    port=session.session_port,
                    timeout_ms=5_000,
                )
                if cdp_page is not None:
                    return cdp_page
                raise RuntimeError("本机 Chrome 已启动，但无法通过 CDP 连接，请关闭占用窗口后重试。")
            except Exception as exc:
                if launch_lock is not None:
                    launch_lock.release()
                if _is_profile_singleton_error(exc):
                    cdp_page = await self._connect_existing_debug_page(
                        playwright=playwright,
                        platform_account_id=platform_account_id,
                        browser_session_id=browser_session_id,
                        port=session.session_port,
                        timeout_ms=5_000,
                    )
                    if cdp_page is not None:
                        return cdp_page
                    raise LlmChatBrowserProfileLockedError(
                        f"{self.display_name} chat 浏览器 profile 正被其他 Chrome 进程占用。"
                    ) from exc
                if attempt == 0 and _is_stale_playwright_page_error(exc):
                    await self._reset_playwright_driver()
                    continue
                raise
        raise RuntimeError(f"{self.display_name} chat browser page could not be created.")

    def _lock_for(self, cache_key: str) -> asyncio.Lock:
        lock = self._page_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._page_locks[cache_key] = lock
        return lock

    async def _reset_playwright_driver(self) -> None:
        close_candidates = [
            *self._pages.values(),
            *self._contexts.values(),
            *self._cdp_browsers.values(),
        ]
        self._pages.clear()
        self._contexts.clear()
        self._cdp_browsers.clear()
        for cached in close_candidates:
            with suppress(Exception):
                await cached.close()
        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
        self._playwright = None

    async def _connect_existing_debug_page(
        self,
        *,
        playwright: Any,
        platform_account_id: str,
        browser_session_id: str,
        port: int,
        timeout_ms: int = 2_000,
    ) -> Any:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                timeout=timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_stale_playwright_page_error(exc):
                raise
            return None
        contexts = browser.contexts
        if not contexts:
            with suppress(Exception):
                await browser.close()
            return None
        context = contexts[0]
        open_pages = [page for page in context.pages if not page.is_closed()]
        if self._is_chat_url(open_pages[0].url if open_pages else ""):
            page = open_pages[0]
        elif open_pages:
            page = open_pages[0]
        else:
            page = await context.new_page()
        cache_key = self._cache_key(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
        )
        self._cdp_browsers[browser_session_id] = browser
        self._contexts[cache_key] = context
        self._pages[cache_key] = page
        return page

    async def _get_playwright(self) -> Any:
        if self._playwright is None:
            configure_windows_subprocess_event_loop_policy()
            if async_playwright is None:
                raise RuntimeError("Playwright 未安装，无法执行浏览器自动化。")
            self._playwright = await async_playwright().start()
        return self._playwright

    # ------------------------------------------------------------------
    # payload 辅助
    # ------------------------------------------------------------------

    def _human_required_payload(
        self,
        platform_account_id: str,
        browser_session_id: str,
        page_state: str,
        current_url: str,
    ) -> dict[str, Any]:
        return {
            "status": "human_required",
            "summary": f"{self.display_name} chat 需要人工处理（{page_state}），本轮跳过。",
            "platform": self.platform,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "manual_login_required": True,
            "answer_text": "",
            "evidence": {
                "browser_mode": "os_chrome_cdp",
                "current_url": current_url,
                "page_state": page_state,
                "profile_path_returned": False,
                "password_returned": False,
                "automation_bypass": False,
            },
            "next_action": f"请在操作台打开 {self.display_name} 登录窗口完成人工登录后重试。",
        }

    def _failed_payload(
        self,
        platform_account_id: str,
        browser_session_id: str,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "summary": summary,
            "platform": self.platform,
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "answer_text": "",
            "evidence": {
                "browser_mode": "os_chrome_cdp",
                "profile_path_returned": False,
                "password_returned": False,
                "automation_bypass": False,
            },
            "next_action": "可稍后重试；若持续失败请检查平台页面是否改版。",
        }
