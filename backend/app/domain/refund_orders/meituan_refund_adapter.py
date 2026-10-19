"""美团退单抓取适配器（订单管理 → 全部订单 → 状态=已取消）。

口径（2026-09-02 与运营确认可实现口径）：美团订单列表只有「购买时间」没有
「取消时间」，故统计「当日下单且已取消」的订单——购买日期=今天的已取消单，
按房型计数。跨天购买的当日取消单需美团侧取消时间数据源，待后续扩展。

流程：ebIframe 打开订单管理 → 全部订单 tab → 状态筛选=已取消 → 查询 →
读列表按「订单号：」分块，取购买时间=今天的条目统计房型。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .adapters import RefundAdapterError

_ORDER_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe"
    "?iUrl=%2Febooking%2Forder-eb%2Findex.html"
)

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_BUY_TIME_RE = re.compile(r"购买时间[：:]\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}):)?")
_ROOM_TYPE_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z0-9]{2,20}(?:套房|房|卧|间)[\u4e00-\u9fa5A-Za-z0-9（）()]{0,16})")

# 三步筛选 DOM 操作（2026-09-02 实测）。
_CLICK_ALL_TAB_JS = (
    "(els)=>{const el=els.find(e=>(e.innerText||'').trim()==='全部订单'); "
    "if(el) el.click(); return !!el;}"
)
_OPEN_STATE_DROPDOWN_JS = """(els)=>{
  for(const s of els){
    let p=s.parentElement;
    for(let k=0;k<4&&p;k++){
      if((p.innerText||'').trim().startsWith('状态')){
        (s.querySelector('.mtd-select-selector,.mtd-select-selection')||s).click();
        return true;
      }
      p=p.parentElement;
    }
  }
  return false;
}"""
_PICK_CANCELLED_JS = (
    "(els)=>{const opt=els.find(e=>(e.innerText||'').trim()==='已取消' && e.offsetParent!==null); "
    "if(opt){opt.click(); return true;} return false;}"
)
_CLICK_SEARCH_JS = (
    "(els)=>{const el=els.find(e=>(e.innerText||'').trim()==='查询'); "
    "if(el){el.click();return true;} return false;}"
)

# 全部订单列表为页码分页（.mtd-pager，今日单按购买时间倒序在第 1 页起）。
# 读全今日需翻页：当某页已出现购买日期早于今天的订单时，今日单已读完，停止翻页。
# 「下一页」按钮为 .mtd-pager 内最后一个数字页码之后的箭头项（mtd-pager-next 或末项）。
_MEITUAN_NEXT_PAGE_JS = """()=>{
  const pager=document.querySelector('.mtd-pager');
  if(!pager) return 'no-pager';
  const next=pager.querySelector('.mtd-pager-next');
  if(next && !/disabled/.test(next.className||'')){ next.click(); return 'clicked'; }
  return 'no-next';
}"""


class MeituanRefundAdapter:
    """美团订单管理退单抓取（read-only）。"""

    platform = "meituan"

    async def fetch_refund_orders(self, *, chat_id: str | None = None) -> dict[str, Any]:
        import asyncio

        from app.domain.order_browser import acquire_order_page

        today = datetime.now(_LOCAL_TZ).strftime("%Y-%m-%d")
        page = await acquire_order_page("meituan", chat_id=chat_id)
        if page is None:
            raise RefundAdapterError("当前店铺未绑定美团 EBK 浏览器账号，无法抓取退单。")
        await page.goto(_ORDER_URL, wait_until="networkidle", timeout=40000)
        await asyncio.sleep(2)
        target = None
        for frame in page.frames:
            if "order-eb" in frame.url and "ebIframe" not in frame.url:
                target = frame
                break
        if target is None:
            raise RefundAdapterError("美团订单管理页未加载，请稍后重试。")
        await target.eval_on_selector_all(
            "a,li,div,span,button", _CLICK_ALL_TAB_JS
        )
        await asyncio.sleep(2)
        opened = await target.eval_on_selector_all(
            ".mtd-select", _OPEN_STATE_DROPDOWN_JS
        )
        await asyncio.sleep(1)
        picked = (
            await target.eval_on_selector_all(
                "li,div[role=option],.mtd-select-item,.mtd-select-dropdown div",
                _PICK_CANCELLED_JS,
            )
            if opened
            else False
        )
        await asyncio.sleep(1)
        if not picked:
            raise RefundAdapterError("未能在状态筛选中选中「已取消」。")
        await target.eval_on_selector_all("button", _CLICK_SEARCH_JS)
        await asyncio.sleep(3)
        body_text = await _paginate_until_today_complete(target, today=today)

        return _parse_meituan_cancelled_orders(body_text, today=today)


async def _paginate_until_today_complete(
    frame: Any, *, today: str, max_pages: int = 15
) -> str:
    """按页码翻页，累积各页文本直至今日单读全，返回合并后的文本。

    美团全部订单为 .mtd-pager 页码分页（每页约 10 单，购买时间倒序，今日单在第 1 页起）。
    今日单可能跨多页，故需翻页累积。终止：某页已出现购买日期早于今天的订单，或
    点「下一页」无响应/翻页后内容不变（最后一页）。合并按「订单号：」整块去重。
    """
    import asyncio

    pages_text: list[str] = []
    for _ in range(max_pages):
        body = await frame.inner_text("body")
        pages_text.append(body)
        if _meituan_refund_list_complete(body, today=today):
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
                if block.strip() and not kept:
                    kept.append(block)
                continue
            if head in seen:
                continue
            seen.add(head)
            kept.append("订单号：" + block)
    return "\n".join(kept)


def _meituan_refund_list_complete(body_text: str, *, today: str) -> bool:
    """美团全部订单当前页是否已读完今日单（出现购买日期早于今天的订单即读全）。"""
    for match in _BUY_TIME_RE.finditer(body_text):
        if match.group(1) != today:
            return True
    return False


def _parse_meituan_cancelled_orders(
    body_text: str, *, today: str
) -> dict[str, Any]:
    """统计购买日期=今天且状态=已取消（筛选后列表）的订单，按房型计数。"""
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
        "refund_date": today,
        "total_count": total,
        "by_room_type": by_room,
        "by_hour": by_hour,
        "time_caliber": "purchase_time",
    }


def _extract_room_type(block: str) -> str:
    """房型行在购买时间行之后：取第一个匹配房型特征的行，去掉[配置]后缀。"""
    seen_time = False
    for ln in block.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if _BUY_TIME_RE.search(s):
            seen_time = True
            continue
        if not seen_time:
            continue
        match = _ROOM_TYPE_RE.match(s)
        if match:
            name = re.sub(r"[\[【（(].*$", "", match.group(1)).strip()
            return name or "未知房型"
    return "未知房型"
