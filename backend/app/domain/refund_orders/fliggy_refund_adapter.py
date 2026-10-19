"""飞猪退单抓取适配器（订单管理 → 全部订单，按预订日期+已取消状态）。

飞猪订单列表无「已取消」筛选按钮，但订单量少；列表按预订日期排序、带预订时间
（MM/DD HH:MM），直接读列表挑「预订时间=今天 且 状态含已取消」的订单按房型计数。
口径=今日新订且已取消（飞猪列表只有预订时间，无取消时间；订单详情里有退款时间
但需逐单展开，本期不取）。

页面：hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/order/listV2（SPA hash 路由，
直接 goto 即可渲染）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .adapters import RefundAdapterError

_ORDER_URL = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/order/listV2"

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_ORDER_ID_RE = re.compile(r"^\d{15,20}$")
_BOOK_TIME_RE = re.compile(r"^(\d{2}/\d{2}) (\d{2}):\d{2}$")
_ROOM_TYPE_RE = re.compile(
    r"^([\u4e00-\u9fa5A-Za-z0-9]+?(?:套房|房|卧))"
)
_CANCELLED_RE = re.compile(r"已取消")

# 订单列表滚动容器（实测 hotelBaseInfoUv listV2 为 div.left___xxx 内部滚动）。
# 列表按预订日期倒序、今日单在最前；今日单读全的判定：已出现预订日期早于今天的订单，
# 或容器滚到底后订单数不再增长。
_FLIGGY_LIST_CONTAINER_CSS_PREFIX = "div[class^='left___']"
_FLIGGY_SCROLL_TO_BOTTOM_JS = (
    "(s)=>{const el=document.querySelector(s); if(el) el.scrollTop=el.scrollHeight;}"
)


class FliggyRefundAdapter:
    """飞猪订单管理退单抓取（read-only，今日新订且已取消口径）。"""

    platform = "fliggy"

    async def fetch_refund_orders(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        now = datetime.now(_LOCAL_TZ)
        page = await acquire_order_page("fliggy", chat_id=chat_id)
        if page is None:
            raise RefundAdapterError("当前店铺未绑定飞猪 EBK 浏览器账号，无法抓取退单。")
        await page.goto(_ORDER_URL, wait_until="networkidle", timeout=40000)
        await asyncio.sleep(3)
        body_text = await _scroll_until_today_complete(
            page, today_md=now.strftime("%m/%d")
        )

        result = _parse_fliggy_cancelled_orders(
            body_text, today_md=now.strftime("%m/%d"), today_iso=now.strftime("%Y-%m-%d")
        )
        if result["total_count"] == 0 and "共有" not in body_text and len(body_text) < 400:
            raise RefundAdapterError("飞猪订单列表未加载，请稍后重试。")
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
        if _fliggy_refund_list_complete(last_text, today_md=today_md):
            break
        count = len(
            {ln for ln in last_text.split("\n") if _ORDER_ID_RE.match(ln.strip())}
        )
        if count == last_count:
            break
        last_count = count
        await page.evaluate(_FLIGGY_SCROLL_TO_BOTTOM_JS, _FLIGGY_LIST_CONTAINER_CSS_PREFIX)
        await asyncio.sleep(2)
        last_text = await page.inner_text("body")
    return last_text


def _fliggy_refund_list_complete(body_text: str, *, today_md: str) -> bool:
    """飞猪订单列表是否已读全今日单（预订日期倒序，出现早于今天的日期即读全）。"""
    for line in body_text.split("\n"):
        time_match = _BOOK_TIME_RE.match(line.strip())
        if time_match and time_match.group(1) != today_md:
            return True
    return False


def _parse_fliggy_cancelled_orders(
    body_text: str, *, today_md: str, today_iso: str
) -> dict[str, Any]:
    """订单列表中 预订时间=今天 且 状态含「已取消」的订单，按房型计数。

    条目行序：订单号 / 状态（已取消(确认后)/已接单...）/ 房型产品 / 间 / 间字 /
    MM/DD HH:MM / 入离 / 晚 / 客人 / 预付。订单详情区会重复订单号，按订单号去重。
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
            cancelled = False
            book_md: str | None = None
            hour: str | None = None
            room: str | None = None
            for w in window:
                if _ORDER_ID_RE.match(w):
                    break  # 到达下一条订单边界，不跨条目
                if _CANCELLED_RE.search(w) and not cancelled:
                    cancelled = True
                time_match = _BOOK_TIME_RE.match(w)
                if time_match and book_md is None:
                    book_md = time_match.group(1)
                    hour = time_match.group(2)
                room_match = _ROOM_TYPE_RE.match(w)
                if room_match and room is None:
                    room = room_match.group(1)
            if cancelled and book_md == today_md:
                total += 1
                name = room or "未知房型"
                by_room[name] = by_room.get(name, 0) + 1
                if hour:
                    by_hour[hour] = by_hour.get(hour, 0) + 1
            seen.add(line)
        i += 1
    return {
        "refund_date": today_iso,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "booking_time",
    }
