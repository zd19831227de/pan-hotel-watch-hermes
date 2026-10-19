from __future__ import annotations

from hashlib import sha256
from typing import Any

from app.domain.browser.sessions import normalize_connection_mode
from app.domain.operator_payload import sanitize_persisted_payload

WRITE_NOT_ALLOWED_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}
TRUSTED_PRICE_SOURCE = "room_rate_dom_display_price"
TRUSTED_SOURCE_STATEMENT = "仅使用可信 DOM 房价 room_rate_dom_display_price 生成建议"


def write_not_allowed_reason(platform: str | None = None) -> str:
    """按平台生成 write_not_allowed 拒绝文案；无平台或未知平台保持携程口径。"""
    label = WRITE_NOT_ALLOWED_PLATFORM_LABELS.get(
        str(platform or "").strip().lower(), "携程"
    )
    return (
        f"当前{label} EBK 账号为直连或未确认账号，仅允许生成调价建议，不允许自动化改价"
    )


WRITE_NOT_ALLOWED_REASON = write_not_allowed_reason()


def pricing_platform_for_browser_session(browser_session_id: str | None) -> str:
    """从浏览器会话解析写价平台；无法解析时回退 ctrip（默认携程口径）。"""
    if browser_session_id:
        try:
            from app.domain.browser.sessions import get_browser_session_store

            session = get_browser_session_store().get(str(browser_session_id))
        except Exception:
            session = None
        if session is not None and getattr(session, "platform", None):
            return str(session.platform).strip().lower()
    return "ctrip"


def pricing_platform_from_preview_payload(preview: dict[str, Any] | None) -> str:
    """从 preview payload（_preview_to_payload 产物）解析写价平台。"""
    payload = preview if isinstance(preview, dict) else {}
    candidate = payload.get("preview_candidate")
    if not isinstance(candidate, dict):
        candidate = payload.get("preview_candidate_json")
    candidate = candidate if isinstance(candidate, dict) else {}
    pricing_account = candidate.get("pricing_account")
    pricing_account = pricing_account if isinstance(pricing_account, dict) else {}
    return pricing_platform_for_browser_session(pricing_account.get("browser_session_id"))


def account_policy_allows_write(policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict):
        return False
    mode = normalize_connection_mode(policy.get("connection_mode"))
    return mode == "non_direct" and bool(policy.get("can_auto_write_price"))


def normalized_account_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    policy = policy if isinstance(policy, dict) else {}
    mode = normalize_connection_mode(policy.get("connection_mode"))
    can_write = mode == "non_direct" and bool(policy.get("can_auto_write_price"))
    return {
        "connection_mode": mode,
        "can_auto_write_price": can_write,
        "write_policy_message": str(
            policy.get("write_policy_message")
            or (
                "非直连账号可在 Feishu 审批通过后继续调价流程"
                if can_write
                else WRITE_NOT_ALLOWED_REASON
            )
        ),
    }


def strip_sensitive(value: Any) -> Any:
    return sanitize_persisted_payload(value)


def hashed_identifier(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return sha256(text.encode("utf-8")).hexdigest()[:16]
