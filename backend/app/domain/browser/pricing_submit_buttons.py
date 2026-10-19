"""提交按钮定位（B6 — 完整文案白名单优先，词表走 page_health_config.json）。

实测（B5.1）：飞猪按钮 `增加 / 提交并继续 / 提交并查看 / 取 消`；美团按钮
`卖价(佣金率) / 预览修改结果 / 提交并前往日历页 / 提交并继续修改`，且三个
动作按钮在未选产品/未预览时始终 enabled——页面不提供硬门禁，提交选择必须由
执行器精确控制。

匹配策略（放弃泛包含匹配）：
1. 配置的 preferred 目标按钮（飞猪=提交并继续、美团=提交并继续修改，两者
   提交后停留在当前页，便于逐目标回读）唯一精确命中才可用；缺失或多命中
   都不做兜底猜测。
2. 未配置 preferred 时：完整文案白名单精确唯一命中 → 前缀「提交」唯一命中。
3. 已知非提交词（含去空白归一，如「取 消」→「取消」）永不入选。
4. 无法定位返回 None，调用方必须 `page_structure_changed`，不点击任何按钮。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.page_change_alerts.config import load_page_health_config

_PRICING_TARGET = "pricing:calendar"


@dataclass(frozen=True)
class PricingButtonConfig:
    submit_labels: tuple[str, ...] = ()
    non_submit_labels: tuple[str, ...] = ()
    preferred_label: str | None = None
    preview_label: str | None = None


@dataclass(frozen=True)
class SubmitButtonChoice:
    label: str
    button: dict[str, Any]
    reason: str


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def resolve_pricing_button_config(platform: str) -> PricingButtonConfig:
    """按平台读取 page_health_config.json 的 {platform}:pricing:calendar 词表。"""
    target = load_page_health_config().for_target(platform, _PRICING_TARGET)
    return PricingButtonConfig(
        submit_labels=tuple(target.submit_button_labels or ()),
        non_submit_labels=tuple(target.non_submit_button_labels or ()),
        preferred_label=target.preferred_submit_label or None,
        preview_label=target.preview_button_label or None,
    )


def choose_submit_button(
    buttons: list[dict[str, Any]],
    *,
    submit_labels: tuple[str, ...] | list[str],
    non_submit_labels: tuple[str, ...] | list[str],
    preferred: str | None,
) -> SubmitButtonChoice | None:
    """从页面按钮列表选择唯一提交按钮；无法唯一定位时返回 None。"""
    normalized_submit = {_normalize_label(label) for label in submit_labels}
    normalized_non_submit = {_normalize_label(label) for label in non_submit_labels}
    candidates = [
        button
        for button in buttons
        if _normalize_label(str(button.get("text") or ""))
        not in normalized_non_submit
    ]

    if preferred:
        preferred_norm = _normalize_label(preferred)
        hits = [
            button
            for button in candidates
            if _normalize_label(str(button.get("text") or "")) == preferred_norm
        ]
        if len(hits) == 1:
            return SubmitButtonChoice(
                label=preferred,
                button=hits[0],
                reason="preferred-exact",
            )
        # 目标按钮缺失或重复：不退回其它提交按钮（防止点到跳页变体破坏回读）。
        return None

    exact_hits = [
        button
        for button in candidates
        if _normalize_label(str(button.get("text") or "")) in normalized_submit
    ]
    if len(exact_hits) == 1:
        return SubmitButtonChoice(
            label=str(exact_hits[0].get("text") or ""),
            button=exact_hits[0],
            reason="label-exact",
        )
    if len(exact_hits) > 1:
        return None

    prefix_hits = [
        button
        for button in candidates
        if _normalize_label(str(button.get("text") or "")).startswith("提交")
    ]
    if len(prefix_hits) == 1:
        return SubmitButtonChoice(
            label=str(prefix_hits[0].get("text") or ""),
            button=prefix_hits[0],
            reason="prefix-unique",
        )
    return None
