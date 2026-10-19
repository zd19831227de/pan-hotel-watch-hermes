"""编排定时和手动触发的竞品价格监控任务。

本模块位于监控组配置与携程公共页面读取器之间：解析浏览器绑定、读取已配置酒店
目标、串行保护浏览器 profile、调用只读浏览器服务、持久化结果、按需推送飞书
卡片，并对已成功入库的 run 执行价格变化提醒评估。

模块中的关键变量引用：

* ``group`` 是当前监控组记录，包含调度和 webhook 配置。
* ``watch_group_key`` 是 ``group`` 在持久化层和 API 中的稳定标识。
* ``browser_binding`` 是绑定解析结果；``selection`` 是实际选中的账号/session，
  并可能持有浏览器池 lease。
* ``configured`` 保存从当前数据库配置解析出的本店 URL、竞品 URL 和显示别名。
* ``result`` 是浏览器读取器的结构化输出；``persistence`` 是已保存 run 的元数据；
  ``push_payload`` 是飞书推送结果。

调度器不会写入 OTA 价格，只负责读取、持久化、通知和建议性质的提醒评估。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.domain.browser.ctrip_ebooking import (
    CtripBrowserProfileLockedError,
    get_ctrip_ebooking_service,
)
from app.domain.competitor_prices.browser_binding import (
    resolve_competitor_browser_binding_for_group,
)
from app.domain.competitor_prices.browser_pool import (
    record_competitor_browser_pool_run_result,
    release_competitor_browser_pool_lease,
)
from app.domain.competitor_prices.public_browser_poll_gate import competitor_profile_poll
from app.domain.competitor_prices.messages import (
    build_competitor_price_detail_table_message,
    build_competitor_price_detail_table_card,
    build_competitor_price_detail_table_cards,
    build_competitor_price_feishu_card,
    build_competitor_price_feishu_message,
    build_competitor_price_feishu_mobile_cards,
)
from app.domain.competitor_prices.repository import (
    cleanup_expired_competitor_price_data,
    competitor_poll_retry_cooldown_summary,
    get_competitor_watch_group_detail,
    get_latest_competitor_price_run_meta,
    list_due_competitor_watch_groups,
    persistence_failure_result,
    resolve_configured_competitor_targets,
    save_ctrip_competitor_price_result,
    update_competitor_price_run_push_result,
)
from app.domain.competitor_prices.scheduler_status import (
    write_competitor_price_scheduler_heartbeat,
)
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
from app.domain.competitor_prices.price_change_pricing_orchestration import (
    try_auto_preview_and_approval_for_alert,
)
from app.domain.price_change_alerts.service import evaluate_price_change_alert

logger = logging.getLogger(__name__)
# 单次真实浏览器读取的硬超时，用于保护 Worker 循环，并保证页面卡死后最终释放
# browser-pool lease。
COMPETITOR_PRICE_READ_TIMEOUT_SECONDS = 240


class CompetitorPriceScheduler:
    """以只读方式轮询已配置的携程公共竞品监控组。

    ``tick_seconds`` 控制外层 Worker 心跳间隔；每个监控组是否到期及冷却时间仍由
    repository 配置计算。Scheduler 实例在 tick 之间不保存业务状态，浏览器/session
    状态和最近 run 元数据分别保存在专用存储中。
    """

    def __init__(self, *, tick_seconds: int = 60) -> None:
        self.tick_seconds = tick_seconds

    async def run_forever(self) -> None:
        """持续运行 scheduler tick，并记录各阶段 heartbeat。"""
        _record_scheduler_heartbeat("started")
        while True:
            _record_scheduler_heartbeat("tick_started")
            try:
                result = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _record_scheduler_heartbeat("tick_failed", status="error", error=exc)
                logger.exception("competitor price scheduler tick failed; continuing")
                await asyncio.sleep(self.tick_seconds)
                continue
            _record_scheduler_heartbeat("tick_finished", tick_result=result)
            if result["due_group_count"]:
                logger.info("competitor price scheduler tick: %s", result)
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> dict[str, Any]:
        """轮询当前全部到期监控组，并返回一次汇总 tick 结果。

        ``groups`` 来自持久化调度状态。内部 ``poll_group`` 把 profile 锁冲突和
        未预期异常转换为逐组结果，避免一个监控组失败时取消其他组。
        ``asyncio.gather`` 允许不同浏览器 profile 并行执行；``_poll_group`` 内的
        ``competitor_profile_poll`` 仍会把解析到同一账号/session 的监控组串行化。
        """
        groups = await list_due_competitor_watch_groups()
        async def poll_group(group: dict[str, Any]) -> dict[str, Any]:
            try:
                return await self._poll_group(
                    group,
                    push_to_feishu=True,
                    actor_id="scheduler",
                    source="scheduler",
                )
            except CtripBrowserProfileLockedError as exc:
                logger.warning("competitor price scheduled browser profile is locked: %s", exc)
                return {
                    "watch_group_key": group.get("group_key"),
                    "status": "human_required",
                    "summary": str(exc),
                    "error_type": type(exc).__name__,
                }
            except Exception as exc:
                logger.exception("competitor price scheduled poll failed")
                return {
                    "watch_group_key": group.get("group_key"),
                    "status": "failed",
                    "summary": str(exc)[:300],
                    "error_type": type(exc).__name__,
                }

        results = list(await asyncio.gather(*(poll_group(group) for group in groups)))
        cleanup = await cleanup_expired_competitor_price_data()
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "due_group_count": len(groups),
            "results": results,
            "cleanup": cleanup.model_dump(mode="json"),
        }

    async def run_watch_group_now(
        self,
        watch_group_key: str,
        *,
        push_to_feishu: bool = True,
        actor_id: str = "operator",
        source: str = "api",
    ) -> dict[str, Any]:
        """让指定监控组执行同一套受保护流程。

        ``watch_group_key`` 用于解析数据库记录；``actor_id`` 和 ``source`` 写入
        审计数据；``push_to_feishu`` 只控制通知发送。手动运行不会绕过浏览器
        profile 锁、配置校验或调价安全边界。
        """
        group = await get_competitor_watch_group_detail(
            watch_group_key=watch_group_key,
            include_secrets=True,
        )
        if group is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到竞品监控组。",
            }
        if not group.get("enabled", True):
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": "竞品监控组已停用。",
            }
        try:
            return await self._poll_group(
                group,
                push_to_feishu=push_to_feishu,
                actor_id=actor_id,
                source=source,
            )
        except CtripBrowserProfileLockedError as exc:
            logger.warning("competitor price browser profile is locked: %s", exc)
            return {
                "watch_group_key": watch_group_key,
                "status": "human_required",
                "summary": str(exc),
                "error_type": type(exc).__name__,
                "browser_session_id": group.get("browser_session_id"),
                "platform_account_id": group.get("platform_account_id"),
                "next_action": "确认没有其他读取任务正在运行；如浏览器窗口已卡住，关闭对应 Chrome 后重新打开登录窗口。",
            }
        except Exception as exc:
            logger.exception("competitor price manual poll failed")
            return {
                "watch_group_key": watch_group_key,
                "status": "failed",
                "summary": f"竞品价格读取失败：{type(exc).__name__}: {str(exc)[:240]}",
                "error_type": type(exc).__name__,
                "persistence": persistence_failure_result(
                    watch_group_key=watch_group_key,
                    exc=exc,
                ),
            }

    async def _poll_group(
        self,
        group: dict[str, Any],
        *,
        push_to_feishu: bool,
        actor_id: str,
        source: str,
    ) -> dict[str, Any]:
        """执行一次完整的读取、持久化、推送和提醒评估。

        ``selection`` 有意保持可重新赋值：浏览器池 failover 可能把它替换为第二个
        账号/session，之后的持久化、证据、推送和 lease 释放都必须引用最终的
        ``selection``。取得 pool lease 后的 await 统一经过
        ``_await_with_pool_lease_release`` 或 ``_await_competitor_price_read``，
        防止异常导致 lease 长期占用。
        """
        watch_group_key = str(group["group_key"])
        # ``cooldown_summary`` 根据最近一次持久化 run 计算。最近仍为待复核或错误时，
        # 不应在两个配置轮询时段之间持续重新打开同一浏览器 profile。
        cooldown_summary = competitor_poll_retry_cooldown_summary(
            group,
            await get_latest_competitor_price_run_meta(watch_group_key=watch_group_key),
        )
        if cooldown_summary:
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": cooldown_summary,
                "browser_session_id": group.get("browser_session_id"),
                "platform_account_id": group.get("platform_account_id"),
                "next_action": "系统将在冷却结束后自动重试；也可在控制台手动「立即读取」。",
            }
        platform_account_id = str(group.get("platform_account_id") or "")
        # ``browser_binding`` 可能表示固定组绑定，也可能表示已租用的浏览器池成员。
        # 解析成功后，真实浏览器调用只使用 ``selection``。
        browser_binding = await resolve_competitor_browser_binding_for_group(
            group=group,
            actor_id=actor_id,
        )
        if not browser_binding.ok or browser_binding.selection is None:
            return {
                "watch_group_key": watch_group_key,
                "status": browser_binding.status,
                "summary": browser_binding.summary,
                "browser_session_id": group.get("browser_session_id"),
                "platform_account_id": platform_account_id,
                "evidence": browser_binding.evidence,
                "next_action": browser_binding.next_action,
            }
        selection = browser_binding.selection
        # 酒店 URL 必须按实际选中的账号解析，不能使用监控组中保存的池占位账号。
        configured = await _await_with_pool_lease_release(
            resolve_configured_competitor_targets(
                platform_account_id=selection.platform_account_id,
                watch_group_key=watch_group_key,
            ),
            selection,
        )
        if configured is None:
            if _selection_uses_pool(selection):
                await release_competitor_browser_pool_lease(selection=selection)
            return {
                "watch_group_key": watch_group_key,
                "status": "config_required",
                "summary": (
                    "竞品监控需要正好 1 个启用本店和至少 1 个启用竞品酒店。"
                ),
                "browser_session_id": selection.browser_session_id,
                "platform_account_id": selection.platform_account_id,
            }

        async def read_selected_prices() -> Any:
            # profile gate 必须绑定当前 selection；pool failover 后 selection 会变更，
            # 因此每次真实浏览器读取都重新进入对应 profile 的 gate。
            async with competitor_profile_poll(
                selection.platform_account_id,
                selection.browser_session_id,
                enabled=True,
            ):
                # 一个 result 可以包含多个酒店/日期快照，但读取器会使用一个共享页面依次采集。
                return await _await_competitor_price_read(
                    get_ctrip_ebooking_service().read_competitor_prices(
                        platform_account_id=selection.platform_account_id,
                        browser_session_id=selection.browser_session_id,
                        own_hotel_url=configured.own_hotel_url,
                        competitor_urls=configured.competitor_urls,
                        check_dates=[],
                        actor_id=actor_id,
                        push_to_feishu=False,
                    ),
                    selection,
                )

        result = await _await_with_pool_lease_release(
            _await_competitor_price_read(read_selected_prices(), selection),
            selection,
        )
        await _await_with_pool_lease_release(
            record_competitor_browser_pool_run_result(
                selection=selection,
                status=result.status,
                trusted_rate_count=_trusted_rate_count(result),
            ),
            selection,
        )
        if _should_fail_over_pool_read(group, selection, result.status):
            # 解析下一浏览器池成员前先释放失败成员。从重新赋值开始，后续引用都
            # 必须使用新的 ``selection`` 和 ``configured``。
            await release_competitor_browser_pool_lease(selection=selection)
            retry_binding = await resolve_competitor_browser_binding_for_group(
                group=group,
                actor_id=actor_id,
            )
            if retry_binding.ok and retry_binding.selection is not None:
                selection = retry_binding.selection
                configured = await _await_with_pool_lease_release(
                    resolve_configured_competitor_targets(
                        platform_account_id=selection.platform_account_id,
                        watch_group_key=watch_group_key,
                    ),
                    selection,
                )
                if configured is None:
                    if _selection_uses_pool(selection):
                        await release_competitor_browser_pool_lease(selection=selection)
                    return {
                        "watch_group_key": watch_group_key,
                        "status": "config_required",
                        "summary": (
                            "竞品监控需要正好 1 个启用本店和至少 1 个启用竞品酒店。"
                        ),
                        "browser_session_id": selection.browser_session_id,
                        "platform_account_id": selection.platform_account_id,
                    }
                result = await _await_with_pool_lease_release(
                    _await_competitor_price_read(read_selected_prices(), selection),
                    selection,
                )
                await _await_with_pool_lease_release(
                    record_competitor_browser_pool_run_result(
                        selection=selection,
                        status=result.status,
                        trusted_rate_count=_trusted_rate_count(result),
                    ),
                    selection,
                )
        # 别名是当前配置中的展示信息，不改变已解析价格或目标身份。
        result.data["target_aliases"] = _configured_target_aliases(configured)
        _apply_configured_aliases_to_snapshots(result.data)
        result.data["browser_selection"] = selection.evidence()
        result.evidence["browser_selection"] = selection.evidence()
        # 先持久化再推送飞书，确保 webhook 失败不会丢失可信读取及审计证据。
        persistence = await _await_with_pool_lease_release(
            save_ctrip_competitor_price_result(
                result_status=result.status,
                summary=result.summary,
                platform_account_id=selection.platform_account_id,
                browser_session_id=selection.browser_session_id,
                actor_id=actor_id,
                data=result.data,
                evidence=result.evidence,
                watch_group_key=watch_group_key,
                source=source,
            ),
            selection,
        )
        persistence_payload = persistence.model_dump(mode="json")
        push_payload = (
            await _await_with_pool_lease_release(
                _push_competitor_watch(group, result),
                selection,
            )
            if push_to_feishu
            else {
                "status": "skipped",
                "summary": "本次只读取，未发送飞书推送。",
                "watch_group_key": watch_group_key,
            }
        )
        if persistence.run_id is not None:
            await _await_with_pool_lease_release(
                update_competitor_price_run_push_result(
                    run_id=persistence.run_id,
                    push_result=push_payload,
                ),
                selection,
            )
        # 提醒评估只消费成功持久化的 run ID。它可以生成建议或参考信息，但不会
        # 直接写入 OTA 价格。
        price_change_alert_payload = (
            await _await_with_pool_lease_release(
                _evaluate_price_change_alert_after_run(
                    watch_group_key=watch_group_key,
                    current_run_id=persistence.run_id,
                    push_to_feishu=push_to_feishu,
                    actor_id=actor_id,
                ),
                selection,
            )
            if persistence.run_id is not None and result.status == "success"
            else {
                "status": "skipped",
                "summary": "本轮没有成功入库的可信价格 run，跳过价格变化提醒。",
            }
        )
        if _selection_uses_pool(selection):
            await release_competitor_browser_pool_lease(selection=selection)
        return {
            "watch_group_key": watch_group_key,
            "status": result.status,
            "summary": result.summary,
            "browser_session_id": selection.browser_session_id,
            "platform_account_id": selection.platform_account_id,
            "feishu_push": push_payload,
            "price_change_alert": price_change_alert_payload,
            "persistence": persistence_payload,
        }


async def _push_competitor_watch(group: dict[str, Any], result: Any) -> dict[str, Any]:
    """为一次读取结果构建并发送已配置的飞书视图。

    ``targets`` 保存从 ``group`` 规范化得到的 webhook/layout 项；``fallback``
    和 ``detail_fallback`` 是纯文本消息；``cards`` 保存一个或多个互动卡片 payload；
    ``target_results`` 保存脱敏后的发送结果，并在返回 scheduler/API 层前主动移除
    原始 evidence。
    """
    targets = _competitor_watch_push_targets(group)
    watch_group_key = group.get("group_key")
    if not targets:
        return {
            "status": "config_required",
            "summary": "未配置竞品监控飞书推送地址，已保存本次读取结果。",
            "watch_group_key": watch_group_key,
            "target_count": 0,
            "targets": [],
        }

    title = str(group.get("brief_title") or group.get("name") or "携程竞品价格盯盘")
    push_date_scope = str(group.get("push_date_scope") or "").strip() or "today_tomorrow"
    fallback = build_competitor_price_feishu_message(
        status=result.status,
        data=result.data,
        push_date_scope=push_date_scope,
    )
    detail_fallback = build_competitor_price_detail_table_message(
        status=result.status,
        data=result.data,
        push_date_scope=push_date_scope,
    )
    target_results: list[dict[str, Any]] = []
    for target in targets:
        if target["layout"] == "desktop_table":
            cards = build_competitor_price_detail_table_cards(
                status=result.status,
                data=result.data,
                title=title,
                push_date_scope=push_date_scope,
            )
            card_results: list[dict[str, Any]] = []
            for card in cards:
                card_results.append(
                    await push_interactive_card_to_feishu_webhook(
                        title=title,
                        card=card,
                        webhook_url=target["webhook_url"],
                        fallback_text=None,
                    )
                )
            if any(item.status == "success" for item in card_results):
                push_result = card_results[0]
                payload = push_result.model_dump(mode="json")
                payload["status"] = "success"
                payload["summary"] = (
                    f"已发送 {sum(1 for item in card_results if item.status == 'success')}/"
                    f"{len(card_results)} 张桌面明细卡片。"
                )
                payload["card_count"] = len(card_results)
                payload["card_results"] = [
                    {
                        "status": item.status,
                        "feishu_code": item.data.get("feishu_code"),
                        "feishu_msg": item.data.get("feishu_msg"),
                        "msg_type": item.data.get("msg_type"),
                    }
                    for item in card_results
                ]
            else:
                push_result = await push_interactive_card_to_feishu_webhook(
                    title=title,
                    card=cards[0],
                    webhook_url=target["webhook_url"],
                    fallback_text=detail_fallback,
                )
                payload = push_result.model_dump(mode="json")
                payload["card_count"] = len(cards)
                payload["card_results"] = [
                    {
                        "status": item.status,
                        "feishu_code": item.data.get("feishu_code"),
                        "feishu_msg": item.data.get("feishu_msg"),
                        "msg_type": item.data.get("msg_type"),
                    }
                    for item in card_results
                ]
        else:
            cards = build_competitor_price_feishu_mobile_cards(
                status=result.status,
                data=result.data,
                title=title,
                push_date_scope=push_date_scope,
            )
            card_results: list[dict[str, Any]] = []
            for card in cards:
                card_results.append(
                    await push_interactive_card_to_feishu_webhook(
                        title=title,
                        card=card,
                        webhook_url=target["webhook_url"],
                        fallback_text=None,
                    )
                )
            if any(item.status == "success" for item in card_results):
                push_result = card_results[0]
                payload = push_result.model_dump(mode="json")
                payload["status"] = "success"
                payload["summary"] = (
                    f"已发送 {sum(1 for item in card_results if item.status == 'success')}/"
                    f"{len(card_results)} 张手机全量卡片。"
                )
                payload["card_count"] = len(card_results)
                payload["card_results"] = [
                    {
                        "status": item.status,
                        "feishu_code": item.data.get("feishu_code"),
                        "feishu_msg": item.data.get("feishu_msg"),
                        "msg_type": item.data.get("msg_type"),
                    }
                    for item in card_results
                ]
            else:
                push_result = await push_interactive_card_to_feishu_webhook(
                    title=title,
                    card=cards[0],
                    webhook_url=target["webhook_url"],
                    fallback_text=fallback,
                )
                payload = push_result.model_dump(mode="json")
                payload["card_count"] = len(cards)
                payload["card_results"] = [
                    {
                        "status": item.status,
                        "feishu_code": item.data.get("feishu_code"),
                        "feishu_msg": item.data.get("feishu_msg"),
                        "msg_type": item.data.get("msg_type"),
                    }
                    for item in card_results
                ]
        payload["target"] = target["target"]
        card_push = payload.get("evidence", {}).get("card_push") if isinstance(payload.get("evidence"), dict) else None
        if isinstance(card_push, dict):
            payload["card_push"] = {
                "status": card_push.get("status"),
                "feishu_code": (card_push.get("data") or {}).get("feishu_code"),
                "feishu_msg": (card_push.get("data") or {}).get("feishu_msg"),
            }
        payload.pop("evidence", None)
        target_results.append(payload)

    failed_count = sum(1 for item in target_results if item.get("status") != "success")
    return {
        "status": "success" if failed_count == 0 else "partial",
        "summary": (
            f"已推送竞品盯盘卡片：成功 {len(target_results) - failed_count}/"
            f"{len(target_results)} 个飞书目标。"
        ),
        "watch_group_key": watch_group_key,
        "target_count": len(target_results),
        "failed_target_count": failed_count,
        "targets": target_results,
    }


def _competitor_watch_push_targets(group: dict[str, Any]) -> list[dict[str, str]]:
    """把监控组中的非空 webhook 转换为推送目标描述。"""
    mobile_webhook = str(group.get("feishu_mobile_webhook_url") or "").strip()
    targets: list[dict[str, str]] = []
    if mobile_webhook:
        targets.append(
            {
                "target": "mobile",
                "webhook_url": mobile_webhook,
                "layout": "mobile_blocks",
            }
        )
    return targets


def _configured_target_aliases(configured: Any) -> dict[str, str]:
    """把目标 role 映射到操作员配置的本店/竞品显示别名。"""
    aliases: dict[str, str] = {}
    own_alias = getattr(configured, "own_hotel_alias", None)
    if isinstance(own_alias, str) and own_alias.strip():
        aliases["own"] = own_alias.strip()
    competitor_aliases = getattr(configured, "competitor_aliases", [])
    if isinstance(competitor_aliases, list):
        for index, alias in enumerate(competitor_aliases, start=1):
            if isinstance(alias, str) and alias.strip():
                aliases[f"competitor_{index}"] = alias.strip()
    return aliases


def _apply_configured_aliases_to_snapshots(data: dict[str, Any]) -> None:
    """把 role 别名复制到今天及嵌套明天的快照视图。

    ``data['target_aliases']`` 以 role 为键；``data['snapshots']`` 的修改仅用于
    展示，不改变已解析酒店身份、价格和来源证据。
    """
    aliases = data.get("target_aliases")
    snapshots = data.get("snapshots")
    if not isinstance(aliases, dict) or not isinstance(snapshots, list):
        return
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        alias = aliases.get(str(snapshot.get("role") or ""))
        if isinstance(alias, str) and alias.strip():
            snapshot["configured_alias"] = alias.strip()
            tomorrow = snapshot.get("tomorrow")
            if isinstance(tomorrow, dict):
                tomorrow["configured_alias"] = alias.strip()


def _should_fail_over_pool_read(
    group: dict[str, Any],
    selection: Any,
    result_status: str,
) -> bool:
    """判断浏览器池读取失败或需人工处理后，是否允许换一个成员重试。"""
    if not bool(group.get("pool_failover_enabled")):
        return False
    if str(result_status or "") not in {"failed", "human_required"}:
        return False
    return _selection_uses_pool(selection)


def _selection_uses_pool(selection: Any) -> bool:
    """从 selection evidence 读取规范化后的 ``pool_enabled`` 标志。"""
    evidence = selection.evidence() if hasattr(selection, "evidence") else {}
    return bool(evidence.get("pool_enabled"))


async def _await_with_pool_lease_release(awaitable: Any, selection: Any) -> Any:
    """等待一个操作，发生异常时先释放 pool lease 再重新抛出。

    ``awaitable`` 可以是配置解析、持久化、推送或提醒任务。成功时 lease 仍由
    调用方持有，等整个监控组流程完成后统一释放。
    """
    try:
        return await awaitable
    except asyncio.CancelledError:
        if _selection_uses_pool(selection):
            await release_competitor_browser_pool_lease(selection=selection)
        raise
    except Exception:
        if _selection_uses_pool(selection):
            await release_competitor_browser_pool_lease(selection=selection)
        raise


async def _await_competitor_price_read(awaitable: Any, selection: Any) -> Any:
    """应用浏览器读取超时；调用方 wrapper 负责统一释放 pool lease。"""
    del selection
    try:
        return await asyncio.wait_for(
            awaitable,
            timeout=COMPETITOR_PRICE_READ_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise TimeoutError("Ctrip competitor price read timed out.") from exc


def _trusted_rate_count(result: Any) -> int | None:
    """从浏览器结果的 evidence map 读取可信 DOM 房价数量。"""
    evidence = getattr(result, "evidence", None)
    if not isinstance(evidence, dict):
        return None
    value = evidence.get("trusted_dom_rate_count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _evaluate_price_change_alert_after_run(
    *,
    watch_group_key: str,
    current_run_id: int,
    push_to_feishu: bool,
    actor_id: str,
) -> dict[str, Any]:
    """评估一个已持久化成功 run，并规范化建议类输出。

    ``current_run_id`` 是提醒服务消费的可信持久化 run；``payload`` 是序列化结果；
    ``alert_status`` 决定是否考虑下游调价审批编排。该编排仍受仓库调价安全契约和
    人工确认要求约束。
    """
    try:
        result = await evaluate_price_change_alert(
            watch_group_key=watch_group_key,
            current_run_id=current_run_id,
            push_to_feishu=push_to_feishu,
            actor_id=actor_id,
        )
    except Exception as exc:
        logger.exception("price-change alert evaluation failed")
        return {
            "status": "failed",
            "summary": f"价格变化提醒检测失败：{type(exc).__name__}: {str(exc)[:200]}",
        }
    payload = result.model_dump(mode="json")
    alert_status = str(getattr(result, "status", None) or payload.get("status") or "")
    if alert_status == "suggestion_ready":
        payload["pricing_approval_auto_flow"] = await try_auto_preview_and_approval_for_alert(
            alert_record=payload,
            watch_group_key=watch_group_key,
            actor_id=actor_id,
        )
    else:
        payload["pricing_approval_auto_flow"] = {
            "outcome": "skipped",
            "reason": "alert_not_suggestion_ready",
            "alert_status": alert_status,
        }
    return payload


def _record_scheduler_heartbeat(
    event: str,
    *,
    status: str = "running",
    tick_result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    """尽力写入 heartbeat；写入失败不能停止 Worker 循环。"""
    try:
        write_competitor_price_scheduler_heartbeat(
            event,
            status=status,
            tick_result=tick_result,
            error=error,
        )
    except Exception:
        logger.warning("failed to write competitor price scheduler heartbeat", exc_info=True)
