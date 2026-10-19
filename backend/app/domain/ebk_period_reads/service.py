"""周/月维度按需读取服务：飞书问答触发，浏览器现读 + append-only 落库。

与自动推送无关：本模块只提供「读当日快照 → 过期则现读」的原子操作，
不挂任何调度器，也不进 EBK 全局推送链路。

自定义日期区间（date_from/date_to，2026-09-16）：历史区间问答直接现读平台
页面的自定义时间控件，不依赖日终快照落库；区间读结果**不落库**（避免上周/
上月区间数据污染「当日 fresh_only」口径），也不参与当日快照缓存。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from app.domain.order_browser import (
    acquire_order_page_for_binding,
    resolve_order_account_binding,
)

from . import repository
from .models import local_now

logger = logging.getLogger(__name__)

_DAY_DATE_PATTERN = re.compile(r"(20\d{2})[/\-年.](\d{1,2})[/\-月.](\d{1,2})")

PERIOD_READ_PLATFORMS = ("ctrip", "meituan", "fliggy")
DATA_KINDS = ("business", "ranking", "pyramid", "flow")
PERIODS = ("day", "week", "month")

_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}

# 各平台页面实际提供的周/月/日维度（2026-09-07 三平台 EBK 页面实测）：
# - 携程经营：预订销售页 预订/在店/离店 各有 按周/按月 按钮组；昨日口径取经营概览
#   页「昨日数据」块（离店销售额/间夜量/成交率/平均卖价+上周同期+竞争圈排名）；
# - 携程排名：竞争排名页 销售排名/流量排名 各有 实时/昨日/上周/上月 单选钮；
# - 美团经营：交易分析 tab 有 今日实时/按日/按周/按月 按钮组（按日=前日数据，
#   页面标注每日9点更新）；
# - 美团排名：同行分析 tab 有 今日实时/昨日/近7天/近30天 单选钮（默认昨日）；
# - 飞猪经营：诊断报告页 日报（含「昨日数据」块）/周报 视图；经营数据页有
#   近30天 单选钮；
# - 飞猪排名：日报/周报内的同行榜单排名表（需关注同行；未关注时为空值）。
_SUPPORTED_DIMENSIONS: set[tuple[str, str, str]] = {
    ("ctrip", "business", "day"),
    ("ctrip", "business", "week"),
    ("ctrip", "business", "month"),
    ("ctrip", "ranking", "day"),
    ("ctrip", "ranking", "week"),
    ("ctrip", "ranking", "month"),
    ("meituan", "business", "day"),
    ("meituan", "business", "week"),
    ("meituan", "business", "month"),
    ("meituan", "ranking", "day"),
    ("meituan", "ranking", "week"),
    ("meituan", "ranking", "month"),
    ("fliggy", "business", "day"),
    ("fliggy", "business", "week"),
    ("fliggy", "business", "month"),
    ("fliggy", "ranking", "day"),
    ("fliggy", "ranking", "week"),
    # 金字塔数据报告（2026-09-16）：今天/过去7天/过去30天/自定义（仅携程）。
    ("ctrip", "pyramid", "day"),
    ("ctrip", "pyramid", "week"),
    ("ctrip", "pyramid", "month"),
    # 流量数据页（2026-09-16 实测）：昨天/过去7天/过去30天/自定义（仅携程）。
    ("ctrip", "flow", "day"),
    ("ctrip", "flow", "week"),
    ("ctrip", "flow", "month"),
}


def is_dimension_supported(platform: str, data_kind: str, period: str) -> bool:
    return (platform, data_kind, period) in _SUPPORTED_DIMENSIONS


# 自定义日期区间支持矩阵（2026-09-16 三平台 EBK 页面 CDP 实测）：
# - 携程经营：预订销售页每组有「自定义」按钮，激活后区间输入框可直接填写；
# - 携程排名：竞争排名页两组「自定义」单选钮 + he-trip-ui 区间日历（1~31 天）；
# - 美团经营：交易分析「自定义」按钮 + mtd 区间日历；
# - 美团排名/飞猪：页面只有固定预设口径（昨日/近7天/近30天 等），无自定义控件。
_CUSTOM_RANGE_UNSUPPORTED_REASONS: dict[tuple[str, str], str] = {
    ("meituan", "ranking"): (
        "美团同行分析页仅提供 今日实时/昨日/近7天/近30天 四个固定口径，"
        "无自定义日期区间；可改问「昨日排名」或按近7天/近30天口径查询。"
    ),
    ("meituan", "pyramid"): "金字塔投放报表仅携程平台提供，美团无此数据。",
    ("fliggy", "pyramid"): "金字塔投放报表仅携程平台提供，飞猪无此数据。",
    ("meituan", "flow"): (
        "流量数据周/月/自定义区间报表仅携程经营报告提供，美团无此维度；"
        "可改问「昨日流量」（实时快照口径）。"
    ),
    ("fliggy", "flow"): (
        "流量数据周/月/自定义区间报表仅携程经营报告提供，飞猪无此维度；"
        "可改问「昨日流量」（实时快照口径）。"
    ),
    ("fliggy", "ranking"): (
        "飞猪排名仅有日报/周报内的同行榜单口径，无自定义日期区间；"
        "可改问「昨日排名」或「上周排名」（周报同行榜单）。"
    ),
}
_CUSTOM_RANGE_MAX_SPAN_DAYS = 31


def supports_custom_range(platform: str, data_kind: str) -> bool:
    return (platform, data_kind) not in _CUSTOM_RANGE_UNSUPPORTED_REASONS


def _validate_custom_range(
    date_from: str | None, date_to: str | None
) -> tuple[str, str] | dict[str, str]:
    """归一化并校验自定义区间；非法时返回 invalid_request 结果 dict。

    只允许读历史区间：date_to 不能晚于昨日（平台页当日/未来日期均不可选，
    当日实时口径由 day/week/month 预设读取覆盖），跨度最多 31 天（携程排名
    页日历的硬限制，统一按最严口径拦截）。
    """
    start = str(date_from or date_to or "").strip()
    end = str(date_to or date_from or "").strip()
    for value in (start, end):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return {
                "status": "invalid_request",
                "reason": f"日期非法：{value}（需为真实存在的 YYYY-MM-DD 日期）。",
            }
    if start > end:
        return {
            "status": "invalid_request",
            "reason": "date_from 不能晚于 date_to。",
        }
    today = local_now().date().isoformat()
    if end >= today:
        return {
            "status": "invalid_request",
            "reason": (
                "自定义区间不能包含今日及未来日期（平台页当日数据未生成，"
                "当日实时口径请直接问「今日数据」）。"
            ),
        }
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if span > _CUSTOM_RANGE_MAX_SPAN_DAYS:
        return {
            "status": "invalid_request",
            "reason": f"自定义区间最多{_CUSTOM_RANGE_MAX_SPAN_DAYS}天（当前{span}天）。",
        }
    return start, end


def _business_date_from_payload(payload: dict[str, Any], *, period: str) -> date | None:
    """day 维度业务日期：优先用页面 data_period_label 提取的真实数据日期，
    提取不到回落 captured - 1（此前一律 captured-1，9 点前美团按日会把前前日
    数据错标成昨日口径）。week/month 无业务日期列。"""

    if period != "day":
        return None
    label = str(payload.get("data_period_label") or "").strip()
    match = _DAY_DATE_PATTERN.search(label) if label else None
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    captured = local_now()
    return (captured - timedelta(days=1)).date()


def _unsupported_reason(platform: str, data_kind: str, period: str) -> str:
    kind_label = {
        "business": "经营数据",
        "flow": "流量数据",
        "pyramid": "金字塔投放",
    }.get(data_kind, "排名数据")
    period_label = {"day": "日", "week": "周", "month": "月"}.get(period, period)
    platform_label = _PLATFORM_LABELS.get(platform, platform)
    if platform == "fliggy" and data_kind == "ranking" and period == "month":
        return (
            f"飞猪 EBK 页面未提供月维度的同行排名"
            f"（诊断报告仅支持日报/周报，经营数据页仅支持今日/昨日/近7天/近30天），"
            f"无法读取{platform_label}{kind_label}的{period_label}维度。"
        )
    return (
        f"{platform_label} EBK 页面未提供{kind_label}的{period_label}维度读取入口。"
    )


def resolve_period_reader(
    platform: str, data_kind: str, period: str
) -> Callable[[Any], Awaitable[dict[str, Any]]] | None:
    """返回 (page, *, period) -> payload 的浏览器读取函数；不支持的维度返回 None。"""
    if not is_dimension_supported(platform, data_kind, period):
        return None
    from app.domain.browser import ebk_period_read_flows as flows

    if platform == "ctrip":
        if data_kind == "pyramid":
            return flows.read_ctrip_pyramid_period
        if data_kind == "flow":
            return flows.read_ctrip_flow_period
        return (
            flows.read_ctrip_business_period
            if data_kind == "business"
            else flows.read_ctrip_ranking_period
        )
    if platform == "meituan":
        return (
            flows.read_meituan_business_period
            if data_kind == "business"
            else flows.read_meituan_ranking_period
        )
    return (
        flows.read_fliggy_business_period
        if data_kind == "business"
        else flows.read_fliggy_ranking_period
    )


async def fetch_period_read(
    *,
    platform: str,
    data_kind: str,
    period: str,
    chat_id: str | None = None,
    source: str = "manual",
    reader: Callable[[Any], Awaitable[dict[str, Any]]] | None = None,
    page: Any | None = None,
    binding: dict[str, str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """现读一个 (platform, data_kind, period) 维度并落库。

    页面无此维度 → not_available（不触浏览器）；店铺无账号绑定 → not_ready；
    需要人工登录 → human_required（不落库）；reader 返回 not_available/error
    等瞬时态也**不落库**（当天下一次问答按过期重读，避免一次页面抖动锁住整日）；
    只有 ok 才 append-only 落库。均以 HTTP 200 + status 的契约返回，不抛出。
    binding 为已解析的店铺账号绑定（日终快照按店铺遍历时注入，避免重复解析），
    落库行记录其 platform_account_id（多店铺同维度快照按账号隔离）。

    date_from/date_to（自定义历史区间）：走平台页「自定义时间」现读，**不落库**
    （历史区间数据不得污染当日 fresh_only 快照口径）；无自定义控件的平台组合
    在解析浏览器前即返回 not_available 并给出可用口径建议。
    """
    platform = str(platform or "").strip().lower()
    data_kind = str(data_kind or "").strip().lower()
    period = str(period or "").strip().lower()

    custom_range: dict[str, str] | None = None
    if str(date_from or "").strip() or str(date_to or "").strip():
        validated = _validate_custom_range(date_from, date_to)
        if isinstance(validated, dict):
            return {
                **validated,
                "platform": platform,
                "data_kind": data_kind,
                "period": period,
            }
        custom_range = {"from": validated[0], "to": validated[1]}
        if not supports_custom_range(platform, data_kind):
            return {
                "status": "not_available",
                "platform": platform,
                "data_kind": data_kind,
                "period": period,
                "date_range": custom_range,
                "reason": _CUSTOM_RANGE_UNSUPPORTED_REASONS[(platform, data_kind)],
            }

    resolved = reader or resolve_period_reader(platform, data_kind, period)
    if resolved is None:
        return {
            "status": "not_available",
            "platform": platform,
            "data_kind": data_kind,
            "period": period,
            "reason": _unsupported_reason(platform, data_kind, period),
        }

    active_page = page
    account_id = str((binding or {}).get("platform_account_id") or "").strip() or None
    if active_page is None:
        resolved_binding = binding or await resolve_order_account_binding(
            platform, chat_id=chat_id
        )
        if resolved_binding is None:
            return {
                "status": "not_ready",
                "platform": platform,
                "data_kind": data_kind,
                "period": period,
                "reason": (
                    "当前店铺未绑定该平台 EBK 浏览器账号，无法按需读取；"
                    "请先在「账号与浏览器」为监控组绑定账号。"
                ),
            }
        account_id = resolved_binding["platform_account_id"]
        active_page = await acquire_order_page_for_binding(platform, resolved_binding)
        if active_page is None:
            return {
                "status": "not_ready",
                "platform": platform,
                "data_kind": data_kind,
                "period": period,
                "reason": (
                    "当前店铺未绑定该平台 EBK 浏览器账号，无法按需读取；"
                    "请先在「账号与浏览器」为监控组绑定账号。"
                ),
            }

    try:
        if custom_range is not None:
            payload = await resolved(
                active_page,
                period=period,
                date_from=custom_range["from"],
                date_to=custom_range["to"],
            )
        else:
            payload = await resolved(active_page, period=period)
    except Exception as exc:  # noqa: BLE001 - Playwright/页面异常兜底，避免 API 500
        logger.exception("period read crashed for %s/%s/%s", platform, data_kind, period)
        return {
            "status": "error",
            "platform": platform,
            "data_kind": data_kind,
            "period": period,
            "date_range": custom_range,
            "reason": f"周/月维度读取异常：{type(exc).__name__}: {str(exc)[:160]}",
        }

    status = str(payload.get("status") or "error")
    if status == "human_required":
        # 登录/验证问题随时可能被运营解决，不落库，下一次问答可立即重试。
        return {
            "status": "human_required",
            "platform": platform,
            "data_kind": data_kind,
            "period": period,
            "date_range": custom_range,
            "reason": str(payload.get("summary") or "需要人工完成登录或验证后重试。"),
            "next_action": str(payload.get("next_action") or "请在 EBK 浏览器窗口完成人工登录或安全验证。"),
        }
    if status != "ok":
        # not_available（按钮缺失/渲染失败等）与 error 同属瞬时态：不落库，
        # 当天下一次问答仍按过期处理并触发重读——避免一次页面抖动锁住整日响应。
        return {
            "status": status,
            "platform": platform,
            "data_kind": data_kind,
            "period": period,
            "date_range": custom_range,
            "caliber": payload.get("caliber"),
            "summary": payload.get("summary"),
            "sections": payload.get("sections"),
            "evidence": payload.get("evidence"),
        }
    if custom_range is not None:
        # 历史区间现读只作答不落库：上周/上月等区间数据一旦落库，会被当日
        # fresh_only 查询当作「今天的周/月快照」读到，口径错位。
        return {
            "status": "ok",
            "platform": platform,
            "data_kind": data_kind,
            "period": period,
            "date_range": custom_range,
            "caliber": payload.get("caliber"),
            "summary": payload.get("summary"),
            "sections": payload.get("sections"),
            "evidence": payload.get("evidence"),
        }

    snapshot = await repository.append_period_read_snapshot(
        platform=platform,
        data_kind=data_kind,
        period=period,
        status=status,
        caliber=payload.get("caliber"),
        summary=payload.get("summary"),
        payload=payload,
        source=source,
        business_date=_business_date_from_payload(payload, period=period),
        platform_account_id=account_id,
    )
    return {"status": "ok", "platform": platform, "snapshot": snapshot}


async def get_period_read(
    *,
    platform: str,
    data_kind: str,
    period: str,
    fresh_only: bool = True,
    platform_account_id: str | None = None,
) -> dict[str, Any] | None:
    """读最新快照；fresh_only 下没有当日数据返回 None（问答端据此触发现读）。

    platform_account_id 给定时按店铺过滤（chat 问答按锚点链解析后传入）。
    """
    return await repository.get_latest_period_read(
        platform=platform,
        data_kind=data_kind,
        period=period,
        fresh_only=fresh_only,
        platform_account_id=platform_account_id,
    )
