"""B5.3 — 飞猪/美团批量改价页「目标产品选定」页面状态机。

编排逻辑（``run_product_selection``）是纯控制流：每一步失败即停并返回
``page_structure_changed`` / ``needs_review`` 与 evidence 行，不留半选状态继续。
页面 DOM 动作通过 ``SelectionActions`` 接口注入（``PlaywrightSelectionActions``
为真实实现），单测用 fake 实现覆盖全部分支。

页面行为依据 docs/pan-hotel-watch-b51-product-selection-probe-2026-08-24.md：
- 飞猪「取消」清空全部勾选；母房型勾选联动全部子价格产品；默认勾选不稳定，
  必须先清空再勾选。
- 美团「取消选择」清空；「卖价(佣金率)」模式默认已选；预览/提交按钮始终
  enabled，提交前必须主动校验预览内容（B6）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.browser.pricing_product_selection import (
    ProductRow,
    match_platform_products,
)

FLIGGY_PRICING_PAGE_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm"
    "#/ebk-rp/batchRoomStatusUpdate?type=price"
)
MEITUAN_PRICING_PAGE_URL = (
    "https://me.meituan.com/ebooking/merchant/product/batch-price"
)
# 2026-09-23 实测：批量改房价页产品树改版后不再展示价格；查房价交互的
# 现价/房态来源改为「产品管理 → 房价房量日历」页（产品子行带
# 房态/房量/卖价/底价）。写价链路仍用 MEITUAN_PRICING_PAGE_URL。
MEITUAN_ROOM_CALENDAR_URL = (
    "https://me.meituan.com/ebooking/merchant/product#/index"
)
# 2026-09-24 实测：飞猪「房价房量管理」日历页（roomsVsManage）是纯展示页，
# 展开房型后产品子行带 房态/房量/卖价；查房价交互改走此页，写价链路
# 仍用 FLIGGY_PRICING_PAGE_URL（批量修改页）。
FLIGGY_ROOM_CALENDAR_URL = (
    "https://hotel.fliggy.com/ebooking/hotelBaseInfoUv.htm"
    "#/ebk-rp/roomsVsManage"
)

FLIGGY_SELECTION_COUNTER_PATTERN = re.compile(r"已选(\d+)个/共(\d+)个")
MEITUAN_SELECTION_COUNTER_PATTERN = re.compile(r"已选(\d+)个产品")

# 美团房型行默认可能折叠（无可见子产品行）。全局「展开全部房型」实测是
# 收起 <-> 默认（母产品）之间的开关、不会展开更多，所以逐行点房型行上的
# 三角图标展开；只点「没有可见子产品行」的行，幂等可重复调用。
MEITUAN_EXPAND_COLLAPSED_JS = """/*lazy_page_collector:meituan_expand*/
() => {
    const vis = (el) => {
        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
        return s.visibility !== 'hidden' && s.display !== 'none'
            && r.width > 0 && r.height > 0;
    };
    let clicked = 0;
    const items = Array.from(document.querySelectorAll('.room-goods-item')).slice(0, 60);
    for (const item of items) {
        const goods = Array.from(
            item.querySelectorAll('.room-goods-item-goods-item')
        ).filter(vis);
        if (goods.length) continue;
        const icon = item.querySelector('.room-goods-item-room-icon');
        if (icon && vis(icon)) {
            icon.click();
            clicked += 1;
        }
    }
    return clicked;
}
"""

# 飞猪展开状态探测：可见产品树行数（checkbox 所在 tr）。展开是异步的，
# 以「子行数 ≥ 计数器的共N个」为收敛目标；marker 注释供测试假页面识别。
FLIGGY_VISIBLE_ROW_COUNT_JS = """/*lazy_page_collector:fliggy_row_count*/
() => {
    const vis = (el) => {
        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
        return s.visibility !== 'hidden' && s.display !== 'none'
            && r.width > 0 && r.height > 0;
    };
    const rows = Array.from(document.querySelectorAll('input[type="checkbox"]'))
        .filter(vis)
        .map((box) => box.closest('tr'))
        .filter(Boolean);
    const parents = rows.filter((tr) => /含\\d+个/.test(tr.innerText || '')).length;
    return {rows: rows.length, parents: parents, children: rows.length - parents - 1};
}
"""


@dataclass(frozen=True)
class SelectionCounter:
    selected: int
    total: int | None = None
    raw: str | None = None


class SelectionActions(Protocol):
    """页面动作接口（真实实现 PlaywrightSelectionActions，单测用 fake）。"""

    async def collect_rows(self) -> list[ProductRow]: ...

    async def read_counter(self) -> SelectionCounter | None: ...

    async def clear_selection(self) -> bool: ...

    async def check_row(self, row: ProductRow) -> bool: ...

    async def set_date_range(self, start: str, end: str) -> bool: ...


@dataclass
class _StepLog:
    kind: str
    detail: str
    changed_page: bool = False


async def run_product_selection(
    actions: SelectionActions,
    *,
    room_type: str,
    breakfast_kind: str | None,
    checkin_date: str,
    checkout_date: str | None,
    platform: str | None = None,
) -> dict[str, Any]:
    """把页面表单收敛到「只选审批目标产品 + 指定日期」，任何一步失败即停。

    ``platform`` 用于平台差异安全闸：飞猪母房型勾选会联动全部子产品
    （含双早，违反无早口径），匹配落到父行时必须 needs_review。
    """
    steps: list[_StepLog] = []

    rows = await actions.collect_rows()
    steps.append(_StepLog("collect_rows", f"读取到 {len(rows)} 行房型产品。"))
    if not rows:
        return _failure(
            "page_structure_changed",
            "页面产品树为空，未读取到任何房型产品行。",
            steps,
        )

    match = match_platform_products(room_type, rows, breakfast_kind=breakfast_kind)
    steps.append(
        _StepLog("map_room_type", f"{match.status}: {match.reason or '命中目标'}")
    )
    if match.status != "matched" or not match.matched_rows:
        return _failure(
            "needs_review" if match.status == "needs_review" else "page_structure_changed",
            match.reason or "房型未能映射到唯一平台产品。",
            steps,
            candidates=match.candidates,
        )
    if str(platform or "").strip().lower() == "fliggy" and all(
        row.is_parent for row in match.matched_rows
    ):
        return _failure(
            "needs_review",
            "飞猪母房型勾选会联动全部子产品（含双早），未读取到该房型的"
            "子价格产品行（无早/主价格），请人工确认页面展开状态后重试。",
            steps,
            candidates=[row.row_text for row in match.matched_rows[:10]],
        )

    cleared = await actions.clear_selection()
    steps.append(_StepLog("clear_selection", "点击清空全部勾选。", changed_page=True))
    if not cleared:
        return _failure("page_structure_changed", "未能点击清空勾选控件。", steps)

    counter = await actions.read_counter()
    steps.append(
        _StepLog(
            "verify_cleared",
            f"清空后勾选计数：{counter.raw if counter else '未读取到'}",
        )
    )
    if counter is None or counter.selected != 0:
        return _failure(
            "page_structure_changed",
            "清空勾选后计数不为 0，页面状态与预期不符。",
            steps,
            counter=counter,
        )

    for row in match.matched_rows:
        checked = await actions.check_row(row)
        steps.append(
            _StepLog("check_target", f"勾选目标产品行：{row.row_text[:60]}", changed_page=True)
        )
        if not checked:
            return _failure(
                "page_structure_changed",
                f"未能勾选目标产品行：{row.row_text[:60]}",
                steps,
                counter=counter,
            )

    counter_after = await actions.read_counter()
    expected = len(match.matched_rows)
    steps.append(
        _StepLog(
            "verify_selected",
            f"勾选后计数：{counter_after.raw if counter_after else '未读取到'}（期望 {expected}）",
        )
    )
    if counter_after is None or counter_after.selected != expected:
        return _failure(
            "page_structure_changed",
            f"勾选后计数与目标数不符（期望 {expected}，实读 "
            f"{counter_after.selected if counter_after else '未知'}），疑似联动了其它产品。",
            steps,
            counter=counter_after,
        )

    if checkin_date:
        end = checkout_date or checkin_date
        dated = await actions.set_date_range(checkin_date, end)
        steps.append(
            _StepLog("set_dates", f"设置日期区间 {checkin_date} ~ {end}", changed_page=True)
        )
        if not dated:
            return _failure(
                "page_structure_changed",
                f"未能设置日期区间 {checkin_date} ~ {end}。",
                steps,
                counter=counter_after,
            )

    return {
        "status": "success",
        "summary": (
            f"页面已只勾选审批目标产品（{expected} 项）并设置日期区间，未填写任何价格。"
        ),
        "matched_rows": [row.row_text for row in match.matched_rows],
        "counter": _counter_payload(counter_after),
        "steps": [_step_payload(step) for step in steps],
        "evidence": {
            "product_selection_only": True,
            "price_fill_invoked": False,
            "submit_invoked": False,
            "changed_page_steps": [step.kind for step in steps if step.changed_page],
        },
    }


def _failure(
    status: str,
    reason: str,
    steps: list[_StepLog],
    *,
    candidates: list[str] | None = None,
    counter: SelectionCounter | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "summary": reason,
        "steps": [_step_payload(step) for step in steps],
        "evidence": {
            "product_selection_only": True,
            "price_fill_invoked": False,
            "submit_invoked": False,
            "stopped_at": steps[-1].kind if steps else None,
        },
    }
    if candidates:
        payload["candidates"] = candidates[:10]
    if counter is not None:
        payload["counter"] = _counter_payload(counter)
    return payload


def _counter_payload(counter: SelectionCounter) -> dict[str, Any]:
    return {"selected": counter.selected, "total": counter.total, "raw": counter.raw}


def _step_payload(step: _StepLog) -> dict[str, Any]:
    return {"kind": step.kind, "detail": step.detail, "changed_page": step.changed_page}


class PlaywrightSelectionActions:
    """飞猪/美团批量改价页的 DOM 动作实现（B5.1 探测的 selector）。"""

    def __init__(self, page: Any, *, platform: str) -> None:
        self.page = page
        self.platform = str(platform or "").strip().lower()

    async def collect_rows(self) -> list[ProductRow]:
        """展开折叠行 + 滚动合并虚拟窗口后返回全量房型产品行。

        2026-09-14 实测：飞猪产品树默认折叠；长列表可能虚拟滚动，单次
        扫描只有视口附近窗口。飞猪先点一次「全部展开」，美团每轮对折叠
        房型行点三角图标展开，再按行文本合并去重滚动窗口；整段为空时
        保留原「等待 4s 重试一次」的 SPA 慢渲染兜底。
        """
        if self.platform not in ("fliggy", "meituan"):
            return []
        window_fn = (
            self._collect_fliggy_window
            if self.platform == "fliggy"
            else self._collect_meituan_window
        )
        if self.platform == "fliggy":
            # 「全部展开」只点一次：若控件是开关型（文本不变），每轮重点
            # 会把树收回，滚动窗口反而读空。
            await self._expand_fliggy_tree()
        pool = await self._scroll_collect(window_fn)
        if not pool:
            # SPA 路由渲染慢于 domcontentloaded；等待后重试一次。
            await self.page.wait_for_timeout(4_000)
            if self.platform == "fliggy":
                await self._expand_fliggy_tree()
            pool = await self._scroll_collect(window_fn)
        return _rows_from_js(pool)

    async def _expand_fliggy_tree(self) -> None:
        """产品树默认折叠：点「全部展开」后子价格产品行才可见。

        2026-09-14 实测：「全部展开」异步生效且会分批增行（行数在中间态
        停留 >1s），固定等待或「行数稳定」判据都会提前收敛，只读到第一
        个房型的子行。这里以表头计数器「共N个」为目标轮询到子行数达标；
        残余未展开父行再逐个点 ``.ant-table-row-expand-icon-collapse``
        箭头兜底。已展开（按钮变「全部收起」）时不点按钮，防止收回。
        """
        for _ in range(6):
            try:
                state = await self.page.evaluate(FLIGGY_VISIBLE_ROW_COUNT_JS)
            except Exception:
                state = None
            if isinstance(state, dict) and state.get("rows", 0) > 1:
                break
            await self.page.wait_for_timeout(1_500)
        target: int | None = None
        counter = await self.read_counter()
        if counter is not None and counter.total:
            target = int(counter.total)
        try:
            expand = self.page.get_by_text("全部展开", exact=True).first
            if await expand.count() > 0:
                await expand.click(timeout=4_000)
        except Exception:
            pass
        await self._wait_fliggy_expand_converged(target)
        try:
            clicked_any = False
            for _ in range(30):
                icons = self.page.locator(".ant-table-row-expand-icon-collapse")
                if await icons.count() == 0:
                    break
                await icons.first.click(timeout=2_000)
                clicked_any = True
                await self.page.wait_for_timeout(400)
            if clicked_any:
                await self._wait_fliggy_expand_converged(target)
        except Exception:
            pass

    async def _wait_fliggy_expand_converged(self, target_children: int | None) -> None:
        """轮询到子行数达标（有目标）或行数连续多次不变（无目标）。"""

        last_children = -1
        stable_rounds = 0
        for _ in range(16):
            try:
                state = await self.page.evaluate(FLIGGY_VISIBLE_ROW_COUNT_JS)
            except Exception:
                return
            children = state.get("children") if isinstance(state, dict) else None
            if isinstance(children, int):
                if target_children is not None and children >= target_children:
                    return
                if children == last_children:
                    stable_rounds += 1
                    if target_children is None and stable_rounds >= 3:
                        return
                    if target_children is not None and stable_rounds >= 5:
                        return  # 长时间无进展，交给箭头兜底
                else:
                    stable_rounds = 0
                last_children = children
            await self.page.wait_for_timeout(600)

    async def _scroll_collect(self, window_fn) -> list[dict[str, Any]]:
        from app.domain.browser.lazy_page_collector import (
            reset_page_scroll,
            scroll_page_and_collect,
        )

        # 去重键 = (行文本, 是否父行, 窗口内同文本出现序)：飞猪多个套房下的
        # 子行文案完全相同（「双早-限时退」等），只按文本去重会把各房型的
        # 主价格行折叠成一条；selector 路径不含 nth-child、同构行完全相同
        # 且重渲染会漂移，不能当身份。
        async def _window_with_rank() -> list[dict[str, Any]]:
            counts: dict[tuple[str, bool], int] = {}
            ranked: list[dict[str, Any]] = []
            for row in (await window_fn()) or []:
                if not isinstance(row, dict):
                    continue
                base = (
                    str(row.get("row_text") or "")[:160],
                    bool(row.get("is_parent")),
                )
                rank = counts.get(base, 0)
                counts[base] = rank + 1
                copied = dict(row)
                copied["_window_rank"] = rank
                ranked.append(copied)
            return ranked

        pool, _debug = await scroll_page_and_collect(
            self.page,
            _window_with_rank,
            dedupe_key=lambda row: (
                str(row.get("row_text") or "")[:160],
                bool(row.get("is_parent")),
                int(row.get("_window_rank") or 0),
            ),
        )
        await reset_page_scroll(self.page)
        for row in pool:
            row.pop("_window_rank", None)
        return pool

    async def _collect_fliggy_window(self) -> list[dict[str, Any]]:
        rows = await self.page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none'
                        && r.width > 0 && r.height > 0;
                };
                const cssPath = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const parts = []; let cur = el;
                    while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
                        const tag = cur.tagName.toLowerCase();
                        const cls = Array.from(cur.classList || []).slice(0, 3)
                            .map(c => `.${CSS.escape(c)}`).join('');
                        parts.unshift(`${tag}${cls}`); cur = cur.parentElement;
                    }
                    return parts.join(' > ');
                };
                const text = (el) => (el && (el.innerText || el.textContent) || '')
                    .replace(/\\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('input[type="checkbox"]'))
                    .filter(visible)
                    .map((box) => {
                        const row = box.closest('tr');
                        if (!row) return null;
                        const rowText = text(row);
                        if (!rowText || rowText.includes('全部房型产品')) return null;
                        return {
                            row_text: rowText.slice(0, 160),
                            is_parent: /含\\d+个/.test(rowText),
                            selector_hint: cssPath(box),
                        };
                    })
                    .filter(Boolean);
            }
            """
        )
        return rows if isinstance(rows, list) else []

    async def _collect_meituan_window(self) -> list[dict[str, Any]]:
        # 房型行默认可能折叠（子产品行不可见）：先逐行点三角图标展开。
        try:
            clicked = await self.page.evaluate(MEITUAN_EXPAND_COLLAPSED_JS)
            if clicked:
                await self.page.wait_for_timeout(600)
        except Exception:
            pass
        rows = await self.page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none'
                        && r.width > 0 && r.height > 0;
                };
                const cssPath = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const parts = []; let cur = el;
                    while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 7) {
                        const tag = cur.tagName.toLowerCase();
                        const cls = Array.from(cur.classList || []).slice(0, 3)
                            .map(c => `.${CSS.escape(c)}`).join('');
                        parts.unshift(`${tag}${cls}`); cur = cur.parentElement;
                    }
                    return parts.join(' > ');
                };
                const text = (el) => (el && (el.innerText || el.textContent) || '')
                    .replace(/\\s+/g, ' ').trim();
                const out = [];
                const roomRows = Array.from(
                    document.querySelectorAll('.room-goods-item-room')
                ).filter(visible);
                for (const roomRow of roomRows) {
                    const roomText = text(roomRow);
                    if (!roomText) continue;
                    const box = roomRow.querySelector('input[type="checkbox"]');
                    out.push({
                        row_text: roomText.slice(0, 160),
                        is_parent: true,
                        selector_hint: box ? cssPath(box) : '',
                    });
                    const goods = Array.from(
                        roomRow.parentElement.querySelectorAll(
                            '[class*="goods-item-goods"] input[type="checkbox"]'
                        )
                    ).filter(visible);
                    const seenTexts = new Set();
                    for (const goodBox of goods) {
                        const goodRow = goodBox.closest(
                            '[class*="goods-item-good"], [class*="product-item"]'
                        ) || goodBox.closest('div');
                        const goodText = text(goodRow);
                        if (!goodText || goodText === roomText) continue;
                        const dedupeKey = goodText.slice(0, 160);
                        if (seenTexts.has(dedupeKey)) continue;
                        seenTexts.add(dedupeKey);
                        out.push({
                            row_text: dedupeKey,
                            is_parent: false,
                            selector_hint: cssPath(goodBox),
                        });
                    }
                }
                return out;
            }
            """
        )
        return rows if isinstance(rows, list) else []

    async def read_counter(self) -> SelectionCounter | None:
        try:
            body = await self.page.locator("body").inner_text(timeout=6_000)
        except Exception:
            return None
        if self.platform == "fliggy":
            match = FLIGGY_SELECTION_COUNTER_PATTERN.search(body)
            if match:
                return SelectionCounter(
                    selected=int(match.group(1)),
                    total=int(match.group(2)),
                    raw=match.group(0),
                )
            return None
        if self.platform == "meituan":
            match = MEITUAN_SELECTION_COUNTER_PATTERN.search(body)
            if match:
                return SelectionCounter(
                    selected=int(match.group(1)),
                    total=None,
                    raw=match.group(0),
                )
        return None

    async def clear_selection(self) -> bool:
        label = "取消" if self.platform == "fliggy" else "取消选择"
        try:
            control = self.page.get_by_text(label, exact=True).first
            if await control.count() == 0:
                return False
            await control.click(timeout=5_000)
            await self.page.wait_for_timeout(1_200)
            return True
        except Exception:
            return False

    async def check_row(self, row: ProductRow) -> bool:
        if not row.selector_hint:
            return False
        try:
            box = self.page.locator(row.selector_hint).first
            await box.wait_for(state="visible", timeout=5_000)
            await box.click(timeout=5_000)
            await self.page.wait_for_timeout(800)
            return True
        except Exception:
            return False

    async def set_date_range(self, start: str, end: str) -> bool:
        try:
            placeholders = ("开始日期", "结束日期") if self.platform == "fliggy" else (
                "请选择日期",
                "请选择日期",
            )
            inputs = self.page.locator(f'input[placeholder="{placeholders[0]}"]')
            if await inputs.count() == 0:
                return False
            await inputs.first.click(timeout=5_000)
            await self.page.wait_for_timeout(400)
            await inputs.first.fill(start, timeout=5_000)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(600)
            if self.platform == "fliggy":
                end_inputs = self.page.locator(f'input[placeholder="{placeholders[1]}"]')
                if await end_inputs.count() == 0:
                    return False
                await end_inputs.first.click(timeout=5_000)
                await end_inputs.first.fill(end, timeout=5_000)
                await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(600)
            return True
        except Exception:
            return False


def _rows_from_js(rows: Any) -> list[ProductRow]:
    if not isinstance(rows, list):
        return []
    result: list[ProductRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_text = str(row.get("row_text") or "").strip()
        if not row_text:
            continue
        result.append(
            ProductRow(
                row_text=row_text,
                is_parent=bool(row.get("is_parent")),
                selector_hint=str(row.get("selector_hint") or "") or None,
            )
        )
    return result


async def ensure_product_selection(
    *,
    page: Any,
    platform: str,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
    query_type: str,
) -> dict[str, Any]:
    """dry-run/execute 入口的页面收敛步骤，返回 run_product_selection 的完整结果。

    调用方按 ``result['status']`` 判断：非 success 用
    ``build_selection_failure_payload`` 构造错误返回；success 时把结果
    （matched_rows / counter / steps）并入返回 evidence 供 B7-2 核验。
    """
    actions = execute_request.get("actions")
    first: dict[str, Any] = {}
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        first = actions[0]
    return await run_product_selection(
        PlaywrightSelectionActions(page, platform=platform),
        room_type=str(first.get("room_type") or ""),
        breakfast_kind=first.get("breakfast_kind"),
        checkin_date=str(first.get("checkin_date") or ""),
        checkout_date=first.get("checkout_date"),
        platform=platform,
    )


def build_selection_failure_payload(
    result: dict[str, Any],
    *,
    platform: str,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    query_type: str,
) -> dict[str, Any]:
    """把 run_product_selection 的失败结果构造成 dry-run/execute 返回 payload。"""
    from datetime import UTC, datetime

    return {
        "status": str(result.get("status") or "page_structure_changed"),
        "summary": f"目标产品选定失败，已停止：{result.get('summary') or ''}",
        "platform": platform,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": query_type,
            "actor_id": actor_id,
            "product_selection": result,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        "evidence": {
            "platform": platform,
            "product_selection": result.get("evidence"),
            "price_fill_invoked": False,
            "ota_submit_invoked": False,
        },
        "next_action": (
            "请人工确认页面房型产品与审批单一致（候选见 data.product_selection."
            "candidates），修正房型/早餐粒度后重试。"
        ),
    }
