"""下滑式/折叠式 EBK 页面的完整采集辅助。

实测（2026-09-14，CDP 只读探查三家门店页）：
- 携程房价日历：房型表在 ``.calendar-wrap`` 内部容器做虚拟滚动，任意时刻
  DOM 只保留视口附近约 20 行价格块，不滚动只能读到第一屏。
- 飞猪批量改价页：产品树默认折叠，需「全部展开」后子价格产品行才可见。
- 美团批量改价页：房型行默认可能折叠；全局「展开全部房型」是
  收起 <-> 默认（母产品）之间的开关，不会展开更多，逐行三角图标才可靠。

``scroll_page_and_collect`` 提供通用收敛循环：每轮把页面上的业务滚动容器
（排除菜单/侧栏/弹层）向下滚一步，调用一次采集函数，按业务键合并去重；
滚到底且无新增即停。``reset_page_scroll`` 读完把容器归位回顶部，避免把
操作者正在看的页面留在页尾。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# 标记字符串供测试假页面识别脚本用途，勿改动前缀。
SCROLL_STEP_JS = """/*lazy_page_collector:scroll_step*/
(deltaRatio) => {
    const excluded = /menu|sidebar|nav|header|footer|modal|dialog|dropdown|popup/i;
    const preferred = /calendar|table|list|content|main|scroll/i;
    const candidates = [];
    const all = Array.from(document.querySelectorAll('div,section,main,ul,tbody'));
    for (const el of all) {
        const cls = String(el.className || '');
        if (excluded.test(cls)) continue;
        const style = getComputedStyle(el);
        if (!/(auto|scroll|overlay)/.test(style.overflowY)) continue;
        if (el.scrollHeight <= el.clientHeight + 40 || el.clientHeight < 120) continue;
        candidates.push({
            el: el,
            score: (preferred.test(cls) ? 2 : 0)
                + Math.min(el.scrollHeight - el.clientHeight, 5000) / 5000,
        });
    }
    const doc = document.scrollingElement || document.documentElement;
    if (doc && doc.scrollHeight > doc.clientHeight + 40) {
        candidates.push({el: doc, score: 1});
    }
    candidates.sort((a, b) => b.score - a.score);
    const taken = candidates.slice(0, 3);
    let moved = false;
    let allBottom = taken.length > 0;
    for (const item of taken) {
        const before = item.el.scrollTop;
        const step = Math.max(120, Math.floor(item.el.clientHeight * (deltaRatio || 0.8)));
        item.el.scrollTop = Math.min(before + step, item.el.scrollHeight);
        if (item.el.scrollTop !== before) moved = true;
        if (item.el.scrollTop + item.el.clientHeight < item.el.scrollHeight - 4) {
            allBottom = false;
        }
    }
    return {moved: moved, atBottom: allBottom, containers: taken.length};
}
"""

RESET_SCROLL_JS = """/*lazy_page_collector:reset_scroll*/
() => {
    const excluded = /menu|sidebar|nav|header|footer|modal|dialog|dropdown|popup/i;
    const reset = [];
    document.querySelectorAll('div,section,main,ul,tbody').forEach((el) => {
        const cls = String(el.className || '');
        if (excluded.test(cls)) return;
        const style = getComputedStyle(el);
        if (!/(auto|scroll|overlay)/.test(style.overflowY)) return;
        if (el.scrollHeight <= el.clientHeight + 40 || el.clientHeight < 120) return;
        if (el.scrollTop !== 0) { el.scrollTop = 0; reset.push(cls.slice(0, 60)); }
    });
    const doc = document.scrollingElement || document.documentElement;
    if (doc && doc.scrollTop !== 0) { doc.scrollTop = 0; reset.push('window'); }
    return reset;
}
"""


async def scroll_page_and_collect(
    page: Any,
    collect_fn: Callable[[], Awaitable[list[Any]]],
    *,
    dedupe_key: Callable[[Any], Any],
    max_rounds: int = 24,
    settle_ms: int = 700,
) -> tuple[list[Any], dict[str, Any]]:
    """逐步滚动页面并多次采集，按 ``dedupe_key`` 合并虚拟滚动窗口。

    停止条件：滚动到业务容器底部且本轮无新增；或没有可滚容器且无新增；
    或达到 ``max_rounds`` 兜底。采集函数抛异常时保留已合并结果返回。
    """

    pool: list[Any] = []
    seen: set[Any] = set()

    async def _absorb() -> int:
        added = 0
        for item in (await collect_fn()) or []:
            key = dedupe_key(item)
            if key is None or key in seen:
                continue
            seen.add(key)
            pool.append(item)
            added += 1
        return added

    await _absorb()
    rounds = 0
    for _ in range(max(0, max_rounds)):
        state: dict[str, Any] = {}
        try:
            evaluated = await page.evaluate(SCROLL_STEP_JS, 0.8)
            state = evaluated if isinstance(evaluated, dict) else {}
        except Exception:
            state = {"moved": False, "atBottom": True}
        rounds += 1
        try:
            await page.wait_for_timeout(settle_ms)
        except Exception:
            pass
        try:
            added = await _absorb()
        except Exception:
            added = 0
            break
        if not state.get("moved") and added == 0:
            break
        if state.get("atBottom") and added == 0:
            break
    return pool, {"scroll_rounds": rounds, "collected": len(pool)}


async def reset_page_scroll(page: Any) -> None:
    """把业务滚动容器归位回顶部（尽力而为，失败不影响读取结果）。"""

    try:
        await page.evaluate(RESET_SCROLL_JS)
    except Exception:
        pass


__all__ = [
    "RESET_SCROLL_JS",
    "SCROLL_STEP_JS",
    "reset_page_scroll",
    "scroll_page_and_collect",
]
