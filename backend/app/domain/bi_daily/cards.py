"""经营 BI 日报飞书卡：口语化摘要 + 共享表格助手渲染；不放原始 JSON。

推送目标 = 店铺绑定的携程 EBK 锚点组 chat_id（与评价日报同通道）；
幂等由 ``bi_daily_card_pushes`` (shop_key, push_date) 唯一约束保证。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bi_daily import repository, service
from app.domain.bi_daily.models import local_now
from app.domain.notifications.feishu_card_tables import render_metric_group_pages

_WEEKDAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

_COMPETITOR_STATUS_TEXT = {
    "fresh": "竞品价格盯盘正常更新中",
    "stale": "竞品价格盯盘已有一段时间没更新，建议到后台看一眼是否掉线",
    "no_group": "这家店铺还没有绑定竞品监控组，绑定后日报才会带竞品价格",
    "empty": "已绑定竞品监控组，暂未采集到竞品价格",
    "unbound": "存在未绑店的竞品监控组（不计入本店）",
}

_METRIC_NOTE_TEXT = (
    "说明：本店未来在手预订/总房量来自携程房价房态日历页（携程渠道已售），未采到先空着（不是 0）；"
    "竞争圈在手/出租率与市场热度为携程市场分析页口径，不由价格推算。"
)


def _fmt_rate(completion: dict[str, Any]) -> str:
    value = completion.get("value") if isinstance(completion, dict) else None
    if value is None:
        return "暂无数据"
    return f"{float(value):.0%}"


def _fmt_date_label(stay_date: str, weekday: int) -> str:
    return f"{stay_date[5:]} {_WEEKDAY_LABELS.get(weekday, '')}".strip()


def build_bi_daily_card(*, report: dict[str, Any], shop_name: str) -> dict[str, Any]:
    """经营 BI 日报卡：数据情况（口语）+ 未来价格带表 + 近几日表现表。"""
    quality = (report.get("quality") or {}).get("coverage") or {}
    dimensions = quality.get("dimensions") or {}

    lines: list[str] = []
    competitor_dim = dimensions.get("competitor_prices") or {}
    competitor_status = str(competitor_dim.get("status") or "empty")
    lines.append(
        f"竞品价格：{_COMPETITOR_STATUS_TEXT.get(competitor_status, competitor_status)}"
    )
    budget_dim = dimensions.get("budget_forecast") or {}
    if budget_dim.get("status") == "imported":
        lines.append(f"预算：已导入版本 {budget_dim.get('latest_version')}")
    else:
        lines.append("预算：还没有导入预算文件；导入后日报会给出预算完成率")
    actual_dim = dimensions.get("own_actuals") or {}
    if actual_dim.get("status") == "covered":
        latest = actual_dim.get("latest_business_date")
        lines.append(f"本店经营实际：最新到 {latest}（每日采集自动更新）")
    else:
        lines.append("本店经营实际：还没有采集到历史经营数据")

    groups: list[dict[str, Any]] = []
    price_rows = []
    for row in report.get("forward") or []:
        competitor = row.get("competitor") or {}
        prices = competitor.get("prices")
        if not prices:
            continue
        own_price = next(
            (str(h.get("price")) for h in competitor.get("hotels", []) if h.get("role") == "own" and h.get("price")),
            "—",
        )
        price_rows.append(
            {
                "field": _fmt_date_label(row["stay_date"], row["weekday"]),
                "parts": [
                    f"竞品 ¥{prices['min']}–¥{prices['max']}",
                    f"本店 ¥{own_price}" if own_price != "—" else "本店 —",
                ],
            }
        )
    if price_rows:
        groups.append({"title": "未来14天竞品价格带（可信价格，含房型价）", "rows": price_rows})

    performance_rows = []
    for day in (report.get("performance") or [])[-3:]:
        totals = day.get("totals") or {}
        prior = day.get("prior_year") or {}
        performance_rows.append(
            {
                "field": _fmt_date_label(day["stay_date"], day["weekday"]),
                "parts": [
                    f"间夜 {totals.get('booked_room_nights') if totals.get('booked_room_nights') is not None else '暂无'}",
                    f"同比 {_fmt_rate(prior.get('completion') or {})}",
                    f"预算 {_fmt_rate((day.get('budget') or {}).get('completion') or {})}",
                    f"预测 {_fmt_rate((day.get('forecast') or {}).get('completion') or {})}",
                ],
            }
        )
    if performance_rows:
        groups.append({"title": "近几日表现（完成率=实际÷基线）", "rows": performance_rows})

    lines.append(_METRIC_NOTE_TEXT)
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "\n".join(lines)},
    ]
    # 当前最多 3 张表（14 行价格带拆 2 表 + 近几日表现 1 表）≤ 单卡 5 表上限，
    # 不会产生第二页；若未来组数增多导致多页，需按助手契约拆成多张卡推送。
    pages = render_metric_group_pages(groups)
    if pages:
        elements.extend(pages[0])

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if competitor_status == "fresh" else "orange",
            "title": {
                "tag": "plain_text",
                "content": f"经营 BI 日报 · {shop_name}",
            },
        },
        "elements": elements,
    }


async def push_bi_daily_reports(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    pusher: Callable[..., Awaitable[Any]] | None = None,
) -> list[dict[str, Any]]:
    """给每家店铺推经营 BI 日报卡（锚点组 chat_id；已推过当日跳过）。"""
    from app.domain.ctrip_ebooking_watch.models import CtripEbookingWatchGroup
    from app.domain.shops import repository as shops_repository

    current = now or local_now()
    results: list[dict[str, Any]] = []

    async def _default_pusher(chat_id: str, card: dict[str, Any], fallback_text: str):
        from app.domain.notifications.feishu_client import FeishuBotClient

        return await FeishuBotClient(chat_id=chat_id).send_interactive_card(
            chat_id=chat_id, card=card, fallback_text=fallback_text
        )

    send = pusher or _default_pusher
    shops = await shops_repository.list_shops()
    for shop in shops:
        entry: dict[str, Any] = {"shop_key": shop.shop_key, "shop_name": shop.name}
        # 幂等记录依赖 (shop_key, push_date) 唯一约束；单 worker 调度下无并发
        # 竞态，跨进程并发由约束兜底拒绝。
        already = await repository.record_card_push_once(
            session,
            shop_key=shop.shop_key,
            push_date=current.date(),
            status="sent",
            detail=None,
        )
        if already is None:
            entry["status"] = "already_pushed_today"
            results.append(entry)
            continue
        try:
            anchor = (
                await session.execute(
                    select(CtripEbookingWatchGroup.feishu_chat_id).where(
                        CtripEbookingWatchGroup.shop_key == shop.shop_key
                    )
                )
            ).scalar_one_or_none()
            if not anchor:
                already.status = "skipped"
                already.detail = "店铺未绑定携程锚点组或锚点组未配置飞书群"
                entry["status"] = "skipped_no_chat"
                results.append(entry)
                continue
            report = await service.build_daily_report(
                session=session, shop_key=shop.shop_key, as_of=current
            )
            card = build_bi_daily_card(report=report, shop_name=shop.name)
            await send(anchor, card, f"经营 BI 日报 · {shop.name}")
            entry["status"] = "sent"
        except Exception as exc:  # noqa: BLE001 - 单店失败记录后继续，不阻断其他店
            already.status = "failed"
            already.detail = str(exc)[:200]
            entry["status"] = "failed"
        results.append(entry)
    return results
