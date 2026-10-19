"""飞猪酒店管理系统真实评价适配器（Phase D 飞猪侧，read-only 抓取已接通）。

飞猪评价管理在 SPA hash 路由 `#/ebk/commentsManager`，评价列表需点「飞猪(65)」来源
tab 触发加载（默认显示统计概览）。通过 CDP attach 到已登录飞猪浏览器（默认 9531），
goto 路由 → 点飞猪来源 → 滚动 → 读 body 按「YYYY年M月D日发表」分块解析单条评价。

- fetch_reviews：真实 read-only，已接通（单条样本验证；解析覆盖度待更多样本打磨）。
- fetch_history_replies / submit_reply：暂未接通。飞猪侧「待回复 60」有样本，
  回复提交逆向后续补；不自动提交（OTA 写，口径一致）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
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

_COMMENTS_URL = "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm#/ebk/commentsManager"

# ant-design 分页页码点击；不可见/禁用返回 false 由上层终止。
_FLIGGY_CLICK_PAGE_JS = """(n) => {
  const items = [...document.querySelectorAll('li.ant-pagination-item')];
  const el = items.find(e => (e.className || '').includes('ant-pagination-item-' + n))
    || items.find(e => (e.innerText || '').trim() === String(n));
  if (!el || el.className.includes('active') || el.className.includes('disabled')) return false;
  el.click();
  return true;
}"""

# 历史回复翻页上限，防失控（按 limit 截断）。
_HISTORY_MAX_PAGES = 40

_FLIGGY_TIME_RE = re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)发表")
# 评分支持 5 / 4.8 / 4.75 形态；限定 1-5 避免把统计区的计数（如 65）当评分。
_FLIGGY_SCORE_RE = re.compile(r"^([1-5](?:\.\d+)?)\s*$", re.MULTILINE)
_FLIGGY_DIM_RE = re.compile(r"^(位置便利|设施体验|清洁程度|服务态度|环境|性价比)\s*$", re.MULTILINE)
_FLIGGY_NOISE_RE = re.compile(
    r"^(异常评价申诉|浏览次数|有用次数|已为您生成评价|取 ?消|发表回复|试试一键发表|"
    r"换一条|选择模版|以上内容由AI生成|共 ?\d+ ?条数据|AI帮你写|"
    r"AI批量写回复|批量回复|设置回复模版|下载评价|点评小贴士|差评回复率|"
    r"差评率|参评率|有图率|一键回复|NEW|高德|飞猪|全部|待回复|重要|"
    r"待回复差评|差评|有图|推荐|快速筛选|其他筛选|.*[：:].*|.*\(\d+\).*)$"
)

logger = logging.getLogger(__name__)

# 点「待回复(N)」快速筛选 tag（2026-09-19 修复）。
#
# 飞猪页面 DOM 实测：「待回复(62)」与「AI帮你写」合在一个 ant-tag 里
# （innerText="AI帮你写\n待回复(62)"，默认已选中），页面上独立的
# startsWith('待回复') 元素只有「待回复差评(0)」。旧 startsWith('待回复')
# 点中差评筛选 → 0 卡 → mark_card_js null → 空 batch → 不推卡。
#
# 修正：匹配含「待回复(」且不含「差评」的 tag；已选中（checked）则跳过
# 避免点掉当前筛选；找不到则返回 false 让上层按默认视图继续。
_FLIGGY_CLICK_PENDING_FILTER_JS = """(els) => {
  const el = els.find(e => {
    const t = (e.innerText || '').trim();
    return t.includes('待回复(') && !t.includes('差评');
  });
  if (!el) return false;
  if ((el.className || '').includes('checked')) return true;
  el.click();
  return true;
}"""


class FliggyReviewAdapter:
    """飞猪酒店管理系统评价列表真实抓取适配器（session_port 指定店铺浏览器）。"""

    platform = "fliggy"

    def __init__(self, session_port: int | None = None) -> None:
        self.session_port = session_port
        # review_id -> 评价内容前缀（用于提交时在页面上定位对应卡片）。
        self._content_cache: dict[str, str] = {}

    async def _with_review_page(self, fn):
        """CDP attach 已登录飞猪浏览器，打开评价页执行 fn(page)。"""
        import contextlib

        from app.core.settings import get_settings

        from .adapters import to_review_adapter_error

        port = self.session_port or getattr(
            get_settings(), "fliggy_ebooking_default_session_port", 9531
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
                await page.goto(_COMMENTS_URL, wait_until="networkidle", timeout=40000)
                # 点「飞猪(N)」来源 tab 触发评价列表加载。
                await page.eval_on_selector_all(
                    "a,li,div,span,button",
                    "(els)=>{const el=els.find(e=>{const t=(e.innerText||'').trim(); "
                    "return t.includes('飞猪') && t.includes('(')}); if(el){el.click();return true;} return false;}",
                )
                await asyncio.sleep(4)
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
            for _ in range(3):
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(1)
            return await page.inner_text("body")

        body_text = await self._with_review_page(read)
        items = _parse_fliggy_reviews(body_text)
        for item in items:
            if item.reply_status == "pending" and item.content:
                self._content_cache[item.review_id] = item.content[:30]
        hotel_id = _extract_fliggy_hotel_name(body_text) or "fliggy_default"
        return PlatformReviewSnapshot(
            hotel_id=hotel_id,
            reviews=items,
            pending_total=_parse_pending_total(body_text),
        )

    async def fetch_history_replies(self, limit: int = 200):
        """双轮拉全已回复评价（此前只取首屏，且解析器不提取回复内容，
        飞猪历史导入从未产出过真实模板样本）：
        1. 默认列表滚动+翻页（好评/中评已回复）；
        2. 点「差评」快速筛选再翻一轮（差评已回复）。"""

        from .adapters import HistoryReply

        async def collect_pages(page, out, seen, hotel_id):
            for page_no in range(1, _HISTORY_MAX_PAGES + 1):
                # 滚动到底触发当前页剩余卡片渲染（飞猪列表懒加载）。
                for _ in range(10):
                    before = await page.evaluate("() => document.body.scrollHeight")
                    await page.mouse.wheel(0, 1500)
                    await asyncio.sleep(0.8)
                    after = await page.evaluate("() => document.body.scrollHeight")
                    if after == before:
                        break
                body_text = await page.inner_text("body")
                for item in _parse_fliggy_reviews(body_text):
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
                if not await page.evaluate(_FLIGGY_CLICK_PAGE_JS, page_no + 1):
                    return
                await page.evaluate("() => window.scrollTo(0, 0)")
                await asyncio.sleep(2.5)

        async def collect(page):
            out: list[HistoryReply] = []
            seen: set[str] = set()
            body_text = await page.inner_text("body")
            hotel_id = _extract_fliggy_hotel_name(body_text) or "fliggy_default"
            # 点「全部(N)」筛选 tag 解除「待回复差评」等快速筛选：页面上有两组
            # 「全部(248)」（评价来源 / 快速筛选，父链共同容器都含「快速筛选」
            # 无法精确区分），逐个点击可见的全部 tag 才能回到全量列表。
            async def click_quick_all() -> bool:
                return await page.evaluate(
                    """() => {
                      let clicked = 0;
                      for (const el of document.querySelectorAll('span,div,a,li,button')) {
                        const t = (el.innerText || '').trim();
                        if (!/^全部\\s*\\(\\d+\\)$/.test(t)) continue;
                        if (!el.offsetParent) continue;
                        let p = el.parentElement;
                        let inFilter = false;
                        for (let k = 0; k < 6 && p; k++) {
                          if ((p.innerText || '').includes('快速筛选') || (p.innerText || '').includes('评价来源')) {
                            inFilter = true; break;
                          }
                          p = p.parentElement;
                        }
                        if (!inFilter) continue;
                        el.click();
                        clicked += 1;
                      }
                      return clicked > 0;
                    }"""
                )

            if await click_quick_all():
                await asyncio.sleep(2.5)
            # 第一轮：「差评(N)」快速筛选优先——差评样本最稀缺，且全部列表翻页
            # 会先把 limit 配额占满，后跑永远收不到。
            bad_tab = await page.evaluate(
                """() => {
                  const el = [...document.querySelectorAll('a,li,div,span,button')]
                    .find(e => /^差评\\s*\\(\\d+\\)$/.test((e.innerText || '').trim()));
                  if (!el) return false;
                  el.click(); return true;
                }"""
            )
            if bad_tab:
                await asyncio.sleep(2.5)
                await page.evaluate("() => window.scrollTo(0, 0)")
                await collect_pages(page, out, seen, hotel_id)
            # 第二轮：重新加载列表页回「全部」快速筛选翻页补满（差评筛选视图里
            # 文本定位回切可能点中无事件的内层元素，goto 重开最可靠）。
            if len(out) < limit:
                await page.goto(_COMMENTS_URL, wait_until="networkidle", timeout=40000)
                await asyncio.sleep(2.5)
                await page.eval_on_selector_all(
                    "a,li,div,span,button",
                    "(els)=>{const el=els.find(e=>(e.innerText||'').trim().startsWith('飞猪')); if(el) el.click(); return true;}",
                )
                await asyncio.sleep(2.5)
                if await click_quick_all():
                    await asyncio.sleep(2.5)
                    await collect_pages(page, out, seen, hotel_id)
            return out[:limit]

        return await self._with_review_page(collect)

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        """对指定待回复评价提交回复：替换卡片内 AI 预填内容并点「发表回复」。"""
        if not (10 <= len(reply_content) <= 500):
            raise ReviewSubmitError("飞猪回复内容需在 10-500 字之间。")
        if review_id not in self._content_cache:
            await self.fetch_reviews()
        prefix = self._content_cache.get(review_id)
        if not prefix:
            raise ReviewSubmitError("未找到该待回复评价，可能已被回复或不在当前列表。")

        # 同卡绑定定位：textarea 与「发表回复」按钮必须来自同一张卡片，且内容
        # 前缀只允许命中一张卡片，避免把回复写到错误的评价上（与美团口径一致）。
        mark_by_prefix_js = """(prefix)=>{
          document.querySelectorAll('textarea.ant-input').forEach(e=>e.removeAttribute('data-rv-target'));
          document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
          const tas=[...document.querySelectorAll('textarea.ant-input')];
          let hits=0;
          for(const ta of tas){
            let p=ta.parentElement, card=null;
            for(let k=0;k<12&&p;k++){
              const t=p.innerText||'';
              if(t.includes('发表回复')&&t.includes(prefix)&&t.length<2500){card=p;break;}
              p=p.parentElement;
            }
            if(!card) continue;
            hits++;
            if(hits>1) return 'ambiguous';
            ta.setAttribute('data-rv-target','1');
            const b=[...card.querySelectorAll('button')].find(x=>(x.innerText||'').replace(/\\s/g,'')==='发表回复');
            if(b) b.setAttribute('data-rv-btn','1');
          }
          return hits===1;
        }"""

        async def do(page):
            mark_result = await page.evaluate(mark_by_prefix_js, prefix)
            if mark_result == "ambiguous":
                raise ReviewSubmitError(
                    "定位到多条内容前缀相同的评价，为避免回复错配已拒绝提交，请到飞猪后台手动回复。"
                )
            if not mark_result:
                raise ReviewSubmitError("页面上未定位到该评价的回复框。")
            await page.locator("textarea.ant-input[data-rv-target='1']").fill(reply_content)
            await asyncio.sleep(0.5)
            button = page.locator("button[data-rv-btn='1']")
            if (await button.count()) == 0:
                raise ReviewSubmitError("未找到该评价的「发表回复」按钮。")
            await button.click()
            await asyncio.sleep(5)
            still = await page.evaluate(
                "()=>document.querySelector('textarea.ant-input[data-rv-target]')!==null"
            )
            if still:
                raise ReviewSubmitError(
                    "回复未提交成功（该评价回复框仍在），请到飞猪后台核实。"
                )
            self._content_cache.pop(review_id, None)
            return {"ok": True, "review_id": review_id}

        return await self._with_review_page(do)

    async def submit_pending_replies(self, generator, *, max_count: int = 20) -> list[dict]:
        """批量回复待回复评价：每条一个独立页面会话（同美团口径）。

        飞猪未回复卡片自带 AI 预填回复（textarea.ant-input 可编辑）+「发表回复」按钮；
        用 DOM 临时标记定位目标卡片，避免文本前缀不唯一误配。generator None 跳过。

        2026-09-17：默认从 5 提到 20（同美团口径）。单卡解析改用
        _parse_fliggy_card 直接处理 card.innerText——此前用
        _parse_fliggy_reviews(text)[0]，待回复卡无子维度行时切卡逻辑找不到
        卡起点（items=[]）→ IndexError 被外层 except 吞掉，整批非 ok（飞猪
        完全不处理的根因）。
        """
        mark_card_js = """(handled)=>{
          document.querySelectorAll('textarea.ant-input').forEach(e=>e.removeAttribute('data-rv-target'));
          document.querySelectorAll('button').forEach(e=>e.removeAttribute('data-rv-btn'));
          const tas=[...document.querySelectorAll('textarea.ant-input')];
          for(const ta of tas){
            let p=ta.parentElement, card=null;
            for(let k=0;k<12&&p;k++){
              const t=p.innerText||'';
              if(t.includes('发表回复')&&t.length<2500){card=p;break;}
              p=p.parentElement;
            }
            if(!card) continue;
            const key=card.innerText.slice(0,40);
            if(handled.includes(key)) continue;
            ta.setAttribute('data-rv-target','1');
            const b=[...card.querySelectorAll('button')].find(x=>(x.innerText||'').replace(/\\s/g,'')==='发表回复');
            if(b) b.setAttribute('data-rv-btn','1');
            return {key:key, text:card.innerText};
          }
          return null;
        }"""
        target_still_js = """()=>document.querySelector('textarea.ant-input[data-rv-target]')!==null"""

        results: list[dict] = []
        handled: list[str] = []
        page_failures = 0
        for _ in range(max_count * 3):
            if sum(1 for r in results if r.get("ok")) >= max_count:
                break

            async def one_session(page):
                await page.eval_on_selector_all(
                    "a,li,div,span,button",
                    "(els)=>{const el=els.find(e=>{const t=(e.innerText||'').trim(); return t.includes('飞猪') && t.includes('(')}); if(el) el.click(); return true;}",
                )
                await asyncio.sleep(3)
                await page.eval_on_selector_all(
                    "a,li,div,span,button",
                    _FLIGGY_CLICK_PENDING_FILTER_JS,
                )
                await asyncio.sleep(3)
                # 有界等待找卡（09-21 加固）：64 条待回复列表渲染常超 3s，
                # 单次 evaluate 为 null 就放弃会过早 break（只碰 1 条就停）。
                card = None
                for _ in range(4):
                    card = await page.evaluate(mark_card_js, handled)
                    if card is not None:
                        break
                    await asyncio.sleep(2)
                if card is None:
                    logger.warning(
                        "fliggy submit: mark_card_js returned null on 待回复 tab; "
                        "no textarea.ant-input card found (page may not have rendered "
                        "pending cards or DOM structure changed)"
                    )
                    return None
                text = card.get("text") or ""
                key = card.get("key") or text[:40]
                # 直接解析单卡 innerText：_parse_fliggy_reviews 的切卡逻辑要求
                # 「总分行 + 紧跟子维度行」，待回复卡常省略子维度行 → items=[] →
                # IndexError 被外层吞掉（飞猪完全不处理的根因）。_parse_fliggy_card
                # 只需总分 + 日期行即可解析，单卡场景更稳健。
                info = _parse_fliggy_card(text, None) if text else None
                if info is None and text:
                    # 解析失败与「无模板」在结果行里同因 no_template，日志区分根因。
                    logger.info(
                        "fliggy submit: card text unparsed (row=no_template); "
                        "head=%r",
                        text[:120],
                    )
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
                if not (10 <= len(reply) <= 500):
                    return {"ok": False, "reason": "reply_length_invalid", "content": key, "key": key, "score": score}
                await page.locator("textarea.ant-input[data-rv-target='1']").fill(reply)
                await asyncio.sleep(0.5)
                button = page.locator("button[data-rv-btn='1']")
                await button.click()
                await asyncio.sleep(5)
                still = await page.evaluate(target_still_js)
                ok = not still
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


def _parse_fliggy_reviews(body_text: str) -> list[ReviewItemInput]:
    """按「总分行 + 子维度行」引导切卡。

    真实卡片行序固定：[昵称][总分][子维度...][内容][日期发表][房型][浏览/
    有用][店家回复(可选)][回复内容(可选)][发表于:(可选)][修改评价]→下一卡。
    此前按日期行切分并归属前段，会把回复区错挂到下一张卡、本卡误判 pending
    （立瑞飞猪 219 条全部漏抓的根因）。
    """
    start = body_text.find("快速筛选")
    payload = body_text[start:] if start >= 0 else body_text
    lines = [ln.strip() for ln in payload.split("\n") if ln.strip()]
    cards: list[list[str]] = []
    cur: list[str] | None = None
    for i, ln in enumerate(lines):
        if (
            _FLIGGY_SCORE_RE.match(ln)
            and i + 1 < len(lines)
            and _FLIGGY_DIM_RE.match(lines[i + 1])
            # 子维度的分值行（如「清洁程度 / 4.5 / 服务态度」里的 4.5）
            # 前一行是子维度词，真总分行的前一行是昵称/内容/页面元素。
            and (i == 0 or not _FLIGGY_DIM_RE.match(lines[i - 1]))
        ):
            if cur:
                cards.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur:
        cards.append(cur)

    items: list[ReviewItemInput] = []
    for card_lines in cards:
        card_text = "\n".join(card_lines)
        time_match = _FLIGGY_TIME_RE.search(card_text)
        parsed = _parse_fliggy_card(
            card_text, time_match.group(1) if time_match else None
        )
        if parsed is not None:
            items.append(parsed)
    return items


def _parse_fliggy_card(text: str, review_time: str | None) -> ReviewItemInput | None:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return None
    score_match = _FLIGGY_SCORE_RE.search(text)
    score = float(score_match.group(1)) if score_match else None
    if score is None:
        return None
    # 昵称：主分行（第一个评分行）往前找最近的非噪音、非子项行。
    nick = None
    score_idx = next(
        (i for i, ln in enumerate(lines) if _FLIGGY_SCORE_RE.match(ln)), None
    )
    if score_idx is not None:
        for j in range(score_idx - 1, -1, -1):
            ln = lines[j]
            if not _FLIGGY_NOISE_RE.match(ln) and not _FLIGGY_DIM_RE.match(ln):
                nick = ln
                break
    # 内容：评分行之后的非噪音、非子项行。
    content_lines: list[str] = []
    seen_score = False
    for ln in lines:
        if _FLIGGY_SCORE_RE.match(ln):
            seen_score = True
            continue
        if not seen_score:
            continue
        # 日期行之后是房型/统计/回复区，都不属于评价内容。
        if _FLIGGY_TIME_RE.search(ln):
            break
        # 回复区从「店家回复/酒店回复/商家回复/回复时间」开始，不属于评价内容。
        # （真实页面已回复卡片的标志词是「店家回复」，回复时间行是「发表于：」。）
        if ln.startswith(("店家回复", "酒店回复", "商家回复", "回复时间")):
            break
        if _FLIGGY_DIM_RE.match(ln) or _FLIGGY_NOISE_RE.match(ln):
            continue
        content_lines.append(ln)
    content = "\n".join(content_lines).strip() or None

    time_match = _FLIGGY_TIME_RE.search(text + "发表")
    review_time = review_time or (time_match.group(1) if time_match else None)

    fingerprint = f"{review_time}|{(content or '')[:80]}"
    review_id = "fliggy_" + hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16]

    # 已回复判定（2026-09-03 修复误判）：
    # - 明确有回复内容（「店家回复/酒店回复/回复时间」标志）→ replied
    # - 有「发表回复」按钮 → pending（未回复，可回复）
    # - 两者都没有（页面加载不全/结构不明）→ 默认 pending，不误判 replied
    #   （原来无按钮就判 replied 导致未回复差评误标已回复）
    has_reply_content = (
        "店家回复" in text or "回复时间" in text or "酒店回复" in text
    )
    has_publish_button = "发表回复" in text

    # 回复内容提取：「店家回复/酒店回复/商家回复」标志行到回复时间行
    # （「发表于：」或「回复时间」）之间；同时把这段从评价内容行里剔除
    # （此前回复文本会混进 content，且 reply_content 恒为 None 导致历史
    # 导入永远收集不到飞猪样本）。
    reply_content: str | None = None
    reply_zone = re.search(
        r"(?:店家回复|酒店回复|商家回复)[^\n]*\n([\s\S]*?)(?:\n发表于[：:]|\n回复时间|$)",
        text,
    )
    if has_reply_content and reply_zone:
        reply_lines = [
            ln.strip()
            for ln in reply_zone.group(1).split("\n")
            if ln.strip() and not _FLIGGY_NOISE_RE.match(ln.strip())
        ]
        reply_content = "\n".join(reply_lines).strip() or None

    return ReviewItemInput(
        review_id=review_id,
        user_nickname=nick,
        score=score,
        content=content,
        review_time=review_time,
        reply_status="replied" if has_reply_content else "pending",
        reply_content=reply_content,
    )


_HOTEL_NAME_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()]+(?:酒店|公寓酒店)[^\n]{0,40})")


def _extract_fliggy_hotel_name(body_text: str) -> str | None:
    start = body_text.find("快速筛选")
    head = body_text[:start] if start >= 0 else body_text[:500]
    for line in head.split("\n"):
        if "酒店" in line:
            return line.strip()
    return None


_PENDING_TOTAL_RE = re.compile(r"待回复\s*[（(]?\s*(\d+)\s*[)）]?")


def _parse_pending_total(body_text: str) -> int | None:
    match = _PENDING_TOTAL_RE.search(body_text or "")
    return int(match.group(1)) if match else None
