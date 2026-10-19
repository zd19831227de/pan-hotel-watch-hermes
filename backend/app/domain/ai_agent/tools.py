from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.ai_agent.registry import (
    AgentToolCallContext,
    AgentToolResult,
    EbkDatasetReadToolArgs,
    ManualPricingApprovalToolArgs,
    WatchControlToolArgs,
    WatchGroupToolArgs,
)
from app.domain.browser.ctrip_ebooking_time_dimensions import (
    project_dataset_data_for_time_dimension,
)
from app.domain.calendar_price_watch.repository import (
    get_calendar_price_watch_config,
    get_latest_calendar_price_matrix,
)
from app.domain.calendar_price_watch.scheduler import CalendarPriceWatchScheduler
from app.domain.calendar_price_watch.scheduler_status import get_calendar_price_scheduler_status
from app.domain.browser.sessions import get_pricing_browser_session_for_feishu_chat_id
from app.domain.competitor_prices.repository import (
    get_latest_competitor_price_snapshot,
    update_competitor_watch_group,
)
from app.domain.competitor_prices.types import CompetitorWatchGroupPatch
from app.domain.ctrip_ebooking_watch.repository import (
    DATASET_LABELS,
    DataCenterWatchGroupPatch,
    DataCenterWatchDatasetRuntime,
    DataCenterWatchGroupRuntime,
    get_data_center_watch_group_runtime,
    get_latest_data_center_watch_run,
    update_data_center_watch_group,
)
from app.domain.ctrip_ebooking_watch.scheduler import CtripEbookingDataCenterWatchScheduler
from app.domain.ctrip_ebooking_watch.service import DataCenterWatchService
from app.domain.notifications.feishu_client import FeishuBotClient
from app.domain.ops.runtime_health import get_runtime_health
from app.domain.price_change_alerts.service import get_latest_price_change_alert
from app.domain.pricing_approvals.cards import (
    build_pricing_approval_card,
    build_pricing_approval_fallback_text,
)
from app.domain.pricing_suggestions.preview import create_manual_pricing_preview

DEFAULT_COMPETITOR_WATCH_GROUP_KEY = "default_competitor_watch"


async def _resolve_authorized_watch_group_key(
    *,
    context: AgentToolCallContext,
    explicit_key: str | None,
    watch_kind: str,
    tool_name: str,
) -> tuple[str | None, AgentToolResult | None]:
    chat_id = str(context.chat_id or "").strip()
    if not chat_id:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary="当前工具缺少飞书群上下文，无法确定允许访问的监控组。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请在已绑定运营问答上下文群的飞书会话中重试。",
        )

    try:
        from app.domain.ops.feishu_chat_context import build_feishu_chat_context

        chat_context = await build_feishu_chat_context(chat_id)
    except Exception:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary="当前飞书群的监控组绑定无法解析，未执行数据访问或状态变更。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请检查该群的运营问答上下文群与浏览器账号绑定。",
        )

    groups_field = "ebk_groups" if watch_kind == "ebk" else "competitor_groups"
    resolution_field = (
        "ebk_watch_group_key" if watch_kind == "ebk" else "competitor_watch_group_key"
    )
    authorized_keys = {
        str(item.get("group_key") or "").strip()
        for item in chat_context.get(groups_field, [])
        if isinstance(item, dict) and str(item.get("group_key") or "").strip()
    }
    requested_key = str(explicit_key or "").strip()
    if requested_key:
        try:
            from app.domain.config.service import resolve_feishu_role_for_chat

            role = await resolve_feishu_role_for_chat(chat_id)
        except Exception:
            role = "operator"
        if role == "supervisor":
            return requested_key, None
        if requested_key in authorized_keys:
            return requested_key, None
        return None, _watch_group_authorization_error(
            status="watch_group_not_allowed",
            summary="指定的监控组不属于当前飞书群，已拒绝访问。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请使用当前群已绑定的监控组，或先在控制台完成群绑定。",
        )

    resolution = (
        chat_context.get("resolution")
        if isinstance(chat_context.get("resolution"), dict)
        else {}
    )
    resolved_key = str(resolution.get(resolution_field) or "").strip()
    if resolution.get("status") == "resolved" and resolved_key in authorized_keys:
        return resolved_key, None
    if len(authorized_keys) == 1:
        return next(iter(authorized_keys)), None
    return None, _watch_group_authorization_error(
        status="config_required",
        summary="当前飞书群无法唯一解析到可用监控组，未使用系统默认组。",
        context=context,
        tool_name=tool_name,
        watch_kind=watch_kind,
        next_action="请补齐群与监控组绑定；一群多店时请明确选择门店。",
    )



_PLATFORM_EBK_REPOS = {
    "meituan": "app.domain.meituan_ebooking_watch.repository",
    "fliggy": "app.domain.fliggy_ebooking_watch.repository",
}


async def _resolve_authorized_platform_watch_group_key(
    *,
    context: AgentToolCallContext,
    platform: str,
    explicit_key: str | None,
    tool_name: str,
) -> tuple[str | None, AgentToolResult | None]:
    """美团/飞猪定时监控的群授权：按当前群绑定的携程组锚点解析。

    平台排程不直接绑群；一个平台监控组是否属于当前群，取决于它的
    ``ctrip_watch_group_key`` 是否在当前群绑定的携程 EBK 监控组集合内。
    """

    import importlib

    normalized_platform = str(platform or "").strip().lower()
    repo_module_name = _PLATFORM_EBK_REPOS.get(normalized_platform)
    if repo_module_name is None:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary=f"不支持的平台：{platform}",
            context=context,
            tool_name=tool_name,
            watch_kind=str(platform),
            next_action="仅支持 meituan / fliggy。",
        )
    watch_kind = f"{normalized_platform}_ebk"
    repo = importlib.import_module(repo_module_name)

    chat_id = str(context.chat_id or "").strip()
    if not chat_id:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary="当前工具缺少飞书群上下文，无法确定允许访问的监控组。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请在已绑定运营问答上下文群的飞书会话中重试。",
        )
    try:
        from app.domain.ops.feishu_chat_context import build_feishu_chat_context

        chat_context = await build_feishu_chat_context(chat_id)
    except Exception:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary="当前飞书群的监控组绑定无法解析，未执行数据访问或状态变更。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请检查该群的运营问答上下文群与浏览器账号绑定。",
        )
    authorized_ctrip_keys = {
        str(item.get("group_key") or "").strip()
        for item in chat_context.get("ebk_groups", [])
        if isinstance(item, dict) and str(item.get("group_key") or "").strip()
    }
    requested_key = str(explicit_key or "").strip()
    if requested_key:
        runtime = await getattr(
            repo, f"get_{normalized_platform}_watch_group_runtime"
        )(watch_group_key=requested_key)
        if runtime is None:
            return None, _watch_group_authorization_error(
                status="config_required",
                summary=f"未找到 {normalized_platform} 定时监控组：{requested_key}",
                context=context,
                tool_name=tool_name,
                watch_kind=watch_kind,
                next_action="请确认平台定时监控组是否存在。",
            )
        anchor = str(getattr(runtime, "ctrip_watch_group_key", "") or "").strip()
        if anchor and anchor in authorized_ctrip_keys:
            return requested_key, None
        try:
            from app.domain.config.service import resolve_feishu_role_for_chat

            role = await resolve_feishu_role_for_chat(chat_id)
        except Exception:
            role = "operator"
        if role == "supervisor":
            return requested_key, None
        return None, _watch_group_authorization_error(
            status="watch_group_not_allowed",
            summary="指定的平台定时监控组未挂在当前飞书群绑定的携程监控组下，已拒绝访问。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action="请先在控制台把该平台定时监控挂到当前群的携程监控组下。",
        )

    candidates: list[str] = []
    for ctrip_key in sorted(authorized_ctrip_keys):
        runtime = await getattr(
            repo, f"get_{normalized_platform}_watch_group_runtime_by_ctrip_anchor"
        )(ctrip_watch_group_key=ctrip_key)
        if runtime is not None:
            candidates.append(runtime.group_key)
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, _watch_group_authorization_error(
            status="config_required",
            summary=f"当前飞书群绑定的携程监控组下没有 {normalized_platform} 定时监控。",
            context=context,
            tool_name=tool_name,
            watch_kind=watch_kind,
            next_action=f"请先在控制台为该群对应门店配置 {normalized_platform} 定时监控。",
        )
    return None, _watch_group_authorization_error(
        status="config_required",
        summary=f"当前飞书群下有多个 {normalized_platform} 定时监控，无法唯一解析。",
        context=context,
        tool_name=tool_name,
        watch_kind=watch_kind,
        next_action="一群多店时请用 watch_group_key 明确指定平台监控组。",
    )

def _watch_group_authorization_error(
    *,
    status: str,
    summary: str,
    context: AgentToolCallContext,
    tool_name: str,
    watch_kind: str,
    next_action: str,
) -> AgentToolResult:
    return AgentToolResult(
        status=status,
        summary=summary,
        data={"watch_kind": watch_kind},
        evidence={
            "tool_name": tool_name,
            "event_id": context.event_id,
            "handler_invoked": False,
            "repository_invoked": False,
            "scheduler_invoked": False,
            "browser_read_invoked": False,
            "webhook_called": False,
            "pricing_execution_invoked": False,
            "ota_write_invoked": False,
        },
        next_action=next_action,
    )


async def get_ebk_latest_result_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    if str(arguments.platform or "ctrip") != "ctrip":
        return await _get_platform_ebk_latest_result_tool(arguments, context)
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="ebk",
        tool_name="get_ebk_latest_result",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    snapshot = await get_latest_data_center_watch_run(watch_group_key=watch_group_key)
    if snapshot is None:
        return AgentToolResult(
            status="data_required",
            summary="EBK 还没有可用于分析的读取记录。",
            data={"watch_group_key": watch_group_key},
            evidence=_read_only_evidence(
                tool_name="get_ebk_latest_result",
                event_id=context.event_id,
            ),
            next_action="先执行一次 EBK 数据中心读取，或等待定时盯盘产生结果。",
        )

    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    dataset_results = [
        _compact_ebk_dataset_result(item)
        for item in snapshot.get("dataset_results", [])
        if isinstance(item, dict)
    ]
    return AgentToolResult(
        status=str(run.get("status") or "success"),
        summary=_latest_ebk_summary(run=run, dataset_results=dataset_results),
        data={
            "watch_group_key": watch_group_key,
            "run": _compact_ebk_run(run),
            "dataset_results": dataset_results,
        },
        evidence=_read_only_evidence(
            tool_name="get_ebk_latest_result",
            event_id=context.event_id,
            captured_at=run.get("captured_at"),
        ),
    )


async def get_competitor_latest_prices_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="competitor",
        tool_name="get_competitor_latest_prices",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    snapshot = await get_latest_competitor_price_snapshot(watch_group_key=watch_group_key)
    if snapshot is None:
        return AgentToolResult(
            status="data_required",
            summary="竞品价格还没有可用于分析的读取记录。",
            data={"watch_group_key": watch_group_key},
            evidence=_read_only_evidence(
                tool_name="get_competitor_latest_prices",
                event_id=context.event_id,
            ),
            next_action="先执行一次竞品价格读取，或等待定时盯盘产生结果。",
        )

    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    hotels = [
        _compact_competitor_hotel(item)
        for item in snapshot.get("hotels", [])
        if isinstance(item, dict)
    ]
    trusted_rate_count = sum(
        1
        for hotel in hotels
        for snapshot_item in hotel["snapshots"]
        for rate in snapshot_item["rates"]
        if rate.get("is_trusted")
    )
    return AgentToolResult(
        status=str(run.get("status") or "success"),
        summary=f"竞品最新价格读取状态：{run.get('summary') or run.get('status') or '已读取'}。",
        data={
            "watch_group_key": watch_group_key,
            "trusted_source": snapshot.get("trusted_source"),
            "run": _compact_competitor_run(run),
            "hotel_count": len(hotels),
            "trusted_rate_count": trusted_rate_count,
            "hotels": hotels,
        },
        evidence=_read_only_evidence(
            tool_name="get_competitor_latest_prices",
            event_id=context.event_id,
            captured_at=run.get("captured_at"),
        ),
    )


async def get_calendar_price_latest_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="competitor",
        tool_name="get_calendar_price_latest",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    matrix = await get_latest_calendar_price_matrix(watch_group_key=watch_group_key)
    if matrix is None:
        return AgentToolResult(
            status="data_required",
            summary="日历价还没有可用于分析的读取记录。",
            data={"watch_group_key": watch_group_key},
            evidence=_read_only_evidence(
                tool_name="get_calendar_price_latest",
                event_id=context.event_id,
            ),
            next_action="先执行一次日历价读取，或等待定时监测产生结果。",
        )

    compact = _compact_calendar_matrix(matrix)
    return AgentToolResult(
        status=str(compact.get("status") or "success"),
        summary=_latest_calendar_price_summary(compact),
        data=compact,
        evidence=_read_only_evidence(
            tool_name="get_calendar_price_latest",
            event_id=context.event_id,
            captured_at=compact.get("latest_captured_at"),
        ),
    )


async def get_calendar_price_watch_status_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="competitor",
        tool_name="get_calendar_price_watch_status",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    config = await get_calendar_price_watch_config(watch_group_key=watch_group_key)
    matrix = await get_latest_calendar_price_matrix(watch_group_key=watch_group_key)
    scheduler = get_calendar_price_scheduler_status()
    if config is None:
        return AgentToolResult(
            status="config_required",
            summary="未找到日历价对应的竞品监控组。",
            data={"watch_group_key": watch_group_key},
            evidence=_calendar_price_status_evidence(
                event_id=context.event_id,
                captured_at=None,
            ),
            next_action="请先在竞品监控组中完成日历价配置。",
        )

    run = matrix.get("run") if isinstance(matrix, dict) and isinstance(matrix.get("run"), dict) else {}
    latest_captured_at = run.get("captured_at") or config.get("last_polled_at")
    interval = config.get("schedule_interval_minutes")
    summary = (
        f"日历价自动轮询{'已开启' if config.get('auto_poll_enabled') else '未开启'}，"
        f"间隔 {interval or '未配置'} 分钟，"
        f"上次读取 {latest_captured_at or '暂无记录'}。"
    )
    return AgentToolResult(
        status=str(run.get("status") or (matrix or {}).get("status") or "success"),
        summary=summary,
        data={
            "watch_group_key": watch_group_key,
            "auto_poll_enabled": bool(config.get("auto_poll_enabled")),
            "schedule_interval_minutes": interval,
            "future_days": config.get("future_days"),
            "latest_run_status": run.get("status") or (matrix or {}).get("status"),
            "latest_captured_at": latest_captured_at,
            "scheduler": scheduler if isinstance(scheduler, dict) else {},
        },
        evidence=_calendar_price_status_evidence(
            event_id=context.event_id,
            captured_at=latest_captured_at,
        ),
    )


async def run_calendar_price_watch_once_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="competitor",
        tool_name="run_calendar_price_watch_once",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    result = await CalendarPriceWatchScheduler(tick_seconds=1).run_watch_group_now(
        watch_group_key,
        push_to_feishu=False,
        actor_id=context.open_id or "feishu-ai-agent",
        source="feishu-ai-agent",
    )
    status = str(result.get("status") or "unknown")
    return AgentToolResult(
        status=status,
        summary=str(result.get("summary") or _run_calendar_price_summary(status)),
        data={
            "watch_group_key": result.get("watch_group_key") or watch_group_key,
            "future_days": result.get("future_days"),
            "alert_count": result.get("alert_count"),
            "alerts": _limited_dicts(result.get("alerts"), limit=10),
            "feishu_push": _compact_push_result(result.get("feishu_push")),
        },
        evidence={
            "tool_name": "run_calendar_price_watch_once",
            "event_id": context.event_id,
            "handler_invoked": True,
            "scheduler_invoked": True,
            "browser_read_invoked": True,
            "webhook_called": False,
            "push_to_feishu": False,
            "pricing_execution_invoked": False,
            "ota_write_invoked": False,
        },
        next_action=_optional_text(result.get("next_action")),
    )



async def _run_platform_ebk_watch_once_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    """美团/飞猪：立即执行一次平台定时监控（推送走锚定携程组机器人）。"""

    platform = str(arguments.platform or "ctrip").strip().lower()
    watch_group_key, blocked = await _resolve_authorized_platform_watch_group_key(
        context=context,
        platform=platform,
        explicit_key=arguments.watch_group_key,
        tool_name="run_ebk_watch_once",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    if platform == "meituan":
        from app.domain.meituan_ebooking_watch.scheduler import (
            MeituanEbookingWatchScheduler,
        )

        result = await MeituanEbookingWatchScheduler(tick_seconds=1).run_watch_group_now(
            watch_group_key=watch_group_key,
        )
    else:
        from app.domain.fliggy_ebooking_watch.scheduler import (
            FliggyEbookingWatchScheduler,
        )

        result = await FliggyEbookingWatchScheduler(tick_seconds=1).run_watch_group_now(
            watch_group_key=watch_group_key,
        )
    status = str(result.get("status") or "unknown")
    return AgentToolResult(
        status=status,
        summary=str(result.get("summary") or _run_ebk_watch_summary(status)),
        data={
            "platform": platform,
            "watch_group_key": result.get("watch_group_key") or watch_group_key,
            "snapshot_id": result.get("snapshot_id"),
            "dataset_count": result.get("dataset_count"),
            "push": _compact_push_result(result.get("feishu_push")),
        },
        evidence={
            "tool_name": "run_ebk_watch_once",
            "event_id": context.event_id,
            "handler_invoked": True,
            "scheduler_invoked": True,
            "browser_read_invoked": True,
            "webhook_may_be_called_by_scheduler": True,
            "pricing_execution_invoked": False,
            "ota_write_invoked": False,
        },
        next_action=_optional_text(result.get("next_action")),
    )


async def _get_platform_ebk_latest_result_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    """美团/飞猪：查询平台定时监控的最新一次快照（只读）。"""

    import importlib

    from app.domain.ebk_snapshots.repository import get_latest_ebk_snapshot

    platform = str(arguments.platform or "ctrip").strip().lower()
    watch_group_key, blocked = await _resolve_authorized_platform_watch_group_key(
        context=context,
        platform=platform,
        explicit_key=arguments.watch_group_key,
        tool_name="get_ebk_latest_result",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    repo = importlib.import_module(
        "app.domain.meituan_ebooking_watch.repository"
        if platform == "meituan"
        else "app.domain.fliggy_ebooking_watch.repository"
    )
    runtime = await getattr(
        repo, f"get_{platform}_watch_group_runtime"
    )(watch_group_key=watch_group_key)
    if runtime is None:
        return AgentToolResult(
            status="not_found",
            summary=f"未找到 {platform} 定时监控组。",
            data={"platform": platform, "watch_group_key": watch_group_key},
            evidence=_read_only_evidence(
                tool_name="get_ebk_latest_result",
                event_id=context.event_id,
            ),
            next_action="请确认平台定时监控组是否存在。",
        )
    snapshot = await get_latest_ebk_snapshot(
        platform=platform,
        platform_account_id=runtime.platform_account_id,
    )
    if snapshot is None:
        return AgentToolResult(
            status="data_required",
            summary=f"{platform} EBK 还没有可用于分析的读取记录。",
            data={"platform": platform, "watch_group_key": watch_group_key},
            evidence=_read_only_evidence(
                tool_name="get_ebk_latest_result",
                event_id=context.event_id,
            ),
            next_action=f"先执行一次 {platform} EBK 定时监控读取，或等待定时任务产生结果。",
        )
    payload = snapshot.get("payload_json") if isinstance(snapshot.get("payload_json"), dict) else {}
    return AgentToolResult(
        status=str(snapshot.get("status") or "success"),
        summary=str(payload.get("summary") or snapshot.get("status") or "已读取"),
        data={
            "platform": platform,
            "watch_group_key": watch_group_key,
            "snapshot_id": snapshot.get("id"),
            "dataset_count": snapshot.get("dataset_count"),
            "created_at": snapshot.get("created_at"),
        },
        evidence=_read_only_evidence(
            tool_name="get_ebk_latest_result",
            event_id=context.event_id,
        ),
        next_action=None,
    )


async def _set_platform_watch_enabled(
    *,
    arguments: "WatchControlToolArgs",
    context: AgentToolCallContext,
    enabled: bool,
    auto_poll_enabled: bool,
    action_label: str,
    tool_name: str,
) -> AgentToolResult:
    """美团/飞猪：暂停/恢复平台定时监控（授权按携程组锚点解析）。"""

    import importlib

    platform = "meituan" if arguments.watch_kind == "meituan_ebk" else "fliggy"
    watch_group_key, blocked = await _resolve_authorized_platform_watch_group_key(
        context=context,
        platform=platform,
        explicit_key=arguments.watch_group_key,
        tool_name=tool_name,
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    repo = importlib.import_module(
        "app.domain.meituan_ebooking_watch.repository"
        if platform == "meituan"
        else "app.domain.fliggy_ebooking_watch.repository"
    )
    patch_model = getattr(repo, f"{'Meituan' if platform == 'meituan' else 'Fliggy'}WatchGroupPatch")
    watch_group = await getattr(repo, f"update_{platform}_watch_group")(
        watch_group_key=watch_group_key,
        patch=patch_model(enabled=enabled, auto_poll_enabled=auto_poll_enabled),
    )
    if watch_group is None:
        return AgentToolResult(
            status="not_found",
            summary=f"未找到要{action_label}的盯盘监控组。",
            data={"watch_kind": arguments.watch_kind, "watch_group_key": watch_group_key},
            evidence=_watch_control_evidence(
                tool_name=tool_name,
                event_id=context.event_id,
            ),
            next_action="请确认监控组是否存在。",
        )
    return AgentToolResult(
        status="success",
        summary=(
            f"已{action_label} {watch_group.get('name') or watch_group_key}（{platform}）。"
        ),
        data={
            "watch_kind": arguments.watch_kind,
            "platform": platform,
            "watch_group_key": watch_group_key,
            "enabled": watch_group.get("enabled"),
            "auto_poll_enabled": watch_group.get("auto_poll_enabled"),
        },
        evidence=_watch_control_evidence(
            tool_name=tool_name,
            event_id=context.event_id,
        ),
        next_action=None,
    )


async def run_ebk_watch_once_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    if str(arguments.platform or "ctrip") != "ctrip":
        return await _run_platform_ebk_watch_once_tool(arguments, context)
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="ebk",
        tool_name="run_ebk_watch_once",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    result = await CtripEbookingDataCenterWatchScheduler(tick_seconds=1).run_watch_group_now(
        watch_group_key=watch_group_key,
    )
    status = str(result.get("status") or "unknown")
    return AgentToolResult(
        status=status,
        summary=str(result.get("summary") or _run_ebk_watch_summary(status)),
        data={
            "watch_group_key": result.get("watch_group_key") or watch_group_key,
            "persistence": result.get("persistence") if isinstance(result.get("persistence"), dict) else {},
            "push": _compact_push_result(result.get("push") or result.get("feishu_push")),
            "dataset_results": [
                _compact_ebk_dataset_result(item)
                for item in result.get("dataset_results", [])
                if isinstance(item, dict)
            ][:20],
        },
        evidence={
            "tool_name": "run_ebk_watch_once",
            "event_id": context.event_id,
            "handler_invoked": True,
            "scheduler_invoked": True,
            "browser_read_invoked": True,
            "webhook_may_be_called_by_scheduler": True,
            "pricing_execution_invoked": False,
            "ota_write_invoked": False,
        },
        next_action=_optional_text(result.get("next_action")),
    )


async def read_ebk_datasets_tool(
    arguments: EbkDatasetReadToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="ebk",
        tool_name="read_ebk_datasets",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    dataset_ids = _resolve_ebk_dataset_ids_for_agent_read(
        dataset_ids=arguments.dataset_ids,
        metric_keys=arguments.metric_keys,
    )
    group = await get_data_center_watch_group_runtime(watch_group_key=watch_group_key)
    if group is None:
        return AgentToolResult(
            status="not_found",
            summary="未找到 EBK 数据中心盯盘组，无法读取数据。",
            data={"watch_group_key": watch_group_key, "requested_dataset_ids": dataset_ids},
            evidence=_ebk_dataset_read_evidence(
                event_id=context.event_id,
                browser_read_invoked=False,
            ),
            next_action="请先在“账号与浏览器 / EBK 盯盘”中完成数据中心盯盘配置。",
        )
    if not group.enabled:
        return AgentToolResult(
            status="skipped",
            summary="EBK 数据中心盯盘组已停用，未触发读取。",
            data={"watch_group_key": watch_group_key, "requested_dataset_ids": dataset_ids},
            evidence=_ebk_dataset_read_evidence(
                event_id=context.event_id,
                browser_read_invoked=False,
            ),
            next_action="请先恢复 EBK 盯盘，再重新发起读取。",
        )

    selected_datasets = _select_ebk_runtime_datasets(group, dataset_ids)
    if not selected_datasets:
        return AgentToolResult(
            status="config_required",
            summary="当前 EBK 盯盘组没有可读取的目标数据集。",
            data={"watch_group_key": watch_group_key, "requested_dataset_ids": dataset_ids},
            evidence=_ebk_dataset_read_evidence(
                event_id=context.event_id,
                browser_read_invoked=False,
            ),
            next_action="请在 EBK 盯盘配置中启用对应数据集后重试。",
        )

    read_group = DataCenterWatchGroupRuntime(
        group_key=group.group_key,
        name=group.name,
        brief_title=group.brief_title,
        platform_account_id=group.platform_account_id,
        browser_session_id=group.browser_session_id,
        feishu_webhook_url=group.feishu_webhook_url,
        feishu_chat_id=group.feishu_chat_id,
        timezone=group.timezone,
        retention_days=group.retention_days,
        query_interval_minutes=group.query_interval_minutes,
        auto_poll_enabled=group.auto_poll_enabled,
        enabled=group.enabled,
        last_polled_at=group.last_polled_at,
        datasets=selected_datasets,
    )
    result = await DataCenterWatchService().run_watch_group(
        group=read_group,
        actor_id=context.open_id or "feishu-ai-agent",
        source="feishu",
        push_to_feishu=False,
    )
    # `AgentToolCallContext` does not currently carry the user's raw message
    # text, so `parse_ebk_time_dimension_intent` fallback cannot be applied
    # here yet. Callers (Feishu router / Hermes) should resolve intent from
    # the incoming message and pass `time_dimension` explicitly.
    requested_time_dimension = arguments.time_dimension
    dataset_results = [
        _compact_ebk_dataset_result_with_data(
            _project_ebk_dataset_result_item(item, requested_time_dimension)
        )
        for item in result.get("dataset_results", [])
        if isinstance(item, dict)
    ]
    requested_metrics = _collect_requested_ebk_metrics(
        dataset_results,
        metric_keys=arguments.metric_keys,
    )
    status = str(result.get("status") or "unknown")
    return AgentToolResult(
        status=status,
        summary=_ebk_dataset_read_summary(
            status=status,
            dataset_results=dataset_results,
            requested_metrics=requested_metrics,
        ),
        data={
            "watch_group_key": result.get("watch_group_key") or watch_group_key,
            "requested_dataset_ids": [dataset.dataset_id for dataset in selected_datasets],
            "requested_metrics": requested_metrics,
            "dataset_results": dataset_results,
            "feishu_push": _compact_push_result(result.get("feishu_push")),
        },
        evidence=_ebk_dataset_read_evidence(
            event_id=context.event_id,
            browser_read_invoked=True,
        ),
        next_action=_optional_text(result.get("next_action")),
    )


async def analyze_competitor_price_change_tool(
    arguments: WatchGroupToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind="competitor",
        tool_name="analyze_competitor_price_change",
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    alert = await get_latest_price_change_alert(watch_group_key=watch_group_key)
    if alert is None:
        return AgentToolResult(
            status="data_required",
            summary="还没有竞品价格变化分析记录。",
            data={"watch_group_key": watch_group_key},
            evidence={
                **_read_only_evidence(
                    tool_name="analyze_competitor_price_change",
                    event_id=context.event_id,
                ),
                "price_change_evaluation_invoked": False,
            },
            next_action="等待竞品轮询产生价格变化记录，或先执行一次竞品读取。",
        )

    data = {
        "watch_group_key": alert.get("watch_group_key"),
        "status": alert.get("status"),
        "summary": alert.get("summary"),
        "changed_count": alert.get("changed_count"),
        "suggestion_count": alert.get("suggestion_count"),
        "compared_key_count": alert.get("compared_key_count"),
        "connection_mode": alert.get("connection_mode"),
        "can_auto_write_price": alert.get("can_auto_write_price"),
        "write_policy_message": alert.get("write_policy_message"),
        "changed_items": _limited_dicts(alert.get("changed_items"), limit=5),
        "suggestions": _limited_dicts(alert.get("suggestions"), limit=5),
        "created_at": alert.get("created_at"),
    }
    return AgentToolResult(
        status=str(alert.get("status") or "success"),
        summary=str(alert.get("summary") or "已读取最新竞品价格变化记录。"),
        data=data,
        evidence={
            **_read_only_evidence(
                tool_name="analyze_competitor_price_change",
                event_id=context.event_id,
            ),
            "price_change_evaluation_invoked": False,
            "approval_card_sent": False,
        },
        next_action=_optional_text(alert.get("next_action")),
    )


async def pause_watch_tool(
    arguments: WatchControlToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    return await _set_watch_enabled(
        arguments=arguments,
        context=context,
        enabled=False,
        auto_poll_enabled=False,
        action_label="暂停",
    )


async def resume_watch_tool(
    arguments: WatchControlToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    return await _set_watch_enabled(
        arguments=arguments,
        context=context,
        enabled=True,
        auto_poll_enabled=True,
        action_label="恢复",
    )


async def create_manual_pricing_approval_tool(
    arguments: ManualPricingApprovalToolArgs,
    context: AgentToolCallContext,
) -> AgentToolResult:
    if not context.chat_id or not context.message_id:
        return AgentToolResult(
            status="config_required",
            summary="需要从飞书群消息中发起调价审批，当前缺少飞书群或消息上下文。",
            evidence=_pricing_approval_evidence(
                event_id=context.event_id,
                approval_card_sent=False,
            ),
            next_action="请在已绑定调价 EBK 账号的飞书群里 @机器人 发起调价审批。",
        )

    try:
        pricing_account = get_pricing_browser_session_for_feishu_chat_id(context.chat_id)
    except ValueError as exc:
        return AgentToolResult(
            status="config_required",
            summary="当前飞书群没有绑定唯一的 EBK 调价账号，无法生成调价审批卡。",
            evidence={
                **_pricing_approval_evidence(
                    event_id=context.event_id,
                    approval_card_sent=False,
                ),
                "error_type": type(exc).__name__,
            },
            next_action="请在账号与浏览器中，把当前飞书群 chat_id 绑定到唯一的 EBK 调价账号后重试。",
        )

    from app.domain.notifications.manual_price_command import (
        _normalize_room_type,
        split_manual_price_platform_prefix,
    )

    room_type, room_platform = split_manual_price_platform_prefix(arguments.room_type)
    room_type = _normalize_room_type(room_type)
    if room_type is None:
        return AgentToolResult(
            status="data_required",
            summary="未能从消息里识别出有效的房型名称，请让用户补充房型后重试。",
            evidence=_pricing_approval_evidence(
                event_id=context.event_id,
                approval_card_sent=False,
            ),
            next_action="请让用户补充明确的房型名称（不含平台名）。",
        )
    preview = await create_manual_pricing_preview(
        watch_group_key=arguments.watch_group_key or DEFAULT_COMPETITOR_WATCH_GROUP_KEY,
        room_type=room_type,
        date_label=arguments.date_label,
        target_price=arguments.target_price,
        checkin_date=getattr(arguments, "checkin_date", None),
        pricing_account=pricing_account,
        actor_id=context.open_id or "feishu-ai-agent",
        reason="Feishu AI Agent 手动调价审批请求",
        platform=arguments.platform or room_platform,
    )
    preview_payload = preview.model_dump(mode="json")
    if preview.status != "preview_ready":
        return AgentToolResult(
            status=preview.status,
            summary=preview.summary,
            data={"pricing_preview": preview_payload},
            evidence={
                **_pricing_approval_evidence(
                    event_id=context.event_id,
                    approval_card_sent=False,
                ),
                "can_auto_write_price": preview.can_auto_write_price,
                "connection_mode": preview.connection_mode,
            },
            next_action=preview.next_action,
        )

    push = await FeishuBotClient(chat_id=context.chat_id).reply_interactive_card(
        context.message_id,
        build_pricing_approval_card(preview_payload),
        fallback_text=build_pricing_approval_fallback_text(preview_payload),
    )
    return AgentToolResult(
        status="approval_card_replied",
        summary="已生成手动调价审批卡，等待飞书人工审批。",
        data={
            "pricing_preview": preview_payload,
            "feishu_push": _compact_push_result(push),
        },
        evidence={
            **_pricing_approval_evidence(
                event_id=context.event_id,
                approval_card_sent=True,
            ),
            "can_auto_write_price": preview.can_auto_write_price,
            "connection_mode": preview.connection_mode,
        },
    )


async def get_watch_scheduler_status_tool(
    arguments: BaseModel,
    context: AgentToolCallContext,
) -> AgentToolResult:
    health = await get_runtime_health()
    summary = health.get("summary") if isinstance(health.get("summary"), dict) else {}
    status = str(summary.get("status") or "unknown")
    modules = health.get("modules") if isinstance(health.get("modules"), dict) else {}
    data = {
        "checked_at": health.get("checked_at"),
        "summary": summary,
        "modules": {
            key: _compact_health_module(value)
            for key, value in modules.items()
            if isinstance(value, dict)
        },
    }
    return AgentToolResult(
        status=status,
        summary=_health_summary(status=status, summary=summary),
        data=data,
        evidence={
            **_read_only_evidence(
                tool_name="get_watch_scheduler_status",
                event_id=context.event_id,
            ),
            "runtime_health_checked": True,
        },
    )


async def _set_watch_enabled(
    *,
    arguments: WatchControlToolArgs,
    context: AgentToolCallContext,
    enabled: bool,
    auto_poll_enabled: bool,
    action_label: str,
) -> AgentToolResult:
    tool_name = "pause_watch" if not enabled else "resume_watch"
    if arguments.watch_kind in {"meituan_ebk", "fliggy_ebk"}:
        return await _set_platform_watch_enabled(
            arguments=arguments,
            context=context,
            enabled=enabled,
            auto_poll_enabled=auto_poll_enabled,
            action_label=action_label,
            tool_name=tool_name,
        )
    watch_group_key, blocked = await _resolve_authorized_watch_group_key(
        context=context,
        explicit_key=arguments.watch_group_key,
        watch_kind=arguments.watch_kind,
        tool_name=tool_name,
    )
    if blocked is not None:
        return blocked
    assert watch_group_key is not None
    if arguments.watch_kind == "ebk":
        watch_group = await update_data_center_watch_group(
            watch_group_key=watch_group_key,
            patch=DataCenterWatchGroupPatch(
                enabled=enabled,
                auto_poll_enabled=auto_poll_enabled,
            ),
        )
    else:
        watch_group = await update_competitor_watch_group(
            watch_group_key=watch_group_key,
            patch=CompetitorWatchGroupPatch(
                enabled=enabled,
                auto_poll_enabled=auto_poll_enabled,
            ),
        )

    if watch_group is None:
        return AgentToolResult(
            status="not_found",
            summary=f"未找到要{action_label}的盯盘监控组。",
            data={"watch_kind": arguments.watch_kind, "watch_group_key": watch_group_key},
            evidence=_watch_control_evidence(
                tool_name=tool_name,
                event_id=context.event_id,
            ),
            next_action="请确认监控组是否存在。",
        )

    return AgentToolResult(
        status="success",
        summary=f"已{action_label}{_watch_kind_label(arguments.watch_kind)}盯盘：{watch_group.get('name') or watch_group_key}。",
        data={
            "watch_kind": arguments.watch_kind,
            "watch_group_key": watch_group_key,
            "watch_group": _compact_watch_group(watch_group),
        },
        evidence=_watch_control_evidence(
            tool_name=tool_name,
            event_id=context.event_id,
        ),
    )


def _compact_ebk_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "summary": run.get("summary"),
        "source": run.get("source"),
        "dataset_count": run.get("dataset_count"),
        "success_count": run.get("success_count"),
        "failed_count": run.get("failed_count"),
        "human_required_count": run.get("human_required_count"),
        "captured_at": run.get("captured_at"),
        "push_result": _compact_push_result(run.get("push_result")),
    }


def _compact_ebk_dataset_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": item.get("dataset_id"),
        "dataset_label": item.get("dataset_label"),
        "status": item.get("status"),
        "summary": item.get("summary"),
        "failure_reason": item.get("failure_reason"),
        "captured_at": item.get("captured_at"),
        "push_enabled": item.get("push_enabled"),
        "pushed": item.get("pushed"),
    }


def _project_ebk_dataset_result_item(
    item: dict[str, Any],
    time_dimension: str | None,
) -> dict[str, Any]:
    if not time_dimension:
        return item
    data = item.get("data")
    if not isinstance(data, dict):
        return item
    projected_item = dict(item)
    projected_item["data"] = project_dataset_data_for_time_dimension(data, time_dimension)
    return projected_item


def _compact_ebk_dataset_result_with_data(item: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_ebk_dataset_result(item)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    projection_status = data.get("projection_status")
    projected_time_dimension = data.get("projected_time_dimension")
    # A `missing_dimension` projection means the requested time dimension has
    # no captured data. Top-level `metrics`/`tables` still reflect the
    # dataset's primary dimension in that case, and must not be surfaced as
    # if they answered the requested dimension.
    if projection_status == "missing_dimension":
        metrics: dict[str, Any] = {}
        tables: list[Any] = []
    else:
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        tables = data.get("tables") if isinstance(data.get("tables"), list) else []
    compact["metrics"] = {
        str(key): _compact_metric(value)
        for key, value in metrics.items()
        if isinstance(value, dict)
    }
    compact["table_count"] = len(tables)
    if projected_time_dimension:
        compact["projected_time_dimension"] = projected_time_dimension
    if projection_status:
        compact["projection_status"] = projection_status
    # When unset (no projection), surface both captured dimensions so the
    # caller can answer dual-section / unclear-intent questions.
    dims = data.get("time_dimensions")
    if isinstance(dims, dict) and dims and projection_status != "missing_dimension":
        compact["time_dimensions"] = {
            str(key): {
                "label": payload.get("label") or key,
                "status": payload.get("status") or "unknown",
                "metrics": {
                    str(metric_key): _compact_metric(metric)
                    for metric_key, metric in (payload.get("metrics") or {}).items()
                    if isinstance(metric, dict)
                },
            }
            for key, payload in dims.items()
            if isinstance(payload, dict)
        }
    return compact


def _compact_metric(value: dict[str, Any]) -> dict[str, Any]:
    display_value = (
        value.get("display_value")
        or value.get("display")
        or value.get("value_text")
        or value.get("value")
    )
    return {
        "key": value.get("key"),
        "label": value.get("label"),
        "value": value.get("value"),
        "display_value": display_value,
        "section": value.get("section"),
        "trend": value.get("trend"),
    }


def _resolve_ebk_dataset_ids_for_agent_read(
    *,
    dataset_ids: list[str] | None,
    metric_keys: list[str] | None,
) -> list[str]:
    if dataset_ids:
        return dataset_ids
    metric_set = set(metric_keys or [])
    if "occupancy_rate" in metric_set:
        return ["beneficial_data"]
    if metric_set & {"average_selling_price", "average_price", "transaction_rate"}:
        return ["business_outline", "beneficial_data"]
    return ["business_outline"]


def _select_ebk_runtime_datasets(
    group: DataCenterWatchGroupRuntime,
    dataset_ids: list[str],
) -> list[DataCenterWatchDatasetRuntime]:
    by_id = {dataset.dataset_id: dataset for dataset in group.datasets}
    selected: list[DataCenterWatchDatasetRuntime] = []
    for sort_order, dataset_id in enumerate(dataset_ids):
        source = by_id.get(dataset_id)
        label = source.dataset_label if source else DATASET_LABELS.get(dataset_id, dataset_id)
        enabled = source.enabled if source else True
        if not enabled:
            continue
        selected.append(
            DataCenterWatchDatasetRuntime(
                dataset_id=dataset_id,
                dataset_label=label,
                enabled=True,
                push_enabled=False,
                sort_order=sort_order,
            )
        )
    return selected


def _collect_requested_ebk_metrics(
    dataset_results: list[dict[str, Any]],
    *,
    metric_keys: list[str] | None,
) -> dict[str, Any]:
    requested = set(metric_keys or [])
    collected: dict[str, Any] = {}
    for dataset in dataset_results:
        metrics = dataset.get("metrics") if isinstance(dataset.get("metrics"), dict) else {}
        for key, value in metrics.items():
            if requested and key not in requested:
                continue
            if key in collected:
                continue
            metric = dict(value) if isinstance(value, dict) else {"value": value}
            metric["dataset_id"] = dataset.get("dataset_id")
            metric["dataset_label"] = dataset.get("dataset_label")
            collected[str(key)] = metric
    if not collected and requested:
        for key in requested:
            collected[key] = {
                "key": key,
                "status": "not_found",
                "summary": "本次读取结果里没有解析到该指标。",
            }
    return collected


def _ebk_dataset_read_summary(
    *,
    status: str,
    dataset_results: list[dict[str, Any]],
    requested_metrics: dict[str, Any],
) -> str:
    if requested_metrics:
        parts = []
        for metric in requested_metrics.values():
            if not isinstance(metric, dict) or metric.get("status") == "not_found":
                continue
            label = metric.get("label") or metric.get("key") or "指标"
            value = metric.get("display_value") or metric.get("value")
            if value is not None:
                parts.append(f"{label}：{value}")
        if parts:
            return "EBK 数据中心读取成功，" + "；".join(parts) + "。"
    success_count = sum(1 for item in dataset_results if item.get("status") == "success")
    return f"EBK 数据中心读取完成：状态 {status}，成功 {success_count}/{len(dataset_results)} 个数据集。"


def _latest_ebk_summary(
    *,
    run: dict[str, Any],
    dataset_results: list[dict[str, Any]],
) -> str:
    success_count = _int_or_zero(run.get("success_count"))
    failed_count = _int_or_zero(run.get("failed_count"))
    human_required_count = _int_or_zero(run.get("human_required_count"))
    total_count = _int_or_zero(run.get("dataset_count")) or len(dataset_results)
    return (
        f"EBK 最新读取：共 {total_count} 项，成功 {success_count} 项，"
        f"失败 {failed_count} 项，需要人工处理 {human_required_count} 项。"
    )


def _run_ebk_watch_summary(status: str) -> str:
    if status == "success":
        return "EBK 数据中心读取完成。"
    if status == "not_found":
        return "未找到 EBK 数据中心监控组。"
    return "EBK 数据中心读取已返回结果。"


def _compact_competitor_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "summary": run.get("summary"),
        "captured_at": run.get("captured_at"),
        "parser_version": run.get("parser_version"),
        "push_result": _compact_push_result(run.get("push_result")),
    }


def _compact_competitor_hotel(item: dict[str, Any]) -> dict[str, Any]:
    config = item.get("hotel_config") if isinstance(item.get("hotel_config"), dict) else {}
    snapshots = item.get("snapshots") if isinstance(item.get("snapshots"), dict) else {}
    return {
        "role": config.get("role"),
        "alias": config.get("alias"),
        "hotel_name": config.get("hotel_name") or config.get("alias"),
        "enabled": config.get("enabled"),
        "snapshots": [
            _compact_competitor_snapshot(date_label, snapshot)
            for date_label, snapshot in snapshots.items()
            if isinstance(snapshot, dict)
        ],
    }


def _compact_competitor_snapshot(date_label: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    rates = snapshot.get("rates") if isinstance(snapshot.get("rates"), list) else []
    return {
        "date_label": date_label,
        "checkin_date": snapshot.get("checkin_date"),
        "checkout_date": snapshot.get("checkout_date"),
        "status": snapshot.get("status"),
        "review_reason": snapshot.get("review_reason"),
        "page_state": snapshot.get("page_state"),
        "rates": [_compact_competitor_rate(item) for item in rates if isinstance(item, dict)][:20],
    }


def _compact_competitor_rate(rate: dict[str, Any]) -> dict[str, Any]:
    return {
        "room_type": rate.get("room_type"),
        "breakfast_text": rate.get("breakfast_text"),
        "breakfast_kind": rate.get("breakfast_kind"),
        "price_amount_yuan": rate.get("price_amount_yuan"),
        "discount_amount_yuan": rate.get("discount_amount_yuan"),
        "discount_display": rate.get("discount_display"),
        "availability_text": rate.get("availability_text"),
        "availability_status": rate.get("availability_status"),
        "is_trusted": rate.get("is_trusted"),
        "source": rate.get("source"),
    }


def _compact_calendar_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    run = matrix.get("run") if isinstance(matrix.get("run"), dict) else {}
    dates = [
        _compact_calendar_date(item)
        for item in matrix.get("dates", [])
        if isinstance(item, dict)
    ][:20]
    alert_count = matrix.get("alert_count")
    if not isinstance(alert_count, int):
        alert_count = sum(
            len(item.get("alerts") or [])
            for item in dates
            if isinstance(item.get("alerts"), list)
        )
    return {
        "watch_group_key": matrix.get("watch_group_key"),
        "status": matrix.get("status") or run.get("status"),
        "summary": matrix.get("summary") or run.get("summary"),
        "future_days": matrix.get("future_days"),
        "trusted_source": matrix.get("trusted_source") or "calendar_dom_cell_price",
        "latest_captured_at": run.get("captured_at"),
        "alert_count": alert_count,
        "calendar_rows": _calendar_rows_from_dates(dates),
        "dates": dates,
    }


def _compact_calendar_date(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "checkin_date": item.get("checkin_date"),
        "own": _compact_calendar_price_point(item.get("own")),
        "competitors": [
            _compact_calendar_price_point(point)
            for point in item.get("competitors", [])
            if isinstance(point, dict)
        ][:8],
        "alerts": _limited_dicts(item.get("alerts"), limit=8),
    }


def _compact_calendar_price_point(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    return {
        "hotel_alias": item.get("hotel_alias") or item.get("hotel_name"),
        "price_amount_yuan": item.get("price_amount_yuan"),
        "status": item.get("status"),
        "source": item.get("source"),
        "is_trusted": item.get("is_trusted"),
    }


def _calendar_rows_from_dates(dates: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for date_item in dates[:15]:
        checkin_date = str(date_item.get("checkin_date") or "未知日期")
        own = date_item.get("own") if isinstance(date_item.get("own"), dict) else {}
        competitors = date_item.get("competitors") if isinstance(date_item.get("competitors"), list) else []
        parts = []
        if own:
            parts.append("本店 " + _calendar_point_text(own, fallback="本店"))
        if competitors:
            parts.append(
                "竞品 "
                + "；".join(
                    _calendar_point_text(point, fallback=f"竞品{index + 1}")
                    for index, point in enumerate(competitors[:4])
                    if isinstance(point, dict)
                )
            )
        alerts = date_item.get("alerts") if isinstance(date_item.get("alerts"), list) else []
        if alerts:
            parts.append(f"提醒 {len(alerts)} 条")
        rows.append({"field": f"{checkin_date} 日历价", "value": "；".join(parts) or "暂无可信日历价"})
    return rows


def _calendar_point_text(point: dict[str, Any], *, fallback: str) -> str:
    name = point.get("hotel_alias") or fallback
    price = point.get("price_amount_yuan")
    price_text = f"{price}元" if price is not None else "暂无可信价"
    return f"{name}: {price_text}"


def _latest_calendar_price_summary(compact: dict[str, Any]) -> str:
    future_days = compact.get("future_days") or "未来"
    alert_count = _int_or_zero(compact.get("alert_count"))
    return f"日历价最新读取：监测未来{future_days}天，提醒日期 {alert_count} 个。"


def _run_calendar_price_summary(status: str) -> str:
    if status == "success":
        return "日历价读取完成。"
    if status == "alert":
        return "日历价读取完成，并产生提醒。"
    if status == "not_found":
        return "未找到竞品监控组或日历价配置。"
    return "日历价读取已返回结果。"


def _compact_push_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": value.get("status"),
        "summary": value.get("summary"),
        "next_action": value.get("next_action"),
    }


def _compact_health_module(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_key": value.get("module_key"),
        "display_name": value.get("display_name"),
        "freshness_status": value.get("freshness_status"),
        "process_confirmed": value.get("process_confirmed"),
        "last_heartbeat_at": value.get("last_heartbeat_at"),
        "heartbeat_age_seconds": value.get("heartbeat_age_seconds"),
        "last_error": value.get("last_error"),
        "last_tick_summary": value.get("last_tick_summary") if isinstance(value.get("last_tick_summary"), dict) else {},
    }


def _compact_watch_group(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_key": value.get("group_key"),
        "name": value.get("name"),
        "brief_title": value.get("brief_title"),
        "enabled": value.get("enabled"),
        "auto_poll_enabled": value.get("auto_poll_enabled"),
        "query_interval_minutes": value.get("query_interval_minutes"),
        "schedule_mode": value.get("schedule_mode"),
        "schedule_start_time": value.get("schedule_start_time"),
        "schedule_end_time": value.get("schedule_end_time"),
        "schedule_interval_minutes": value.get("schedule_interval_minutes"),
    }


def _watch_control_evidence(*, tool_name: str, event_id: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "event_id": event_id,
        "handler_invoked": True,
        "watch_config_updated": True,
        "browser_read_invoked": False,
        "pricing_execution_invoked": False,
        "ota_write_invoked": False,
        "webhook_called": False,
    }


def _pricing_approval_evidence(*, event_id: str, approval_card_sent: bool) -> dict[str, Any]:
    return {
        "tool_name": "create_manual_pricing_approval",
        "event_id": event_id,
        "handler_invoked": True,
        "approval_card_sent": approval_card_sent,
        "execute_triggered": False,
        "pricing_execution_invoked": False,
        "ota_write_invoked": False,
        "browser_read_invoked": False,
        "webhook_called": False,
    }


def _ebk_dataset_read_evidence(*, event_id: str, browser_read_invoked: bool) -> dict[str, Any]:
    return {
        "tool_name": "read_ebk_datasets",
        "event_id": event_id,
        "handler_invoked": True,
        "browser_read_invoked": browser_read_invoked,
        "scheduler_invoked": False,
        "webhook_called": False,
        "push_to_feishu": False,
        "pricing_execution_invoked": False,
        "ota_write_invoked": False,
    }


def _watch_kind_label(watch_kind: str) -> str:
    return "EBK" if watch_kind == "ebk" else "竞品"


def _health_summary(*, status: str, summary: dict[str, Any]) -> str:
    if status in {"fresh", "healthy"}:
        return "盯盘服务状态正常。"
    attention = summary.get("attention_modules")
    if isinstance(attention, list) and attention:
        return f"盯盘服务需要关注：{', '.join(str(item) for item in attention)}。"
    return "盯盘服务状态需要关注。"


def _read_only_evidence(
    *,
    tool_name: str,
    event_id: str,
    captured_at: Any | None = None,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "event_id": event_id,
        "handler_invoked": True,
        "persisted_data_read": True,
        "browser_read_invoked": False,
        "webhook_called": False,
        "ota_write_invoked": False,
        "captured_at": captured_at,
    }


def _calendar_price_status_evidence(
    *,
    event_id: str,
    captured_at: Any | None,
) -> dict[str, Any]:
    evidence = _read_only_evidence(
        tool_name="get_calendar_price_watch_status",
        event_id=event_id,
        captured_at=captured_at,
    )
    return {
        **evidence,
        "read_only": True,
        "pricing_execution_invoked": False,
        "ota_write_invoked": False,
    }


def _limited_dicts(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:limit]


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
