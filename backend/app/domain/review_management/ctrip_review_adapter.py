"""Ctrip EBK 真实评价适配器（Phase D 携程侧）。

通过 CDP attach 到已登录携程 EBK 浏览器（默认 9221），导航到点评列表页
`/comment/commentList`，点「全部点评」展开单条评价解析（fetch），
或点「待回复」tab 定位回复框提交（submit）。

- fetch_reviews：真实 read-only，已接通。
- submit_reply / submit_pending_replies：携程回复提交逻辑已按美团模式预写
  （DOM 标记定位 textarea + 含「回复」按钮）。TODO：该酒店当前待回复为 0，
  selector 未实测；待收到未回复评价时直接可测。
"""

from __future__ import annotations

import hashlib
import re

from .adapters import (
    SUBMIT_PAGE_FAILURE_ABORT_THRESHOLD,
    PlatformReviewSnapshot,
    ReviewAdapterError,
    ReviewAdapterNotReadyError,
    ReviewSubmitError,
    to_review_adapter_error,
)
from .types import ReviewItemInput

_COMMENT_LIST_URL = "https://ebooking.ctrip.com/comment/commentList?microJump=true"

_SCORE_RE = re.compile(r"([1-5])\s*(?:超棒|很好|好|一般|较差|很差)")
# 差评/中评卡片不显示「N 很差」词形，总分以独立小数行呈现（如 2.7 / 0.6）。
_SCORE_DECIMAL_RE = re.compile(r"^([0-5](?:\.\d+)?)\s*$", re.MULTILINE)
_TIME_RE = re.compile(r"发表于[:：]\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9:：]+)")
_REPLY_RE = re.compile(r"酒店回复内容[:：]\s*([\s\S]*?)(?:\n\s*回复员工|$)")

_CARD_JS = """() => {
  const sels = document.querySelectorAll('he-trip-kit-ui-space-vertical he-trip-kit-ui-space-item');
  let texts = [...sels].map(e => (e.innerText || '').trim()).filter(t => t.includes('发表于'));
  if (texts.length === 0) {
    const seen = new Set();
    const divs = [...document.querySelectorAll('div')];
    for (const d of divs) {
      const t = (d.innerText || '').trim();
      if (t.includes('发表于') && t.length > 80 && t.length < 1600 && !seen.has(t)) {
        const p = d.parentElement;
        if (p && (p.innerText || '').trim() === t) continue;
        seen.add(t); texts.push(t);
      }
    }
  }
  return texts.slice(0, 20);
}"""

_HOTEL_NAME_JS = """() => {
  const el = [...document.querySelectorAll('div,span,a')]
    .find(e => { const t = (e.innerText || '').trim(); return t.includes('店') && t.length < 50 && e.children.length === 0; });
  return el ? el.innerText.trim() : '';
}"""

# 按 prefix 定位特定评价卡片（手动单条回复用）：标记其 textarea + 含「回复」按钮。
# 多张卡片内容前缀相同（短评价/模板化好评常见）时返回 'ambiguous'，由上层报错
# 拒绝提交，避免把回复写到错误的评价上。
_MARK_BY_PREFIX_JS = """(prefix)=>{
  document.querySelectorAll('textarea').forEach(e=>e.removeAttribute('data-rv-target'));
  document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
  const tas=[...document.querySelectorAll('textarea')];
  let hits=0;
  for(const ta of tas){
    let p=ta.parentElement, card=null;
    for(let k=0;k<10&&p;k++){
      const t=p.innerText||'';
      if(t.includes('发表于')&&t.includes(prefix)&&t.length<3000){card=p;break;}
      p=p.parentElement;
    }
    if(!card) continue;
    hits++;
    if(hits>1) return 'ambiguous';
    ta.setAttribute('data-rv-target','1');
    let bp=card;
    for(let k=0;k<6&&bp;k++){
      const b=[...bp.querySelectorAll('button')].find(x=>{
        const tx=(x.innerText||'').replace(/\\s/g,'');
        return (tx.includes('回复')||tx.includes('发表'))&&!x.className.includes('disabled')&&!x.disabled;
      });
      if(b){b.setAttribute('data-rv-btn','1');break;}
      bp=bp.parentElement;
    }
    return true;
  }
  return hits===1;
}"""

# 定位第一个未处理卡片（批量用）：handled 为已处理 key 列表。
_MARK_FIRST_JS = """(handled)=>{
  document.querySelectorAll('textarea').forEach(e=>e.removeAttribute('data-rv-target'));
  document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
  const tas=[...document.querySelectorAll('textarea')];
  for(const ta of tas){
    let p=ta.parentElement, card=null;
    for(let k=0;k<10&&p;k++){
      const t=p.innerText||'';
      if(t.includes('发表于')&&t.length<3000){card=p;break;}
      p=p.parentElement;
    }
    if(!card) continue;
    const key=card.innerText.slice(0,40);
    if(handled.includes(key)) continue;
    ta.setAttribute('data-rv-target','1');
    let bp=card;
    for(let k=0;k<6&&bp;k++){
      const b=[...bp.querySelectorAll('button')].find(x=>{
        const tx=(x.innerText||'').replace(/\\s/g,'');
        return (tx.includes('回复')||tx.includes('发表'))&&!x.className.includes('disabled')&&!x.disabled;
      });
      if(b){b.setAttribute('data-rv-btn','1');break;}
      bp=bp.parentElement;
    }
    return {key:key, text:card.innerText};
  }
  return null;
}"""

_TARGET_STILL_JS = """()=>document.querySelector('textarea[data-rv-target]')!==null"""

# 点指定页码（列表底部分页）。优先按 class 精确匹配（pagination-item-{n}），
# 省略号折叠时目标页码通常仍在当前页邻近窗口内；找不到返回 false 由上层终止。
_CLICK_PAGE_JS = """(n) => {
  const items = [...document.querySelectorAll('li[class*="pagination-item"]')];
  const el = items.find(e => (e.className || '').includes('pagination-item-' + n))
    || items.find(e => (e.innerText || '').trim() === String(n));
  if (!el || el.className.includes('active') || el.className.includes('disabled')) return false;
  el.click();
  return true;
}"""

# 历史回复翻页上限：每页约 20 条，40 页封顶防失控（大列表店铺按 limit 截断）。
_HISTORY_MAX_PAGES = 40


class CtripReviewAdapter:
    """携程 EBK 点评列表真实抓取适配器（session_port 指定店铺浏览器，缺省默认端口）。"""

    platform = "ctrip"

    def __init__(self, session_port: int | None = None) -> None:
        self.session_port = session_port
        self._content_cache: dict[str, str] = {}

    async def _with_review_page(self, fn, *, click_all: bool = True):
        """CDP attach 已登录携程浏览器，打开点评列表页执行 fn(page)。"""
        import contextlib

        from app.core.settings import get_settings

        from .adapters import to_review_adapter_error

        port = self.session_port or get_settings().ctrip_ebooking_default_session_port
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}", timeout=3000
                )
            except Exception as exc:
                raise to_review_adapter_error(exc) from exc
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(_COMMENT_LIST_URL, wait_until="networkidle", timeout=40000)
                if click_all:
                    await page.eval_on_selector_all(
                        "a,li,div,span,button",
                        "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('全部点评')); if(el) el.click(); return true;}",
                    )
                    await page.wait_for_timeout(3000)
                return await fn(page)
            except ReviewAdapterError:
                raise
            except Exception as exc:
                raise to_review_adapter_error(exc) from exc
            finally:
                # close 自身抛错（浏览器已被人工关闭等）不能掩盖原始异常。
                with contextlib.suppress(Exception):
                    await page.close()

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        async def read(page):
            hotel_name = await page.evaluate(_HOTEL_NAME_JS)
            card_texts = await page.evaluate(_CARD_JS)
            body_text = await page.evaluate("() => document.body.innerText")
            return hotel_name, card_texts, body_text

        hotel_name, card_texts, body_text = await self._with_review_page(read)
        items: list[ReviewItemInput] = []
        for card_text in card_texts:
            parsed = _parse_ctrip_review_card(card_text)
            if parsed is not None:
                items.append(parsed)
        for item in items:
            if item.reply_status == "pending" and item.content:
                self._content_cache[item.review_id] = item.content[:30]
        return PlatformReviewSnapshot(
            hotel_id=(hotel_name or "").strip() or "ctrip_default",
            reviews=items,
            pending_total=_parse_pending_total(body_text),
        )

    async def fetch_history_replies(self, limit: int = 200):
        """双轮拉全已回复评价（此前只取首页约 20 条，近期评价几乎全是好评，
        店铺历史里回复过的中/差评永远进不了模板样本）：
        1. 「差评」筛选轮优先（差评已回复样本最稀缺）；
        2. 「全部点评」列表翻页补满（好评/中评已回复）。"""

        from .adapters import HistoryReply

        async def collect_pages(page, out, seen, hotel_id):
            for page_no in range(1, _HISTORY_MAX_PAGES + 1):
                for text in await page.evaluate(_CARD_JS):
                    parsed = _parse_ctrip_review_card(text)
                    if parsed is None or parsed.review_id in seen:
                        continue
                    seen.add(parsed.review_id)
                    if parsed.reply_status != "replied" or not parsed.reply_content:
                        continue
                    out.append(
                        HistoryReply(
                            review_id=parsed.review_id,
                            hotel_id=hotel_id,
                            user_nickname=parsed.user_nickname,
                            score=parsed.score,
                            content=parsed.content,
                            review_time=parsed.review_time,
                            reply_content=parsed.reply_content,
                        )
                    )
                if len(out) >= limit or page_no >= _HISTORY_MAX_PAGES:
                    return
                if not await page.evaluate(_CLICK_PAGE_JS, page_no + 1):
                    return
                await page.wait_for_timeout(2500)

        async def collect(page):
            hotel_name = await page.evaluate(_HOTEL_NAME_JS)
            hotel_id = (hotel_name or "").strip() or "ctrip_default"
            out: list[HistoryReply] = []
            seen: set[str] = set()
            # 第一轮：差评筛选优先——差评样本最稀缺，且全部列表翻页会先把
            # limit 配额占满（近期已回复几乎全是好评/中评），后跑永远收不到。
            bad_tab = await page.eval_on_selector_all(
                "a,li,div,span,button",
                "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('差评'));"
                " if(el){el.click(); return true;} return false;}",
            )
            if bad_tab:
                await page.wait_for_timeout(2500)
                await collect_pages(page, out, seen, hotel_id)
            # 第二轮：重新加载列表页回到「全部点评」第 1 页（差评视图里用文本
            # 定位回切可能点中无事件的内层 span，goto 重开最可靠）。
            if len(out) < limit:
                await page.goto(_COMMENT_LIST_URL, wait_until="networkidle", timeout=40000)
                await page.wait_for_timeout(2500)
                await collect_pages(page, out, seen, hotel_id)
            return out[:limit]

        return await self._with_review_page(collect)

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        """单条提交（手动回复路径）。TODO 待有待回复样本时验证 selector。"""
        if len(reply_content) > 500:
            raise ReviewSubmitError("携程回复内容不能超过 500 字。")
        if review_id not in self._content_cache:
            await self.fetch_reviews()
        prefix = self._content_cache.get(review_id)
        if not prefix:
            raise ReviewSubmitError("未找到该待回复评价，可能已被回复或不在当前列表。")

        async def do(page):
            await page.eval_on_selector_all(
                "a,li,div,span,button",
                "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('待回复')); if(el) el.click(); return true;}",
            )
            await page.wait_for_timeout(2000)
            found = await page.evaluate(_MARK_BY_PREFIX_JS, prefix)
            if found == "ambiguous":
                raise ReviewSubmitError(
                    "定位到多条内容前缀相同的评价，为避免回复错配已拒绝提交，请到携程后台手动回复。"
                )
            if not found:
                raise ReviewSubmitError("页面上未定位到该评价的回复框。")
            await page.locator("textarea[data-rv-target='1']").fill(reply_content)
            await page.wait_for_timeout(500)
            button = page.locator("button[data-rv-btn='1']")
            cls = (await button.get_attribute("class")) or ""
            if "disabled" in cls:
                raise ReviewSubmitError("「回复」按钮未启用（内容可能未生效）。")
            await button.click()
            await page.wait_for_timeout(4000)
            still = await page.evaluate(_TARGET_STILL_JS)
            if still:
                raise ReviewSubmitError("回复未提交成功（该评价仍在未回复列表）。")
            self._content_cache.pop(review_id, None)
            return {"ok": True, "review_id": review_id}

        return await self._with_review_page(do, click_all=False)

    async def submit_pending_replies(self, generator, *, max_count: int = 20) -> list[dict]:
        """批量回复待回复评价：每条一个独立会话（同美团口径）。

        TODO：携程待回复 selector 待样本验证。当前该酒店待回复为 0，mark 找不到
        卡片会返回空列表（安全）；有待回复时直接可测。

        2026-09-17：默认从 5 提到 20（同美团口径）。
        """
        results: list[dict] = []
        handled: list[str] = []
        page_failures = 0
        for _ in range(max_count * 3):
            if sum(1 for r in results if r.get("ok")) >= max_count:
                break

            async def one_session(page):
                await page.eval_on_selector_all(
                    "a,li,div,span,button",
                    "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('待回复')); if(el) el.click(); return true;}",
                )
                await page.wait_for_timeout(2000)
                card = await page.evaluate(_MARK_FIRST_JS, handled)
                if card is None:
                    return None
                text = card.get("text") or ""
                key = card.get("key") or text[:40]
                parsed = _parse_ctrip_review_card(text)
                score = parsed.score if parsed is not None else None
                reply = None
                if parsed is not None:
                    reply = generator(
                        {
                            "score": parsed.score,
                            "content": parsed.content,
                            "review_time": parsed.review_time,
                            "user_nickname": parsed.user_nickname,
                        }
                    )
                if not reply:
                    return {"ok": False, "reason": "no_template", "content": key, "key": key, "score": score}
                if len(reply) > 500:
                    return {"ok": False, "reason": "reply_too_long", "content": key, "key": key, "score": score}
                await page.locator("textarea[data-rv-target='1']").fill(reply)
                await page.wait_for_timeout(500)
                button = page.locator("button[data-rv-btn='1']")
                for _ in range(10):
                    cls = (await button.get_attribute("class")) or ""
                    if "disabled" not in cls:
                        break
                    await page.wait_for_timeout(300)
                await button.click()
                await page.wait_for_timeout(4000)
                still = await page.evaluate(_TARGET_STILL_JS)
                ok = not still
                if ok and parsed is not None:
                    fingerprint = f"{parsed.review_time}|{(parsed.content or '')[:80]}"
                    self._content_cache.pop(
                        "ctrip_" + hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16],
                        None,
                    )
                return {"ok": ok, "reason": None if ok else "still_pending", "content": key, "key": key, "score": score}

            try:
                row = await self._with_review_page(one_session, click_all=False)
            except Exception as exc:
                results.append({"ok": False, "reason": str(exc)[:80], "content": ""})
                # 连续页面会话失败＝浏览器掉线/假死：熔断放弃本轮（F3），
                # 避免 60 次撞墙白耗约 40 分钟；间歇失败成功后即清零。
                page_failures += 1
                if page_failures >= SUBMIT_PAGE_FAILURE_ABORT_THRESHOLD:
                    break
                continue
            page_failures = 0
            if row is None:
                break
            if row.get("key"):
                handled.append(row["key"])
            results.append(
                {
                    "ok": row.get("ok"),
                    "reason": row.get("reason"),
                    "content": row.get("content"),
                    "key": row.get("key"),
                    "score": row.get("score"),
                }
            )
        return results


def _parse_ctrip_review_card(text: str) -> ReviewItemInput | None:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return None
    nick = _extract_ctrip_nickname(lines, text)
    score_match = _SCORE_RE.search(text)
    score = float(score_match.group(1)) if score_match else None
    if score is None:
        # 差评/中评卡片：独立小数总分行（2.7 / 0.6），无「N 很差」词形。
        decimal_match = _SCORE_DECIMAL_RE.search(text)
        score = float(decimal_match.group(1)) if decimal_match else None
    time_match = _TIME_RE.search(text)
    review_time = time_match.group(1) if time_match else None

    time_split = _TIME_RE.split(text)
    before_published = time_split[0] if time_split else text
    content_lines: list[str] = []
    for ln in before_published.split("\n"):
        s = ln.strip()
        if not s or s == nick:
            continue
        if _SCORE_RE.search(s) or _NICK_LINE_RE.match(s):
            continue
        if s.startswith(("设施", "卫生", "环境", "服务")):
            continue
        if "反馈异常" in s or s.startswith("20") or "超棒" in s or "很好" in s:
            continue
        if _PAGE_NOTICE_RE.search(s) or _ROOM_TYPE_RE.match(s):
            continue
        content_lines.append(s)
    content = "\n".join(content_lines).strip() or None

    replied = "酒店回复内容" in text
    reply_content: str | None = None
    if replied:
        reply_match = _REPLY_RE.search(text)
        reply_content = reply_match.group(1).strip() if reply_match else None

    fingerprint = f"{review_time}|{(content or '')[:80]}"
    review_id = "ctrip_" + hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16]
    if len(review_id) > 128:
        review_id = review_id[:128]

    return ReviewItemInput(
        review_id=review_id,
        user_nickname=nick,
        score=score,
        content=content,
        review_time=review_time,
        reply_status="replied" if replied else "pending",
        reply_content=reply_content,
    )


_NICK_LINE_RE = re.compile(
    r"^(匿名用户|M[0-9]{2,}\*{0,}|E[0-9]{2,}\*{0,}|[A-Z]\d{2,}\*{0,})"
)
_PAGE_NOTICE_RE = re.compile(r"(不计入点评分数|一人一票|新鲜度|选取规则|系统考虑|展示，采取)")
_ROOM_TYPE_RE = re.compile(r"^[^。！？，；]{0,40}(套房|房|卧|厅|卫|家电|智能|会客)[^。！！]{0,30}$")


def _extract_ctrip_nickname(lines: list[str], text: str) -> str | None:
    for line in lines:
        if _NICK_LINE_RE.match(line):
            return line
    return None


_PENDING_TOTAL_RE = re.compile(r"待回复\s*[（(]?\s*(\d+)\s*[)）]?")


def _parse_pending_total(body_text: str) -> int | None:
    match = _PENDING_TOTAL_RE.search(body_text or "")
    return int(match.group(1)) if match else None
