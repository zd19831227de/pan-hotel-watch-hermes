"""美团今日预定抓取适配器（订单管理 → 全部订单，今日全部订单口径）。

复用退单适配器的页面 URL、点击 JS 与解析正则；与退单不同处：不筛「已取消」，
统计购买时间=今天的全部订单（任意状态），按房型 + 购买小时段计数。
口径 time_caliber=purchase_time（购买时间，即下单时间）。

全部订单列表按购买时间倒序、今日单在最前；读首屏 body 即可取到今日单（量大会分页，
今日单始终在首屏顶部，跨页历史单不在首屏 body 内、自然不计入）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.refund_orders.meituan_refund_adapter import (
    _BUY_TIME_RE,
    _CLICK_ALL_TAB_JS,
    _CLICK_SEARCH_JS,
    _MEITUAN_NEXT_PAGE_JS,
    _ORDER_URL,
    _extract_room_type,
)

from .adapters import ReservationAdapterError

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class MeituanReservationAdapter:
    """美团订单管理今日预定抓取（read-only，全部今日订单口径）。"""

    platform = "meituan"

    async def fetch_today_reservations(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        today = datetime.now(_LOCAL_TZ).strftime("%Y-%m-%d")
        page = await acquire_order_page("meituan", chat_id=chat_id)
        if page is None:
            raise ReservationAdapterError(
                "当前店铺未绑定美团 EBK 浏览器账号，无法抓取今日预定。"
            )
        await page.goto(_ORDER_URL, wait_until="networkidle", timeout=40000)
        await asyncio.sleep(2)
        target = None
        for frame in page.frames:
            if "order-eb" in frame.url and "ebIframe" not in frame.url:
                target = frame
                break
        if target is None:
            raise ReservationAdapterError("美团订单管理页未加载，请稍后重试。")
        # 全部订单 tab（不筛状态），再点查询触发首屏渲染。
        await target.eval_on_selector_all(
            "a,li,div,span,button", _CLICK_ALL_TAB_JS
        )
        await asyncio.sleep(2)
        await target.eval_on_selector_all("button", _CLICK_SEARCH_JS)
        await asyncio.sleep(3)
        body_text = await _paginate_until_today_complete(target, today=today)

        return _parse_meituan_today_reservations(body_text, today=today)


async def _paginate_until_today_complete(
    frame: Any, *, today: str, max_pages: int = 15
) -> str:
    """按页码翻页，累积各页文本直至今日单读全，返回合并后的文本。

    美团全部订单为 .mtd-pager 页码分页（每页约 10 单，购买时间倒序，今日单在第 1 页起）。
    今日单可能跨多页，故需翻页累积。终止：某页已出现购买日期早于今天的订单
    （今日单读全），或点「下一页」无响应/翻页后内容不变（已是最后一页）。
    合并按「订单号：」整块去重，避免跨页边界或重复页导致重复计数。
    """
    import asyncio

    pages_text: list[str] = []
    for _ in range(max_pages):
        body = await frame.inner_text("body")
        pages_text.append(body)
        if _meituan_today_orders_complete(body, today=today):
            break
        result = await frame.evaluate(_MEITUAN_NEXT_PAGE_JS)
        if result != "clicked":
            break
        await asyncio.sleep(3)
        new_body = await frame.inner_text("body")
        if new_body == body:
            break
    return _merge_order_blocks(pages_text)


def _merge_order_blocks(pages_text: list[str]) -> str:
    """把多页文本按「订单号：」分块去重后合并（同订单号块只保留首次出现）。"""
    seen: set[str] = set()
    kept: list[str] = []
    for body in pages_text:
        for block in body.split("订单号："):
            head = block.split("\n", 1)[0].strip()
            if not head:
                # 第一页首个「订单号：」之前的表头文本保留一次即可。
                if block.strip() and not kept:
                    kept.append(block)
                continue
            if head in seen:
                continue
            seen.add(head)
            kept.append("订单号：" + block)
    return "\n".join(kept)


def _meituan_today_orders_complete(body_text: str, *, today: str) -> bool:
    """美团全部订单当前页是否已读完今日单（出现购买日期早于今天的订单即读全）。"""
    for match in _BUY_TIME_RE.finditer(body_text):
        if match.group(1) != today:
            return True
    return False


def _parse_meituan_today_reservations(
    body_text: str, *, today: str
) -> dict[str, Any]:
    """统计购买日期=今天的全部订单（任意状态），按房型 + 购买小时段计数。"""
    blocks = body_text.split("订单号：")[1:]
    total = 0
    by_room: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    for block in blocks:
        time_match = _BUY_TIME_RE.search(block)
        if not time_match or time_match.group(1) != today:
            continue
        hour = time_match.group(2)
        room = _extract_room_type(block)
        total += 1
        by_room[room] = by_room.get(room, 0) + 1
        if hour:
            by_hour[hour] = by_hour.get(hour, 0) + 1
    return {
        "reservation_date": today,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "purchase_time",
    }
