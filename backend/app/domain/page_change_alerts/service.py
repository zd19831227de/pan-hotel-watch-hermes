"""页面变更检测服务（V7 Step 1）：采集后校验层。

用法（不改采集逻辑，只在采集结果产生后调用）::

    from app.domain.page_change_alerts.service import observe_ctrip_dataset_result

    for result in dataset_results:          # ctrip 数据中心 watch
        await observe_ctrip_dataset_result(result)

    from app.domain.page_change_alerts.service import observe_meituan_read_payload

    await observe_meituan_read_payload("data_center_home", payload)  # 美团 EBK 读取后

级别：
- ``hard_fail``      selector/解析连续 N 次（默认 3，配置文件可调）失效 → 红色告警
- ``partial_fail``   部分字段缺失（partial） → 橙色提醒
- ``layout_changed`` 成功读取但结构指纹变化 → 蓝色通知

同一 (platform, target, problem) 默认 1 小时内最多告警一次。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app.core.settings import get_settings
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
from app.domain.page_change_alerts.cards import (
    ALERT_LEVEL_HARD_FAIL,
    ALERT_LEVEL_LAYOUT_CHANGED,
    ALERT_LEVEL_PARTIAL_FAIL,
    build_page_health_alert_card,
    build_page_health_alert_fallback_text,
)
from app.domain.page_change_alerts.config import load_page_health_config
from app.domain.page_change_alerts import repository

logger = logging.getLogger(__name__)

# 视为 selector/解析完全失效的状态（human_required 是登录/风控问题，不计入）
_HARD_MISS_STATUSES = {"failed", "needs_review", "page_structure_changed"}
_PARTIAL_STATUSES = {"partial"}
_SKIP_STATUSES = {"human_required", "skipped", "config_required"}

_DIGIT_RE = re.compile(r"\d+")


def normalize_structure_terms(terms: list[str] | None) -> list[str]:
    """结构指纹归一化：去空白、数字替换为 #（避免数值/日期漂移）、排序去重。"""
    if not terms:
        return []
    normalized = {_DIGIT_RE.sub("#", str(term).strip()) for term in terms if str(term).strip()}
    return sorted(normalized)


def compute_structure_hash(terms: list[str] | None, *, parser_version: str | None = None) -> str | None:
    normalized = normalize_structure_terms(terms)
    if not normalized:
        return None
    material = "\n".join(normalized)
    if parser_version:
        material = f"{parser_version}\n{material}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class PageChangeAlertService:
    """每个 (platform, target) 维护进程内连续失败计数 + DB 指纹/告警去重。"""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._consecutive_failures: dict[tuple[str, str], int] = {}

    async def observe_read_result(
        self,
        *,
        platform: str,
        target: str,
        status: str,
        parser_version: str | None = None,
        matched_terms: list[str] | None = None,
        summary: str | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any] | None:
        """采集后校验入口。返回已发送的告警信息；无告警/被去重时返回 None。"""
        platform = str(platform or "").strip().lower()
        target = str(target or "").strip()
        status = str(status or "").strip().lower()
        if not platform or not target or status in _SKIP_STATUSES:
            return None

        try:
            return await self._observe(
                platform=platform,
                target=target,
                status=status,
                parser_version=parser_version,
                matched_terms=matched_terms,
                summary=summary,
                webhook_url=webhook_url,
            )
        except Exception:  # noqa: BLE001 - 校验层绝不阻断采集链路
            logger.exception(
                "page change observation failed for %s/%s", platform, target
            )
            return None

    async def _observe(
        self,
        *,
        platform: str,
        target: str,
        status: str,
        parser_version: str | None,
        matched_terms: list[str] | None,
        summary: str | None,
        webhook_url: str | None,
    ) -> dict[str, Any] | None:
        config = load_page_health_config(self._config_path).for_target(platform, target)
        counter_key = (platform, target)

        if status in _HARD_MISS_STATUSES:
            with self._lock:
                self._consecutive_failures[counter_key] = (
                    self._consecutive_failures.get(counter_key, 0) + 1
                )
                failures = self._consecutive_failures[counter_key]
            if failures < config.consecutive_failures_to_alert:
                return None
            alert = await self._try_send_alert(
                platform=platform,
                target=target,
                level=ALERT_LEVEL_HARD_FAIL,
                problem_key="selector_hard_fail",
                summary=summary or f"连续 {failures} 次未命中关键节点（status={status}）",
                consecutive_failures=failures,
                repeat_seconds=config.alert_repeat_seconds,
                webhook_url=webhook_url,
            )
            with self._lock:
                # 告警后重置计数，下一轮 N 次连续失败再触发（仍受 1 小时去重约束）
                self._consecutive_failures[counter_key] = 0
            return alert

        with self._lock:
            self._consecutive_failures[counter_key] = 0

        if status in _PARTIAL_STATUSES:
            return await self._try_send_alert(
                platform=platform,
                target=target,
                level=ALERT_LEVEL_PARTIAL_FAIL,
                problem_key="partial_fail",
                summary=summary or "部分字段缺失，采集降级",
                repeat_seconds=config.alert_repeat_seconds,
                webhook_url=webhook_url,
            )

        # 采集成功：对比结构指纹（无指纹输入时跳过 layout 检测）
        structure_hash = compute_structure_hash(matched_terms, parser_version=parser_version)
        if structure_hash is None:
            return None
        snapshot = await repository.upsert_structure_snapshot(
            platform=platform, target=target, structure_hash=structure_hash
        )
        if not snapshot.get("changed"):
            return None
        return await self._try_send_alert(
            platform=platform,
            target=target,
            level=ALERT_LEVEL_LAYOUT_CHANGED,
            problem_key="layout_changed",
            summary=summary or "页面结构指纹变化，但本轮采集成功",
            repeat_seconds=config.alert_repeat_seconds,
            webhook_url=webhook_url,
        )

    async def _try_send_alert(
        self,
        *,
        platform: str,
        target: str,
        level: str,
        problem_key: str,
        summary: str,
        repeat_seconds: int,
        webhook_url: str | None,
        consecutive_failures: int | None = None,
    ) -> dict[str, Any] | None:
        last_alert_at = await repository.get_last_alert_time(
            platform=platform, target=target, problem_key=problem_key
        )
        if not repository.should_repeat_alert(last_alert_at, repeat_seconds=repeat_seconds):
            return None

        card = build_page_health_alert_card(
            level=level,
            platform=platform,
            target=target,
            summary=summary,
            consecutive_failures=consecutive_failures,
        )
        fallback_text = build_page_health_alert_fallback_text(
            level=level, platform=platform, target=target, summary=summary
        )
        resolved_webhook = str(webhook_url or "").strip() or (
            get_settings().feishu_webhook_url or ""
        )
        alert = {
            "level": level,
            "platform": platform,
            "target": target,
            "problem_key": problem_key,
            "summary": summary,
            "delivery": "skipped",
        }
        if not resolved_webhook:
            logger.warning(
                "page health alert skipped (no webhook configured): %s/%s %s",
                platform,
                target,
                level,
            )
            await repository.record_alert(
                platform=platform,
                target=target,
                problem_key=problem_key,
                level=level,
                summary=summary,
            )
            return alert

        try:
            push = await push_interactive_card_to_feishu_webhook(
                title="页面变更告警",
                card=card,
                webhook_url=resolved_webhook,
                fallback_text=fallback_text,
            )
            alert["delivery"] = getattr(push, "status", "failed")
        except Exception:  # noqa: BLE001 - 推送失败不影响告警记录与返回
            logger.exception(
                "page health alert push failed for %s/%s", platform, target
            )
            alert["delivery"] = "failed"
        await repository.record_alert(
            platform=platform,
            target=target,
            problem_key=problem_key,
            level=level,
            summary=summary,
        )
        return alert


_PAGE_CHANGE_ALERT_SERVICE: PageChangeAlertService | None = None


def get_page_change_alert_service() -> PageChangeAlertService:
    global _PAGE_CHANGE_ALERT_SERVICE
    if _PAGE_CHANGE_ALERT_SERVICE is None:
        _PAGE_CHANGE_ALERT_SERVICE = PageChangeAlertService()
    return _PAGE_CHANGE_ALERT_SERVICE


# ----------------------------------------------------------------------
# 采集结果适配器：把各平台读取结果转成 observe_read_result 入参
# ----------------------------------------------------------------------


async def observe_ctrip_dataset_result(
    result: dict[str, Any],
    *,
    webhook_url: str | None = None,
) -> dict[str, Any] | None:
    """携程数据中心 watch 的 dataset 结果（ctrip_ebooking_watch/service.py 的 dict）。"""
    dataset_id = str(result.get("dataset_id") or "").strip()
    if not dataset_id:
        return None
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    matched_terms = evidence.get("matched_expected_terms")
    if not isinstance(matched_terms, list):
        matched_terms = None
    return await get_page_change_alert_service().observe_read_result(
        platform="ctrip",
        target=f"dataset:{dataset_id}",
        status=str(result.get("status") or ""),
        parser_version=evidence.get("parser_version"),
        matched_terms=matched_terms,
        summary=result.get("summary"),
        webhook_url=webhook_url,
    )


async def observe_meituan_read_payload(
    target: str,
    payload: dict[str, Any],
    *,
    webhook_url: str | None = None,
) -> dict[str, Any] | None:
    """美团 EBK 读取 payload：指纹取 data 顶层标签 key + parser 版本。"""
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    terms = [str(key) for key in data.keys() if isinstance(key, str)]
    return await get_page_change_alert_service().observe_read_result(
        platform="meituan",
        target=str(target or "").strip(),
        status=str(payload.get("status") or ""),
        parser_version=evidence.get("parser_version"),
        matched_terms=terms or None,
        summary=payload.get("summary"),
        webhook_url=webhook_url,
    )


async def observe_fliggy_read_payload(
    target: str,
    payload: dict[str, Any],
    *,
    webhook_url: str | None = None,
) -> dict[str, Any] | None:
    """飞猪 EBK 读取 payload（UI-Fix-F3B）：对齐美团适配器模式。"""
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    terms = [str(key) for key in data.keys() if isinstance(key, str)]
    return await get_page_change_alert_service().observe_read_result(
        platform="fliggy",
        target=str(target or "").strip(),
        status=str(payload.get("status") or ""),
        parser_version=evidence.get("parser_version"),
        matched_terms=terms or None,
        summary=payload.get("summary"),
        webhook_url=webhook_url,
    )
