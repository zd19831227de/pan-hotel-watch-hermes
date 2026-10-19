"""EBK 全局推送编排服务（设计文档 §8.1/§8.2/§8.4）。

一次全局运行 = 五步前置检查（调用方 scheduler 负责 ①②，本服务负责锚点/
账号/webhook 预解析）→ 携程→美团→飞猪梯队读取（30 秒冷却）→ 证据适配 →
三张分类卡 → 逐卡投递 → 失败告警。所有外部依赖可注入，便于单测。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.domain.ebk_global_push.adapters import GLOBAL_CTRIP_DATASET_IDS, ADAPTERS_BY_PLATFORM
from app.domain.ebk_global_push.global_push_cards import build_global_push_cards
from app.domain.ebk_global_push.projection import (
    normalize_projection_rows,
    extract_ctrip_score_summary,
)
from app.domain.ebk_global_push.scheduling import aggregate_run_status, compute_next_run_at

logger = logging.getLogger(__name__)

PLATFORM_COOLDOWN_SECONDS = 30
FAILURE_ALERT_THROTTLE_PREFIX = "ebk_global_push_failure"

# 运行摘要可能承载原始异常文本（如 ProcessSingleton/Playwright 启动错误含
# profile 绝对路径）；落入运行表/API/告警前统一脱敏（AGENTS.md §3/设计 §8.4）。
_LOCAL_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^，。；）)\s]{0,160}")


def _sanitize_summary_text(text: str) -> str:
    return _LOCAL_PATH_PATTERN.sub("[本地路径已隐藏]", text)[:200]


def _push_outcome_status(outcome: Any) -> str:
    """飞书推送结果可能是 dict（测试 fake）或 FeishuPushResult pydantic 模型（生产）；
    统一取 status 字段，避免对模型调用 .get 触发 AttributeError（实测 run-now 500 根因）。"""

    if isinstance(outcome, dict):
        return str(outcome.get("status") or "")
    return str(getattr(outcome, "status", "") or "")


@dataclass(frozen=True)
class GlobalPushPlatformTarget:
    platform: str
    status: str  # success / config_required / ...
    account_id: str | None = None
    browser_session_id: str | None = None
    summary: str = ""
    # 对应平台店铺名（ctrip=锚点监控组名；美团/飞猪=挂载监控组名），
    # 经营卡分页门店行显示各自店铺名（2026-09-04 运营确认）。
    store_name: str = ""


@dataclass
class GlobalPushDeps:
    get_ctrip_group: Callable[[str], Awaitable[Any]]
    find_mounted_group: Callable[[str, str], Awaitable[Any]]
    read_ctrip: Callable[..., Awaitable[dict[str, Any]]]
    read_platform: Callable[[str, Any, str | None], Awaitable[dict[str, Any]]]
    push_card: Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]
    send_failure_alert: Callable[[str, str, str, str, str, str], Awaitable[None]]
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    cooldown_seconds: float = PLATFORM_COOLDOWN_SECONDS
    read_time_text: str = ""
    # 需求2：收益概览对比列，值为上一次推送运行（如中午12点 vs 早9点）的数值环比。
    # 返回 {field_key: previous_value} 的 dict，缺数据时返回空 dict。
    get_previous_metrics: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None


async def _anchor_group_shop_key(anchor_key: str) -> str | None:
    from app.domain.ctrip_ebooking_watch.repository import (
        get_data_center_watch_group_detail,
    )

    try:
        detail = await get_data_center_watch_group_detail(
            watch_group_key=anchor_key, create_if_missing=False
        )
    except Exception:
        logger.debug("global push anchor shop lookup failed", exc_info=True)
        return None
    if not detail:
        return None
    return str(detail.get("shop_key") or "").strip() or None


async def resolve_mounted_platform_group(platform: str, anchor_key: str):
    """v2 §3.4 收敛：美团/飞猪组解析店铺直连优先，legacy 挂载兜底。

    锚点携程组归属店铺时按 shop_key 取该店平台组（挂载列缺失也能解析）；
    锚点未归属店铺、或店内暂无该平台组时，回落 ctrip_watch_group_key 挂载链
    （迁移期行为不变）。
    """
    shop_key = await _anchor_group_shop_key(anchor_key)
    if shop_key:
        if platform == "meituan":
            from app.domain.meituan_ebooking_watch.repository import (
                get_meituan_watch_group_runtime_by_shop_key,
            )

            runtime = await get_meituan_watch_group_runtime_by_shop_key(shop_key=shop_key)
        else:
            from app.domain.fliggy_ebooking_watch.repository import (
                get_fliggy_watch_group_runtime_by_shop_key,
            )

            runtime = await get_fliggy_watch_group_runtime_by_shop_key(shop_key=shop_key)
        if runtime is not None:
            return runtime
    if platform == "meituan":
        from app.domain.meituan_ebooking_watch.repository import (
            get_meituan_watch_group_runtime_by_ctrip_anchor,
        )

        return await get_meituan_watch_group_runtime_by_ctrip_anchor(
            ctrip_watch_group_key=anchor_key
        )
    from app.domain.fliggy_ebooking_watch.repository import (
        get_fliggy_watch_group_runtime_by_ctrip_anchor,
    )

    return await get_fliggy_watch_group_runtime_by_ctrip_anchor(
        ctrip_watch_group_key=anchor_key
    )


def default_deps() -> GlobalPushDeps:
    """生产依赖：真实仓储/读取器/飞书推送/告警接线（延迟导入避免环）。"""

    async def _get_ctrip_group(group_key: str):
        from app.domain.ctrip_ebooking_watch.repository import (
            get_data_center_watch_group_runtime,
        )

        return await get_data_center_watch_group_runtime(watch_group_key=group_key)

    async def _find_mounted_group(platform: str, anchor_key: str):
        return await resolve_mounted_platform_group(platform, anchor_key)

    async def _read_ctrip(group: Any, global_run_id: str | None = None) -> dict[str, Any]:
        from app.domain.ctrip_ebooking_watch.service import get_data_center_watch_service
        from app.domain.ebk_snapshots.ebk_snapshot_reader import (
            persist_ctrip_run_snapshot,
        )

        payload = await get_data_center_watch_service().run_watch_group(
            group=group,
            actor_id="ebk_global_push_scheduler",
            source="global_push",
            push_to_feishu=False,
            dataset_ids=GLOBAL_CTRIP_DATASET_IDS,
            include_disabled_datasets=True,
        )
        # 基线修复：携程 run 结果补存统一快照表，否则下次运行
        # get_latest_success_ebk_snapshot("ctrip") 取不到全局推送数据，
        # 环比基线会错拿其它来源（如定时看板早 8:30）的旧快照。
        # P4：带 run id 幂等补存 + 店铺归属固化。
        try:
            await persist_ctrip_run_snapshot(
                run_payload=payload,
                platform_account_id=str(getattr(group, "platform_account_id", "") or ""),
                browser_session_id=str(getattr(group, "browser_session_id", "") or ""),
                actor_id="ebk_global_push_scheduler",
                trigger_via="global_push",
                global_run_id=global_run_id,
                shop_key=str(getattr(group, "shop_key", "") or "").strip() or None,
            )
        except Exception:  # noqa: BLE001 - 补存失败不阻断本次推送
            logger.warning("persist ctrip global-push snapshot failed", exc_info=True)
        return payload

    async def _read_platform(
        platform: str, group: Any, global_run_id: str | None = None
    ) -> dict[str, Any]:
        from app.domain.ebk_snapshots.ebk_snapshot_reader import (
            read_meituan_platform_snapshot,
            read_fliggy_platform_snapshot,
        )

        reader = (
            read_meituan_platform_snapshot
            if platform == "meituan"
            else read_fliggy_platform_snapshot
        )
        return await reader(
            platform_account_id=group.platform_account_id,
            browser_session_id=group.browser_session_id,
            actor_id="ebk_global_push_scheduler",
            trigger_via="global_push",
            global_run_id=global_run_id,
        )

    async def _push_card(title: str, card: dict[str, Any], webhook_url: str):
        from app.domain.notifications.feishu import (
            push_interactive_card_to_feishu_webhook,
        )

        result = await push_interactive_card_to_feishu_webhook(
            title=title,
            card=card,
            webhook_url=webhook_url,
            fallback_text=None,
        )
        # FeishuPushResult 是 pydantic 模型；归一化为 dict，保持 push_card 的 dict 契约，
        # 也避免上层对模型误调 .get（实测 run-now 500 根因）。
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result

    async def _send_failure_alert(
        config_id_text: str,
        platform: str,
        store_name: str,
        status: str,
        summary: str,
        webhook_url: str,
    ) -> None:
        from app.domain.ebk_snapshots.feishu_push import (
            build_platform_failure_alert_card,
            build_platform_failure_alert_text,
        )
        from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
        from app.domain.platform_ebk_watch.push_policy import (
            record_failure_alert,
            should_send_failure_alert,
        )

        throttle_key = f"{FAILURE_ALERT_THROTTLE_PREFIX}:{config_id_text}:{platform}"
        allowed, _reason = should_send_failure_alert(throttle_key)
        if not allowed:
            return
        card = build_platform_failure_alert_card(
            platform=platform,
            store_name=store_name or None,
            watch_group_name=f"全局推送 #{config_id_text}",
            status=status,
            summary=summary,
        )
        await push_interactive_card_to_feishu_webhook(
            title="EBK 全局推送读取异常提醒",
            card=card,
            webhook_url=webhook_url,
            fallback_text=build_platform_failure_alert_text(
                platform=platform,
                store_name=store_name or None,
                watch_group_name=f"全局推送 #{config_id_text}",
                status=status,
                summary=summary,
            ),
        )
        record_failure_alert(throttle_key)

    async def _get_previous_metrics(platform: str, account_id: str) -> dict[str, Any]:
        from app.domain.ebk_snapshots.repository import (
            list_recent_success_ebk_snapshots,
        )

        snapshots = await list_recent_success_ebk_snapshots(
            platform=platform, platform_account_id=account_id, limit=_BASELINE_SNAPSHOT_LOOKBACK
        )
        # 2026-09-08 口径：跨天快照（如昨日 23:30 全天量）不作为当日首推基线，
        # 让「今天第一次推送」显示「凌晨数据」而不是今晨实时量误比昨日全天量；
        # 同日内按字段回溯补齐（单条快照可能缺个别模块，见 _merge_previous_metrics_from_snapshots）。
        return _merge_previous_metrics_from_snapshots(snapshots, platform=platform)

    return GlobalPushDeps(
        get_ctrip_group=_get_ctrip_group,
        find_mounted_group=_find_mounted_group,
        read_ctrip=_read_ctrip,
        read_platform=_read_platform,
        push_card=_push_card,
        send_failure_alert=_send_failure_alert,
        get_previous_metrics=_get_previous_metrics,
    )


async def resolve_platform_targets(
    *,
    config: dict[str, Any],
    anchor: Any,
    deps: GlobalPushDeps,
) -> dict[str, GlobalPushPlatformTarget]:
    """预解析三平台账号（§8.1 第⑤步）：缺账号平台 config_required，不阻塞其它平台。"""

    targets: dict[str, GlobalPushPlatformTarget] = {}
    enabled = set(config.get("enabled_platforms") or [])
    if "ctrip" in enabled:
        account_id = str(getattr(anchor, "platform_account_id", "") or "")
        session_id = str(getattr(anchor, "browser_session_id", "") or "")
        store_name = str(getattr(anchor, "name", "") or "")
        if account_id and session_id:
            targets["ctrip"] = GlobalPushPlatformTarget(
                platform="ctrip", status="resolved", account_id=account_id,
                browser_session_id=session_id, store_name=store_name,
            )
        else:
            targets["ctrip"] = GlobalPushPlatformTarget(
                platform="ctrip", status="config_required",
                summary="携程锚点组缺少账号或浏览器会话绑定。",
                store_name=store_name,
            )
    for platform in ("meituan", "fliggy"):
        if platform not in enabled:
            continue
        mounted = await deps.find_mounted_group(platform, config["ctrip_watch_group_key"])
        store_name = str(getattr(mounted, "name", "") or "") if mounted is not None else ""
        if mounted is None:
            targets[platform] = GlobalPushPlatformTarget(
                platform=platform, status="config_required",
                summary=f"未找到该店铺的{platform}监控组（店铺未接入{platform}，也无挂载到该携程组的旧组）。",
            )
            continue
        account_id = str(getattr(mounted, "platform_account_id", "") or "")
        session_id = str(getattr(mounted, "browser_session_id", "") or "")
        if account_id and session_id:
            targets[platform] = GlobalPushPlatformTarget(
                platform=platform, status="resolved", account_id=account_id,
                browser_session_id=session_id, store_name=store_name,
            )
        else:
            targets[platform] = GlobalPushPlatformTarget(
                platform=platform, status="config_required",
                summary=f"{platform}监控组缺少账号或浏览器会话绑定。",
                store_name=store_name,
            )
    return targets


async def run_global_push(
    *,
    config: dict[str, Any],
    run_id: int | None,
    deps: GlobalPushDeps,
) -> dict[str, Any]:
    """执行一次全局运行；返回运行结果（含最终聚合状态与游标推进输入）。"""

    anchor = await deps.get_ctrip_group(config["ctrip_watch_group_key"])
    if anchor is None:
        return _result(
            status="config_required",
            error_summary="锚点携程监控组不存在。",
        )
    if not bool(getattr(anchor, "enabled", False)):
        return _result(
            status="config_required",
            error_summary="锚点携程监控组已停用（§7.3：调度层拦截）。",
        )
    webhook_url = str(getattr(anchor, "feishu_webhook_url", None) or "")
    store_name = str(getattr(anchor, "name", "") or "")

    targets = await resolve_platform_targets(config=config, anchor=anchor, deps=deps)

    platform_payloads: dict[str, Any] = {}
    platform_statuses: list[str] = []
    platform_results: dict[str, Any] = {}
    read_order = [p for p in ("ctrip", "meituan", "fliggy") if p in targets]
    global_run_id_text = str(run_id) if run_id is not None else None

    # 需求2/基线修复：基线必须先于读取加载 —— 语义为「本次读取之前最近一条
    # 成功快照（不限来源，但仅限当日）」。读取器在读取过程中会保存本次快照，
    # 若基线晚于读取加载，将查到本次自己刚存的快照，环比恒为 ±0（自比自）。
    # 当日限定（2026-09-08）：跨天快照不作基线，当天第一次推送显示「凌晨数据」。
    previous_metrics = await _load_previous_metrics(
        deps=deps, targets=targets, anchor=anchor,
    )

    for index, platform in enumerate(read_order):
        target = targets[platform]
        if target.status == "config_required":
            platform_statuses.append("config_required")
            platform_results[platform] = {
                "status": "config_required",
                "summary": target.summary,
            }
            continue
        try:
            if platform == "ctrip":
                payload = await deps.read_ctrip(anchor, global_run_id_text)
            else:
                mounted = await deps.find_mounted_group(
                    platform, config["ctrip_watch_group_key"]
                )
                payload = await deps.read_platform(
                    platform, mounted, global_run_id_text
                )
        except Exception as exc:  # noqa: BLE001 - 单平台失败不阻断其它平台
            logger.error("global push read failed: %s", platform, exc_info=exc)
            status = _human_like_status(exc)
            platform_statuses.append(status)
            platform_results[platform] = {
                "status": status,
                "summary": _sanitize_summary_text(
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                ),
            }
            await _alert(deps, config, platform, store_name, status, webhook_url)
            continue
        platform_payloads[platform] = payload
        # 退单情况（2026-09-02）：推送读取时顺带抓当日退单，落库供飞书问答调用；
        # 与推送内容无关，任何失败不影响主推送（NotReady 静默跳过）。
        from app.domain.refund_orders.service import fetch_refund_orders_silent

        await fetch_refund_orders_silent(platform)
        status = str(payload.get("status") or "success")
        if status not in {"success", "partial", "human_required", "needs_review", "error"}:
            status = "success" if payload else "needs_review"
        platform_statuses.append(status)
        platform_results[platform] = {
            "status": status,
            "snapshot_id": _snapshot_id_from_payload(platform, payload),
        }
        if status in {"error", "human_required", "needs_review"}:
            await _alert(deps, config, platform, store_name, status, webhook_url)
        if index < len(read_order) - 1 and deps.cooldown_seconds > 0:
            await deps.sleep(deps.cooldown_seconds)

    sections = _build_sections(
        platform_payloads, platform_results, previous_metrics=previous_metrics
    )
    header_status = aggregate_run_status(
        platform_statuses=platform_statuses, all_cards_delivered=True
    )
    ctrip_rows = sections.get("ctrip", {}).get("rows", [])
    score_summary = extract_ctrip_score_summary(ctrip_rows)

    cards = build_global_push_cards(
        store_name=store_name,
        read_time=deps.read_time_text or None,
        overall_status=header_status,
        overall_summary=_overall_summary(platform_results),
        score_summary=score_summary,
        platform_sections=list(sections.values()),
        title_prefixes={
            key: config.get(f"card_title_{key}")
            for key in ("business", "promotion", "advertising")
            if config.get(f"card_title_{key}")
        },
        platform_store_names={
            platform: target.store_name
            for platform, target in targets.items()
            if target.store_name
        },
    )

    card_results: dict[str, Any] = {}
    delivered = 0
    total_cards = 0
    if webhook_url:
        for category, card_list in cards.items():
            outcomes = []
            for card in card_list:
                outcome = await deps.push_card(
                    _card_plain_title(card), card, webhook_url
                )
                ok = _push_outcome_status(outcome) == "success"
                outcomes.append("success" if ok else "failed")
                total_cards += 1
                delivered += 1 if ok else 0
            card_results[category] = outcomes
    else:
        card_results["delivery"] = "config_required"

    all_cards_delivered = (
        bool(webhook_url) and total_cards > 0 and delivered == total_cards
    )
    final_status = aggregate_run_status(
        platform_statuses=platform_statuses,
        all_cards_delivered=all_cards_delivered,
    )
    succeeded = final_status in {"success", "config_required"} and all_cards_delivered
    return _result(
        status=final_status,
        platform_results=platform_results,
        card_results=card_results,
        succeeded=succeeded,
        error_summary=None if succeeded else _overall_summary(platform_results),
        score_summary=score_summary,
        cards=cards,
    )


# 收益概览需要计算「较上次」增减幅度的字段（实时/昨日口径）。
# 携程用稳定 field_key；美团/飞猪 field_key 为页面中文标签（或带 section 前缀），
# 故同时按 field_key 末段与 label 匹配。
_COMPARISON_FIELD_KEYS = frozenset({
    "booking_sales",
    "realtime_booking_orders",
    "realtime_in_house_room_nights",
    # 2026-09-21：离店销售额（昨日口径）同步接入「较上次」增减幅度。
    "departure_sales_amount",
    "realtime_sales_amount",
    "booking_orders",
    "in_house_room_nights",
})

# 美团/飞猪中文标签 -> 参与增减幅度的收益指标
_COMPARISON_LABELS = frozenset({
    "销售额",
    "支付订单数",
    "预订订单量",
    "预订订单",
    "预订间夜",
    "预定间夜",
    "预订收入金额",
    "入住间夜",
    "在店间夜",
    # 飞猪在店区（2026-09-02 复审#3）：收益概览模块行要么有差值要么统一
    # 「凌晨数据」，不再与白名单外的「—」混杂。
    "房费收入",
    "间夜单价",
})

_EARLY_MORNING_MARKER = "凌晨数据"

# 环比基线回溯的当日快照条数：飞猪页面偶发漏渲染个别模块（2026-09-08 实测
# 17:47 快照缺 MCI/今日在店），只看最近一条会让这些行误标「凌晨数据」。
_BASELINE_SNAPSHOT_LOOKBACK = 5


def _merge_previous_metrics_from_snapshots(
    snapshots: list[dict[str, Any]] | None,
    *,
    platform: str,
    now: Any = None,
) -> dict[str, Any]:
    """把当日成功快照（新→旧）合并成环比基线：每字段取包含它的最新一条。

    快照序列来自推送读取之前（本次快照尚未入库），跨天快照被
    `_snapshot_is_from_today` 过滤；全部不可用时返回空 dict（首拉口径，
    卡片显示「凌晨数据」）。
    """
    adapter = ADAPTERS_BY_PLATFORM.get(platform)
    if adapter is None or not isinstance(snapshots, list):
        return {}
    merged: dict[str, Any] = {}
    for snapshot in snapshots:
        if not _snapshot_is_from_today(snapshot, now=now):
            continue
        payload = snapshot.get("payload_json") if isinstance(snapshot, dict) else None
        if not isinstance(payload, dict):
            continue
        try:
            rows, _needs = adapter(payload)
        except Exception:  # noqa: BLE001 - 坏快照不阻断基线合并
            logger.debug("baseline adapter parse failed", exc_info=True)
            continue
        accepted, _rejected = normalize_projection_rows(rows)
        for field_key, value in _baseline_values_from_rows(accepted).items():
            if field_key not in merged:
                merged[field_key] = value
    return merged


def _baseline_values_from_rows(accepted_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """适配器行 -> 基线键值。同 field_key 多行（美团多数据集同名指标）时
    优先可计算数值的真实值，占位（如「-元」）会让环比误标「凌晨数据」。"""
    by_key: dict[str, list[Any]] = {}
    for row in accepted_rows:
        field_key = str(row.get("field_key") or "")
        if field_key:
            by_key.setdefault(field_key, []).append(row.get("value"))
    values: dict[str, Any] = {}
    for field_key, candidates in by_key.items():
        if not candidates:
            continue
        values[field_key] = next(
            (candidate for candidate in candidates if _to_number(candidate) is not None),
            candidates[0],
        )
    return values


def _snapshot_is_from_today(
    snapshot: dict[str, Any] | None,
    *,
    now: Any = None,
) -> bool:
    """快照 created_at 是否落在本地今天；跨天快照不作为当日环比基线。

    created_at 为本地 naive datetime（repository `_row_to_dict` isoformat）。
    解析失败按非当日处理（宁缺基线显示「凌晨数据」，不误比跨天量）。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not isinstance(snapshot, dict):
        return False
    try:
        captured = datetime.fromisoformat(str(snapshot.get("created_at") or ""))
    except ValueError:
        return False
    # 落库侧（ebk_snapshots/models.py）钉死 Asia/Shanghai；naive 值按上海时间
    # 解读，带 tz 的值显式转到上海，再与上海“现在”对比。绝不用服务器本地
    # 时区（Docker 默认 UTC 时，北京时间 00:00-08:00 会被误判跨天）。
    shanghai = ZoneInfo("Asia/Shanghai")
    if captured.tzinfo is not None:
        captured = captured.astimezone(shanghai).replace(tzinfo=None)
    current = now or datetime.now(shanghai).replace(tzinfo=None)
    return captured.date() == current.date()


def _inject_comparison(
    accepted_rows: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> None:
    """将上一次运行的增减幅度注入到经营字段行的 comparison 中（就地修改）。

    首拉（无历史快照）时标记「凌晨数据」，不输出数值；
    有历史时输出「数值差 (+百分比%)」；数值相等显示「较上期/较上次无变化」。
    2026-09-04 口径（运营反馈大量「—」）：经营行统一「差值/凌晨数据」不混「—」；
    仅当前值本身是页面占位（如「-间夜」）时保留「—」，表示该格未读到。
    2026-09-11 审查：基线缺失/不可算时，行上已带页面「较上期 X%」的保留页面值
    （真实对比好过「凌晨数据」）；无页面对比才标「凌晨数据」。
    """

    first_pull = not isinstance(previous, dict) or not previous
    for row in accepted_rows:
        if not _is_comparison_row(row):
            continue
        if _to_number(row.get("value")) is None:
            # 当前值非数值（页面占位/文本）：对比列维持「—」，不误标凌晨数据。
            continue
        page_comparison = str(row.get("comparison") or "").strip()
        if first_pull:
            row["comparison"] = page_comparison or _EARLY_MORNING_MARKER
            continue
        previous_value = _previous_value_for(row, previous)
        if previous_value is None:
            row["comparison"] = page_comparison or _EARLY_MORNING_MARKER
            continue
        current_number = _to_number(row.get("value"))
        previous_number = _to_number(previous_value)
        if (
            current_number is not None
            and previous_number is not None
            and current_number == previous_number
        ):
            row["comparison"] = _no_change_marker(str(row.get("platform") or ""))
            continue
        diff = _change_text(row.get("value"), previous_value)
        row["comparison"] = (
            diff if diff is not None else (page_comparison or _EARLY_MORNING_MARKER)
        )


def _no_change_marker(platform: str) -> str:
    """环比无变化文案：跟随各平台卡片的列头口径（美团较上期，其余较上次）。"""

    return "较上期无变化" if platform == "meituan" else "较上次无变化"


def _is_comparison_row(row: dict[str, Any]) -> bool:
    if _is_revenue_row(row):
        return True
    # 2026-09-04 运营反馈：经营分类行统一参与环比，杜绝收益表大量「—」
    # （白名单只覆盖少数标签，名单外行本应同样有差值/凌晨数据）。
    if str(row.get("category") or "") == "business":
        return True
    # 美团流量曝光行（今日实时口径）同样按上一次读取输出较上期幅度（2026-09-01 需求3）。
    return (
        str(row.get("platform") or "") == "meituan"
        and str(row.get("dimension") or "") == "流量趋势"
    )


def _is_revenue_row(row: dict[str, Any]) -> bool:
    field_key = str(row.get("field_key") or "")
    if field_key in _COMPARISON_FIELD_KEYS:
        return True
    tail = field_key.rsplit(".", 1)[-1]
    label = str(row.get("label") or "")
    return tail in _COMPARISON_LABELS or label in _COMPARISON_LABELS


def _previous_value_for(row: dict[str, Any], previous: dict[str, Any]) -> Any:
    field_key = str(row.get("field_key") or "")
    if field_key in previous:
        return previous[field_key]
    tail = field_key.rsplit(".", 1)[-1]
    label = str(row.get("label") or "")
    for candidate in (tail, label):
        if candidate and candidate in previous:
            return previous[candidate]
    # previous 的 key 也可能带 section 前缀，按末段匹配
    for key, value in previous.items():
        if str(key).rsplit(".", 1)[-1] in {tail, label} and tail:
            return value
    return None


def _change_text(current: Any, previous: Any) -> str | None:
    current_number = _to_number(current)
    previous_number = _to_number(previous)
    if current_number is None or previous_number is None:
        return None
    diff = current_number - previous_number
    amount = f"{int(diff):+d}" if diff == int(diff) else f"{diff:+.2f}"
    if previous_number == 0:
        return amount
    pct = diff / abs(previous_number) * 100
    return f"{amount} ({pct:+.1f}%)"


def _numeric_diff(current: Any, previous: Any) -> str | None:
    current_number = _to_number(current)
    previous_number = _to_number(previous)
    if current_number is None or previous_number is None:
        return None
    diff = current_number - previous_number
    if diff == int(diff):
        return f"{int(diff):+d}"
    return f"{diff:+.2f}"


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = (
        str(value or "")
        .replace(",", "")
        .replace("%", "")
        .replace("元", "")
        .replace("单", "")
        .replace("间夜", "")
        .replace("人", "")
        .replace("分", "")
        .strip()
    )
    if not text:
        return None
    # 携程竞争画像销售额为「23.59万」格式（2026-09-01 实测），换算后参与环比。
    multiplier = 1.0
    if text.endswith("万"):
        multiplier, text = 1_0000.0, text[:-1]
    elif text.endswith("亿"):
        multiplier, text = 1_0000_0000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None





async def _load_previous_metrics(
    *,
    deps: GlobalPushDeps,
    targets: dict[str, "GlobalPushPlatformTarget"],
    anchor: Any,
) -> dict[str, dict[str, Any]]:
    """读取前加载每个已解析目标的「上一次成功快照」指标，返回 field_key -> value 映射。

    须在读循环之前调用：基线语义为本次读取之前最近一条成功快照（不限来源，
    自动推送/定时看板/人工刷新均可，但仅限当日——跨天快照被
    `_snapshot_is_from_today` 过滤，当天第一次推送显示「凌晨数据」），
    此时本次快照尚未入库。
    """

    if deps.get_previous_metrics is None:
        return {}
    account_ids = _platform_account_ids(targets, anchor)
    result: dict[str, dict[str, Any]] = {}
    for platform, target in targets.items():
        if target.status != "resolved":
            continue
        account_id = account_ids.get(platform)
        if not account_id:
            continue
        try:
            previous = await deps.get_previous_metrics(platform, account_id)
        except Exception:  # noqa: BLE001 - 缺历史数据时忽略
            logger.debug("load previous metrics failed: %s", platform, exc_info=True)
            continue
        if isinstance(previous, dict) and previous:
            result[platform] = previous
    return result


def _platform_account_ids(
    targets: dict[str, "GlobalPushPlatformTarget"], anchor: Any
) -> dict[str, str]:
    ids: dict[str, str] = {}
    anchor_account = str(getattr(anchor, "platform_account_id", "") or "")
    if anchor_account:
        ids["ctrip"] = anchor_account
    for platform, target in targets.items():
        account_id = str(getattr(target, "account_id", "") or "")
        if account_id:
            ids[platform] = account_id
    return ids


def _build_sections(
    platform_payloads: dict[str, Any],
    platform_results: dict[str, Any],
    *,
    previous_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for platform, result in platform_results.items():
        payload = platform_payloads.get(platform)
        if not isinstance(payload, dict):
            sections[platform] = {
                "platform": platform,
                "status": result.get("status") or "failed",
                "rows": [],
                "summary": result.get("summary", ""),
            }
            continue
        adapter = ADAPTERS_BY_PLATFORM.get(platform)
        if adapter is None:
            continue
        rows, needs_review = adapter(payload)
        accepted, _rejected = normalize_projection_rows(rows)
        _inject_comparison(accepted, (previous_metrics or {}).get(platform))
        result = platform_results.get(platform, {})
        sections[platform] = {
            "platform": platform,
            "status": result.get("status") or "success",
            "rows": accepted,
            "summary": (
                f"证据不足已过滤 {sum(needs_review.values())} 个数据集（needs_review）"
                if needs_review
                else result.get("summary", "")
            ),
        }
    return sections


def _snapshot_id_from_payload(platform: str, payload: dict[str, Any]) -> Any:
    direct = payload.get("snapshot_id") or payload.get("run_id")
    if direct is not None:
        return direct
    if platform == "ctrip":
        persistence = payload.get("persistence")
        if isinstance(persistence, dict):
            return persistence.get("run_id")
    return None


def _human_like_status(exc: Exception) -> str:
    text = str(exc).lower()
    if "human_required" in text or "login" in text or "验证" in text:
        return "human_required"
    return "failed"


async def _alert(
    deps: GlobalPushDeps,
    config: dict[str, Any],
    platform: str,
    store_name: str,
    status: str,
    webhook_url: str,
) -> None:
    try:
        await deps.send_failure_alert(
            str(config.get("id")), platform, store_name, status, status, webhook_url
        )
    except Exception:  # noqa: BLE001 - 告警失败不影响主流程
        logger.warning("global push failure alert failed", exc_info=True)


def _overall_summary(platform_results: dict[str, Any]) -> str:
    parts = []
    for platform in ("ctrip", "meituan", "fliggy"):
        result = platform_results.get(platform)
        if not result:
            continue
        label = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}[platform]
        parts.append(f"{label}：{result.get('status')}")
    return "；".join(parts)


def _card_plain_title(card: dict[str, Any]) -> str:
    return str(card.get("header", {}).get("title", {}).get("content") or "EBK 全局推送")


def _result(
    *,
    status: str,
    platform_results: dict[str, Any] | None = None,
    card_results: dict[str, Any] | None = None,
    succeeded: bool = False,
    error_summary: str | None = None,
    score_summary: dict[str, str] | None = None,
    cards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "platform_results": platform_results or {},
        "card_results": card_results or {},
        "succeeded": succeeded,
        "error_summary": error_summary,
        "score_summary": score_summary or {},
        "cards": cards or {},
    }
