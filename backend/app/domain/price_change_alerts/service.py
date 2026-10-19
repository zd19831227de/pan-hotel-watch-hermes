from __future__ import annotations

from typing import Any

from app.db.session import ensure_db_ready, session_scope
from app.domain.browser.sessions import (
    account_write_policy as account_write_policy_for_session,
)
from app.domain.notifications.feishu import push_interactive_card_to_feishu_webhook
from app.domain.price_change_alerts.cards import (
    build_price_change_alert_fallback_text,
    build_price_change_alert_feishu_card,
    price_change_alert_title,
)
from app.domain.price_change_alerts.evaluator import (
    best_rates_by_key,
    changed_item,
    competitor_rates_by_key,
    latest_success_run,
    load_watch_group,
    own_rates_by_match_key,
    previous_success_run,
    review_issue_for_run,
    trigger_result,
    trusted_alert_rates_for_run,
)
from app.domain.price_change_alerts.repository import (
    get_latest_price_change_alert_record,
    list_price_change_alert_records,
    save_price_change_alert_record,
    update_price_change_alert_push_result,
)
from app.domain.price_change_alerts.suggestions import build_rule_based_suggestions
from app.domain.price_change_alerts.types import (
    TRUSTED_PRICE_SOURCE,
    TRUSTED_SOURCE_STATEMENT,
    PriceChangeAlertResult,
)
from app.domain.pricing_strategy.repository import (
    DEFAULT_STRATEGY_KEY,
    get_pricing_strategy_config,
)
from app.domain.pricing_suggestions.preview import pricing_account_policy_from_watch_group_model


async def evaluate_price_change_alert(
    *,
    watch_group_key: str,
    current_run_id: int | None = None,
    push_to_feishu: bool = False,
    actor_id: str = "operator",
    strategy_key: str = DEFAULT_STRATEGY_KEY,
) -> PriceChangeAlertResult:
    await ensure_db_ready()
    account_policy = account_write_policy_for_session(None)
    async with session_scope() as session:
        group = await load_watch_group(session, watch_group_key)
        if group is None:
            result = PriceChangeAlertResult(
                status="config_required",
                summary="未找到竞品监控组，无法检测价格变化。",
                watch_group_key=watch_group_key,
                **account_policy,
                current_run_id=current_run_id,
                evidence=_base_evidence(strategy_key=strategy_key),
            )
            return await save_price_change_alert_record(result=result, actor_id=actor_id)
        account_policy = pricing_account_policy_from_watch_group_model(group)

        strategy = await get_pricing_strategy_config(strategy_key=strategy_key)
        if not strategy.enabled:
            result = PriceChangeAlertResult(
                status="no_trigger",
                summary="价格变化提醒策略已停用。",
                watch_group_key=watch_group_key,
                **account_policy,
                current_run_id=current_run_id,
                evidence={
                    **_base_evidence(strategy_key=strategy_key),
                    "account_write_policy": account_policy,
                },
            )
            return await save_price_change_alert_record(result=result, actor_id=actor_id)

        current_run = await latest_success_run(
            session,
            group_id=int(group.id),
            current_run_id=current_run_id,
        )
        if current_run is None or current_run.status != "success":
            status = "needs_review" if current_run is not None else "data_required"
            result = PriceChangeAlertResult(
                status=status,
                summary="缺少当前成功读取 run，无法检测价格变化。",
                watch_group_key=watch_group_key,
                **account_policy,
                current_run_id=int(current_run.id) if current_run is not None else current_run_id,
                evidence={
                    **_base_evidence(strategy_key=strategy_key),
                    "account_write_policy": account_policy,
                },
            )
            return await save_price_change_alert_record(result=result, actor_id=actor_id)

        previous_run = await previous_success_run(
            session,
            group_id=int(group.id),
            current_run=current_run,
        )
        if previous_run is None:
            result = PriceChangeAlertResult(
                status="data_required",
                summary="缺少上一轮成功读取 run，至少需要两次可信读取后才能比较。",
                watch_group_key=watch_group_key,
                **account_policy,
                current_run_id=int(current_run.id),
                evidence={
                    **_base_evidence(strategy_key=strategy_key),
                    "account_write_policy": account_policy,
                },
            )
            return await save_price_change_alert_record(result=result, actor_id=actor_id)

        current_rates = await trusted_alert_rates_for_run(
            session,
            group_id=int(group.id),
            run_id=int(current_run.id),
        )
        previous_rates = await trusted_alert_rates_for_run(
            session,
            group_id=int(group.id),
            run_id=int(previous_run.id),
        )
        current_review_issue = await review_issue_for_run(
            session,
            group_id=int(group.id),
            run_id=int(current_run.id),
        )
        previous_review_issue = await review_issue_for_run(
            session,
            group_id=int(group.id),
            run_id=int(previous_run.id),
        )

    current_by_key = best_rates_by_key(current_rates)
    previous_by_key = best_rates_by_key(previous_rates)
    current_competitor_by_key = competitor_rates_by_key(current_by_key)
    previous_competitor_by_key = competitor_rates_by_key(previous_by_key)
    own_current_by_match = own_rates_by_match_key(current_rates)

    evidence = {
        **_base_evidence(strategy_key=strategy_key),
        "current_run_id": int(current_run.id),
        "previous_run_id": int(previous_run.id),
        "current_key_count": len(current_by_key),
        "previous_key_count": len(previous_by_key),
        "current_competitor_key_count": len(current_competitor_by_key),
        "previous_competitor_key_count": len(previous_competitor_by_key),
        "change_trigger_mode": strategy.change_trigger_mode,
        "change_trigger_percent": strategy.change_trigger_percent,
        "min_delta_yuan": strategy.min_delta_yuan,
        "max_delta_yuan": strategy.max_delta_yuan,
        "account_write_policy": account_policy,
    }
    if not current_competitor_by_key or not previous_competitor_by_key:
        review_issue = current_review_issue or previous_review_issue
        result = PriceChangeAlertResult(
            status="needs_review" if review_issue else "data_required",
            summary=(
                "存在页面结构或价格来源不可信的数据，无法生成价格变化建议。"
                if review_issue
                else "缺少可比较的竞品可信 DOM 房价。"
            ),
            watch_group_key=watch_group_key,
            **account_policy,
            current_run_id=int(current_run.id),
            previous_run_id=int(previous_run.id),
            evidence={**evidence, "review_issue": review_issue} if review_issue else evidence,
        )
        return await save_price_change_alert_record(result=result, actor_id=actor_id)

    changed_items: list[dict[str, Any]] = []
    compared_key_count = 0
    for key, current_rate in current_competitor_by_key.items():
        previous_rate = previous_competitor_by_key.get(key)
        if previous_rate is None:
            continue
        compared_key_count += 1
        delta = current_rate.price_amount_yuan - previous_rate.price_amount_yuan
        trigger = trigger_result(
            previous_price=previous_rate.price_amount_yuan,
            delta=delta,
            trigger_mode=strategy.change_trigger_mode,
            trigger_percent=strategy.change_trigger_percent,
            min_delta_yuan=strategy.min_delta_yuan,
            max_delta_yuan=strategy.max_delta_yuan,
        )
        if trigger["triggered"]:
            changed_items.append(
                changed_item(
                    current_rate=current_rate,
                    previous_rate=previous_rate,
                    current_run=current_run,
                    previous_run=previous_run,
                    trigger=trigger,
                )
            )

    if compared_key_count == 0:
        result = PriceChangeAlertResult(
            status="data_required",
            summary="当前 run 与上一 run 没有匹配的竞品价格维度。",
            watch_group_key=watch_group_key,
            **account_policy,
            current_run_id=int(current_run.id),
            previous_run_id=int(previous_run.id),
            evidence=evidence,
        )
        return await save_price_change_alert_record(result=result, actor_id=actor_id)

    if not changed_items:
        result = PriceChangeAlertResult(
            status="no_trigger",
            summary="本轮竞品可信价格变化未超过配置阈值。",
            watch_group_key=watch_group_key,
            **account_policy,
            current_run_id=int(current_run.id),
            previous_run_id=int(previous_run.id),
            compared_key_count=compared_key_count,
            evidence=evidence,
        )
        return await save_price_change_alert_record(result=result, actor_id=actor_id)

    suggestions, missing_reasons = build_rule_based_suggestions(
        changed_items=changed_items,
        current_competitor_rates=current_competitor_by_key,
        own_current_rates=own_current_by_match,
        strategy=strategy,
        account_write_policy=account_policy,
    )
    if not suggestions:
        only_increase_reasons = set(missing_reasons) == {
            "competitor_price_increase_no_own_price_suggestion"
        }
        status = "change_detected" if only_increase_reasons else "data_required"
        summary = (
            f"检测到 {len(changed_items)} 个超过阈值的竞品价格变化；"
            "本轮为竞品涨价，仅提醒价格变化和涨幅，不建议本店涨价。"
            if only_increase_reasons
            else "竞品价格变化已超过阈值，但缺少本店匹配可信价格，无法生成建议。"
        )
        result = PriceChangeAlertResult(
            status=status,
            summary=summary,
            watch_group_key=watch_group_key,
            **account_policy,
            current_run_id=int(current_run.id),
            previous_run_id=int(previous_run.id),
            changed_count=len(changed_items),
            compared_key_count=compared_key_count,
            changed_items=changed_items,
            evidence={**evidence, "missing_suggestion_reasons": sorted(set(missing_reasons))},
        )
        result = await save_price_change_alert_record(result=result, actor_id=actor_id)
        if push_to_feishu and status == "change_detected":
            push_payload = await push_price_change_alert_to_feishu(
                result=result,
                watch_group_key=watch_group_key,
            )
            result.feishu_push = push_payload
            if result.alert_record_id is not None:
                await update_price_change_alert_push_result(
                    alert_record_id=result.alert_record_id,
                    feishu_push=push_payload,
                )
        return result

    result = PriceChangeAlertResult(
        status="suggestion_ready",
        summary=f"检测到 {len(changed_items)} 个超过阈值的竞品价格变化，已生成规则化建议。",
        watch_group_key=watch_group_key,
        **account_policy,
        current_run_id=int(current_run.id),
        previous_run_id=int(previous_run.id),
        changed_count=len(changed_items),
        suggestion_count=len(suggestions),
        compared_key_count=compared_key_count,
        changed_items=changed_items,
        suggestions=suggestions,
        evidence=evidence,
    )
    result = await save_price_change_alert_record(result=result, actor_id=actor_id)
    if push_to_feishu:
        push_payload = await push_price_change_alert_to_feishu(
            result=result,
            watch_group_key=watch_group_key,
        )
        result.feishu_push = push_payload
        if result.alert_record_id is not None:
            await update_price_change_alert_push_result(
                alert_record_id=result.alert_record_id,
                feishu_push=push_payload,
            )
    return result


async def push_price_change_alert_to_feishu(
    *,
    result: PriceChangeAlertResult,
    watch_group_key: str,
) -> dict[str, Any]:
    from app.domain.competitor_prices.repository import get_competitor_watch_group_detail

    group = await get_competitor_watch_group_detail(
        watch_group_key=watch_group_key,
        include_secrets=True,
        create_if_missing=False,
    )
    if group is None:
        return {"status": "config_required", "summary": "未找到竞品监控组。"}
    webhook_url = str(group.get("feishu_mobile_webhook_url") or group.get("feishu_desktop_webhook_url") or "")
    if not webhook_url:
        return {"status": "config_required", "summary": "未配置飞书推送地址。"}
    title_source = (
        group.get("price_change_alert_title")
        or group.get("brief_title")
        or group.get("name")
        or ""
    )
    title = price_change_alert_title(str(title_source))
    card = build_price_change_alert_feishu_card(result, title=title)
    push_result = await push_interactive_card_to_feishu_webhook(
        title=title,
        card=card,
        webhook_url=webhook_url,
        fallback_text=build_price_change_alert_fallback_text(result),
    )
    payload = push_result.model_dump(mode="json")
    payload.pop("evidence", None)
    return payload


async def get_latest_price_change_alert(
    *,
    watch_group_key: str | None = None,
) -> dict[str, Any] | None:
    return await get_latest_price_change_alert_record(watch_group_key=watch_group_key)


async def list_price_change_alerts(
    *,
    watch_group_key: str | None = None,
    limit: int = 20,
    days: int | None = None,
) -> list[dict[str, Any]]:
    return await list_price_change_alert_records(
        watch_group_key=watch_group_key,
        limit=limit,
        days=days,
    )


def _base_evidence(*, strategy_key: str) -> dict[str, Any]:
    return {
        "strategy_key": strategy_key,
        "trusted_price_source": TRUSTED_PRICE_SOURCE,
        "trusted_source_statement": TRUSTED_SOURCE_STATEMENT,
        "comparison_key": (
            "hotel_config_id + checkin_date/date_label + breakfast_kind + normalized_room_type"
        ),
        "alert_rate_scope": "first_visible_room_type_per_hotel_date",
    }
