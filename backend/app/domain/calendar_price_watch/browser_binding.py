from __future__ import annotations

from typing import Any

from app.domain.competitor_prices.browser_binding import (
    CompetitorBrowserBindingResolution,
    resolve_competitor_browser_binding,
)


async def resolve_calendar_browser_binding_for_config(
    *,
    config: dict[str, Any] | None,
    watch_group_key: str,
) -> CompetitorBrowserBindingResolution:
    if not isinstance(config, dict):
        return CompetitorBrowserBindingResolution(
            status="config_required",
            summary="未找到日历价监测配置。",
            next_action="请先打开日历价监测页并保存配置。",
            evidence={"watch_group_key": watch_group_key, "calendar_configured": False},
        )
    browser_session_id = str(config.get("browser_session_id") or "").strip()
    if not browser_session_id:
        return CompetitorBrowserBindingResolution(
            status="config_required",
            summary="请先为日历价监测绑定独立的携程浏览器账号。",
            next_action=(
                "在日历价监测页选择一个用途为「竞品价格」的浏览器账号并保存；"
                "建议与竞品价格读取账号使用不同 Profile，避免互抢浏览器窗口。"
            ),
            evidence={
                "watch_group_key": watch_group_key,
                "browser_session_id_configured": False,
                "browser_binding_source": "calendar_price_watch_config",
            },
        )
    return resolve_competitor_browser_binding(
        browser_session_id=browser_session_id,
        requested_platform_account_id=config.get("platform_account_id"),
        requested_browser_session_id=browser_session_id,
        watch_group_key=watch_group_key,
        source="calendar_price_watch_config",
    )
