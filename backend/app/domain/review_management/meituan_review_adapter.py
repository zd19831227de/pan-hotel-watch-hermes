"""Meituan 商家后台真实评价适配器（Phase D 美团侧，read-only 抓取已接通）。

美团商家后台评价管理在 ebIframe 包裹的 iframe 内：
`https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fnew-feedback%2Findex.html%23%2Fmt`
真实评价 SPA 在 `eb.meituan.com/ebooking/new-feedback/index.html`。

通过 CDP attach 到已登录美团浏览器（默认 9521），goto ebIframe，定位评价 iframe，
读 body 文本按「昵称行 + 评价举报」分块解析单条评价。

- fetch_reviews：真实 read-only，已接通。
- fetch_history_replies / submit_reply：暂未接通。美团侧有「未回复」样本，
  回复提交逆向后续补；不自动提交（OTA 写，与携程口径一致）。
"""

from __future__ import annotations

import asyncio
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

_EBIFRAME_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe"
    "?iUrl=%2Febooking%2Fnew-feedback%2Findex.html%23%2Fmt"
)

# iframe 默认列表分页（mtd-pager 页码）。点目标页码；不可见/禁用返回 false。
_MT_CLICK_PAGE_JS = """(n) => {
  const items = [...document.querySelectorAll('li.mtd-pager-item')];
  const el = items.find(e => (e.innerText || '').trim() === String(n));
  if (!el || el.className.includes('active') || el.className.includes('disabled')) return false;
  el.click();
  return true;
}"""

# 历史回复翻页上限：每页约 10 条，40 页封顶防失控（按 limit 截断）。
_HISTORY_MAX_PAGES = 40

# 主评分：「5.0 很好」「4.0 不错」「3.0 一般」「2.0 差评」「1.0 很差」
_MT_SCORE_RE = re.compile(r"([1-5]\.\d)\s*(很好|不错|一般|差评|很差)")
# 差评卡总分是独立小数行（1.0/2.0，无「很差」词形）；缺失时 score=None
# 会被归成中评，差评模板永远出不来。
_MT_SCORE_DECIMAL_RE = re.compile(r"^([1-5](?:\.\d+)?)\s*$")
_MT_TIME_RE = re.compile(r"评价时间[：:]\s*([0-9/]+)")
_MT_REPLY_RE = re.compile(r"酒店回复内容[：:]\s*([\s\S]*?)\n\s*回复时间")
_MT_REPLY_TIME_RE = re.compile(r"回复时间[：:]\s*([0-9/: ]+)")
# 昵称行：l*** / H*** / 中文+星号
_MT_NICK_RE = re.compile(r"^([A-Za-z\u4e00-\u9fa5]{1,4}\*{2,})\s*$", re.MULTILINE)
_MT_DIMENSION_RE = re.compile(r"^(卫生|设施|位置|服务|环境|性价比)[\s：:]?\s*\d+")
# 美团子项是 50 分制，这里忽略，用主分（5 分制）入库。


class MeituanReviewAdapter:
    """美团商家后台评价列表真实抓取适配器（session_port 指定店铺浏览器）。"""

    platform = "meituan"

    def __init__(self, session_port: int | None = None) -> None:
        self.session_port = session_port
        # review_id -> 评价内容前缀（用于提交时在页面上定位对应卡片）。
        self._content_cache: dict[str, str] = {}

    async def _with_review_page(self, fn):
        """CDP attach 已登录美团浏览器，打开评价页执行 fn(page, target_frame)。"""
        import contextlib

        from app.core.settings import get_settings

        from .adapters import to_review_adapter_error

        port = self.session_port or getattr(
            get_settings(), "meituan_ebooking_default_session_port", 9521
        )
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
                await page.goto(_EBIFRAME_URL, wait_until="networkidle", timeout=40000)
                await asyncio.sleep(1.5)
                # 排除 ebIframe 壳页（其 url 的 iUrl 参数也含 new-feedback），
                # 锁定真实评价 SPA：eb.meituan.com 域下的 new-feedback。
                target = None
                for frame in page.frames:
                    if (
                        "new-feedback" in frame.url
                        and "ebIframe" not in frame.url
                        and "eb.meituan.com" in frame.url
                    ):
                        target = frame
                        break
                return await fn(page, target)
            except ReviewAdapterError:
                raise
            except Exception as exc:
                raise to_review_adapter_error(exc) from exc
            finally:
                # close 自身抛错（浏览器已被人工关闭等）不能掩盖原始异常。
                with contextlib.suppress(Exception):
                    await page.close()

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        async def read(page, target):
            if target is None:
                # iframe 未就绪多半是登录失效/加载失败：静默返回空快照会被当成
                # 「今日 0 条评价」落库（假数据），必须显式报错。
                raise ReviewAdapterError(
                    "美团评价页未加载（iframe 未就绪或登录失效），请稍后重试。"
                )
            # 默认列表（近期评价，多为已回复）
            default_body = await target.inner_text("body")
            # 「未回复」列表（待回复评价带空回复框）
            await target.eval_on_selector_all(
                "a,li,div,span,button",
                "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('未回复')); if(el) el.click(); return true;}",
            )
            await asyncio.sleep(2)
            pending_body = await target.inner_text("body")
            return default_body, pending_body

        default_body, pending_body = await self._with_review_page(read)
        merged: dict[str, ReviewItemInput] = {}
        for body_text in (default_body, pending_body):
            for item in _parse_meituan_reviews(body_text):
                merged.setdefault(item.review_id, item)
        items = list(merged.values())
        for item in items:
            if item.reply_status == "pending" and item.content:
                self._content_cache[item.review_id] = item.content[:30]
        hotel_id = _extract_meituan_hotel_name(default_body) or "meituan_default"
        return PlatformReviewSnapshot(
            hotel_id=hotel_id,
            reviews=items,
            pending_total=_parse_pending_total(pending_body or default_body),
        )

    async def fetch_history_replies(self, limit: int = 200):
        """双轮拉全已回复评价（此前只取首页约 10 条，近期评价几乎全是好评
        已回复，店铺历史里回复过的中/差评永远进不了模板样本）：
        1. 「差评」tab 优先（差评已回复样本最稀缺）；
        2. 「全部评价」列表翻页补满。
        不再复用 fetch_reviews：历史导入不需要切「未回复」tab。"""

        from .adapters import HistoryReply

        async def collect_pages(target, out, seen, hotel_ids: list[str]):
            for page_no in range(1, _HISTORY_MAX_PAGES + 1):
                body_text = await target.inner_text("body")
                # 真实店名必须落库：review_items 唯一键是 (platform, hotel_id,
                # review_id)，多店铺共用的占位 hotel_id 会让同集团模板化回复
                # （内容指纹相同）在跨店落库时撞唯一键。
                if page_no == 1 and not hotel_ids:
                    name = _extract_meituan_hotel_name(body_text)
                    hotel_ids.append(name or "meituan_default")
                hotel_id = hotel_ids[0] if hotel_ids else "meituan_default"
                for item in _parse_meituan_reviews(body_text):
                    if item.review_id in seen:
                        continue
                    seen.add(item.review_id)
                    if item.reply_status != "replied" or not item.reply_content:
                        continue
                    out.append(
                        HistoryReply(
                            review_id=item.review_id,
                            hotel_id=hotel_id,
                            user_nickname=item.user_nickname,
                            score=item.score,
                            content=item.content,
                            review_time=item.review_time,
                            reply_content=item.reply_content,
                        )
                    )
                if len(out) >= limit or page_no >= _HISTORY_MAX_PAGES:
                    return
                if not await target.evaluate(_MT_CLICK_PAGE_JS, page_no + 1):
                    return
                await asyncio.sleep(2.5)

        async def collect(page, target):
            if target is None:
                raise ReviewAdapterError(
                    "美团评价页未加载（iframe 未就绪或登录失效），请稍后重试。"
                )
            out: list[HistoryReply] = []
            seen: set[str] = set()
            hotel_ids: list[str] = []
            # 第一轮：「差评」tab 优先——差评样本最稀缺，且全部列表翻页会先把
            # limit 配额占满（近期已回复几乎全是好评），后跑永远收不到。
            bad_tab = await target.evaluate(
                """() => {
                  const el = [...document.querySelectorAll('div.mtd-tabs-item')]
                    .find(e => (e.innerText || '').trim().startsWith('差评'));
                  if (!el) return false;
                  el.click(); return true;
                }"""
            )
            if bad_tab:
                await asyncio.sleep(2.5)
                await collect_pages(target, out, seen, hotel_ids)
            # 第二轮：切回「全部评价」翻页补满。
            if len(out) < limit:
                back_all = await target.evaluate(
                    """() => {
                      const el = [...document.querySelectorAll('div.mtd-tabs-item')]
                        .find(e => (e.innerText || '').trim().startsWith('全部评价'));
                      if (!el) return false;
                      el.click(); return true;
                    }"""
                )
                if back_all:
                    await asyncio.sleep(2.5)
                    await collect_pages(target, out, seen, hotel_ids)
            return out[:limit]

        return await self._with_review_page(collect)

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        """对指定待回复评价提交回复：定位卡片内 textarea 填写并点「发表回复」。"""
        if len(reply_content) > 500:
            raise ReviewSubmitError("美团回复内容不能超过 500 字。")
        if review_id not in self._content_cache:
            await self.fetch_reviews()
        prefix = self._content_cache.get(review_id)
        if not prefix:
            raise ReviewSubmitError("未找到该待回复评价，可能已被回复或不在当前列表。")

        async def do(page, target):
            if target is None:
                raise ReviewSubmitError("美团评价页未加载，请稍后重试。")
            await target.eval_on_selector_all(
                "a,li,div,span,button",
                "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('未回复')); if(el) el.click(); return true;}",
            )
            await asyncio.sleep(2)
            # 同卡绑定定位：textarea 与「发表回复」按钮必须来自同一张卡片，且内容
            # 前缀只允许命中一张卡片；两条评价前 30 字相同（短评价常见）时拒绝提交，
            # 避免填 A 卡、点 B 卡把回复写到错误评价上。
            mark_result = await target.evaluate(
                """(prefix)=>{
                  document.querySelectorAll('textarea').forEach(e=>e.removeAttribute('data-rv-target'));
                  document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
                  const tas=[...document.querySelectorAll('textarea')];
                  let hits=0;
                  for(const ta of tas){
                    let p=ta.parentElement, card=null;
                    for(let k=0;k<10&&p;k++){
                      const t=p.innerText||'';
                      if(t.includes('评价时间')&&t.includes(prefix)&&t.length<2500){card=p;break;}
                      p=p.parentElement;
                    }
                    if(!card) continue;
                    hits++;
                    if(hits>1) return 'ambiguous';
                    ta.setAttribute('data-rv-target','1');
                    let bp=card;
                    for(let k=0;k<6&&bp;k++){
                      const b=[...bp.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='发表回复');
                      if(b){b.setAttribute('data-rv-btn','1');break;}
                      bp=bp.parentElement;
                    }
                  }
                  return hits===1;
                }""",
                prefix,
            )
            if mark_result == "ambiguous":
                raise ReviewSubmitError(
                    "定位到多条内容前缀相同的评价，为避免回复错配已拒绝提交，请到美团后台手动回复。"
                )
            if not mark_result:
                raise ReviewSubmitError("页面上未定位到该评价的回复框。")
            await target.locator("textarea[data-rv-target='1']").fill(reply_content)
            await asyncio.sleep(0.5)
            button = target.locator("button[data-rv-btn='1']")
            if (await button.count()) == 0:
                raise ReviewSubmitError("未找到该评价的「发表回复」按钮。")
            for _ in range(10):
                cls = (await button.get_attribute("class")) or ""
                if "disabled" not in cls:
                    break
                await asyncio.sleep(0.3)
            cls = (await button.get_attribute("class")) or ""
            if "disabled" in cls:
                raise ReviewSubmitError("「发表回复」按钮未启用（内容可能未生效），未提交。")
            await button.click()
            await asyncio.sleep(5)
            # 提交成功后卡片会移出未回复列表（回复框消失）；回复框仍在则说明未提交成功。
            still_pending = await target.evaluate(
                "()=>document.querySelector('textarea[data-rv-target]')!==null"
            )
            if still_pending:
                raise ReviewSubmitError(
                    "回复未提交成功（该评价仍在未回复列表），请到美团后台核实。"
                )
            self._content_cache.pop(review_id, None)
            return {"ok": True, "review_id": review_id}

        return await self._with_review_page(do)

    async def submit_pending_replies(self, generator, *, max_count: int = 20) -> list[dict]:
        """批量回复待回复评价：每条一个独立页面会话。

        美团未回复列表分页随机且提交后 DOM 重排，同会话内复用元素序号会失效；
        因此每条单独开会话：定位当前可见、未处理过的第一张卡片 -> generator 生成
        回复 -> 填写提交 -> 验证卡片消失。generator(info) 返回 None 表示跳过。
        每批上限 max_count 条，避免连续提交触发风控；余量由下轮调度消化。

        2026-09-17：默认从 5 提到 20——每日一次定时不足以消化余量，实测美团只
        回复 5 条就停。一次跑完（按 pending_count 动态传值）单 session 约 14s
        × 60 次超 14 分钟超时风险高，20 条是安全上限，余量靠次日定时或手动补跑。
        """
        # 用 DOM 临时标记定位目标卡片，避免文本前缀不唯一导致误配。
        mark_card_js = """(handled)=>{
          document.querySelectorAll('textarea').forEach(e=>e.removeAttribute('data-rv-target'));
          document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
          const tas=[...document.querySelectorAll('textarea')];
          for(const ta of tas){
            let p=ta.parentElement, card=null;
            for(let k=0;k<10&&p;k++){
              const t=p.innerText||'';
              if(t.includes('评价时间')&&t.length<2500){card=p;break;}
              p=p.parentElement;
            }
            if(!card) continue;
            const key=card.innerText.slice(0,40);
            if(handled.includes(key)) continue;
            ta.setAttribute('data-rv-target','1');
            let bp=card;
            for(let k=0;k<6&&bp;k++){
              const b=[...bp.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='发表回复');
              if(b){b.setAttribute('data-rv-btn','1'); break;}
              bp=bp.parentElement;
            }
            return {key:key, text:card.innerText};
          }
          return null;
        }"""
        target_still_js = """()=>document.querySelector('textarea[data-rv-target]')!==null"""

        results: list[dict] = []
        handled: list[str] = []
        page_failures = 0
        for _ in range(max_count * 3):
            if sum(1 for r in results if r.get("ok")) >= max_count:
                break

            # 单会话完成「定位当前可见第一张未处理卡片 + 生成 + 提交」，
            # 避免跨会话/提交后 DOM 重排导致元素序号失配。
            async def one_session(page, target):
                if target is None:
                    raise ReviewSubmitError("美团评价页未加载。")
                await target.eval_on_selector_all(
                    "a,li,div,span,button",
                    "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('未回复')); if(el) el.click(); return true;}",
                )
                await asyncio.sleep(2)
                card = await target.evaluate(mark_card_js, handled)
                if card is None:
                    return None
                text = card.get("text") or ""
                key = card.get("key") or text[:40]
                info = _parse_meituan_card(text)
                score = info.score if info is not None else None
                reply = None
                if info is not None:
                    reply = generator(
                        {
                            "score": info.score,
                            "content": info.content,
                            "review_time": info.review_time,
                            "user_nickname": info.user_nickname,
                        }
                    )
                if not reply:
                    return {"ok": False, "reason": "no_template", "content": key, "key": key, "score": score}
                if len(reply) > 500:
                    return {"ok": False, "reason": "reply_too_long", "content": key, "key": key, "score": score}
                await target.locator("textarea[data-rv-target='1']").fill(reply)
                await asyncio.sleep(0.5)
                button = target.locator("button[data-rv-btn='1']")
                for _ in range(10):
                    cls = (await button.get_attribute("class")) or ""
                    if "disabled" not in cls:
                        break
                    await asyncio.sleep(0.3)
                await button.click()
                await asyncio.sleep(4)
                still = await target.evaluate(target_still_js)
                ok = not still
                if ok and info is not None:
                    fingerprint = f"{info.review_time}|{(info.content or '')[:80]}"
                    self._content_cache.pop(
                        "meituan_" + hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16],
                        None,
                    )
                return {"ok": ok, "reason": None if ok else "still_pending", "content": key, "key": key, "score": score}

            try:
                row = await self._with_review_page(one_session)
            except Exception as exc:
                results.append({"ok": False, "reason": str(exc)[:80], "content": ""})
                # 连续页面会话失败＝浏览器掉线/假死：熔断放弃本轮（同携程 F3）。
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


def _parse_meituan_reviews(body_text: str) -> list[ReviewItemInput]:
    # 截掉头部统计区（到「添加回复模板下载列表内容」为止）。
    cut = body_text.find("添加回复模板")
    payload = body_text[cut + len("添加回复模板下载列表内容") :] if cut >= 0 else body_text
    # 按「昵称行（行末）+ 下一行 评价举报」分块。
    blocks = re.split(r"\n(?=[A-Za-z\u4e00-\u9fa5]{1,4}\*{2,}\n评价举报)", payload)
    items: list[ReviewItemInput] = []
    for block in blocks:
        parsed = _parse_meituan_card(block.strip())
        if parsed is not None:
            items.append(parsed)
    return items


def _parse_meituan_card(text: str) -> ReviewItemInput | None:
    if not text or len(text) < 20:
        return None
    nick_match = _MT_NICK_RE.search(text)
    nick = nick_match.group(1) if nick_match else None
    score_match = _MT_SCORE_RE.search(text)
    score = float(score_match.group(1)) if score_match else None
    if score is None:
        # 差评卡：独立小数总分行（1.0/2.0），无「很差」词形。
        for ln in text.split("\n"):
            m = _MT_SCORE_DECIMAL_RE.match(ln.strip())
            if m:
                score = float(m.group(1))
                break
    time_match = _MT_TIME_RE.search(text)
    review_time = time_match.group(1) if time_match else None

    time_split = _MT_TIME_RE.split(text)
    before_time = time_split[0] if time_split else text
    content_lines: list[str] = []
    for ln in before_time.split("\n"):
        s = ln.strip()
        if not s or s == nick:
            continue
        if _MT_SCORE_RE.search(s) or _MT_DIMENSION_RE.match(s):
            continue
        if _MT_SCORE_DECIMAL_RE.match(s):
            continue
        if s in {"评价举报", "【预订】"} or s.startswith("添加回复") or "编辑删除" in s:
            continue
        if _MT_NICK_RE.search(s):
            continue
        content_lines.append(s)
    content = "\n".join(content_lines).strip() or None

    # 已回复 = 「酒店回复内容」后有实际内容且无「发表回复」按钮；
    # 未回复卡片也带「酒店回复内容：」空区 + 「发表回复」按钮。
    has_publish = "发表回复" in text
    replied = ("酒店回复内容" in text) and not has_publish
    reply_content: str | None = None
    if replied:
        reply_match = _MT_REPLY_RE.search(text)
        reply_content = reply_match.group(1).strip() if reply_match else None

    fingerprint = f"{review_time}|{(content or '')[:80]}"
    review_id = "meituan_" + hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16]

    return ReviewItemInput(
        review_id=review_id,
        user_nickname=nick,
        score=score,
        content=content,
        review_time=review_time,
        reply_status="replied" if replied else "pending",
        reply_content=reply_content,
    )


_HOTEL_NAME_RE = re.compile(r"([^\n]{2,40}(?:酒店|管理有限公司)[^\n]{0,30})")


def _extract_meituan_hotel_name(body_text: str) -> str | None:
    cut = body_text.find("添加回复模板")
    head = body_text[:cut] if cut >= 0 else body_text[:500]
    # 优先取含「酒店」的行，避开「门店选择」等菜单词。
    for line in head.split("\n"):
        if "酒店" in line or "管理有限公司" in line:
            return line.strip()
    return None


# 平台未回复总数（「未回复(N)」/「待回复(N)」/「待回复 N」），供对账判定用。
_PENDING_TOTAL_RE = re.compile(r"(?:未回复|待回复)\s*[（(]?\s*(\d+)\s*[)）]?")


def _parse_pending_total(body_text: str) -> int | None:
    match = _PENDING_TOTAL_RE.search(body_text or "")
    return int(match.group(1)) if match else None
