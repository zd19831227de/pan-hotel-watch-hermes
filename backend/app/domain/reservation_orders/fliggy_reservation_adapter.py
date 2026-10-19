"""飞猪今日预定抓取适配器（订单列表 listV2，今日全部订单口径）。

复用退单适配器的页面 URL 与解析正则；不筛「已取消」，统计预订时间=今天的全部订单
（任意状态），按房型 + 预订小时段计数。口径 time_caliber=booking_time（预订时间）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.refund_orders.fliggy_refund_adapter import (
    _BOOK_TIME_RE,
    _FLIGGY_LIST_CONTAINER_CSS_PREFIX,
    _FLIGGY_SCROLL_TO_BOTTOM_JS,
    _ORDER_ID_RE,
    _ORDER_URL,
    _ROOM_TYPE_RE,
)

from .adapters import ReservationAdapterError

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class FliggyReservationAdapter:
    """飞猪订单列表今日预定抓取（read-only，今日全部订单口径）。"""

    platform = "fliggy"

    async def fetch_today_reservations(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        now = datetime.now(_LOCAL_TZ)
        page = await acquire_order_page("fliggy", chat_id=chat_id)
        if page is None:
            raise ReservationAdapterError(
                "当前店铺未绑定飞猪 EBK 浏览器账号，无法抓取今日预定。"
            )
        await page.goto(_ORDER_URL, wait_until="networkidle", timeout=40000)
        await asyncio.sleep(3)
        body_text = await _scroll_until_today_complete(
            page, today_md=now.strftime("%m/%d")
        )

        result = _parse_fliggy_today_reservations(
            body_text, today_md=now.strftime("%m/%d"), today_iso=now.strftime("%Y-%m-%d")
        )
        if result["total_count"] == 0 and "共有" not in body_text and len(body_text) < 400:
            raise ReservationAdapterError("飞猪订单列表未加载，请稍后重试。")
        return result


async def _scroll_until_today_complete(
    page: Any, *, today_md: str, max_scrolls: int = 30
) -> str:
    """把订单列表容器循环滚到底，直至今日单读全，返回完整 body 文本。

    飞猪 listV2 列表按预订日期倒序、内部容器滚动加载。终止：已出现预订日期早于今天的
    订单，或容器滚到底后订单数不再增长（兜底防死循环）。
    """
    import asyncio

    last_text = await page.inner_text("body")
    last_count = -1
    for _ in range(max_scrolls):
        if _fliggy_today_orders_complete(last_text, today_md=today_md):
            break
        count = _count_order_ids(last_text)
        if count == last_count:
            break
        last_count = count
        await page.evaluate(
            _FLIGGY_SCROLL_TO_BOTTOM_JS, _FLIGGY_LIST_CONTAINER_CSS_PREFIX
        )
        await asyncio.sleep(2)
        last_text = await page.inner_text("body")
    return last_text


def _count_order_ids(body_text: str) -> int:
    return len({ln for ln in body_text.split("\n") if _ORDER_ID_RE.match(ln.strip())})


def _fliggy_today_orders_complete(body_text: str, *, today_md: str) -> bool:
    """飞猪订单列表是否已读全今日单（预订日期倒序，出现早于今天的日期即读全）。"""
    for line in body_text.split("\n"):
        time_match = _BOOK_TIME_RE.match(line.strip())
        if time_match and time_match.group(1) != today_md:
            return True
    return False


def _parse_fliggy_today_reservations(
    body_text: str, *, today_md: str, today_iso: str
) -> dict[str, Any]:
    """订单列表中 预订时间=今天 的全部订单（任意状态），按房型 + 预订小时段计数。

    条目行序同退单；按订单号去重，到达下一条订单边界不跨条目。
    """
    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
    total = 0
    by_room: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _ORDER_ID_RE.match(line) and line not in seen:
            window = lines[i + 1 : i + 9]
            book_md: str | None = None
            hour: str | None = None
            room: str | None = None
            for w in window:
                if _ORDER_ID_RE.match(w):
                    break
                time_match = _BOOK_TIME_RE.match(w)
                if time_match and book_md is None:
                    book_md = time_match.group(1)
                    hour = time_match.group(2)
                room_match = _ROOM_TYPE_RE.match(w)
                if room_match and room is None:
                    room = room_match.group(1)
            if book_md == today_md:
                total += 1
                name = room or "未知房型"
                by_room[name] = by_room.get(name, 0) + 1
                if hour:
                    by_hour[hour] = by_hour.get(hour, 0) + 1
            seen.add(line)
        i += 1
    return {
        "reservation_date": today_iso,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "booking_time",
    }
