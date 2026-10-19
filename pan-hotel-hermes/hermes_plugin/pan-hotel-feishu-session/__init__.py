from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# hermes_plugin/pan-hotel-feishu-session → pan-hotel-hermes root (for client.*)
_HERMES_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_HERMES_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_HERMES_PKG_ROOT))

_MCP_TOOL_PREFIX = "mcp_pan_hotel_watch_"
_FEISHU_PLATFORMS = {"feishu", "lark"}
# 2026-10-09 通用 agent 模式：路由翻转后仅保留 pricing_query / manual_pricing
# 两条确定性安全通道（走 dispatch / create_manual_pricing_approval），其余
# 一切问法统一 generic_agent 全目录放行，由 LLM 自主选工具（见
# client/domain_intent_router.py 头注释）。本插件只剩三件事：安全通道外的
# 执行类工具硬拦、浏览器现读每轮预算、飞书会话注入/卡片渲染。
_ACTION_CARD_TOOLS = frozenset(
    {
        f"{_MCP_TOOL_PREFIX}create_manual_pricing_approval",
        f"{_MCP_TOOL_PREFIX}dispatch_watch_feishu_event",
    }
)
_CARD_SENT_BY_SESSION: dict[str, bool] = {}
_CARD_SENT_MESSAGE_BY_SESSION: dict[str, str] = {}
_TEXT_REPLY_BY_SESSION: dict[str, bool] = {}
_ROUTE_BY_SESSION: dict[str, Any] = {}
_ROUTE_MESSAGE_BY_SESSION: dict[str, str] = {}
# 用户本轮原文（按 chat_id / session_id），注入 MCP user_text 供工具侧上下文使用。
_USER_TEXT_BY_CHAT: dict[str, str] = {}
_USER_TEXT_BY_SESSION: dict[str, str] = {}
_CARD_ALREADY_SENT_BLOCK_MESSAGE = (
    "本轮已经成功发送飞书卡片；禁止再发第二张卡或 response_mode=detail。"
    "若还需补充证据，可继续调用只读工具，最终用一句话或不可见确认结束。"
)
# Hermes 需要非空字符串才会替换最终输出；零宽字符避免再发一条可见长文。
_INVISIBLE_ACK = "\u200b"
_PRE_TOOL_PREAMBLE_PATTERNS = (
    "我先查",
    "我查一下",
    "先确认",
    "我确认一下",
    "稍等",
    "马上看",
    "我看一下",
    "先看一下",
)


def _bridge_dir() -> Path:
    root = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "hermes",
    )
    return Path(root) / "feishu-session-bridge"


def _safe_key(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(value or "").strip())[:200]


def _write_bridge(
    *,
    chat_id: str = "",
    session_id: str = "",
    message_id: str = "",
    open_id: str = "",
) -> None:
    payload = {
        "chat_id": str(chat_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "message_id": str(message_id or "").strip(),
        "open_id": str(open_id or "").strip(),
    }
    if not payload["message_id"] and not payload["chat_id"]:
        return
    directory = _bridge_dir()
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False)
    for key in (payload["chat_id"], payload["session_id"]):
        if not key:
            continue
        path = directory / f"{_safe_key(key)}.json"
        path.write_text(encoded, encoding="utf-8")


def _read_bridge(*, chat_id: str = "", session_id: str = "") -> dict[str, Any]:
    directory = _bridge_dir()
    for key in (str(chat_id or "").strip(), str(session_id or "").strip()):
        if not key:
            continue
        path = directory / f"{_safe_key(key)}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _session_fields() -> tuple[str, str, str]:
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return "", "", ""
    message_id = get_session_env("HERMES_SESSION_MESSAGE_ID", "")
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
    open_id = get_session_env("HERMES_SESSION_USER_ID", "")
    return message_id, chat_id, open_id


def _allowed_chat_ids() -> set[str]:
    value = str(os.environ.get("FEISHU_ALLOWED_CHATS") or "").strip()
    if not value:
        return set()
    return {item for item in re.split(r"[\s,;]+", value) if item}


def _is_allowed_chat_id(chat_id: str) -> bool:
    allowed = _allowed_chat_ids()
    if not allowed:
        strict = str(
            os.environ.get("FEISHU_STRICT_TENANT_ROUTING") or ""
        ).strip().lower()
        return strict not in {"1", "true", "yes", "on"}
    return str(chat_id or "").strip() in allowed


def _extract_event_chat_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    for candidate in (
        message.get("chat_id"),
        context.get("open_chat_id"),
        event.get("chat_id"),
        event.get("open_chat_id"),
        payload.get("chat_id"),
        payload.get("open_chat_id"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def _resolve_chat_id(session_id: str = "") -> str:
    _, chat_id, _ = _session_fields()
    if chat_id:
        return chat_id
    bridge = _read_bridge(session_id=session_id)
    return str(bridge.get("chat_id") or "").strip()


def _inject_feishu_session(args: dict[str, Any], *, session_id: str = "") -> None:
    message_id, chat_id, open_id = _session_fields()
    candidate_chat_id = str(chat_id or args.get("chat_id") or "").strip()
    if candidate_chat_id and not _is_allowed_chat_id(candidate_chat_id):
        for key in ("message_id", "chat_id", "open_id"):
            args.pop(key, None)
        return
    if message_id and not str(args.get("message_id") or "").strip():
        args["message_id"] = message_id
    if chat_id and not str(args.get("chat_id") or "").strip():
        args["chat_id"] = chat_id
    if open_id and not str(args.get("open_id") or "").strip():
        args["open_id"] = open_id
    resolved_chat = str(chat_id or args.get("chat_id") or "").strip()
    user_text = _user_text_for_session(session_id=session_id, chat_id=resolved_chat)
    if user_text and not str(args.get("user_text") or "").strip():
        args["user_text"] = user_text
    _write_bridge(
        chat_id=chat_id,
        session_id=session_id,
        message_id=message_id,
        open_id=open_id,
    )


def _watch_api_base_url() -> str:
    for key in ("WATCH_API_BASE_URL", "PAN_HOTEL_WATCH_API_BASE_URL"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    config_path = Path(os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "hermes",
    )) / "config.yaml"
    if config_path.is_file():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
            pan_hotel = servers.get("pan-hotel-watch") if isinstance(servers.get("pan-hotel-watch"), dict) else {}
            env = pan_hotel.get("env") if isinstance(pan_hotel.get("env"), dict) else {}
            value = str(env.get("WATCH_API_BASE_URL") or "").strip()
            if value:
                return value.rstrip("/")
        except Exception:
            pass
    return "http://127.0.0.1:8100"


def _send_text_as_card(*, chat_id: str, text: str, title: str = "运营助手") -> bool:
    """Wrap a Q&A text reply as a Feishu interactive card via the backend.

    POSTs to the backend ``/api/v1/feishu/operator-card-sends`` endpoint, which
    calls ``deliver_operator_text_as_card`` → ``build_conversation_reply_card``
    and sends ``msg_type=interactive`` through the app bot. Requires the watch
    service token (``WATCH_API_SHARED_SECRET``) so the backend accepts the call.

    Returns True on success, False on any failure (missing token, HTTP error,
    backend unavailable) so callers can fall back to the native text channel.
    """
    body_text = str(text or "").strip()
    if not body_text or not str(chat_id or "").strip():
        return False
    token = os.environ.get("WATCH_API_SHARED_SECRET", "").strip()
    if not token:
        return False
    body = json.dumps(
        {"chat_id": chat_id, "text": body_text, "title": title},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{_watch_api_base_url()}/api/v1/feishu/operator-card-sends",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Pan-Hotel-Service-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return 200 <= int(getattr(response, "status", 200)) < 300
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("feishu Q&A card send failed: %s", exc)
        return False
    except Exception as exc:
        logger.warning("feishu Q&A card send failed: %s", exc)
        return False



def _looks_like_tool_preamble(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "")).strip()
    if not normalized:
        return False
    if len(normalized) > 42:
        return False
    if not any(pattern in normalized for pattern in _PRE_TOOL_PREAMBLE_PATTERNS):
        return False
    substantive_markers = ("已开启", "未开启", "间隔", "上次", "读取成功", "状态", "价格", "元", "%")
    if any(marker in normalized for marker in substantive_markers) and re.search(r"\d", normalized):
        return False
    return True


def _sanitize_feishu_text_reply(text: str) -> str:
    """Convert document-style Markdown into compact native Feishu text."""
    sanitized_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if re.fullmatch(r"```.*", stripped):
            continue
        if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", stripped):
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", stripped):
            continue

        if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            line = " · ".join(cell for cell in cells if cell)
        else:
            line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
            line = re.sub(r"^\s*[*+-]\s+", "• ", line)
            line = re.sub(r"^\s+(\d+[.)])\s+", r"\1 ", line)

        line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_\n]+)__", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        line = line.replace("`", "")
        sanitized_lines.append(line.strip() if line.strip() else "")

    sanitized = "\n".join(sanitized_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", sanitized)


def _to_plain_payload(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_to_plain_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain_payload(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _to_plain_payload(value.model_dump(mode="json"))
    if hasattr(value, "to_dict"):
        return _to_plain_payload(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _to_plain_payload(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return None


def _feishu_event_type(payload: dict[str, Any]) -> str:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    return str(header.get("event_type") or "").strip()


def _extract_message_text(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content_raw = message.get("content")
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            return content_raw.strip()
    elif isinstance(content_raw, dict):
        content = content_raw
    else:
        return ""
    text = content.get("text") if isinstance(content, dict) else None
    return str(text or "").strip()


def _is_watch_feishu_event(payload: dict[str, Any]) -> bool:
    # 2026-10-14 全量 LLM 先行：文本消息不再按词表直转后端（词表子串会
    # 劫持测算/图表追问等问法，生产两起实证），一律交 Hermes LLM 判断后
    # 自主调 dispatch 转发原话。仅 url_verification 与卡片回调仍直转：
    # 前者是 webhook 握手，后者是审批按钮等卡片链路，必须进后端。
    if payload.get("type") == "url_verification":
        return True
    event_type = _feishu_event_type(payload)
    if event_type == "url_verification":
        return True
    if event_type in {"card.action.trigger", "card.action.trigger_v1"}:
        return True
    return isinstance(payload.get("action"), dict)


def _forward_watch_feishu_event(payload: dict[str, Any]) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    # /api/v1/feishu/events 由 require_watch_service_token 保护；后端配置了
    # WATCH_API_SHARED_SECRET 时必须携带，否则调价指令/卡片动作转发全部 401。
    service_token = os.environ.get("WATCH_API_SHARED_SECRET", "").strip()
    if service_token:
        headers["X-Pan-Hotel-Service-Token"] = service_token
    request = urllib.request.Request(
        f"{_watch_api_base_url()}/api/v1/feishu/events",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return 200 <= int(getattr(response, "status", 200)) < 300
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("pan-hotel-feishu-session watch event forward failed: %s", exc)
        return False
    except Exception as exc:
        logger.warning("pan-hotel-feishu-session watch event forward failed: %s", exc)
        return False


# ── 群资料归档（2026-10-10）──────────────────────────────────────────
# 运营每天把「日期实施经营数据」（文本/截图/Excel）人工丢群里，AI 需要跨日
# 积累分析。此处把允许群里的 im.message.receive_v1 原始事件转发后端
# /api/v1/group-archive/events 永久归档（SQLite 真库 + data/feishu-group-archive
# 附件目录，任何清理任务不得触碰）。三条硬约束：
# 1. 转发在守护线程里跑（urllib 阻塞调用不得占住网关事件循环——本钩子对
#    每条群消息都会触发，不同于只偶发命中的调价转发）；
# 2. 数据不丢：后端不可达（重启/宕机）时事件写本地 spool（HERMES_HOME/
#    group-archive-spool），恢复后成功投递时自动重放并清 spool；
# 3. 绝不影响正常对话：异常只记日志，熔断 60s；
#    FEISHU_GROUP_ARCHIVE_ENABLED=false 可整体停用。只归档群聊（chat_type=
#    group）人发的消息，机器人自发与单聊不归档。
_ARCHIVE_FORWARD_FAIL_AT = 0.0
_ARCHIVE_FORWARD_TIMEOUT_SECONDS = 8.0
_ARCHIVE_FORWARD_BREAKER_SECONDS = 60.0
_ARCHIVE_SPOOL_DIR_NAME = "group-archive-spool"
_ARCHIVE_SPOOL_MAX_DRAIN = 20


def _group_archive_enabled() -> bool:
    value = str(os.environ.get("FEISHU_GROUP_ARCHIVE_ENABLED") or "").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _event_sender_type(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    return str(sender.get("sender_type") or "").strip()


def _archive_event_message(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    return message


def _archive_spool_dir() -> Path:
    root = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "hermes",
    )
    return Path(root) / _ARCHIVE_SPOOL_DIR_NAME


def _spool_archive_event(payload: dict[str, Any], message_id: str) -> None:
    directory = _archive_spool_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # 随机后缀防同毫秒同名覆盖（排序仍按毫秒时间戳前缀保持时序）。
    path = directory / (
        f"{int(time.time() * 1000)}-{_safe_key(message_id)}-{uuid.uuid4().hex[:8]}.json"
    )
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info("group archive event spooled: %s", message_id)


def _drain_archive_spool() -> None:
    directory = _archive_spool_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json"))[:_ARCHIVE_SPOOL_MAX_DRAIN]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        if not isinstance(payload, dict) or not _post_archive_event(payload):
            return  # 后端仍不可达：保留剩余积压，等下一轮
        path.unlink(missing_ok=True)
        logger.info("group archive spool drained: %s", path.name)


def _post_archive_event(payload: dict[str, Any]) -> bool:
    global _ARCHIVE_FORWARD_FAIL_AT
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    service_token = os.environ.get("WATCH_API_SHARED_SECRET", "").strip()
    if service_token:
        headers["X-Pan-Hotel-Service-Token"] = service_token
    request = urllib.request.Request(
        f"{_watch_api_base_url()}/api/v1/group-archive/events",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_ARCHIVE_FORWARD_TIMEOUT_SECONDS
        ) as response:
            response.read()
        return True
    except Exception as exc:
        _ARCHIVE_FORWARD_FAIL_AT = time.monotonic()
        logger.warning("group archive forward failed: %s", exc)
        return False


def _archive_group_message_event(payload: dict[str, Any]) -> None:
    """归档工作体（在守护线程里跑）：post 当前事件；成功后重放 spool 积压。"""
    try:
        # 应用消息（机器人自发/卡片回执）与单聊不归档，只积累群资料。
        if _event_sender_type(payload) == "app":
            return
        message = _archive_event_message(payload)
        if str(message.get("chat_type") or "").strip() != "group":
            return
        message_id = str(message.get("message_id") or "").strip()
        if not message_id:
            return
        if time.monotonic() - _ARCHIVE_FORWARD_FAIL_AT < _ARCHIVE_FORWARD_BREAKER_SECONDS:
            _spool_archive_event(payload, message_id)
            return
        if not _post_archive_event(payload):
            _spool_archive_event(payload, message_id)
            return
        _drain_archive_spool()
    except Exception as exc:  # 归档永不拖垮对话链路
        logger.warning("group archive capture failed: %s", exc)


def _dispatch_group_message_event(payload: dict[str, Any]) -> None:
    threading.Thread(
        target=_archive_group_message_event, args=(payload,), daemon=True
    ).start()


def _should_archive_event_payload(payload: dict[str, Any]) -> bool:
    """群聊人发消息才归档（机器人自发/单聊不归档）；线程启动前先过滤。"""
    if _event_sender_type(payload) == "app":
        return False
    message = _archive_event_message(payload)
    if str(message.get("chat_type") or "").strip() != "group":
        return False
    return bool(str(message.get("message_id") or "").strip())


def _patch_feishu_adapter_intake() -> None:
    """飞书适配器进站口观察点（2026-10-10 静里群 4 份 Excel 失踪实证）。

    hermes 核心对 require_mention 群在适配器准入层（``_admit``）直接丢弃
    不带 @机器人 的消息——而文件/图片消息结构上无法携带 @——这类事件永远
    到不了 pre_gateway_dispatch 钩子，归档与「发完文件再问」链路全断。
    此处包装 ``FeishuAdapter._handle_message_event_data``（ws/webhook 共用
    进站口，两处调用点均为运行时属性查找）：进站先投递归档线程，再原样
    调用原实现——只观察、零对话行为改动。hermes 升级改内部名时补丁自动
    失效（import 失败静默跳过），回退到 pre_gateway_dispatch 路径（仅覆盖
    被准入的 @ 消息）。"""
    try:
        from plugins.platforms.feishu.adapter import FeishuAdapter
    except Exception as exc:
        logger.info("group archive intake patch unavailable: %s", exc)
        return
    original = getattr(FeishuAdapter, "_handle_message_event_data", None)
    if original is None or getattr(original, "_pan_hotel_archive_intake", False):
        return

    async def _archiving_intake(self: Any, data: Any) -> Any:
        try:
            if _group_archive_enabled():
                payload = _to_plain_payload(data)
                if (
                    isinstance(payload, dict)
                    and _feishu_event_type(payload) == "im.message.receive_v1"
                    and _should_archive_event_payload(payload)
                ):
                    _dispatch_group_message_event(payload)
        except Exception as exc:  # 归档绝不影响正常消息处理
            logger.warning("group archive capture failed: %s", exc)
        return await original(self, data)

    _archiving_intake._pan_hotel_archive_intake = True  # type: ignore[attr-defined]
    FeishuAdapter._handle_message_event_data = _archiving_intake  # type: ignore[assignment]
    logger.info("group archive intake patch installed on FeishuAdapter")


def _remember_user_text(chat_id: str = "", text: str = "", session_id: str = "") -> None:
    body = str(text or "").strip()
    if not body:
        return
    cid = str(chat_id or "").strip()
    sid = str(session_id or "").strip()
    if cid:
        _USER_TEXT_BY_CHAT[cid] = body
    if sid:
        _USER_TEXT_BY_SESSION[sid] = body


def _extract_user_text_from_kwargs(kwargs: dict[str, Any]) -> str:
    for key in ("user_message", "user_text", "message_text", "prompt", "text", "input", "query"):
        candidate = kwargs.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    messages = kwargs.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = item.get("content")
            if role in {"user", "human"} and isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _prune_failed_tool_history(messages: Any, *, new_turn: bool = False) -> None:
    """Remove completed failed tool turns from the next model call."""
    if not isinstance(messages, list) or not messages:
        return
    remove_indexes: set[int] = set()
    for failed_index, item in enumerate(messages):
        if not isinstance(item, dict) or str(item.get("role") or "").lower() != "tool":
            continue
        if not _history_payload_failed(item.get("content")):
            continue
        if not new_turn and not any(
            isinstance(later, dict)
            and str(later.get("role") or "").strip().lower() in {"user", "human"}
            for later in messages[failed_index + 1 :]
        ):
            continue

        start_index = failed_index
        failed_call_id = str(item.get("tool_call_id") or "").strip()
        for candidate_index in range(failed_index - 1, -1, -1):
            candidate = messages[candidate_index]
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("role") or "").strip().lower() != "assistant":
                continue
            tool_calls = candidate.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            call_ids = {
                str(call.get("id") or "").strip()
                for call in tool_calls
                if isinstance(call, dict)
            }
            if failed_call_id and failed_call_id not in call_ids:
                continue
            start_index = candidate_index
            break

        for candidate_index in range(start_index - 1, -1, -1):
            candidate = messages[candidate_index]
            if isinstance(candidate, dict) and str(candidate.get("role") or "").strip().lower() in {
                "user",
                "human",
            }:
                start_index = candidate_index
                break
        remove_indexes.update(range(start_index, failed_index + 1))

    if remove_indexes:
        messages[:] = [item for index, item in enumerate(messages) if index not in remove_indexes]


def _history_payload_failed(value: Any) -> bool:
    failure_statuses = {"failed", "misrouted", "tool_not_allowed", "invalid_arguments"}
    if isinstance(value, dict):
        if str(value.get("status") or "").strip().lower() in failure_statuses:
            return True
        return any(
            _history_payload_failed(value.get(key))
            for key in ("result", "payload")
            if key in value
        )
    if isinstance(value, str):
        body = value.strip()
        if not body:
            return False
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError):
            return "调用失败" in body or "读取超时" in body
        if parsed != value:
            return _history_payload_failed(parsed)
    return False


def _user_text_for_session(session_id: str = "", chat_id: str = "") -> str:
    cid = str(chat_id or "").strip()
    if cid and cid in _USER_TEXT_BY_CHAT:
        return _USER_TEXT_BY_CHAT[cid]
    sid = str(session_id or "").strip()
    if sid and sid in _USER_TEXT_BY_SESSION:
        return _USER_TEXT_BY_SESSION[sid]
    if not cid:
        bridge = _read_bridge(session_id=session_id)
        cid = str(bridge.get("chat_id") or "").strip()
        if cid and cid in _USER_TEXT_BY_CHAT:
            return _USER_TEXT_BY_CHAT[cid]
    return ""


def _on_pre_gateway_dispatch(event: Any = None, **_: Any) -> dict[str, str] | None:
    payload = _to_plain_payload(getattr(event, "raw_message", None))
    if not isinstance(payload, dict):
        return None
    chat_id = _extract_event_chat_id(payload)
    if chat_id and not _is_allowed_chat_id(chat_id):
        return {"action": "skip", "reason": "feishu_chat_not_allowed"}
    message_text = _extract_message_text(payload)
    if chat_id and message_text:
        _remember_user_text(chat_id=chat_id, text=message_text)
    if _group_archive_enabled() and _feishu_event_type(payload) == "im.message.receive_v1":
        try:
            _dispatch_group_message_event(payload)
        except Exception as exc:
            logger.warning("group archive capture failed: %s", exc)
    if not _is_watch_feishu_event(payload):
        return None
    if _forward_watch_feishu_event(payload):
        return {"action": "skip", "reason": "pan_hotel_watch_feishu_event_forwarded"}
    return None

def _on_pre_llm_call(session_id: str = "", **kwargs: Any) -> dict[str, str] | None:
    message_id, chat_id, open_id = _session_fields()
    if chat_id and not _is_allowed_chat_id(chat_id):
        return None
    history = kwargs.get("conversation_history")
    if not isinstance(history, list):
        history = kwargs.get("messages")
    candidate = _extract_user_text_from_kwargs(kwargs)
    _prune_failed_tool_history(history, new_turn=bool(candidate))
    if candidate:
        _remember_user_text(chat_id=chat_id, text=candidate, session_id=session_id or "")
    if chat_id or message_id:
        _write_bridge(
            chat_id=chat_id,
            session_id=session_id or "",
            message_id=message_id,
            open_id=open_id,
        )
    if not candidate:
        return None
    try:
        from client.domain_intent_router import (
            build_pre_llm_route_context,
            route_domain_intent,
        )
        route = route_domain_intent(candidate)
        if session_id:
            _ROUTE_BY_SESSION[session_id] = route
            _ROUTE_MESSAGE_BY_SESSION[session_id] = str(message_id or "").strip()
        return {
            "context": build_pre_llm_route_context(route),
            "route_intent": str(route.intent or "unclassified"),
        }
    except Exception as exc:
        logger.warning("pre-llm domain route failed: %s", exc)
        return None


# 2026-10-08 常规 agent 能力：兜底/学习路由允许按需现读（run_*），不再把
# refresh 参数钳回 false。重工具的成本控制改为每轮预算：同一条消息内现读
# 至多预算次，超出即 block 并要求基于本轮已取数据作答（防 LLM 循环拉
# 浏览器）。计数按 (session, message_id) 归零。
# 2026-10-09 通用 agent 模式：预算默认 2，可由 HERMES_LIVE_READ_BUDGET
# 环境变量调整（1~6）；这是通用模式唯一的浏览器成本闸（词表白名单已废）。
# 现读口径：除 run_* 外，退款状态/今日预定 MCP 侧默认 refresh=true（未带
# 自定义区间时每次都现读），read_ebk_datasets 本身即数据集现读——这三个
# 不数进预算就成了无限拉浏览器的旁路。
_HEAVY_RUN_TOOLS = frozenset(
    {
        f"{_MCP_TOOL_PREFIX}run_ebk_watch_once",
        f"{_MCP_TOOL_PREFIX}run_competitor_watch_once",
        f"{_MCP_TOOL_PREFIX}run_calendar_price_watch_once",
        f"{_MCP_TOOL_PREFIX}read_ebk_datasets",
        f"{_MCP_TOOL_PREFIX}get_refund_status",
        f"{_MCP_TOOL_PREFIX}get_today_reservations",
    }
)


def _heavy_run_budget() -> int:
    raw = str(os.environ.get("HERMES_LIVE_READ_BUDGET") or "").strip()
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 2


def _heavy_run_block_message() -> str:
    return (
        f"本轮浏览器现读（run_* / 退款状态 / 今日预定 / 数据集现读）次数已达上限"
        f"{_heavy_run_budget()} 次，不要再启动新的浏览器读取。"
        "请基于本轮已取到的工具结果直接作答；确实还缺的数据，如实说明缺口并"
        "建议运营稍后单独发起刷新。"
    )
# 2026-10-08 晚生产实证（静里 21:14 原句）：用户问对比曲线图，LLM 漏传
# with_chart，转而自绘 PNG 存本地——飞书应用无文件上传权限，图物理发不出去，
# 回复却谎称「图在下方」。硬约束：消息含图表词 + 调趋势工具 → 一律补
# with_chart=true，走后端渲染 PNG 图卡的已验证链路。
_CHART_REQUEST_TOKENS = ("图", "曲线", "走势", "折线", "chart")
# 通用 agent 轮的工具使用追踪：transform_tool_result 记录本轮非失败工具，
# 最终回答送达后由 _record_learned_route_for_turn 写入学习库（探索→记录）。
# 2026-10-09 路由翻转：generic_agent 轮即是可学习轮（先验软提示，下一轮
# 同问法直接带「此前用过哪些工具」）；安全通道轮不记（有固定链路）。
_TOOLS_USED_BY_SESSION: dict[str, set[str]] = {}
_HEAVY_CALLS_BY_SESSION: dict[str, dict[str, Any]] = {}
_LEARNED_REASONS = frozenset({"generic_agent_mode"})
_CONTEXT_TOOL_NAMES = frozenset({"resolve_feishu_chat_context"})


def _session_key(session_id: str = "", task_id: str = "") -> str:
    return str(session_id or task_id or "").strip()


def _enforce_heavy_run_budget(session_id: str, tool_name: str) -> dict[str, str] | None:
    """同一条消息内 run_* 现读至多预算次（默认 2，环境变量可调）；超出 block。

    计数按 (session, message_id)：新消息自然归零；轮结束清态时一并移除。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None
    message_id = _current_message_id(sid)
    counter = _HEAVY_CALLS_BY_SESSION.get(sid)
    if not isinstance(counter, dict) or counter.get("message_id") != message_id:
        counter = {"message_id": message_id, "calls": []}
        _HEAVY_CALLS_BY_SESSION[sid] = counter
    calls = counter.setdefault("calls", [])
    if len(calls) >= _heavy_run_budget():
        return {"action": "block", "message": _heavy_run_block_message()}
    calls.append(tool_name)
    return None


def _force_chart_when_requested(tool_name: str, args: Any, user_text: str) -> bool:
    """用户本轮明确要图时，趋势工具必须带 with_chart=true 出图卡。

    出图链路（后端渲染 PNG + 图卡）只有这一个开关；LLM 偶发漏传参数会退化成
    纯文字，甚至自绘本地 PNG 却无通道发送。此处就地补参数，返回是否补过。
    """
    if tool_name != f"{_MCP_TOOL_PREFIX}get_competitor_price_trend":
        return False
    if not isinstance(args, dict) or args.get("with_chart") is True:
        return False
    text = str(user_text or "").lower()
    if not any(token in text for token in _CHART_REQUEST_TOKENS):
        return False
    args["with_chart"] = True
    return True


# 2026-10-09 生产实证（静里 10:38「前三天竞争圈排名，给一份图表」）：回复带
# MEDIA:<本地附件> 时若被发卡链路换成隐形占位符，网关原生附件投递（base.py
# extract_media → send_multiple_images → 飞书图片上传）只能在占位文本上提取，
# MEDIA 标签被吞、图永远发不出（cron 推送走 scheduler 独立投递不受影响）。
# 扩展名锚定的匹配口径与网关 _TOOL_MEDIA_RE 对齐，防止散文里的裸 MEDIA: 误判。
_MEDIA_TAG_RE = re.compile(
    r"MEDIA:((?:[A-Za-z]:[/\\]|/|~/)\S+\."
    r"(?:png|jpe?g|gif|webp|mp4|mov|avi|mkv|webm|"
    r"ogg|opus|mp3|wav|m4a|flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?))",
    re.IGNORECASE,
)


def _has_deliverable_media_tag(text: str) -> bool:
    """回复里是否带指向真实存在文件的 MEDIA: 附件标签。"""
    for match in _MEDIA_TAG_RE.finditer(str(text or "")):
        path = match.group(1).strip().rstrip('",}')
        if path and Path(path).is_file():
            return True
    return False


def _record_tool_used_for_session(session_id: str, tool_name: str, payload: dict[str, Any]) -> None:
    """记录本轮非失败工具（学习库的「探索」素材）；纯 context 工具不计。"""
    sid = str(session_id or "").strip()
    if not sid:
        return
    plain = tool_name[len(_MCP_TOOL_PREFIX) :] if tool_name.startswith(_MCP_TOOL_PREFIX) else tool_name
    if plain in _CONTEXT_TOOL_NAMES:
        return
    if _history_payload_failed(payload):
        return
    _TOOLS_USED_BY_SESSION.setdefault(sid, set()).add(plain)


def _record_learned_route_for_turn(session_id: str) -> None:
    """兜底/学习轮成功送达回答后，把「问法 → 实际用过的工具」写入学习库。

    只在路由 reason 属于兜底/学习时记录（确定性词表路由已有固定路径）；
    没用过数据工具的纯闲聊不记。写入失败只 debug 日志，不影响对话。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return
    route = _ROUTE_BY_SESSION.get(sid)
    reason = str(getattr(route, "reason", "") or "") if route is not None else ""
    if reason not in _LEARNED_REASONS:
        return
    tools = {tool for tool in (_TOOLS_USED_BY_SESSION.get(sid) or set()) if tool not in _CONTEXT_TOOL_NAMES}
    if not tools:
        return
    user_text = _user_text_for_session(session_id=sid)
    if not user_text:
        return
    try:
        from client.learned_routes import record_learned_route

        recorded = record_learned_route(user_text, sorted(tools))
        if recorded:
            logger.info("learned route recorded for session %s (%d tools)", sid, len(tools))
    except Exception as exc:
        logger.debug("learned route record failed: %s", exc)


def _current_message_id(session_id: str) -> str:
    message_id, _, _ = _session_fields()
    if message_id:
        return str(message_id).strip()
    bridge = _read_bridge(session_id=session_id)
    return str(bridge.get("message_id") or "").strip()


def _mark_card_sent(session_id: str) -> None:
    if not session_id:
        return
    _CARD_SENT_BY_SESSION[session_id] = True
    message_id = _current_message_id(session_id)
    if message_id:
        _CARD_SENT_MESSAGE_BY_SESSION[session_id] = message_id


def _clear_card_sent_state(session_id: str) -> None:
    _CARD_SENT_BY_SESSION.pop(session_id, None)
    _CARD_SENT_MESSAGE_BY_SESSION.pop(session_id, None)
    _TEXT_REPLY_BY_SESSION.pop(session_id, None)


def _payload_is_text_reply_mode(payload: dict[str, Any]) -> bool:
    mode = str(payload.get("response_mode") or payload.get("reply_mode") or "").strip().lower()
    return mode == "text"


def _mark_text_reply_mode(session_id: str, payload: dict[str, Any]) -> None:
    if not session_id or not _payload_is_text_reply_mode(payload):
        return
    _TEXT_REPLY_BY_SESSION[session_id] = True


def _card_sent_for_current_message(session_id: str) -> bool:
    if not session_id or not _CARD_SENT_BY_SESSION.get(session_id):
        return False
    sent_message_id = str(_CARD_SENT_MESSAGE_BY_SESSION.get(session_id) or "").strip()
    current_message_id = _current_message_id(session_id)
    if sent_message_id and current_message_id and sent_message_id != current_message_id:
        _clear_card_sent_state(session_id)
        return False
    return True


def _clear_session_turn_state(session_id: str) -> None:
    if not session_id:
        return
    _clear_card_sent_state(session_id)
    _ROUTE_BY_SESSION.pop(session_id, None)
    _ROUTE_MESSAGE_BY_SESSION.pop(session_id, None)
    _USER_TEXT_BY_SESSION.pop(session_id, None)
    _TOOLS_USED_BY_SESSION.pop(session_id, None)
    _HEAVY_CALLS_BY_SESSION.pop(session_id, None)
    bridge = _read_bridge(session_id=session_id)
    chat_id = str(bridge.get("chat_id") or "").strip()
    if chat_id:
        _USER_TEXT_BY_CHAT.pop(chat_id, None)


def _resolve_user_text_for_tool(
    *,
    args: Any,
    session_id: str,
) -> str:
    if isinstance(args, dict):
        explicit = str(args.get("user_text") or "").strip()
        if explicit:
            return explicit
    _message_id, chat_id, _open_id = _session_fields()
    return _user_text_for_session(session_id=session_id, chat_id=str(chat_id or "").strip())


def _domain_misroute_block(
    tool_name: str,
    user_text: str,
    args: Any = None,
) -> dict[str, str] | None:
    """Keep hotel-data questions on one authoritative backend route per turn."""
    body = str(user_text or "").strip()
    if not body:
        return None
    try:
        from client.domain_intent_router import (
            build_misroute_message,
            is_tool_allowed,
            route_domain_intent,
        )
    except Exception as exc:
        logger.warning("domain intent router import failed: %s", exc)
        return None
    route = route_domain_intent(body)
    if is_tool_allowed(route, tool_name):
        return None
    return {
        "action": "block",
        "message": build_misroute_message(route, tool_name),
    }


def _missing_route_block() -> dict[str, str]:
    return {
        "action": "block",
        "message": "本轮尚未捕获到用户消息，无法建立通用数据路由。请先向运营确认要查的数据对象再调用工具。",
    }


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> dict[str, str] | None:
    name = str(tool_name or "")
    if not name.startswith(_MCP_TOOL_PREFIX):
        return None
    sid = _session_key(session_id=session_id, task_id=task_id)
    if _card_sent_for_current_message(sid):
        requested_mode = (
            str(args.get("response_mode") or "").strip().lower()
            if isinstance(args, dict)
            else ""
        )
        if requested_mode == "detail" or name in _ACTION_CARD_TOOLS:
            return {"action": "block", "message": _CARD_ALREADY_SENT_BLOCK_MESSAGE}
    user_text = _resolve_user_text_for_tool(args=args, session_id=sid)
    saved_route = _ROUTE_BY_SESSION.get(sid)
    if saved_route is not None:
        saved_message_id = str(_ROUTE_MESSAGE_BY_SESSION.get(sid) or "").strip()
        current_message_id = _current_message_id(sid)
        if saved_message_id and current_message_id and saved_message_id != current_message_id:
            _ROUTE_BY_SESSION.pop(sid, None)
            _ROUTE_MESSAGE_BY_SESSION.pop(sid, None)
            return _missing_route_block()
        try:
            from client.domain_intent_router import build_misroute_message, is_tool_allowed

            if not is_tool_allowed(saved_route, name):
                return {
                    "action": "block",
                    "message": build_misroute_message(saved_route, name),
                }
        except Exception as exc:
            logger.warning("saved domain route check failed: %s", exc)
            return _missing_route_block()
    elif not user_text:
        return _missing_route_block()
    else:
        domain_block = _domain_misroute_block(name, user_text, args=args)
        if domain_block is not None:
            return domain_block
    if name in _HEAVY_RUN_TOOLS:
        heavy_block = _enforce_heavy_run_budget(sid, name)
        if heavy_block is not None:
            return heavy_block
    if not isinstance(args, dict):
        return None
    try:
        if _force_chart_when_requested(name, args, user_text):
            logger.info("chart requested in message; forced with_chart=true on trend tool")
    except Exception as exc:
        logger.warning("chart force check failed: %s", exc)
    try:
        _inject_feishu_session(args, session_id=sid)
    except Exception as exc:
        logger.warning("pan-hotel-feishu-session inject failed: %s", exc)
    return None


def _on_transform_tool_result(
    tool_name: str = "",
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    name = str(tool_name or "")
    if not name.startswith(_MCP_TOOL_PREFIX):
        return None
    sid = _session_key(session_id=session_id, task_id=task_id)
    if not sid:
        return None
    plain_result = _to_plain_payload(result)
    if isinstance(plain_result, str):
        try:
            payload = json.loads(plain_result)
        except (TypeError, ValueError):
            return None
    elif isinstance(plain_result, dict):
        payload = plain_result
    else:
        return None
    if not isinstance(payload, dict):
        return None
    # 学习素材：本轮工具成功（含发卡/text 模式）即计入；失败结果不计。
    effective_payload = payload
    nested_raw = payload.get("result")
    if isinstance(nested_raw, dict):
        effective_payload = nested_raw
    _record_tool_used_for_session(sid, name, effective_payload)
    if payload.get("feishu_card_sent") is True:
        _mark_card_sent(sid)
        return None
    if _payload_is_text_reply_mode(payload):
        _mark_text_reply_mode(sid, payload)
        return None
    nested_raw = payload.get("result")
    if isinstance(nested_raw, str):
        try:
            nested = json.loads(nested_raw)
        except (TypeError, ValueError):
            nested = None
    elif isinstance(nested_raw, dict):
        nested = nested_raw
    else:
        nested = None
    if isinstance(nested, dict):
        if nested.get("feishu_card_sent") is True:
            _mark_card_sent(sid)
            return None
        if _payload_is_text_reply_mode(nested):
            _mark_text_reply_mode(sid, nested)
            return None
        return None
    return None


def _on_transform_llm_output(
    response_text: str = "",
    session_id: str = "",
    task_id: str = "",
    platform: str = "",
    **_: Any,
) -> str | None:
    if str(platform or "").strip().lower() not in _FEISHU_PLATFORMS:
        return None
    text = str(response_text or "").strip()
    if not text:
        return None

    sid = _session_key(session_id=session_id, task_id=task_id)
    # 不在此处因 _CARD_SENT_BY_SESSION / _TEXT_REPLY_BY_SESSION 短路：
    # finalize_operator_payload 可能标记 card_sent 但实际未送达；统一交给
    # _send_text_as_card 决定是否发卡，避免漏发。
    chat_id = _resolve_chat_id(session_id=sid)
    if chat_id and not _is_allowed_chat_id(chat_id):
        _clear_session_turn_state(sid)
        return _INVISIBLE_ACK

    if _looks_like_tool_preamble(text):
        return _INVISIBLE_ACK

    # Internal protocol markers (MOBILE_SUMMARY_JSON, raw JSON blobs) are not
    # user-facing reply text — never card-ify or send them. Suppress silently.
    if "MOBILE_SUMMARY_JSON" in text or text.lstrip().startswith("{"):
        _clear_session_turn_state(sid)
        return _INVISIBLE_ACK

    # 带真实附件（MEDIA:<存在文件>）的回复必须放行给网关原生投递：发卡链路
    # 会把回复换成隐形占位符，附件提取在占位文本上跑，MEDIA 标签被吞、图/
    # 文件永远发不出（2026-10-09 静里排名图生产实证）。原生管线会剥掉
    # MEDIA 行、上传附件、再发清洗后的正文。
    if _has_deliverable_media_tag(text):
        _record_learned_route_for_turn(sid)
        _clear_session_turn_state(sid)
        return None

    # Ordinary Hermes replies are rendered as Feishu interactive cards via the
    # backend app bot (operator-card-sends). If the card send succeeds, return
    # _INVISIBLE_ACK so the native text send becomes invisible. If it fails
    # (backend down, no service token), fall back to the sanitized native text.
    chat_id = _resolve_chat_id(session_id=sid)
    if chat_id and _send_text_as_card(chat_id=chat_id, text=text):
        _record_learned_route_for_turn(sid)
        _clear_session_turn_state(sid)
        return _INVISIBLE_ACK
    sanitized = _sanitize_feishu_text_reply(text)
    _record_learned_route_for_turn(sid)
    _clear_session_turn_state(sid)
    return sanitized if sanitized != text else None


def register(ctx) -> None:
    _patch_feishu_adapter_intake()
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
