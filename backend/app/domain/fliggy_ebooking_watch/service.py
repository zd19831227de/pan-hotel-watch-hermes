from __future__ import annotations

import logging
from typing import Any

from app.domain.browser.sessions import (
    get_browser_session_id_for_platform_account,
    get_browser_session_store,
)
from app.domain.ctrip_ebooking_watch.push_throttle import record_push
from app.domain.ctrip_ebooking_watch.repository import (
    get_data_center_watch_group_runtime,
)
from app.domain.ebk_snapshots.ebk_snapshot_reader import read_fliggy_platform_snapshot
from app.domain.ebk_snapshots.feishu_push import (
    build_platform_failure_alert_card,
    build_platform_failure_alert_text,
    build_platform_snapshot_feishu_card,
    build_platform_snapshot_feishu_text,
)
from app.domain.fliggy_ebooking_watch.repository import (
    FliggyWatchGroupRuntime,
    get_fliggy_watch_group_runtime,
    mark_fliggy_watch_group_polled,
)
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
from app.domain.operator_payload import sanitize_operator_payload
from app.domain.platform_ebk_watch.push_policy import (
    platform_throttle_key,
    record_failure_alert,
    should_send_failure_alert,
)

logger = logging.getLogger(__name__)
_PUSH_TITLE = "飞猪 EBK 数据中心盯盘"
_TRUSTED_PUSH_STATUSES = frozenset({"success", "partial"})


class FliggyEbookingWatchService:
    """飞猪 EBK 定时/手动抓取：读取 → 统一快照持久化 → 飞书卡片推送。

    浏览器单实例约束（AGENTS.md Runtime Notes）：读取一律通过
    ``FliggyEbookingBrowserService._get_or_create_page``（read_dataset 内部含
    复用/CDP/跨进程锁逻辑），本服务不直接 launch Chrome。

    投递与失败告警（设计 platform-ebk-watch-anchor-on-ctrip-group §3.4/§5 P0）：
    - 群与机器人地址完全继承锚定携程组（``ctrip_watch_group_key``）；
    - 仅 success/partial 推完整盯盘卡，并按平台前缀 key 记录推送节流；
    - 失败态不发完整卡，按冷却窗口（默认 60 分钟）发一张异常提醒到携程组机器人。
    """

    async def run_watch_group_now(
        self,
        *,
        watch_group_key: str,
        actor_id: str = "operator",
        source: str = "api",
        push_to_feishu: bool = True,
    ) -> dict[str, Any]:
        group = await get_fliggy_watch_group_runtime(watch_group_key=watch_group_key)
        if group is None:
            return {
                "watch_group_key": watch_group_key,
                "status": "not_found",
                "summary": "未找到飞猪 EBK 监控组。",
            }
        if not group.enabled:
            return {
                "watch_group_key": watch_group_key,
                "status": "skipped",
                "summary": "飞猪 EBK 监控组已停用。",
            }
        return await self.run_watch_group(
            group=group,
            actor_id=actor_id,
            source=source,
            push_to_feishu=push_to_feishu,
        )

    async def run_watch_group(
        self,
        *,
        group: FliggyWatchGroupRuntime,
        actor_id: str,
        source: str,
        push_to_feishu: bool = True,
    ) -> dict[str, Any]:
        browser_session_id = await _resolve_fliggy_browser_session_id(group)
        if not browser_session_id:
            return await self._finalize(
                group=group,
                result={
                    "status": "human_required",
                    "summary": "未绑定可用的飞猪 EBK 浏览器账号。",
                    "snapshot_id": None,
                    "dataset_count": 0,
                    "payload": {},
                },
                actor_id=actor_id,
                source=source,
                push_to_feishu=push_to_feishu,
            )
        if not _browser_session_registered(browser_session_id):
            return await self._finalize(
                group=group,
                result={
                    "status": "human_required",
                    "summary": "飞猪 EBK 浏览器会话未注册，请先在浏览器管理中登录飞猪 EBK 账号。",
                    "snapshot_id": None,
                    "dataset_count": 0,
                    "payload": {},
                },
                actor_id=actor_id,
                source=source,
                push_to_feishu=push_to_feishu,
            )

        try:
            read_result = await read_fliggy_platform_snapshot(
                platform_account_id=group.platform_account_id,
                browser_session_id=browser_session_id,
                actor_id=actor_id,
                trigger_via="console" if source == "api" else source,
            )
        except Exception as exc:
            logger.exception("fliggy ebooking watch read failed")
            read_result = {
                "status": "error",
                "summary": f"飞猪 EBK 读取失败：{type(exc).__name__}: {str(exc)[:240]}",
                "snapshot_id": None,
                "dataset_count": 0,
                "payload": {},
            }
        return await self._finalize(
            group=group,
            result=read_result,
            actor_id=actor_id,
            source=source,
            push_to_feishu=push_to_feishu,
        )

    async def _finalize(
        self,
        *,
        group: FliggyWatchGroupRuntime,
        result: dict[str, Any],
        actor_id: str,
        source: str,
        push_to_feishu: bool,
    ) -> dict[str, Any]:
        # Only advance the schedule cursor after a trusted snapshot was persisted.
        # human_required/error results must remain due for a later retry.
        result_status = str(result.get("status") or "error")
        if result_status in _TRUSTED_PUSH_STATUSES and result.get("snapshot_id") is not None:
            await mark_fliggy_watch_group_polled(watch_group_key=group.group_key)
        push_result: dict[str, Any] = {
            "status": "skipped",
            "summary": "本次读取未发送飞书推送。",
            "watch_group_key": group.group_key,
        }
        if push_to_feishu:
            if result_status in _TRUSTED_PUSH_STATUSES:
                push_result = await _safe_push_snapshot_to_feishu(group, result, source=source)
            else:
                push_result = await _maybe_push_failure_alert(group, result)
        return sanitize_operator_payload({
            "watch_group_key": group.group_key,
            "status": result_status,
            "summary": str(result.get("summary") or ""),
            "platform": "fliggy",
            "platform_account_id": group.platform_account_id,
            "browser_session_id": group.browser_session_id,
            "snapshot_id": result.get("snapshot_id"),
            "dataset_count": result.get("dataset_count"),
            "feishu_push": push_result,
            "source": source,
        })


def get_fliggy_ebooking_watch_service() -> FliggyEbookingWatchService:
    return FliggyEbookingWatchService()


async def _resolve_fliggy_browser_session_id(
    group: FliggyWatchGroupRuntime,
) -> str | None:
    """显式绑定优先；旧数据按 platform_account_id 兜底解析时必须仍是飞猪账号。"""

    if group.browser_session_id:
        return group.browser_session_id
    fallback_id = get_browser_session_id_for_platform_account(group.platform_account_id)
    if not fallback_id:
        return None
    try:
        session = get_browser_session_store().get(fallback_id)
    except KeyError:
        return None
    if getattr(session, "platform", None) != "fliggy":
        return None
    return fallback_id


async def _load_anchor_delivery(
    group: FliggyWatchGroupRuntime,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """返回 (store_name, webhook_url, 不可投递时的说明)。

    v2 §2 直连组：自身 feishu_webhook_url 优先；legacy 挂载组回落锚点
    携程组 webhook；两者都无 → config_required（不再依赖携程配置先行）。
    """

    own_webhook = str(getattr(group, "feishu_webhook_url", "") or "").strip()
    if own_webhook:
        return group.name, own_webhook, {}
    anchor_key = str(group.ctrip_watch_group_key or "").strip()
    if not anchor_key:
        return None, None, {
            "status": "config_required",
            "summary": "该飞猪监控组未配置飞书机器人地址，已保存本次读取结果。",
            "next_action": "请在飞猪 EBK 定时监控窗口为本组配置飞书机器人地址。",
        }
    try:
        anchor = await get_data_center_watch_group_runtime(watch_group_key=anchor_key)
    except Exception:
        logger.exception("fliggy ebooking watch anchor lookup failed")
        anchor = None
    if anchor is None:
        return None, None, {
            "status": "config_required",
            "summary": f"挂载的携程监控组不存在：{anchor_key}",
            "next_action": "请为本组配置飞书机器人地址，或重新选择挂载的携程监控组。",
        }
    webhook_url = str(anchor.feishu_webhook_url or "").strip()
    if not webhook_url:
        return None, None, {
            "status": "config_required",
            "summary": f"携程监控组「{anchor.name}」未配置飞书机器人地址，已保存本次读取结果。",
            "next_action": "在携程 EBK 数据中心监控组中配置飞书机器人地址。",
        }
    return anchor.name, webhook_url, {}


async def _push_snapshot_to_feishu(
    group: FliggyWatchGroupRuntime,
    result: dict[str, Any],
) -> dict[str, Any]:
    store_name, webhook_url, blocked = await _load_anchor_delivery(group)
    if blocked:
        return {**blocked, "watch_group_key": group.group_key}
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    datasets = payload.get("datasets") if isinstance(payload.get("datasets"), dict) else {}
    status = str(result.get("status") or payload.get("status") or "unknown")
    summary = str(result.get("summary") or payload.get("summary") or "")
    captured_at = payload.get("read_at")
    title = group.brief_title or f"{store_name}·{_PUSH_TITLE}"
    cards = build_platform_snapshot_feishu_card(
        platform="fliggy",
        watch_group_key=group.group_key,
        status=status,
        summary=summary,
        captured_at=captured_at if isinstance(captured_at, (str,)) else None,
        datasets=datasets,
        store_name=store_name,
    )
    fallback_text = build_platform_snapshot_feishu_text(
        platform="fliggy",
        watch_group_key=group.group_key,
        status=status,
        summary=summary,
        captured_at=captured_at if isinstance(captured_at, (str,)) else None,
        datasets=datasets,
        include_title=False,
        store_name=store_name,
    )
    # 多卡连续推送（同竞品盯盘机制）：逐卡发送互不中断，任一成功即算
    # 推送成功；全部失败才用第一张卡降级重发纯文本。
    card_results = [
        await push_interactive_card_to_feishu_webhook(
            title=title,
            card=card,
            webhook_url=webhook_url,
            fallback_text=None,
        )
        for card in cards
    ]
    if any(item.status == "success" for item in card_results):
        push_payload = card_results[0].model_dump(mode="json")
        push_payload["status"] = "success"
        succeeded = sum(1 for item in card_results if item.status == "success")
        push_payload["summary"] = f"已发送 {succeeded}/{len(card_results)} 张盯盘卡片。"
    else:
        pushed = await push_interactive_card_to_feishu_webhook(
            title=title,
            card=cards[0],
            webhook_url=webhook_url,
            fallback_text=fallback_text,
        )
        push_payload = pushed.model_dump(mode="json")
    push_payload["watch_group_key"] = group.group_key
    push_payload["delivery"] = "ctrip_group_webhook"
    push_payload["card_count"] = len(card_results)
    push_payload["card_results"] = [
        {
            "status": item.status,
            "feishu_code": item.data.get("feishu_code"),
            "feishu_msg": item.data.get("feishu_msg"),
            "msg_type": item.data.get("msg_type"),
        }
        for item in card_results
    ]
    return push_payload


async def _safe_push_snapshot_to_feishu(
    group: FliggyWatchGroupRuntime,
    result: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    try:
        push_result = await _push_snapshot_to_feishu(group, result)
    except Exception as exc:
        logger.exception("fliggy ebooking watch Feishu push failed")
        return {
            "status": "failed",
            "summary": f"飞书推送失败：{type(exc).__name__}: {str(exc)[:240]}",
            "watch_group_key": group.group_key,
            "error_type": type(exc).__name__,
        }
    if push_result.get("status") == "success":
        try:
            record_push(
                platform_throttle_key("fliggy", group.group_key),
                source=source or "",
                run_kind="watch",
            )
        except Exception:
            logger.debug("fliggy push throttle record failed", exc_info=True)
    return push_result


async def _maybe_push_failure_alert(
    group: FliggyWatchGroupRuntime,
    result: dict[str, Any],
) -> dict[str, Any]:
    """P0：失败态不推完整卡；按冷却窗口向锚定携程组发一张异常提醒。"""

    throttle_key = platform_throttle_key("fliggy", group.group_key)
    allowed, reason = should_send_failure_alert(throttle_key)
    if not allowed:
        return {
            "status": "alert_cooldown",
            "summary": reason,
            "watch_group_key": group.group_key,
        }
    store_name, webhook_url, blocked = await _load_anchor_delivery(group)
    if blocked:
        return {**blocked, "watch_group_key": group.group_key}
    status = str(result.get("status") or "error")
    summary = str(result.get("summary") or "")
    card = build_platform_failure_alert_card(
        platform="fliggy",
        store_name=store_name,
        watch_group_name=group.name,
        status=status,
        summary=summary,
    )
    fallback_text = build_platform_failure_alert_text(
        platform="fliggy",
        store_name=store_name,
        watch_group_name=group.name,
        status=status,
        summary=summary,
    )
    try:
        pushed = await push_interactive_card_to_feishu_webhook(
            title=f"{store_name}·飞猪 EBK 定时读取异常提醒",
            card=card,
            webhook_url=webhook_url,
            fallback_text=fallback_text,
        )
    except Exception as exc:
        logger.exception("fliggy ebooking watch failure-alert push failed")
        return {
            "status": "failed",
            "summary": f"异常提醒发送失败：{type(exc).__name__}: {str(exc)[:240]}",
            "watch_group_key": group.group_key,
        }
    record_failure_alert(throttle_key)
    push_payload = pushed.model_dump(mode="json")
    push_payload["watch_group_key"] = group.group_key
    push_payload["delivery"] = "ctrip_group_webhook"
    push_payload["alert_kind"] = "failure_alert"
    return push_payload


def _browser_session_registered(browser_session_id: str | None) -> bool:
    if not browser_session_id:
        return False
    try:
        get_browser_session_store().get(browser_session_id)
        return True
    except KeyError:
        return False
