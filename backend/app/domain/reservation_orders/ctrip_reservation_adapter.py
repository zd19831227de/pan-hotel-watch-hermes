"""携程今日预定抓取适配器（订单管理 → 今日预订，全部今日订单口径）。

复用退单适配器的页面导航常量与解析正则（同 CDP 端口、同菜单点击）；与退单不同处：
不筛「已取消」，统计今日预订 tab 内通知时间=今天的全部订单（任意状态），按房型 +
通知小时段计数。口径 time_caliber=notify_time（通知时间）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.refund_orders.ctrip_refund_adapter import (
    _CLICK_ORDER_MENU_JS,
    _CLICK_TODAY_TAB_JS,
    _CTRIP_END_MARKER,
    _CTRIP_LIST_CONTAINER_SELECTOR,
    _CTRIP_SCROLL_TO_BOTTOM_JS,
    _ENTRY_URL,
    _NOTIFY_TIME_RE,
    _ORDER_ID_RE,
    _ROOM_TYPE_RE,
)

from .adapters import ReservationAdapterError

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class CtripReservationAdapter:
    """携程订单管理今日预定抓取（read-only，全部今日订单口径）。"""

    platform = "ctrip"

    async def fetch_today_reservations(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        now = datetime.now(_LOCAL_TZ)
        page = await acquire_order_page("ctrip", chat_id=chat_id)
        if page is None:
            raise ReservationAdapterError(
                "当前店铺未绑定携程 EBK 浏览器账号，无法抓取今日预定。"
            )
        await page.goto(_ENTRY_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(2)
        clicked_menu = await page.eval_on_selector_all(
            "li,div,a,span", _CLICK_ORDER_MENU_JS
        )
        if not clicked_menu:
            raise ReservationAdapterError("未能进入携程订单管理（菜单未找到）。")
        await asyncio.sleep(6)
        clicked_today = await page.eval_on_selector_all(
            "a,li,div,span,button", _CLICK_TODAY_TAB_JS
        )
        if not clicked_today:
            raise ReservationAdapterError("未找到「今日预订」页签。")
        await asyncio.sleep(4)
        body_text = await _scroll_until_today_complete(
            page, today_md=now.strftime("%m/%d")
        )

        return _parse_ctrip_today_reservations(
            body_text, today_md=now.strftime("%m/%d"), today_iso=now.strftime("%Y-%m-%d")
        )


async def _scroll_until_today_complete(
    page: Any, *, today_md: str, max_scrolls: int = 30
) -> str:
    """把今日预订列表容器循环滚到底，直至今日单读全，返回完整 body 文本。

    携程 ebkorderv3 今日预订列表为 div.scrollbar-reveal 内部滚动加载（每段约 20 单），
    首屏只渲染当前时间附近的单，凌晨/早上的单需滚动加载。终止：出现结束标记
    「没有更多订单了」或最旧通知时间早于今天 00:00，或连续两次内容/订单数不再增长。
    """
    import asyncio

    last_text = await page.inner_text("body")
    last_count = -1
    for _ in range(max_scrolls):
        if _ctrip_today_orders_complete(last_text, today_md=today_md):
            break
        count = _count_order_ids(last_text)
        if count == last_count:
            # 连续两次订单数不变，视作已加载完（兜底，防选择器失效导致死循环）。
            break
        last_count = count
        await page.evaluate(
            _CTRIP_SCROLL_TO_BOTTOM_JS, _CTRIP_LIST_CONTAINER_SELECTOR
        )
        await asyncio.sleep(2)
        last_text = await page.inner_text("body")
    return last_text


def _count_order_ids(body_text: str) -> int:
    return len({ln for ln in body_text.split("\n") if _ORDER_ID_RE.match(ln.strip())})


def _ctrip_today_orders_complete(body_text: str, *, today_md: str) -> bool:
    """今日预订列表是否已读全今日单（据此判断是否停止滚动加载）。

    读全信号：出现结束标记「没有更多订单了」，或列表中已出现通知时间早于今天的订单
    （列表按通知时间倒序，一旦读到昨天/更早的单说明今日单已全部加载）。
    """
    if _CTRIP_END_MARKER in body_text:
        return True
    # 逐行匹配通知时间，出现任何非今天的日期即说明已越过今日边界。
    for line in body_text.split("\n"):
        time_match = _NOTIFY_TIME_RE.match(line.strip())
        if time_match and time_match.group(1) != today_md:
            return True
    return False


def _parse_ctrip_today_reservations(
    body_text: str, *, today_md: str, today_iso: str
) -> dict[str, Any]:
    """今日预订列表中 通知时间=今天 的全部订单（任意状态），按房型 + 通知小时段计数。

    条目行序同退单：订单号 / MM/DD HH:MM / 状态 / N 间 / 房型；按订单号去重取首段。
    """
    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
    total = 0
    by_room: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    seen_orders: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _ORDER_ID_RE.match(line) and line not in seen_orders:
            window = lines[i + 1 : i + 7]
            time_md: str | None = None
            hour: str | None = None
            room: str | None = None
            for w in window:
                time_match = _NOTIFY_TIME_RE.match(w)
                if time_match:
                    time_md = time_match.group(1)
                    hour = time_match.group(2)
                room_match = _ROOM_TYPE_RE.match(w)
                if room_match and room is None:
                    room = room_match.group(1)
            if time_md == today_md:
                total += 1
                name = room or "未知房型"
                by_room[name] = by_room.get(name, 0) + 1
                if hour:
                    by_hour[hour] = by_hour.get(hour, 0) + 1
            seen_orders.add(line)
        i += 1
    return {
        "reservation_date": today_iso,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "notify_time",
    }
