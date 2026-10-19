"""携程退单抓取适配器（订单管理 → 今日预订，通知时间含取消时间）。

携程订单列表条目带通知时间（对已取消单即取消时间），可精确统计
「当天取消」：今日预订 tab 中 状态=已取消 且通知时间=今天，按房型计数。

流程：dataReport 页点「订单管理」微前端菜单（直接 goto 订单页列表不渲染，
必须菜单点击进入）→ 点「今日预订」→ 读列表解析。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .adapters import RefundAdapterError

_ENTRY_URL = "https://ebooking.ctrip.com/toolcenter/cpc/dataReport?microJump=true"

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_ORDER_ID_RE = re.compile(r"^\d{15,20}$")
_NOTIFY_TIME_RE = re.compile(r"^(\d{2}/\d{2}) (\d{2}):\d{2}$")
_ROOM_TYPE_RE = re.compile(
    r"^([\u4e00-\u9fa5A-Za-z0-9]{2,24}?(?:套房|房|卧)[\u4e00-\u9fa5A-Za-z0-9]*)[(（<【]"
)

# 订单列表滚到底时的结束提示语（实测于 ebkorderv3 今日预订 tab）。
_CTRIP_END_MARKER = "没有更多订单了"

# 今日预订列表为内部容器（div.scrollbar-reveal）滚动加载：滚到底触发加载下一段，
# 每段约 20 单。读全今日需循环滚到底，直至出现结束标记或最旧通知时间早于今天。
_CTRIP_LIST_CONTAINER_SELECTOR = "div.scrollbar-reveal"
_CTRIP_SCROLL_TO_BOTTOM_JS = (
    "(s)=>{const el=document.querySelector(s); if(el) el.scrollTop=el.scrollHeight;}"
)

_CLICK_ORDER_MENU_JS = """(els)=>{
  const c=els.filter(e=>(e.innerText||'').trim()==='订单管理');
  if(!c.length) return false;
  let t=c.find(e=>e.tagName==='LI')||c[0];
  (t.querySelector('[he-click]')||t).click();
  return true;
}"""
_CLICK_TODAY_TAB_JS = (
    "(els)=>{const el=els.find(e=>(e.innerText||'').trim()==='今日预订'); "
    "if(el){el.click();return true;} return false;}"
)


class CtripRefundAdapter:
    """携程订单管理退单抓取（read-only，当日取消口径）。"""

    platform = "ctrip"

    async def fetch_refund_orders(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        now = datetime.now(_LOCAL_TZ)
        page = await acquire_order_page("ctrip", chat_id=chat_id)
        if page is None:
            raise RefundAdapterError("当前店铺未绑定携程 EBK 浏览器账号，无法抓取退单。")
        await page.goto(_ENTRY_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(2)
        clicked_menu = await page.eval_on_selector_all(
            "li,div,a,span", _CLICK_ORDER_MENU_JS
        )
        if not clicked_menu:
            raise RefundAdapterError("未能进入携程订单管理（菜单未找到）。")
        await asyncio.sleep(6)
        clicked_today = await page.eval_on_selector_all(
            "a,li,div,span,button", _CLICK_TODAY_TAB_JS
        )
        if not clicked_today:
            raise RefundAdapterError("未找到「今日预订」页签。")
        await asyncio.sleep(4)
        body_text = await _scroll_until_today_complete(
            page, today_md=now.strftime("%m/%d")
        )

        return _parse_ctrip_cancelled_orders(
            body_text, today_md=now.strftime("%m/%d"), today_iso=now.strftime("%Y-%m-%d")
        )


async def _scroll_until_today_complete(
    page: Any, *, today_md: str, max_scrolls: int = 30
) -> str:
    """把今日预订列表容器循环滚到底，直至今日单读全，返回完整 body 文本。

    携程 ebkorderv3 今日预订列表为 div.scrollbar-reveal 内部滚动加载（每段约 20 单），
    首屏只渲染当前时间附近的单，凌晨/早上的单需滚动加载。终止：出现结束标记
    「没有更多订单了」或最旧通知时间早于今天 00:00，或连续两次订单数不再增长（兜底）。
    """
    import asyncio

    last_text = await page.inner_text("body")
    last_count = -1
    for _ in range(max_scrolls):
        if _ctrip_refund_list_complete(last_text, today_md=today_md):
            break
        count = len(
            {ln for ln in last_text.split("\n") if _ORDER_ID_RE.match(ln.strip())}
        )
        if count == last_count:
            break
        last_count = count
        await page.evaluate(_CTRIP_SCROLL_TO_BOTTOM_JS, _CTRIP_LIST_CONTAINER_SELECTOR)
        await asyncio.sleep(2)
        last_text = await page.inner_text("body")
    return last_text


def _ctrip_refund_list_complete(body_text: str, *, today_md: str) -> bool:
    """今日预订列表是否已读全今日单（结束标记或出现早于今天的通知时间）。"""
    if _CTRIP_END_MARKER in body_text:
        return True
    for line in body_text.split("\n"):
        time_match = _NOTIFY_TIME_RE.match(line.strip())
        if time_match and time_match.group(1) != today_md:
            return True
    return False


def _parse_ctrip_cancelled_orders(
    body_text: str, *, today_md: str, today_iso: str
) -> dict[str, Any]:
    """今日预订列表中 状态=已取消 且通知时间=今天 的订单，按房型计数。

    条目行序：订单号 / MM/DD HH:MM / 状态 / N 间 / 房型行（带(配置)<标签>）；
    操作记录区会重复订单号，按订单号去重取首段。
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
            # 向后看 5 行：时间 / 状态 / 间数 / （空）/ 房型
            window = lines[i + 1 : i + 7]
            time_md = None
            hour: str | None = None
            cancelled = False
            room: str | None = None
            for j, w in enumerate(window):
                time_match = _NOTIFY_TIME_RE.match(w)
                if time_match:
                    time_md = time_match.group(1)
                    hour = time_match.group(2)
                    if j + 1 < len(window) and window[j + 1] == "已取消":
                        cancelled = True
                room_match = _ROOM_TYPE_RE.match(w)
                if room_match and room is None:
                    room = room_match.group(1)
            if cancelled and time_md == today_md:
                seen_orders.add(line)
                total += 1
                name = room or "未知房型"
                by_room[name] = by_room.get(name, 0) + 1
                if hour:
                    by_hour[hour] = by_hour.get(hour, 0) + 1
            else:
                seen_orders.add(line)
        i += 1
    return {
        "refund_date": today_iso,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "notify_time",
    }
