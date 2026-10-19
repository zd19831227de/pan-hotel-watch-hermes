"""页面健康告警卡片（V7 Step 1）：hard_fail 红 / partial_fail 橙 / layout_changed 蓝。"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ALERT_LEVEL_HARD_FAIL = "hard_fail"
ALERT_LEVEL_PARTIAL_FAIL = "partial_fail"
ALERT_LEVEL_LAYOUT_CHANGED = "layout_changed"

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_LEVEL_LABELS = {
    ALERT_LEVEL_HARD_FAIL: "页面采集失效（hard_fail）",
    ALERT_LEVEL_PARTIAL_FAIL: "部分字段缺失（partial_fail）",
    ALERT_LEVEL_LAYOUT_CHANGED: "页面结构变化（layout_changed）",
}

_LEVEL_COLORS = {
    ALERT_LEVEL_HARD_FAIL: "red",
    ALERT_LEVEL_PARTIAL_FAIL: "orange",
    ALERT_LEVEL_LAYOUT_CHANGED: "blue",
}

_LEVEL_ACTIONS = {
    ALERT_LEVEL_HARD_FAIL: (
        "关键节点连续未命中，页面可能已改版。请核对平台页面并更新"
        " page_health_config.json / 对应平台 parser 配置后观察下一轮采集。"
    ),
    ALERT_LEVEL_PARTIAL_FAIL: "采集已降级（部分字段缺失）。请关注数据完整性，必要时检查页面是否局部改版。",
    ALERT_LEVEL_LAYOUT_CHANGED: "页面结构指纹变化但本轮采集成功。可能影响后续稳定性，请留意下一轮采集结果。",
}


def build_page_health_alert_card(
    *,
    level: str,
    platform: str,
    target: str,
    summary: str,
    consecutive_failures: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = datetime.now(_SHANGHAI) if now is None else now
    color = _LEVEL_COLORS.get(str(level), "blue")
    label = _LEVEL_LABELS.get(str(level), str(level))
    lines = [
        f"**{label}**",
        f"平台：{_escape(platform)}",
        f"监控目标：{_escape(target)}",
    ]
    if consecutive_failures:
        lines.append(f"连续失败次数：{int(consecutive_failures)}")
    if summary:
        lines.append(f"详情：{_escape(str(summary)[:300])}")
    lines.append(f"建议：{_LEVEL_ACTIONS.get(str(level), '请人工复核采集页面。')}")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {
                "tag": "plain_text",
                "content": f"页面变更告警 · {current:%H:%M}"[:80],
            },
        },
        "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
    }


def build_page_health_alert_fallback_text(
    *,
    level: str,
    platform: str,
    target: str,
    summary: str,
) -> str:
    label = _LEVEL_LABELS.get(str(level), str(level))
    return f"[{label}] {platform} / {target}：{str(summary or '')[:200]}"


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)
