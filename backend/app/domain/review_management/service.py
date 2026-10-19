"""Core business logic for review fetching, reply generation and auto-reply modes.

自动回复三种模式（review_store_bindings.auto_reply_mode，2026-09-16 起每日
auto_process_time 定时触发，抓取端点/飞书刷新只抓取不处理）：
- off       关闭自动回复：到点只抓取并统计未回复数量，不推送、不自动回复。
- semi_auto 半自动：到点统计未回复数量（含好/中/差明细），推一张数量提醒卡到
  监控组飞书群，提示运营去 OTA 后台回复；不在飞书里确认回复。
- full_auto 全自动：到点按模板渲染后经平台适配器批量提交，完成后推送
  「自动回复完成」统计卡（好评/中评/差评数量，以实际提交结果为准）。

安全口径：回复内容只能来自模板渲染或运营输入；LLM 微调仅在模板基础上生成
草案且必须经过半自动审批（TODO Phase C+，当前仅模板渲染）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from . import repository
from .adapters import (
    ReviewAdapterError,
    ReviewAdapterNotReadyError,
    ReviewSubmitError,
    default_store_browser_session_ids,
    get_review_adapter,
)
from .types import (
    REVIEW_PLATFORMS,
    ReplyTemplateInput,
    ReviewAutoConfigPatch,
    classify_review_score,
)

logger = logging.getLogger(__name__)

# 回复长度上限（TODO Phase D 逆向确认各平台真实限制后按平台细分）。
REPLY_MAX_LENGTH = 500
# 历史回复导入为模板时的单条内容长度上限。
_TEMPLATE_IMPORT_MAX_LENGTH = 500

classify_review = classify_review_score

PushCardFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def pick_reply_template(
    templates: list[dict[str, Any]],
    *,
    score: float | None,
) -> dict[str, Any] | None:
    """按 启用 -> 分数区间 -> 优先级 选择一条模板；无匹配返回 None。"""
    candidates = [tpl for tpl in templates if tpl.get("enabled")]
    if score is not None:
        candidates = [
            tpl
            for tpl in candidates
            if _score_in_range(tpl.get("score_range"), score)
        ]
    else:
        candidates = [tpl for tpl in candidates if not tpl.get("score_range")]
    if not candidates:
        return None
    # 配了分数区间的模板更具体，优先于无区间的兜底模板；同级按 priority 升序。
    return sorted(
        candidates,
        key=lambda tpl: (
            0 if tpl.get("score_range") else 1,
            tpl.get("priority", 100),
            tpl.get("id") or 0,
        ),
    )[0]


def _score_in_range(score_range: dict[str, Any] | None, score: float) -> bool:
    if not score_range:
        return True
    minimum = score_range.get("min")
    maximum = score_range.get("max")
    if minimum is not None and score < float(minimum):
        return False
    if maximum is not None and score > float(maximum):
        return False
    return True


def render_template(content: str, user_nickname: str | None) -> str:
    return content.replace("{user_nickname}", user_nickname or "客人")


def _validate_reply_length(reply_content: str) -> None:
    if len(reply_content) > REPLY_MAX_LENGTH:
        raise ValueError(
            f"回复内容超过平台限制（{REPLY_MAX_LENGTH} 字），当前 {len(reply_content)} 字。"
        )


def _crash_error_message(prefix: str, exc: BaseException) -> str:
    """领域外异常（数据库/Playwright 残留等）转可读错误文案，细节只进日志。"""
    logger.exception("%s crashed", prefix)
    return f"{prefix}异常（{type(exc).__name__}），请稍后重试；持续失败请查看服务日志。"


def _normalize_store_session(platform: str, browser_session_id: str | None) -> str | None:
    """店铺维度归一：空值回落平台默认端口账号（保持旧数据的店铺归属连续）。"""
    session_id = str(browser_session_id or "").strip() or None
    if session_id is not None:
        return session_id
    try:
        return default_store_browser_session_ids().get(platform)
    except Exception:
        return None


def _resolve_store_label(
    config: dict[str, Any], browser_session_id: str | None
) -> str | None:
    """卡片店铺名：binding.display_name 优先；空时回落浏览器会话 display_name。

    监控组保存路径（reviews API）upsert_store_binding 写 display_name=""，
    binding 表 display_name 永远为空，卡片回落到平台名（「携程」）而非真实
    店名。浏览器会话里的 display_name 是运营命名（如「静里携程ebk」），回落
    到它即可恢复真实店名（2026-09-17 修复）。
    """
    label = str((config or {}).get("display_name") or "").strip()
    if label:
        return label
    sid = str(browser_session_id or "").strip()
    if not sid:
        return None
    try:
        from app.domain.browser.sessions import get_browser_session_store

        session = get_browser_session_store().get(sid)
        return (getattr(session, "display_name", None) or "").strip() or None
    except Exception:
        logger.debug("resolve store label: session lookup failed", exc_info=True)
        return None


async def _resolve_ctrip_group(group_key: str, *, include_secrets: bool = False):
    """取一个携程 EBK 监控组（店铺锚点）的摘要。"""
    from app.domain.ctrip_ebooking_watch.repository import (
        list_data_center_watch_group_summaries,
    )

    groups = await list_data_center_watch_group_summaries(include_secrets=include_secrets)
    for group in groups:
        if group.get("group_key") == group_key:
            return group
    return None


async def resolve_store_browser_session(
    store_key: str,
    platform: str,
) -> tuple[str | None, str | None]:
    """按 (店铺, 平台) 解析评价操作要用的浏览器账号 + 飞书群（v2 §2）。

    store_key 优先按 shop_key 解析（店铺=唯一锚点）：平台账号 = 该店该平台
    归属的 EBK 会话；飞书群 = 店铺携程监控组的 chat（迁移期继承）。
    非店铺键回落旧携程锚点链（迁移期兜底）。未绑定时返回 (None, feishu)，
    调用方据此禁用该子平台、不静默回落默认端口。
    """
    from app.domain.shops import service as shops_service
    from app.domain.shops.service import ShopNotFound

    shop: dict[str, Any] | None = None
    try:
        shop = await shops_service.get_shop(str(store_key or "").strip())
    except ShopNotFound:
        shop = None
    except Exception:
        logger.debug("resolve store session: shop lookup failed", exc_info=True)
        shop = None
    if shop is not None:
        session_id: str | None = None
        try:
            from app.domain.browser.sessions import get_browser_session_store

            for session in get_browser_session_store().list():
                if (
                    session.platform == platform
                    and session.intended_use == "ebk"
                    and str(session.shop_key or "").strip() == shop["shop_key"]
                ):
                    session_id = session.browser_session_id
                    break
        except Exception:
            logger.debug("resolve store session: session store failed", exc_info=True)
        feishu = await _shop_inherited_feishu_chat_id(shop["shop_key"])
        return session_id, feishu

    ctrip_group = await _resolve_ctrip_group(
        store_key, include_secrets=True
    )
    feishu = (ctrip_group or {}).get("feishu_chat_id") if ctrip_group else None
    if ctrip_group is None:
        return None, None
    if platform == "ctrip":
        return ctrip_group.get("browser_session_id"), feishu
    if platform == "meituan":
        from app.domain.meituan_ebooking_watch.repository import (
            get_meituan_watch_group_runtime_by_ctrip_anchor,
        )

        runtime = await get_meituan_watch_group_runtime_by_ctrip_anchor(
            ctrip_watch_group_key=store_key
        )
        return (runtime.browser_session_id if runtime else None), feishu
    if platform == "fliggy":
        from app.domain.fliggy_ebooking_watch.repository import (
            get_fliggy_watch_group_runtime_by_ctrip_anchor,
        )

        runtime = await get_fliggy_watch_group_runtime_by_ctrip_anchor(
            ctrip_watch_group_key=store_key
        )
        return (runtime.browser_session_id if runtime else None), feishu
    return None, feishu


async def _shop_inherited_feishu_chat_id(shop_key: str) -> str | None:
    """店铺继承的飞书群：店铺携程监控组的 chat（迁移期；无锚点组=None）。"""
    try:
        from app.domain.ctrip_ebooking_watch.repository import (
            list_data_center_watch_group_summaries,
        )

        for group in await list_data_center_watch_group_summaries(
            include_secrets=True
        ):
            if str(group.get("shop_key") or "").strip() == shop_key:
                chat = str(group.get("feishu_chat_id") or "").strip()
                return chat or None
    except Exception:
        logger.debug("shop inherited chat lookup failed", exc_info=True)
    return None


async def list_review_groups() -> list[dict[str, Any]]:
    """评价店铺下拉 = 店铺（v2 §2），带三平台账号绑定状态。

    一个店铺一条；platforms.mounted = 该店该平台是否有归属 EBK 会话（账号级
    事实）。店铺有携程监控组时附 ctrip_watch_group_key（legacy 键，飞书群
    迁移期从它继承）；无店铺归属的旧携程组追加为 legacy 条目，保证旧配置
    仍可选、不静默丢失。
    """
    from app.domain.browser.sessions import get_browser_session_store
    from app.domain.ctrip_ebooking_watch.repository import (
        list_data_center_watch_group_summaries,
    )
    from app.domain.shops import service as shops_service

    shops = await shops_service.list_shops()
    try:
        sessions = [
            session
            for session in get_browser_session_store().list()
            if session.intended_use == "ebk"
        ]
    except Exception:
        sessions = []

    def platform_slot(shop_key: str, platform: str) -> dict[str, Any]:
        matched = next(
            (
                session
                for session in sessions
                if session.platform == platform
                and str(session.shop_key or "").strip() == shop_key
            ),
            None,
        )
        return {
            "mounted": matched is not None,
            "browser_session_id": matched.browser_session_id if matched else None,
        }

    ctrip_groups = await list_data_center_watch_group_summaries(include_secrets=False)
    anchor_by_shop: dict[str, dict[str, Any]] = {}
    legacy_anchors: list[dict[str, Any]] = []
    for group in ctrip_groups:
        shop_key = str(group.get("shop_key") or "").strip()
        if shop_key:
            anchor_by_shop[shop_key] = group
        else:
            legacy_anchors.append(group)

    def anchor_chat_configured(anchor: dict[str, Any] | None) -> bool:
        return bool((anchor or {}).get("feishu_chat_id_configured"))

    stores: list[dict[str, Any]] = []
    for shop in shops:
        anchor = anchor_by_shop.get(shop["shop_key"])
        stores.append(
            {
                "group_key": shop["shop_key"],
                "shop_key": shop["shop_key"],
                "name": shop["name"],
                "ctrip_watch_group_key": (anchor or {}).get("group_key"),
                "feishu_chat_id_configured": anchor_chat_configured(anchor),
                "platforms": {
                    platform: platform_slot(shop["shop_key"], platform)
                    for platform in ("ctrip", "meituan", "fliggy")
                },
            }
        )
    # 无店铺归属的旧携程组：保持旧条目形状（group_key=携程组键），可继续选用。
    for group in legacy_anchors:
        group_key = group.get("group_key")
        ctrip_browser = group.get("browser_session_id")
        from app.domain.fliggy_ebooking_watch.repository import (
            get_fliggy_watch_group_runtime_by_ctrip_anchor,
        )
        from app.domain.meituan_ebooking_watch.repository import (
            get_meituan_watch_group_runtime_by_ctrip_anchor,
        )

        meituan_runtime = await get_meituan_watch_group_runtime_by_ctrip_anchor(
            ctrip_watch_group_key=group_key
        )
        fliggy_runtime = await get_fliggy_watch_group_runtime_by_ctrip_anchor(
            ctrip_watch_group_key=group_key
        )
        stores.append(
            {
                "group_key": group_key,
                "shop_key": None,
                "name": f"{group.get('name') or group_key}（未归属店铺）",
                "ctrip_watch_group_key": group_key,
                "feishu_chat_id_configured": bool(group.get("feishu_chat_id_configured")),
                "platforms": {
                    "ctrip": {
                        "mounted": bool(ctrip_browser),
                        "browser_session_id": ctrip_browser,
                    },
                    "meituan": {
                        "mounted": meituan_runtime is not None
                        and bool(meituan_runtime.browser_session_id),
                        "browser_session_id": meituan_runtime.browser_session_id
                        if meituan_runtime
                        else None,
                    },
                    "fliggy": {
                        "mounted": fliggy_runtime is not None
                        and bool(fliggy_runtime.browser_session_id),
                        "browser_session_id": fliggy_runtime.browser_session_id
                        if fliggy_runtime
                        else None,
                    },
                },
            }
        )
    return stores


async def list_review_stores(*, platform: str) -> list[dict[str, Any]]:
    """评价管理可选店铺列表 = 平台 EBK 浏览器账号 + 已保存配置状态（同 EBK）。"""
    from app.domain.browser.sessions import get_browser_session_store

    bindings = {
        row["browser_session_id"]: row
        for row in await repository.list_store_bindings(platform=platform)
    }
    stores: list[dict[str, Any]] = []
    try:
        sessions = get_browser_session_store().list()
    except Exception:
        sessions = []
    for session in sessions:
        if session.platform != platform or session.intended_use != "ebk":
            continue
        binding = bindings.get(session.browser_session_id)
        stores.append(
            {
                "browser_session_id": session.browser_session_id,
                "platform_account_id": session.platform_account_id,
                "display_name": session.display_name
                or session.platform_account_id,
                "configured": binding is not None,
                "auto_reply_mode": (binding or {}).get("auto_reply_mode", "off"),
                "auto_fetch_enabled": (binding or {}).get("auto_fetch_enabled", False),
                "feishu_chat_id_configured": bool((binding or {}).get("feishu_chat_id")),
                "last_fetched_at": (binding or {}).get("last_fetched_at"),
            }
        )
    return stores


async def fetch_and_store_reviews(
    *,
    platform: str,
    browser_session_id: str | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """拉取评价列表并落库；适配器未接入时返回 not_ready，不产生假数据。

    browser_session_id 指定店铺（EBK 浏览器账号）时按该店铺浏览器抓取并落库。
    """
    session_id = _normalize_store_session(platform, browser_session_id)
    adapter = adapter or get_review_adapter(platform, session_id)
    await _ensure_review_browser_alive(platform, session_id)
    task = await repository.create_review_task(
        platform=platform, task_type="fetch", browser_session_id=session_id
    )
    try:
        snapshot = await adapter.fetch_reviews()
    except ReviewAdapterNotReadyError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "not_ready", "task_id": task["id"], "error": str(exc)}
    except ReviewAdapterError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "error", "task_id": task["id"], "error": str(exc)}
    except Exception as exc:
        # 兜底：不留悬挂的 running 任务行，也不把原始异常冒泡成 API 500。
        message = _crash_error_message("评价抓取", exc)
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=message
        )
        return {"status": "error", "task_id": task["id"], "error": message}

    # 2026-09-03 收敛：评价列表已从控制台移除（待回复走 OTA 后台），抓取不再
    # 持久化评价正文（避免 review_items 无限堆积），只把未回复数量记到店铺绑定。
    # 2026-09-16：同时按实际读取到的未回复评价统计好/中/差（首屏，可能小于
    # 平台侧未回复总数），供半自动提醒卡/全自动统计卡展示。
    pending_items = [item for item in snapshot.reviews if item.reply_status == "pending"]
    pending_by_category = {"good": 0, "medium": 0, "bad": 0}
    for item in pending_items:
        pending_by_category[classify_review(item.score)] += 1
    pending_count = snapshot.pending_total
    await repository.set_last_fetched_at(
        platform=platform,
        browser_session_id=session_id,
        pending_count=pending_count,
    )
    await repository.complete_review_task(
        task_id=task["id"],
        status="completed",
        review_count=len(snapshot.reviews),
        result_json={
            "pending_count": pending_count,
            "pending_read": len(pending_items),
            "pending_by_category": pending_by_category,
        },
    )
    return {
        "status": "ok",
        "task_id": task["id"],
        "pending_count": pending_count,
        "fetched": len(snapshot.reviews),
        "pending_read": len(pending_items),
        "pending_by_category": pending_by_category,
    }


async def resolve_store_feishu_chat_id(
    platform: str,
    browser_session_id: str | None,
) -> str | None:
    """从监控组解析店铺的飞书推送群（店铺绑定缺 feishu_chat_id 时的兜底）。

    携程店铺 = 浏览器账号所在的携程 EBK 监控组；美团/飞猪店铺 = 挂载该
    浏览器账号的平台监控组的携程锚点组（飞书群随锚点继承）。已绑监控组但
    未单独配飞书群时，读取监控组的飞书推送 ID 即可；监控组也没配则返回
    None，由调用方提示人工配置。
    """
    session_id = str(browser_session_id or "").strip()
    if not session_id:
        return None
    try:
        from app.domain.ctrip_ebooking_watch.repository import (
            list_data_center_watch_group_summaries,
        )

        ctrip_groups = await list_data_center_watch_group_summaries(
            include_secrets=True
        )
    except Exception:
        logger.exception("resolve store feishu chat id: ctrip groups read failed")
        return None

    def _clean(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    if platform == "ctrip":
        for group in ctrip_groups:
            if str(group.get("browser_session_id") or "") == session_id:
                return _clean(group.get("feishu_chat_id"))
        return None
    if platform == "meituan":
        from app.domain.meituan_ebooking_watch.repository import (
            list_meituan_watch_groups,
        )

        platform_groups = await list_meituan_watch_groups()
    elif platform == "fliggy":
        from app.domain.fliggy_ebooking_watch.repository import (
            list_fliggy_watch_groups,
        )

        platform_groups = await list_fliggy_watch_groups()
    else:
        return None
    anchor_key = None
    own_chat = None
    for group in platform_groups:
        if str(group.get("browser_session_id") or "") == session_id:
            anchor_key = _clean(group.get("ctrip_watch_group_key"))
            # v2 §2：直连组自身的飞书群优先；legacy 挂载组无自身群，回落锚点。
            own_chat = _clean(group.get("feishu_chat_id"))
            break
    if own_chat:
        return own_chat
    if not anchor_key:
        return None
    for group in ctrip_groups:
        if group.get("group_key") == anchor_key:
            return _clean(group.get("feishu_chat_id"))
    return None


async def process_pending_reviews(
    *,
    platform: str,
    browser_session_id: str | None = None,
    adapter: Any | None = None,
    push_card: PushCardFn | None = None,
    source: str = "scheduler",
    fetch_result: dict[str, Any] | None = None,
    push_summary_card: bool = True,
) -> dict[str, Any]:
    """按平台/店铺开关处理待回复评价：off 不动 / semi_auto 推提醒卡 / full_auto 直接提交。

    fetch_result 是刚完成的 fetch_and_store_reviews 结果（定时/手动流水线传入）：
    携带本轮实际读取的好/中/差明细，供半自动提醒卡展示；缺省时无明细（不编造）。

    push_summary_card=False 用于调度链路：逐店卡停推，由调度器的日报合并卡
    汇总三平台一张卡（2026-09-21 需求）；手动/API 链路保持默认逐店卡。
    """
    session_id = _normalize_store_session(platform, browser_session_id)
    config = await repository.get_effective_review_config(
        platform=platform, browser_session_id=session_id
    )
    mode = config["auto_reply_mode"]
    if mode == "off":
        return {
            "status": "mode_off",
            "mode": mode,
            "browser_session_id": session_id,
            "submitted": [],
            "approvals": [],
            "skipped": [],
        }
    approval_chat_id = str(config.get("approval_chat_id") or "").strip() or None
    if mode == "semi_auto" and not approval_chat_id:
        # 店铺未单独配飞书群时，读取监控组的飞书推送 ID（已绑监控组即可用）。
        approval_chat_id = await resolve_store_feishu_chat_id(platform, session_id)
    if mode == "semi_auto" and not approval_chat_id:
        return {
            "status": "missing_chat_id",
            "mode": mode,
            "browser_session_id": session_id,
            "submitted": [],
            "approvals": [],
            "skipped": [],
            "error": (
                "半自动模式需要飞书推送群：请先给该店铺绑定的监控组配置飞书群，"
                "或在店铺绑定里单独填写审批飞书群 chat_id。"
            ),
        }

    adapter = adapter or get_review_adapter(platform, session_id)

    # 全自动优先走「单页面会话内批量」：未回复列表分页随机，跨会话定位会失配。
    if mode == "full_auto" and hasattr(adapter, "submit_pending_replies"):
        # 批量提交读 live 页：此时才拉起店铺浏览器（缓存计数/不可用路径不拉）。
        await _ensure_review_browser_alive(platform, session_id)
        templates = await repository.list_templates(
            platform=platform,
            only_enabled=True,
            browser_session_id=session_id,
        )

        def _generator(info: dict[str, Any]) -> str | None:
            candidates = [
                tpl for tpl in templates if tpl["category"] == classify_review(info.get("score"))
            ]
            template = pick_reply_template(candidates, score=info.get("score"))
            if template is None:
                return None
            return render_template(template["content"], info.get("user_nickname"))

        batch = await adapter.submit_pending_replies(_generator)
        submitted_count = sum(1 for row in batch if row.get("ok"))
        refetch_result: dict[str, Any] | None = None
        if submitted_count:
            # 重新抓取同步状态：平台已回复的评价以平台为准落库。
            refetch_result = await fetch_and_store_reviews(
                platform=platform,
                browser_session_id=browser_session_id,
                adapter=adapter,
            )
        # 统计实际提交成功的好/中/差数量，推「自动回复完成」统计卡到监控组
        # 绑定飞书群（2026-09-16 需求）；无绑定群时记录原因不阻断回复本身。
        by_category = {"good": 0, "medium": 0, "bad": 0}
        for row in batch:
            if row.get("ok"):
                by_category[classify_review(row.get("score"))] += 1
        skipped_count = len(batch) - submitted_count
        skip_reasons: dict[str, int] = {}
        for row in batch:
            if not row.get("ok"):
                reason = str(row.get("reason") or "error")[:40]
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        summary_push: dict[str, Any] = {"status": "no_pending", "chat_id": None}
        if batch:
            summary_chat_id = approval_chat_id or await resolve_store_feishu_chat_id(
                platform, session_id
            )
            if summary_chat_id and push_summary_card:
                pusher = push_card or _default_push_card
                await pusher(
                    summary_chat_id,
                    build_auto_processed_summary_card(
                        platform=platform,
                        store_label=_resolve_store_label(config, session_id),
                        by_category=by_category,
                        processed=submitted_count,
                        skipped=skipped_count,
                    ),
                )
                summary_push = {"status": "ok", "chat_id": summary_chat_id}
            elif summary_chat_id:
                summary_push = {
                    "status": "merged_daily_card",
                    "chat_id": summary_chat_id,
                }
            else:
                summary_push = {"status": "missing_chat_id", "chat_id": None}
        await _record_auto_process_outcome(
            platform=platform,
            session_id=session_id,
            source=source,
            payload={
                "mode": mode,
                "status": "ok",
                "submitted": submitted_count,
                "skipped": skipped_count,
                "by_category": by_category,
                "skip_reasons": skip_reasons,
                "pending_count": (refetch_result or fetch_result or {}).get(
                    "pending_count"
                ),
            },
        )
        return {
            "status": "ok",
            "mode": mode,
            "browser_session_id": session_id,
            "submitted": submitted_count,
            "details": batch,
            "skipped": [row for row in batch if not row.get("ok")],
            "by_category": by_category,
            "summary_push": summary_push,
            "source": source,
        }

    # 全自动：批量提交依赖适配器的 submit_pending_replies（读 live 页，不落库）；
    # 适配器未接入批量提交时全自动不可用（不再回退到读库逐条——库不再落评价正文）。
    if mode == "full_auto":
        await _record_auto_process_outcome(
            platform=platform,
            session_id=session_id,
            source=source,
            payload={"mode": mode, "status": "not_ready"},
        )
        return {
            "status": "not_ready",
            "mode": mode,
            "browser_session_id": session_id,
            "submitted": [],
            "approvals": [],
            "pushed_cards": 0,
            "error": "该平台评价提交接口未接入批量提交，全自动暂不可用。",
            "source": source,
        }

    # semi_auto：收敛为「未回复数量提醒」——只读数量、推一张提醒卡到监控组飞书群，
    # 不逐条推审批卡/不生成草案/不落审批行（待回复查看与回复走 OTA 后台）。
    pending_count = config.get("last_pending_count")
    by_category: dict[str, int] | None = None
    pending_read: int | None = None
    if (fetch_result or {}).get("pending_by_category"):
        # 定时/手动流水线刚抓取过：用本轮实际读取的好/中/差明细。
        by_category = dict(fetch_result["pending_by_category"])  # type: ignore[index]
        pending_read = fetch_result.get("pending_read")  # type: ignore[union-attr]
    if pending_count is None:
        # 无缓存计数需要现场读取：此时才拉起店铺浏览器（适配器只 attach 不拉起）。
        await _ensure_review_browser_alive(platform, session_id)
        try:
            snapshot = await adapter.fetch_reviews()
            pending_count = snapshot.pending_total or 0
            if by_category is None:
                pending_items = [
                    item for item in snapshot.reviews if item.reply_status == "pending"
                ]
                by_category = {"good": 0, "medium": 0, "bad": 0}
                for item in pending_items:
                    by_category[classify_review(item.score)] += 1
                pending_read = len(pending_items)
            await repository.set_last_fetched_at(
                platform=platform,
                browser_session_id=browser_session_id,
                pending_count=pending_count,
            )
        except ReviewAdapterNotReadyError as exc:
            return {
                "status": "not_ready",
                "mode": mode,
                "browser_session_id": session_id,
                "pushed_cards": 0,
                "pending_count": pending_count,
                "error": str(exc),
                "source": source,
            }
        except ReviewAdapterError as exc:
            return {
                "status": "error",
                "mode": mode,
                "browser_session_id": session_id,
                "pushed_cards": 0,
                "pending_count": pending_count,
                "error": str(exc),
                "source": source,
            }
    pushed_cards = 0
    if pending_count and pending_count > 0 and push_summary_card:
        pusher = push_card or _default_push_card
        await pusher(
            approval_chat_id,
            build_pending_count_alert_card(
                platform=platform,
                pending_count=pending_count,
                store_label=_resolve_store_label(config, session_id),
                by_category=by_category,
                pending_read=pending_read,
            ),
        )
        pushed_cards = 1
    await _record_auto_process_outcome(
        platform=platform,
        session_id=session_id,
        source=source,
        payload={
            "mode": mode,
            "status": "ok",
            "pending_count": pending_count,
            "pushed_cards": pushed_cards,
        },
    )
    return {
        "status": "ok",
        "mode": mode,
        "browser_session_id": session_id,
        "pushed_cards": pushed_cards,
        "pending_count": pending_count,
        "source": source,
    }


def build_pending_count_alert_card(
    *,
    platform: str,
    pending_count: int,
    store_label: str | None,
    by_category: dict[str, int] | None = None,
    pending_read: int | None = None,
) -> dict[str, Any]:
    from .cards import build_review_pending_count_alert_card

    return build_review_pending_count_alert_card(
        platform=platform,
        pending_count=pending_count,
        store_label=store_label,
        by_category=by_category,
        pending_read=pending_read,
    )


def build_auto_processed_summary_card(
    *,
    platform: str,
    store_label: str | None,
    by_category: dict[str, int],
    processed: int,
    skipped: int = 0,
) -> dict[str, Any]:
    from .cards import build_review_auto_processed_summary_card

    return build_review_auto_processed_summary_card(
        platform=platform,
        store_label=store_label,
        by_category=by_category,
        processed=processed,
        skipped=skipped,
    )


async def _default_push_card(chat_id: str, card: dict[str, Any]) -> dict[str, Any]:
    from app.domain.notifications.feishu_client import FeishuBotClient

    return await FeishuBotClient(chat_id=chat_id).send_interactive_card(
        chat_id, card, fallback_text="评价回复审批卡推送失败，请到后台处理。"
    )


async def _record_auto_process_outcome(
    *,
    platform: str,
    session_id: str | None,
    source: str,
    payload: dict[str, Any],
) -> None:
    """处理结果落库（review_tasks.task_type='auto_process'）；失败不阻断主流程。"""
    try:
        await repository.record_auto_process_outcome(
            platform=platform,
            browser_session_id=session_id,
            payload={"source": source, **payload},
        )
    except Exception:
        logger.exception(
            "record auto process outcome failed for platform %s store %s",
            platform,
            session_id,
        )


async def import_history_as_templates(
    *,
    platform: str,
    browser_session_id: str | None = None,
    limit: int = 200,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """拉取历史已回复评价：落库为已回复 + 按好/中/差归类去重生成 imported 模板。"""
    session_id = _normalize_store_session(platform, browser_session_id)
    adapter = adapter or get_review_adapter(platform, session_id)
    await _ensure_review_browser_alive(platform, session_id)
    task = await repository.create_review_task(
        platform=platform, task_type="import", browser_session_id=session_id
    )
    try:
        history = await adapter.fetch_history_replies(limit)
    except ReviewAdapterNotReadyError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "not_ready", "task_id": task["id"], "error": str(exc)}
    except ReviewAdapterError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "error", "task_id": task["id"], "error": str(exc)}
    except Exception as exc:
        message = _crash_error_message("历史回复导入", exc)
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=message
        )
        return {"status": "error", "task_id": task["id"], "error": message}

    from .types import ReviewItemInput

    existing_templates = await repository.list_templates(
        platform=platform, browser_session_id=session_id
    )
    existing_contents = {
        _normalize_template_content(tpl["content"]) for tpl in existing_templates
    }
    by_category = {"good": 0, "medium": 0, "bad": 0}
    seen: set[str] = set()
    created = 0
    category_counter = {"good": 0, "medium": 0, "bad": 0}
    for item in history:
        review_items = [
            ReviewItemInput(
                review_id=item.review_id,
                user_nickname=item.user_nickname,
                score=item.score,
                content=item.content,
                review_time=item.review_time,
                reply_status="replied",
                reply_content=item.reply_content,
            )
        ]
        await repository.upsert_review_items(
            platform=platform,
            hotel_id=item.hotel_id,
            items=review_items,
            browser_session_id=session_id,
        )
        content = (item.reply_content or "").strip()
        if not content or len(content) > _TEMPLATE_IMPORT_MAX_LENGTH:
            continue
        normalized = _normalize_template_content(content)
        if normalized in seen or normalized in existing_contents:
            continue
        seen.add(normalized)
        category = classify_review(item.score)
        category_counter[category] += 1
        await repository.save_template(
            template=ReplyTemplateInput(
                platform=platform,
                category=category,  # type: ignore[arg-type]
                name=f"历史{_category_label(category)}回复 {category_counter[category]}",
                content=content,
                priority=200 + category_counter[category],
                source="imported",
            ),
            browser_session_id=session_id,
        )
        by_category[category] += 1
        created += 1
    await repository.complete_review_task(
        task_id=task["id"],
        status="completed",
        review_count=len(history),
        result_json={"templates_created": created, **{f"{k}_templates": v for k, v in by_category.items()}},
    )
    return {
        "status": "ok",
        "task_id": task["id"],
        "templates_created": created,
        "by_category": by_category,
        "history_reviews": len(history),
    }


def _platform_ebooking_service(platform: str):
    """取平台 EBK 浏览器服务实例（用于评价抓取前拉起/复用店铺浏览器）。

    评价适配器本身只做 CDP attach，浏览器没开就失败；EBK 监控服务有
    launch_persistent_context 能拉起系统托管浏览器。延迟导入避免环。
    """
    try:
        if platform == "ctrip":
            from app.domain.browser.ctrip_ebooking import get_ctrip_ebooking_service

            return get_ctrip_ebooking_service()
        if platform == "meituan":
            from app.domain.browser.meituan_ebooking import (
                get_meituan_ebooking_service,
            )

            return get_meituan_ebooking_service()
        if platform == "fliggy":
            from app.domain.browser.fliggy_ebooking import (
                get_fliggy_ebooking_service,
            )

            return get_fliggy_ebooking_service()
    except Exception:
        logger.debug("platform ebooking service unavailable for %s", platform, exc_info=True)
    return None


async def _ensure_review_browser_alive(platform: str, browser_session_id: str | None) -> None:
    """评价抓取前确保店铺浏览器已拉起（复用平台 EBK 浏览器服务）。

    评价适配器只 attach 不拉起：浏览器没开时 CDP attach 失败、开了也未必是
    系统托管的可控浏览器。这里先让 EBK 服务 `_get_or_create_page` 拉起/复用
    系统托管浏览器（headless=False，操作员可见），再让适配器 attach 开新 tab
    跳转到评价页。拉起失败不阻断——适配器仍尝试 CDP attach（保留旧行为）。
    """
    session_id = str(browser_session_id or "").strip()
    if not session_id:
        return
    service = _platform_ebooking_service(platform)
    if service is None:
        return
    # 优先从评价店铺绑定取 platform_account_id；绑定未记录时回落浏览器会话存储。
    account_id = ""
    binding = await repository.get_store_binding(browser_session_id=session_id)
    if binding is not None:
        account_id = str(binding.get("platform_account_id") or "")
    if not account_id:
        try:
            from app.domain.browser.sessions import get_browser_session_store

            session = get_browser_session_store().get(session_id)
            account_id = str(getattr(session, "platform_account_id", "") or "")
        except Exception:
            return
    if not account_id:
        return
    try:
        await service._get_or_create_page(
            platform_account_id=account_id,
            browser_session_id=session_id,
            headless=False,
        )
    except Exception as exc:
        logger.warning(
            "ensure review browser alive failed for %s store %s (%s)",
            platform,
            session_id,
            type(exc).__name__,
        )


def _normalize_template_content(content: str) -> str:
    return "".join(content.split())


def _category_label(category: str) -> str:
    return {"good": "好评", "medium": "中评", "bad": "差评"}.get(category, category)


async def run_review_pipeline(
    *,
    platform: str,
    browser_session_id: str | None = None,
    adapter: Any | None = None,
    push_card: PushCardFn | None = None,
    source: str = "manual",
    push_summary_card: bool = True,
) -> dict[str, Any]:
    """一次完整执行：抓取 -> 落库 -> 按模式处理待回复评价（定时到点触发用）。

    调度链路传 push_summary_card=False：逐店卡停推，改由调度器在所有到点
    店铺结算后推一张三平台日报合并卡（2026-09-21 需求）。
    """
    fetch_result = await fetch_and_store_reviews(
        platform=platform,
        browser_session_id=browser_session_id,
        adapter=adapter,
    )
    if fetch_result["status"] != "ok":
        return {"fetch": fetch_result, "process": None}
    process_result = await process_pending_reviews(
        platform=platform,
        browser_session_id=browser_session_id,
        adapter=adapter,
        push_card=push_card,
        source=source,
        fetch_result=fetch_result,
        push_summary_card=push_summary_card,
    )
    return {"fetch": fetch_result, "process": process_result}


async def collect_daily_review_summary(
    *,
    today_start: datetime,
) -> dict[str, Any]:
    """汇总当日各启用店铺绑定的抓取/自动处理结果，供日报合并卡。

    每店铺取当日最新一条 fetch / auto_process 任务（fetch 可能有多条：
    提交成功后的同步重抓取排最后，pending_count 即剩余待回复）。
    """
    entries: list[dict[str, Any]] = []
    for platform in REVIEW_PLATFORMS:
        bindings = await repository.list_store_bindings(platform=platform)
        for binding in bindings:
            if not (binding.get("enabled") and binding.get("auto_fetch_enabled")):
                continue
            session_id = binding.get("browser_session_id")
            tasks = await repository.list_review_tasks_since(
                created_since=today_start,
                task_types=("fetch", "auto_process"),
                platform=platform,
                browser_session_id=session_id,
            )
            fetch_task = None
            process_task = None
            for task in tasks:
                if task["task_type"] == "fetch":
                    fetch_task = task
                else:
                    process_task = task
            fetch_info: dict[str, Any] | None = None
            if fetch_task is not None:
                fetch_result = fetch_task.get("result") or {}
                fetch_info = {
                    "status": fetch_task.get("status"),
                    "pending_count": fetch_result.get("pending_count"),
                    "error": fetch_task.get("error_msg"),
                }
            process_info: dict[str, Any] | None = None
            if process_task is not None:
                payload = process_task.get("result") or {}
                process_info = {
                    "status": payload.get("status"),
                    "mode": payload.get("mode"),
                    "submitted": payload.get("submitted"),
                    "skipped": payload.get("skipped"),
                    "by_category": payload.get("by_category"),
                    "skip_reasons": payload.get("skip_reasons"),
                }
            chat_id = str(binding.get("feishu_chat_id") or "").strip() or None
            if not chat_id:
                chat_id = await resolve_store_feishu_chat_id(platform, session_id)
            display_name = str(binding.get("display_name") or "").strip()
            entries.append(
                {
                    "platform": platform,
                    "store_label": display_name or None,
                    "mode": binding.get("auto_reply_mode"),
                    "chat_id": chat_id,
                    "fetch": fetch_info,
                    "process": process_info,
                }
            )
    return {"entries": entries}


# ---------------------------------------------------------------------------
# 「自动更新评价」：抓店铺浏览器已回复评价 -> LLM 整理 -> 覆盖重建店铺模板。
# ---------------------------------------------------------------------------

# 每类样本送 LLM 的上限：避免长评价列表撑爆 prompt。
_LLM_SAMPLES_PER_CATEGORY = 12
# LLM 单类别输出模板条数上限。
_LLM_TEMPLATES_PER_CATEGORY = 4
_LLM_SYSTEM_PROMPT = (
    "你是酒店客户评价回复模板整理助手。请把商家历史回复整理成可复用的回复模板："
    "保留通用表达，去除具体人名、房号、日期、金额等一次性信息；"
    "可以在合适位置使用 {user_nickname} 占位符指代客人。"
    "只输出严格的 JSON 对象：{\"good\": [...], \"medium\": [...], \"bad\": [...]}，"
    "每个数组是字符串列表（每条不超过 200 字），不得输出其他文字。"
    "各类别只能基于给出的对应样本改写，没有样本的类别输出空数组。"
)


def _category_samples_from_history(history: list[Any]) -> dict[str, list[str]]:
    """把历史回复按好/中/差归类，去重（空白归一）并限量。"""
    samples: dict[str, list[str]] = {"good": [], "medium": [], "bad": []}
    seen: set[str] = set()
    for item in history:
        content = (getattr(item, "reply_content", None) or "").strip()
        if not content or len(content) > _TEMPLATE_IMPORT_MAX_LENGTH:
            continue
        category = classify_review(getattr(item, "score", None))
        normalized = _normalize_template_content(content)
        if normalized in seen:
            continue
        seen.add(normalized)
        if len(samples[category]) < _LLM_SAMPLES_PER_CATEGORY:
            samples[category].append(content)
    return samples


def _parse_llm_template_payload(raw: str | None) -> dict[str, list[str]] | None:
    """解析 LLM 输出的 JSON 模板载荷；不合法返回 None（上层走规则回退）。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            text = text[first : last + 1]
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    parsed: dict[str, list[str]] = {}
    for category in ("good", "medium", "bad"):
        rows = payload.get(category, [])
        if not isinstance(rows, list):
            return None
        contents: list[str] = []
        for row in rows:
            content = str(row or "").strip()
            if not content or len(content) > REPLY_MAX_LENGTH:
                continue
            contents.append(content)
        parsed[category] = contents[:_LLM_TEMPLATES_PER_CATEGORY]
    return parsed


async def _default_review_llm_chat(messages: list[dict[str, Any]]) -> str | None:
    """Hermes 同源 LLM 调用（与 Hermes 共用一套模型运行时配置）。"""
    from app.domain.ai_agent.llm import (
        AgentLlmConfigError,
        chat_with_fallback_chain,
    )
    from app.domain.config.service import get_runtime_config

    runtime_config = await get_runtime_config()
    chain = await runtime_config.llm_runtime_chain()
    response = await chat_with_fallback_chain(chain, messages=messages, tools=[])
    return response.content


def _distill_templates_via_llm(
    llm_payload: dict[str, list[str]] | None,
    samples: dict[str, list[str]],
) -> dict[str, list[str]]:
    """校验 LLM 输出：无样本的类别丢弃（不允许凭空发明），逐条限长。"""
    if llm_payload is None:
        return {}
    result: dict[str, list[str]] = {}
    for category, contents in llm_payload.items():
        if not samples.get(category):
            continue
        cleaned: list[str] = []
        seen: set[str] = set()
        for content in contents:
            normalized = _normalize_template_content(content)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(content)
        if cleaned:
            result[category] = cleaned
    return result


def _distill_templates_rule_based(samples: dict[str, list[str]]) -> dict[str, list[str]]:
    """规则回退：每类取去重后的原始回复（最多 5 条），不做任何改写。"""
    result: dict[str, list[str]] = {}
    for category, contents in samples.items():
        if contents:
            result[category] = contents[:5]
    return result


async def auto_update_store_templates(
    *,
    platform: str,
    browser_session_id: str,
    limit: int = 200,
    adapter: Any | None = None,
    llm_chat: Any | None = None,
) -> dict[str, Any]:
    """「自动更新评价」：抓店铺已回复评价（好/中/差）-> LLM 整理 -> 覆盖重建模板。

    前端必须在确认弹窗（提示会覆盖该店铺全部评价模板）后才调用本接口。
    LLM 不可用或输出不合法时回退规则式去重；无历史样本时不覆盖现有模板。
    """
    from .types import ReviewItemInput

    session_id = str(browser_session_id or "").strip()
    if not session_id:
        return {"status": "invalid_request", "error": "请先选择店铺（EBK 浏览器账号）。"}
    adapter = adapter or get_review_adapter(platform, session_id)
    await _ensure_review_browser_alive(platform, session_id)
    task = await repository.create_review_task(
        platform=platform, task_type="auto_update", browser_session_id=session_id
    )
    try:
        history = await adapter.fetch_history_replies(limit)
    except ReviewAdapterNotReadyError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "not_ready", "task_id": task["id"], "error": str(exc)}
    except ReviewAdapterError as exc:
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=str(exc)
        )
        return {"status": "error", "task_id": task["id"], "error": str(exc)}
    except Exception as exc:
        message = _crash_error_message("自动更新评价", exc)
        await repository.complete_review_task(
            task_id=task["id"], status="failed", error_msg=message
        )
        return {"status": "error", "task_id": task["id"], "error": message}

    if not history:
        await repository.complete_review_task(
            task_id=task["id"], status="completed", review_count=0,
            result_json={"templates_created": 0, "reason": "no_history"},
        )
        return {
            "status": "no_history",
            "task_id": task["id"],
            "error": "该店铺浏览器内没有已回复的评价，未更新模板。",
        }

    # 历史已回复评价落库（审计 + 后续再整理的样本底座）。
    for item in history:
        await repository.upsert_review_items(
            platform=platform,
            hotel_id=item.hotel_id,
            items=[
                ReviewItemInput(
                    review_id=item.review_id,
                    user_nickname=item.user_nickname,
                    score=item.score,
                    content=item.content,
                    review_time=item.review_time,
                    reply_status="replied",
                    reply_content=item.reply_content,
                )
            ],
            browser_session_id=session_id,
        )

    samples = _category_samples_from_history(history)
    generator = "llm"
    distilled: dict[str, list[str]] = {}
    chat = llm_chat or _default_review_llm_chat
    try:
        raw = await chat(
            [
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(samples, ensure_ascii=False),
                },
            ]
        )
        distilled = _distill_templates_via_llm(_parse_llm_template_payload(raw), samples)
    except Exception:
        logger.exception("auto update templates llm distill failed; fallback to rules")
        distilled = {}
    if not distilled:
        distilled = _distill_templates_rule_based(samples)
        generator = "rule_based"

    if not distilled:
        # 有历史但无有效回复内容：不覆盖现有模板，防误清空。
        await repository.complete_review_task(
            task_id=task["id"], status="completed", review_count=len(history),
            result_json={"templates_created": 0, "reason": "no_distillable_replies"},
        )
        return {
            "status": "no_distillable_replies",
            "task_id": task["id"],
            "error": "历史评价没有可整理的回复内容，未更新模板。",
        }

    # 覆盖式重建：只重建有新样本的类别（其余店铺/平台级/无样本类别不受影响）。
    # 携程等平台已回复列表常为纯好评，若全量删除会把中/差评旧模板连带清空。
    removed = 0
    created = 0
    by_category: dict[str, int] = {}
    for category in ("good", "medium", "bad"):
        contents = distilled.get(category, [])
        if not contents:
            continue
        removed += await repository.delete_templates_for_store(
            platform=platform, browser_session_id=session_id, category=category
        )
        for index, content in enumerate(contents, start=1):
            await repository.save_template(
                template=ReplyTemplateInput(
                    platform=platform,
                    category=category,  # type: ignore[arg-type]
                    name=f"{_category_label(category)}自动模板 {index}",
                    content=content,
                    priority=100 + index,
                    source="auto",
                ),
                browser_session_id=session_id,
            )
            by_category[category] = by_category.get(category, 0) + 1
            created += 1
    await repository.complete_review_task(
        task_id=task["id"],
        status="completed",
        review_count=len(history),
        result_json={
            "generator": generator,
            "templates_removed": removed,
            "templates_created": created,
            **{f"{k}_templates": v for k, v in by_category.items()},
        },
    )
    return {
        "status": "ok",
        "task_id": task["id"],
        "browser_session_id": session_id,
        "generator": generator,
        "templates_removed": removed,
        "templates_created": created,
        "by_category": by_category,
        "history_reviews": len(history),
    }
