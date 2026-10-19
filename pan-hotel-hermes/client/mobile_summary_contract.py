from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

MOBILE_CONTRACT_VERSION = "mobile_summary.v1"
MAX_FINDINGS = 3
MAX_ACTIONS = 2
MAX_CHINESE_CHARS = 350

_SENSITIVE_MARKERS = (
    "webhook",
    "token",
    "secret",
    "cookie",
    "password",
    "verification",
    "profile",
    "screenshot",
    "appdata",
    "open-apis/bot",
    "chrome.exe",
    "run_id",
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DIGIT_RE = re.compile(r"\d")


def count_chinese_chars(text: str) -> int:
    return len(_CJK_RE.findall(str(text or "")))


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "敏感信息已隐藏"
    if "\\" in text and len(text) > 80:
        return "敏感信息已隐藏"
    return text


def validate_mobile_summary(
    summary: dict[str, Any],
    *,
    evidence_index: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    findings = summary.get("findings") if isinstance(summary.get("findings"), list) else []
    if len(findings) > MAX_FINDINGS:
        errors.append("findings_exceed_max")
    for item in findings:
        if not isinstance(item, dict):
            errors.append("finding_not_object")
            continue
        refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
        refs = [str(r) for r in refs if str(r).strip()]
        text = str(item.get("text") or "")
        if _DIGIT_RE.search(text) and not refs:
            errors.append("numeric_finding_missing_evidence_ref")
        if not refs:
            errors.append("finding_missing_evidence_ref")
            continue
        for ref in refs:
            if ref not in evidence_index:
                errors.append(f"unknown_evidence_ref:{ref}")
    actions = summary.get("actions") if isinstance(summary.get("actions"), list) else []
    if len(actions) > MAX_ACTIONS:
        errors.append("actions_exceed_max")
    return (not errors), errors


def _trim_to_budget(summary: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(summary)

    def visible_len(s: dict[str, Any]) -> int:
        parts = [str(s.get("conclusion") or "")]
        parts.extend(str(f.get("text") or "") for f in s.get("findings") or [] if isinstance(f, dict))
        parts.extend(str(a.get("text") or "") for a in s.get("actions") or [] if isinstance(a, dict))
        parts.append(str(s.get("data_time") or ""))
        return count_chinese_chars("".join(parts))

    while visible_len(result) > MAX_CHINESE_CHARS and result.get("findings"):
        result["findings"] = list(result["findings"])[:-1]
    while visible_len(result) > MAX_CHINESE_CHARS and result.get("actions"):
        result["actions"] = list(result["actions"])[:-1]
    conclusion = str(result.get("conclusion") or "")
    while count_chinese_chars(conclusion) > 80 and visible_len({**result, "conclusion": conclusion}) > MAX_CHINESE_CHARS:
        conclusion = conclusion[:-1]
    result["conclusion"] = conclusion
    return result


def normalize_mobile_summary(
    payload: dict[str, Any],
    *,
    evidence_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw = deepcopy(payload) if isinstance(payload, dict) else {}
    findings_in = raw.get("findings") if isinstance(raw.get("findings"), list) else []
    actions_in = raw.get("actions") if isinstance(raw.get("actions"), list) else []
    findings: list[dict[str, Any]] = []
    for item in findings_in[:MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        refs = [str(r).strip() for r in (item.get("evidence_refs") or []) if str(r).strip()]
        text = _safe_text(item.get("text"))
        if not text:
            continue
        findings.append({"text": text, "evidence_refs": refs})
    actions: list[dict[str, Any]] = []
    for item in actions_in[:MAX_ACTIONS]:
        if isinstance(item, dict):
            text = _safe_text(item.get("text") or item.get("label"))
        else:
            text = _safe_text(item)
        if text:
            actions.append({"text": text})
    result = {
        "contract_version": MOBILE_CONTRACT_VERSION,
        "status": str(raw.get("status") or "success"),
        "conclusion": _safe_text(raw.get("conclusion")) or "数据不足，暂无法给出强结论。",
        "findings": findings,
        "actions": actions,
        "data_time": _safe_text(raw.get("data_time")) or "未提供明确读取时间",
        "freshness": str(raw.get("freshness") or "unknown"),
        "missing_data": [_safe_text(x) for x in (raw.get("missing_data") or []) if _safe_text(x)][:4],
        "risk_flags": [_safe_text(x) for x in (raw.get("risk_flags") or []) if _safe_text(x)][:6],
        "evidence_refs": sorted(
            {
                str(r)
                for f in findings
                for r in f.get("evidence_refs", [])
                if str(r) in evidence_index
            }
        ),
        "detail_intents": [str(x) for x in (raw.get("detail_intents") or []) if str(x).strip()][:4],
        "safety_boundary": str(raw.get("safety_boundary") or "read_only"),
        "response_mode": str(raw.get("response_mode") or "summary"),
    }
    return _trim_to_budget(result)


def build_rule_mobile_summary(
    *,
    evidence_items: list[dict[str, Any]],
    status: str = "success",
    missing_data: list[str] | None = None,
    risk_flags: list[str] | None = None,
    safety_boundary: str = "read_only",
    detail_intents: list[str] | None = None,
    response_mode: str = "summary",
) -> dict[str, Any]:
    index = {
        str(item.get("evidence_ref")): item
        for item in evidence_items
        if isinstance(item, dict) and str(item.get("evidence_ref") or "").strip()
    }
    ranked = sorted(
        evidence_items,
        key=lambda item: int(item.get("priority") or 100),
    )[:MAX_FINDINGS]
    findings = []
    for item in ranked:
        ref = str(item.get("evidence_ref") or "")
        label = _safe_text(item.get("label") or "指标")
        value = _safe_text(item.get("value") or "")
        unit = _safe_text(item.get("unit") or "")
        text = f"{label} {value}{unit}".strip()
        if text and ref:
            findings.append({"text": text, "evidence_refs": [ref]})
    if missing_data:
        conclusion = "数据不足，先补齐关键读取再下强结论。"
    elif risk_flags:
        conclusion = "今日需关注，请先处理下方异常。"
    elif findings:
        conclusion = "今日经营总体正常，详见关键发现。"
    else:
        conclusion = "暂无足够证据生成经营摘要。"
    actions: list[dict[str, Any]] = []
    if missing_data:
        actions.append({"text": "先刷新最关键的后台数据"})
    elif risk_flags:
        actions.append({"text": "核对异常模块并决定是否现读"})
    else:
        actions.append({"text": "如需完整表格请说「查看明细」"})
    if len(actions) < MAX_ACTIONS and detail_intents:
        actions.append({"text": f"可继续追问：{detail_intents[0]}"})
    data_time = next(
        (_safe_text(i.get("captured_at")) for i in ranked if i.get("captured_at")),
        "未提供明确读取时间",
    )
    freshness = next((str(i.get("freshness") or "unknown") for i in ranked), "unknown")
    return normalize_mobile_summary(
        {
            "status": status,
            "conclusion": conclusion,
            "findings": findings,
            "actions": actions[:MAX_ACTIONS],
            "data_time": data_time,
            "freshness": freshness,
            "missing_data": missing_data or [],
            "risk_flags": risk_flags or [],
            "detail_intents": detail_intents or ["查看明细"],
            "safety_boundary": safety_boundary,
            "response_mode": response_mode,
        },
        evidence_index=index,
    )
