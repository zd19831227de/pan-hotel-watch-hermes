"""Feishu cards for the review management domain.

2026-09-04：半自动收敛为数量提醒后，逐条审批卡/结果卡已下线。
2026-09-16：定时触发改版——数量提醒卡可带本轮实际读取的好/中/差明细；
全自动批处理完成后新增「自动回复完成」统计卡（数量以实际读取/提交结果为准）。
"""

from __future__ import annotations

from typing import Any

PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}
CATEGORY_LABELS = {"good": "好评", "medium": "中评", "bad": "差评"}

# 日报卡跳过原因的运营可读文案：内部 reason 键原样上卡运营读不懂
# （no_template 曾静默积压 71 条待回复，2026-10-08 巡检 F2）。
_SKIP_REASON_LABELS = {
    "no_template": "回复模板缺失",
    "still_pending": "提交未生效",
    "reply_too_long": "回复超长",
    "reply_length_invalid": "回复长度异常",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def build_review_pending_count_alert_card(
    *,
    platform: str,
    pending_count: int,
    store_label: str | None = None,
    by_category: dict[str, int] | None = None,
    pending_read: int | None = None,
) -> dict[str, Any]:
    """半自动收敛为数量提醒：只告知 N 条待回复，提示去 OTA 后台处理，不带正文。

    by_category/pending_read 来自本轮实际读取到的未回复评价（页面首屏），
    数量可能小于平台侧未回复总数，卡上如实标注「本轮实际读取」。
    """
    scope = store_label or platform_label(platform)
    lines = [
        f"**店铺**：{scope}",
        f"**平台**：{platform_label(platform)}",
        f"**待回复**：{pending_count} 条",
    ]
    if by_category:
        read_count = (
            pending_read if pending_read is not None else sum(by_category.values())
        )
        lines.append(
            f"本轮实际读取 {read_count} 条：好评 {by_category.get('good', 0)} 条、"
            f"中评 {by_category.get('medium', 0)} 条、差评 {by_category.get('bad', 0)} 条"
        )
        if by_category.get("bad", 0) > 0:
            lines.append(f"**注意：差评 {by_category['bad']} 条**，请优先处理。")
    lines.append("请到该平台 EBK 后台「评价管理」查看并回复（后台筛选更直接）。")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue" if pending_count > 0 else "grey",
            "title": {
                "tag": "plain_text",
                "content": f"{platform_label(platform)}评价 · {pending_count} 条待回复",
            },
        },
        "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
    }


def build_review_auto_processed_summary_card(
    *,
    platform: str,
    by_category: dict[str, int],
    processed: int,
    skipped: int = 0,
    store_label: str | None = None,
) -> dict[str, Any]:
    """全自动批处理完成统计卡：全部未回复评价已处理 + 好/中/差数量。

    数量只统计实际提交成功的评价（提交失败/无模板的另列一行提醒），
    差评存在时高亮提醒人工跟进。
    """
    scope = store_label or platform_label(platform)
    good = int(by_category.get("good", 0))
    medium = int(by_category.get("medium", 0))
    bad = int(by_category.get("bad", 0))
    lines = [
        f"**店铺**：{scope}",
        f"**平台**：{platform_label(platform)}",
        f"全部未回复评价已处理 {processed} 条，其中好评 {good} 条、"
        f"中评 {medium} 条、差评 {bad} 条。",
    ]
    if bad > 0:
        lines.append(f"**注意：差评 {bad} 条**，请及时人工跟进。")
    if skipped > 0:
        lines.append(f"另有 {skipped} 条未自动处理（无匹配模板或提交失败），请到后台查看。")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if bad > 0 else "green",
            "title": {
                "tag": "plain_text",
                "content": f"{platform_label(platform)}评价 · 自动回复完成（{processed} 条）",
            },
        },
        "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
    }


def build_review_daily_summary_card(
    *,
    entries: list[dict[str, Any]],
    day_label: str,
) -> dict[str, Any]:
    """日报合并卡（2026-09-21 需求）：三平台一张卡，0 待回复平台也占一行。

    entries 每项是一个店铺绑定当日汇总（service.collect_daily_review_summary）：
    platform/store_label/mode/chat_id/fetch{status,pending_count,error}/
    process{submitted,skipped,by_category,skip_reasons}|None。抓取失败标橙，
    其余绿色；数值只来自实际抓取/提交结果，不编造。
    """
    lines: list[str] = []
    any_attention = False
    for entry in entries:
        label = entry.get("store_label") or platform_label(entry.get("platform", ""))
        fetch = entry.get("fetch") or {}
        process = entry.get("process")
        if fetch.get("status") != "completed":
            any_attention = True
            reason = str(fetch.get("error") or "无成功抓取记录")[:40]
            lines.append(f"**{label}**：抓取失败（{reason}），本轮未自动处理")
            continue
        pending = fetch.get("pending_count")
        pending_text = "未知" if pending is None else str(int(pending))
        if entry.get("mode") == "semi_auto":
            lines.append(f"**{label}**：待回复 {pending_text} 条（半自动，请到后台处理）")
            continue
        if not process:
            lines.append(f"**{label}**：待回复 {pending_text} 条，自动处理未执行")
            continue
        submitted = int(process.get("submitted") or 0)
        skipped = int(process.get("skipped") or 0)
        if submitted == 0 and skipped == 0:
            detail = "无待回复" if pending == 0 else f"待回复 {pending_text} 条，本轮未提交"
            lines.append(f"**{label}**：{detail}")
            continue
        reasons = process.get("skip_reasons") or {}
        reason_text = ""
        if skipped:
            top_reasons = "、".join(
                f"{_SKIP_REASON_LABELS.get(reason, reason)}×{count}"
                for reason, count in sorted(
                    reasons.items(), key=lambda item: -int(item[1])
                )[:2]
            )
            reason_text = f"（{top_reasons}）" if top_reasons else ""
        by_category = process.get("by_category") or {}
        lines.append(
            f"**{label}**：待回复 {pending_text} 条，已回复 {submitted}"
            f"（好 {by_category.get('good', 0)} / 中 {by_category.get('medium', 0)}"
            f" / 差 {by_category.get('bad', 0)}），未处理 {skipped}{reason_text}"
        )
        # 全自动店铺整轮零提交且全部被跳过＝链路实际失效（模板缺失/浏览器
        # 掉线）：转橙醒目提示，不能绿色「已结算」静默积压（2026-10-08 F2/F3）。
        if submitted == 0 and skipped > 0:
            any_attention = True
            if reasons.get("no_template"):
                lines[-1] += "；请到「评价管理」补齐该店铺的回复模板，否则待回复将持续积压"
    lines.append("由每日定时任务汇总；单条详情请到各平台 EBK「评价管理」查看。")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if any_attention else "green",
            "title": {
                "tag": "plain_text",
                "content": f"评价日报 · {day_label}",
            },
        },
        "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
    }
