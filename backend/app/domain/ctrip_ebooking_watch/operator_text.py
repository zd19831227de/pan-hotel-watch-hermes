from __future__ import annotations

from typing import Any


def operator_failure_reason(value: Any, *, status: str | None = None) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    lower = text.lower()
    if "expected business fields were not found" in lower:
        return "页面结构变化，缺少可确认业务字段。"
    if "dataset page changed" in lower:
        return "页面结构变化，需要复核。"
    if "browser session did not respond" in lower:
        return "浏览器读取超时，请确认本机 Chrome 已打开且 EBK 已登录。"
    if "no browser session" in lower:
        return "未绑定可用的 EBK 浏览器账号。"
    if "human_required" in lower:
        return "需要人工完成登录或验证。"
    if "playwright" in lower and "subprocess" in lower:
        return "浏览器运行环境需要复核。"
    if status == "page_structure_changed":
        return "页面结构变化，需要复核。"
    if status == "needs_review":
        return "需要复核。"
    return text[:255]
