"""三平台 EBK「房型房价名单」只读读取（查询房价交互的浏览器面）。

用途：飞书群「查房价 / 查询房价」指令 → 给运营一份完整的房型名单与
页面可见现价，作为手动调价前的参考。只读：只切视图、只读 DOM，不填价、
不提交任何 OTA 表单。

- 携程：EBK 房价日历页（不选房型过滤），按日期列扫描全部价格块，
  房型名从价格块所属行文本启发式提取（截到首个价格/房态噪声词）。
  房型表在内部容器虚拟滚动（2026-09-14 实测），需逐步滚动逐窗口收集
  再合并（lazy_page_collector.scroll_page_and_collect）。
- 美团：批量改房价页产品树 2026-09 改版后不再展示任何价格，现价迁到
  「房价房量日历」页（product#/index）的产品子行。读取流程：把顶部
  日期窗口设为入住日、点表头「展开全部房型」展开产品子行（残余房型
  逐个点房型名兜底）、确认「显示价格」开关，再按 房态/房量/卖价/底价
  结构化单元格收集（2026-09-23/24 实测）。
- 飞猪：同理改走「房价房量管理」日历页（roomsVsManage，2026-09-24
  实测）：「显示 房价/房态」开关默认已开，点「全部展开」后产品子行每
  日期格渲染 房态/房量/卖价。纯展示页，不勾选、不填价、不提交；入住日
  在默认 14 天窗口外时平移「请选择日期」。

真实数据优先：页面没展示的价格就是 None，不推断、不编造。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# 房型行文本里的价格/房态噪声词：房型名截到第一个噪声词为止。
_ROOM_LABEL_NOISE_RE = re.compile(
    r"(卖价|售价|现价|底价|成本价|房价|价格|已开房|已关房|开房|关房|满房|停售|可售|"
    r"不限量|限量|剩余|仅剩|房量|预付|现付|担保|¥|￥|"
    r"(?<!\d)\d{2,5}(?:\.\d{1,2})?(?!\d))"
)
_LABELED_SELL_RE = re.compile(
    r"(?:卖价|售价|现价|房价)\s*(?:[:：]|\(元\)|（元）|元)?\s*(\d{2,5}(?:\.\d{1,2})?)"
)
_LABELED_BASE_RE = re.compile(
    r"(?:底价|成本价)\s*(?:[:：]|\(元\)|（元）|元)?\s*(\d{2,5}(?:\.\d{1,2})?)"
)
_MONEY_RE = re.compile(r"(?<!\d)([1-9]\d{1,4}(?:\.\d{1,2})?)(?!\d)")
_STATUS_KEYWORDS = ("已开房", "已关房", "开房", "关房", "满房", "停售", "可售")
# 2026-09-14：多房型门店（携程日历单列 60 个价格块候选）曾被 60 截断，
# 房价查询卡丢失下半段房型；卡片层已分页，这里只作超大异常页的兜底上限。
_MAX_ROOM_RATES = 120


def extract_room_label(room_text: Any) -> str:
    """从房型行文本提取展示名：截到首个价格/房态噪声词，最長 30 字。"""

    text = re.sub(r"\s+", " ", str(room_text or "")).strip()
    # 飞猪母房型行的「含N个」尾标不属于房型名。
    text = re.sub(r"含\d+个$", "", text).strip()
    if not text:
        return "-"
    match = _ROOM_LABEL_NOISE_RE.search(text)
    label = text[: match.start()].strip(" ｜|,，-—·") if match else text
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        label = text[:20]
    return label[:30]


def extract_suite_label(room_text: Any) -> str:
    """房型套件名：携程产品行截到首个「<」（价格方案/早餐标记之前），
    其余平台沿用噪声词截断（父行的「含N个」尾标先去掉）。

    2026-09-14 口径：卡片/调价都以房型（套件）为单位，变体（无早/单早/
    双早/预售）只用于挑无早基准价，不进房型名。
    """

    text = re.sub(r"\s+", " ", str(room_text or "")).strip()
    text = re.sub(r"含\d+个$", "", text).strip()
    if not text:
        return "-"
    angle = text.find("<")
    if 0 < angle <= 60:
        return text[:angle].strip(" ｜|,，-—·")[:30] or extract_room_label(text)
    return extract_room_label(text)


def parse_room_rate_text(*parts: Any) -> dict[str, Any]:
    """从价格块/产品行文本解析 卖价/底价/房态（无标签时金额兜底）。"""

    source_text = re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip()
    sell = _first_number(_LABELED_SELL_RE.search(source_text))
    base = _first_number(_LABELED_BASE_RE.search(source_text))
    if sell is None or base is None:
        money_values = _money_like_values(source_text)
        if sell is None and money_values:
            sell = money_values[0]
        if base is None and len(money_values) >= 2:
            base = money_values[1]
    status = next((word for word in _STATUS_KEYWORDS if word in source_text), None)
    return {
        "sell_price_yuan": sell,
        "base_price_yuan": base,
        "status_text": status,
        "source_text": source_text[:200] or None,
    }


def ctrip_room_rates_from_candidates(candidates: Any) -> list[dict[str, Any]]:
    """把携程日历价格块候选按房型（套件）分组，每组只输出无早基准价。

    2026-09-14 业务口径：调价/展示都以房型×无早为准（单早/双早随无早联动）。
    组内择优：无早口径分级（标签/干净无早 优于 预售/钟点类无早）→ 同级
    优先未关房 → score 高者。房型没有无早变体时保留一行「页面未展示」，
    不隐藏房型。
    """

    from app.domain.browser.pricing_product_selection import (
        canonical_product_tier,
        is_unavailable_status_text,
    )

    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        room_text = str(candidate.get("room_text") or "").strip()
        if not room_text:
            continue
        suite = extract_suite_label(room_text)
        parsed = parse_room_rate_text(
            candidate.get("text"),
            candidate.get("nearby_text"),
        )
        score = int(candidate.get("score") or 0)
        unavailable = is_unavailable_status_text(
            parsed.get("status_text"),
            candidate.get("text"),
        )
        tier = canonical_product_tier(room_text)
        rank = (
            tier is None,  # 无早口径优先于非口径
            tier if tier is not None else 9,
            unavailable,  # 同级优先未关房
            -score,
        )
        existing = best.get(suite)
        if existing is None:
            best[suite] = {"rank": rank, "parsed": parsed, "fallback": parsed}
            order.append(suite)
            continue
        # 无无早变体的房型也要有房态可展示：记住 score 最高的候选做兜底。
        if score > existing.get("fallback_score", -1):
            existing["fallback"] = parsed
            existing["fallback_score"] = score
        if rank < existing["rank"]:
            best[suite] = {
                "rank": rank,
                "parsed": parsed,
                "fallback": existing["fallback"],
                "fallback_score": existing.get("fallback_score", -1),
            }

    room_rates: list[dict[str, Any]] = []
    for suite in order[:_MAX_ROOM_RATES]:
        chosen = best[suite]
        parsed = chosen["parsed"]
        if chosen["rank"][0]:  # 该房型没有无早口径变体
            fallback = chosen["fallback"]
            room_rates.append(
                {
                    "room_label": suite,
                    "sell_price_yuan": None,
                    "base_price_yuan": None,
                    "status_text": fallback.get("status_text"),
                }
            )
            continue
        room_rates.append({"room_label": suite, **parsed})
    return room_rates


def _row_field(row: Any, key: str) -> Any:
    """行字段读取：兼容 dict（JS 原始行）与 ProductRow dataclass（采集器输出）。"""

    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def product_tree_rates_from_rows(rows: Any) -> list[dict[str, Any]]:
    """把美团/飞猪产品树行转成房型×无早口径名单。

    父房型行提供房型名；子行里挑无早基准价（美团「母产品」/飞猪「主价格」
    = 无早价格；同级优先非 满房/已关房 行）。没有无早子行或父行下没有
    子行时保留「页面未展示」一行，不隐藏房型。子行本身不再逐条输出。
    """

    from app.domain.browser.pricing_product_selection import (
        canonical_product_tier,
        is_unavailable_status_text,
    )

    result: list[dict[str, Any]] = []
    suite_label: str | None = None
    suite_children: list[str] = []

    def _flush() -> None:
        if suite_label is None:
            return
        parsed = _pick_canonical_child(suite_children)
        result.append({"room_label": suite_label, **parsed})

    def _pick_canonical_child(children: list[str]) -> dict[str, Any]:
        best: dict[str, Any] | None = None
        best_rank: tuple | None = None
        for child_text in children:
            parsed = parse_room_rate_text(child_text)
            tier = canonical_product_tier(child_text)
            rank = (
                tier is None,
                tier if tier is not None else 9,
                is_unavailable_status_text(parsed.get("status_text"), child_text),
            )
            if best_rank is None or rank < best_rank:
                best = parsed
                best_rank = rank
        if best is None or best_rank is None or best_rank[0]:
            return {
                "sell_price_yuan": None,
                "base_price_yuan": None,
                "status_text": (best or {}).get("status_text"),
            }
        return best

    for row in rows if isinstance(rows, list) else []:
        row_text = str(_row_field(row, "row_text") or "").strip()
        if not row_text:
            continue
        if _row_field(row, "is_parent"):
            _flush()
            suite_children = []
            suite_label = extract_suite_label(row_text)
            continue
        if suite_label is None:
            # 父行缺失（异常序）：子行按自身文本独立成行，保证不丢数据。
            suite_label = extract_suite_label(row_text)
            suite_children = []
        suite_children.append(row_text)
    _flush()
    return result[:_MAX_ROOM_RATES]


def _ctrip_candidate_key(candidate: Any) -> tuple[str, str] | None:
    """携程价格块候选的跨窗口身份：虚拟滚动重渲染会换节点，文本键稳定。"""

    if not isinstance(candidate, dict):
        return None
    return (
        str(candidate.get("room_text") or "")[:80],
        str(candidate.get("text") or "")[:80],
    )


async def read_ctrip_calendar_room_rates(
    page: Any,
    *,
    checkin_date: str,
) -> dict[str, Any]:
    """携程 EBK 日历页读取指定日期全部房型的现价（不选房型过滤）。

    2026-09-14 实测：房型表在 .calendar-wrap 内部容器虚拟滚动，DOM 任意
    时刻只保留视口附近窗口，单次扫描必丢房型。这里逐步滚动容器、逐窗口
    收集价格块候选再按房型合并，读全后把容器归位回顶部。
    """

    from app.domain.browser.ctrip_room_type_selector import (
        detect_price_block_candidates,
    )
    from app.domain.browser.lazy_page_collector import (
        reset_page_scroll,
        scroll_page_and_collect,
    )

    if not re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", str(checkin_date or "")):
        return {
            "status": "data_required",
            "summary": "查询房价需要有效的入住日期。",
            "room_rates": [],
        }

    last_detect_status: str | None = None

    async def _collect_window() -> list[Any]:
        nonlocal last_detect_status
        result = await detect_price_block_candidates(
            page,
            room_type="",
            checkin_date=checkin_date,
        )
        if not isinstance(result, dict):
            last_detect_status = "unexpected_result"
            return []
        last_detect_status = str(result.get("status") or "")
        return list(result.get("candidates") or [])

    candidates, _scroll_debug = await scroll_page_and_collect(
        page,
        _collect_window,
        dedupe_key=_ctrip_candidate_key,
    )
    await reset_page_scroll(page)
    room_rates = ctrip_room_rates_from_candidates(candidates)
    if not room_rates:
        return {
            "status": "not_found",
            "summary": (
                "携程 EBK 日历上没有读取到该日期的房型价格块"
                f"（{last_detect_status or 'unknown'}）。"
            ),
            "room_rates": [],
        }
    return {
        "status": "success",
        "summary": f"携程 EBK 日历读取到 {len(room_rates)} 个房型的无早基准价。",
        "room_rates": room_rates,
    }


async def read_product_tree_room_rates(
    page: Any,
    *,
    platform: str,
) -> dict[str, Any]:
    """美团/飞猪批量改价页产品树读取房型名单与行内可见价格。"""

    from app.domain.browser.pricing_page_product_selection import (
        PlaywrightSelectionActions,
    )

    actions = PlaywrightSelectionActions(page, platform=platform)
    rows = await actions.collect_rows()
    room_rates = product_tree_rates_from_rows(rows)
    if not room_rates:
        return {
            "status": "not_found",
            "platform": platform,
            "summary": f"{_platform_label(platform)}批量改价页没有读取到房型产品行。",
            "room_rates": [],
        }
    return finalize_product_tree_read(
        {
            "status": "success",
            "platform": platform,
            "summary": f"{_platform_label(platform)}批量改价页读取到 {len(room_rates)} 个房型（无早口径）。",
            "room_rates": room_rates,
        }
    )


def finalize_product_tree_read(read: dict[str, Any]) -> dict[str, Any]:
    """整树无价格且无房态 → 明确 page_structure_changed，不再伪成功。

    2026-09-23 实测：美团/飞猪批量改价页产品树改版后行文本不再包含价格，
    读取结果曾是「success + 全部价格页面未展示」，卡片看起来像读成功
    却一列数字都没有。整页无价时降级为结构变化口径（房型名单保留，
    卡片副标题带 ⚠️）；部分行有价/有房态（真缺某房型价格）不算异常。
    """

    if str(read.get("status") or "") != "success":
        return read
    rates = [r for r in (read.get("room_rates") or []) if isinstance(r, dict)]
    if not rates:
        return read
    all_priceless = all(
        r.get("sell_price_yuan") is None
        and r.get("base_price_yuan") is None
        and not r.get("status_text")
        for r in rates
    )
    if not all_priceless:
        return read
    platform = _platform_label(str(read.get("platform") or "").strip().lower()) if read.get("platform") else _platform_label("")
    return {
        **read,
        "status": "page_structure_changed",
        "summary": f"{platform}批量改价页产品树未展示行内价格（页面改版或未加载完成），仅返回房型名单。",
    }


# --- 美团「房价房量日历」读取（2026-09-23 改版后的现价来源） ---

# 逐轮只点「第一个仍折叠的房型名」，串行展开，避免同一轮内 DOM 变化误点。
_MEITUAN_EXPAND_ONE_JS = """/*pricing_room_rates:meituan_expand_one*/
() => {
    const rows = Array.from(document.querySelectorAll('.vxe-body--row'));
    for (const row of rows) {
        if (!String(row.className || '').includes('level-0')) continue;
        const next = row.nextElementSibling;
        if (next && String(next.className || '').includes('level-1')) continue;
        const name = row.querySelector('.room-name .name-text');
        if (name) { name.click(); return 1; }
    }
    return 0;
}
"""

# 表头「展开全部房型」是真正的总开关：一键展开全部产品子行，展开后文案
# 变收起、再找不到本按钮（missing 即已展开，天然幂等）。工具栏同名
# .lz-checkbox 是隐藏控件，点了只翻自身勾选态不出行（2026-09-24 实测）。
_MEITUAN_EXPAND_ALL_JS = """/*pricing_room_rates:meituan_expand_all*/
() => {
    const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
    const btn = Array.from(
        document.querySelectorAll('.open-or-close-all button, .open-all-title')
    ).find(el => clean(el.innerText) === '展开全部房型');
    if (!btn) return 0;
    btn.click();
    return 1;
}
"""

_MEITUAN_LEVEL1_COUNT_JS = """/*pricing_room_rates:meituan_level1_count*/
() => Array.from(document.querySelectorAll('.vxe-body--row'))
    .filter(r => String(r.className || '').includes('level-1')).length
"""

_MEITUAN_COLLECT_JS = """/*pricing_room_rates:meituan_collect*/
() => {
    const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
    const text = (el) => clean(el && el.innerText);
    const rooms = [];
    let current = null;
    for (const row of Array.from(document.querySelectorAll('.vxe-body--row'))) {
        const cls = String(row.className || '');
        if (cls.includes('level-0')) {
            const switchEl = row.querySelector('.room-status-switch');
            current = {
                name: text(row.querySelector('.room-name .name-text')),
                status: switchEl && String(switchEl.className).includes('lz-switch-active')
                    ? '已开房' : '已关房',
                count: text(row.querySelector('.room_count')),
                products: [],
            };
            rooms.push(current);
            continue;
        }
        if (!cls.includes('level-1') || !current) continue;
        const cells = Array.from(row.querySelectorAll('.vxe-body--column'));
        const tags = Array.from(row.querySelectorAll('.tag-trigger, .tags-trigger'))
            .map((t) => text(t)).join(' ');
        const dateCells = [];
        for (const cell of cells.slice(1)) {
            if (!cell.querySelector('.goods-price-item')) continue;
            dateCells.push({
                status: text(cell.querySelector('.display-roomStatus .display-text')),
                count: text(cell.querySelector('.display-count .display-text')),
                sell: text(cell.querySelector('.saleprice-wrapper')),
                base: text(cell.querySelector('.baseprice-wrapper')),
            });
        }
        current.products.push({
            name: text(row.querySelector('.goods-name-ellipsis')) || text(cells[0]),
            tags: tags,
            cells: dateCells,
        });
    }
    const pickers = Array.from(document.querySelectorAll('input.lz-date-picker-editor'))
        .map((input) => input.value || '');
    return {rooms: rooms, pickers: pickers};
}
"""


def _normalize_date_text(value: Any) -> str | None:
    """'2026-9-5' / '2026-09-05' → '2026-09-05'；非法输入返回 None。"""

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", str(value or "").strip())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _enumerate_date_window(start: Any, end: Any, *, limit: int = 31) -> list[str]:
    """日期窗口 [start, end] 展开成连续日期列表（非法/倒序时退化为单日）。"""

    from datetime import date as date_cls, timedelta

    start_text = _normalize_date_text(start)
    if not start_text:
        return []
    start_date = date_cls.fromisoformat(start_text)
    end_text = _normalize_date_text(end) or start_text
    end_date = date_cls.fromisoformat(end_text)
    if end_date < start_date:
        end_date = start_date
    days = int((end_date - start_date).days)
    return [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range(min(days, limit - 1) + 1)
    ]


def _to_price_number(value: Any) -> float | int | None:
    """'506.00'/'506' → 506.0/506；空/非数值返回 None。"""

    text = str(value or "").strip().replace(",", "").replace("¥", "")
    if not re.match(r"^\d+(?:\.\d+)?$", text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def meituan_calendar_room_rates(
    collected: Any,
    *,
    checkin_date: str,
) -> list[dict[str, Any]]:
    """把日历页采集结果转成指定入住日的 房型×无早 名单。

    ``collected`` 是 ``_MEITUAN_COLLECT_JS`` 的返回：``{rooms, pickers}``。
    日期列与窗口日期按出现顺序对齐；入住日不在窗口时回退首列（调用方
    已先把窗口设成入住日，这里是兜底）。产品择优沿用无早口径分级，
    同级优先未关房；房型无产品时保留房型行 + 房型级房态/房量。
    """

    from app.domain.browser.pricing_product_selection import (
        canonical_product_tier,
        is_unavailable_status_text,
    )

    if not isinstance(collected, dict):
        return []
    dates = _enumerate_date_window(
        *(collected.get("pickers") or [])[:2] or (None, None)
    )
    target_date = _normalize_date_text(checkin_date) or ""
    target_index = dates.index(target_date) if target_date in dates else 0

    room_rates: list[dict[str, Any]] = []
    for room in collected.get("rooms") or []:
        if not isinstance(room, dict):
            continue
        room_label = str(room.get("name") or "").strip()
        if not room_label:
            continue
        products = [p for p in (room.get("products") or []) if isinstance(p, dict)]
        if not products:
            room_rates.append(
                {
                    "room_label": room_label,
                    "sell_price_yuan": None,
                    "base_price_yuan": None,
                    "status_text": _meituan_status_text(room.get("status"), room.get("count")),
                }
            )
            continue
        best: dict[str, Any] | None = None
        best_rank: tuple | None = None
        fallback_status: str | None = None
        for product in products:
            cells = product.get("cells") or []
            cell = cells[target_index] if 0 <= target_index < len(cells) else {}
            sell = _to_price_number(cell.get("sell"))
            base = _to_price_number(cell.get("base"))
            status = _meituan_status_text(cell.get("status"), cell.get("count"))
            parsed = {
                "sell_price_yuan": sell,
                "base_price_yuan": base,
                "status_text": status,
            }
            if fallback_status is None:
                fallback_status = status
            tier = canonical_product_tier(
                f"{product.get('name') or ''} {product.get('tags') or ''}"
            )
            rank = (
                tier is None,
                tier if tier is not None else 9,
                is_unavailable_status_text(cell.get("status")),
            )
            if best_rank is None or rank < best_rank:
                best = parsed
                best_rank = rank
        if best is not None and best_rank is not None and not best_rank[0]:
            chosen = best
        else:
            # 房型没有无早口径变体：保留房型行（房态用首个产品的），
            # 价格按 2026-09-14 口径留空，不拿单早/双早价格冒充。
            chosen = {
                "sell_price_yuan": None,
                "base_price_yuan": None,
                "status_text": fallback_status,
            }
        room_rates.append(
            {
                "room_label": room_label,
                "sell_price_yuan": chosen.get("sell_price_yuan"),
                "base_price_yuan": chosen.get("base_price_yuan"),
                "status_text": chosen.get("status_text"),
            }
        )
    return room_rates[:_MAX_ROOM_RATES]


def _meituan_status_text(status: Any, count: Any) -> str | None:
    """美团日历格子的房态+房量合成一个展示值：'已开房·不限' / '已开房·5/0/5'。"""

    status_text = str(status or "").strip()
    count_text = str(count or "").strip()
    if status_text and count_text:
        return f"{status_text}·{count_text}"
    return status_text or count_text or None


async def read_meituan_calendar_room_rates(
    page: Any,
    *,
    checkin_date: str,
) -> dict[str, Any]:
    """美团「房价房量日历」页读取指定入住日的房型现价/房态（只读）。

    2026-09-23 实测：批量改房价页产品树改版后没有任何价格；现价只在
    日历页展开的产品子行上（每日期格 = 房态/房量/卖价/底价）。读取只
    切换视图：设日期窗口、点表头「展开全部房型」一键展开（残余房型逐个
    点房型名兜底）、必要时点「显示价格」显示开关，不触碰任何写入按钮。
    """

    try:
        await page.wait_for_selector(".vxe-body--row", timeout=15_000)
    except Exception:
        return {
            "status": "not_found",
            "summary": "美团房价房量日历没有渲染出房型行。",
            "room_rates": [],
        }

    normalized_date = _normalize_date_text(checkin_date)
    window_applied = False
    if normalized_date:
        window_applied = await _set_meituan_calendar_date_window(page, normalized_date)

    await _expand_meituan_rooms(page)
    await _ensure_meituan_price_display(page)

    try:
        collected = await page.evaluate(_MEITUAN_COLLECT_JS)
    except Exception:
        collected = None
    room_rates = meituan_calendar_room_rates(collected, checkin_date=normalized_date or "")
    if not room_rates:
        return {
            "status": "not_found",
            "summary": "美团房价房量日历没有读取到房型价格行。",
            "room_rates": [],
        }
    summary = f"美团房价房量日历读取到 {len(room_rates)} 个房型的现价（无早口径）。"
    if normalized_date and not window_applied:
        summary += "（日期窗口设置失败，按页面当前窗口读取）"
    return {"status": "success", "summary": summary, "room_rates": room_rates}


async def _set_meituan_calendar_date_window(page: Any, checkin_date: str) -> bool:
    """把日历顶部日期窗口设成 [入住日, 入住日]；失败返回 False。"""

    try:
        pickers = page.locator("input.lz-date-picker-editor")
        if await pickers.count() < 2:
            return False
        for index in (0, 1):
            target = pickers.nth(index)
            await target.click(timeout=4_000)
            await page.wait_for_timeout(300)
            await target.fill(checkin_date, timeout=4_000)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(600)
        await page.wait_for_timeout(2_000)
        return True
    except Exception:
        return False


async def _expand_meituan_rooms(page: Any) -> None:
    """房型行默认折叠（产品子行不可见）：优先点表头「展开全部房型」一键
    展开，残余未展开房型再逐个点房型名兜底（幂等收敛）。"""

    try:
        clicked = await page.evaluate(_MEITUAN_EXPAND_ALL_JS)
    except Exception:
        clicked = 0
    if clicked:
        await _wait_meituan_level1_rows(page)
    for _ in range(_MAX_ROOM_RATES):
        try:
            clicked = await page.evaluate(_MEITUAN_EXPAND_ONE_JS)
        except Exception:
            return
        if not clicked:
            return
        await page.wait_for_timeout(350)


async def _wait_meituan_level1_rows(page: Any) -> None:
    """一键展开异步渲染：轮询到产品子行出现（有界超时）。"""

    for _ in range(10):
        try:
            count = await page.evaluate(_MEITUAN_LEVEL1_COUNT_JS)
        except Exception:
            return
        if int(count or 0) > 0:
            return
        await page.wait_for_timeout(500)


async def _ensure_meituan_price_display(page: Any) -> None:
    """「显示价格」是持久化显示开关；关着时产品行没有价格文本，点一次开关。"""

    for _ in range(2):
        try:
            found = await page.evaluate(
                "() => document.querySelectorAll"
                "('.goods-price-item .saleprice-wrapper').length"
            )
        except Exception:
            return
        if int(found or 0) > 0:
            return
        try:
            await page.locator(".lz-checkbox", has_text="显示价格").first.click(
                timeout=3_000
            )
            await page.wait_for_timeout(1_500)
        except Exception:
            return


# --- 飞猪「房价房量管理」日历读取（2026-09-24 实测新读法） ---

_FLIGGY_CAL_ENSURE_TOGGLE_JS = """/*pricing_room_rates:fliggy_cal_toggle*/
(mode) => {
    const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
    const span = Array.from(document.querySelectorAll('span'))
        .find(el => clean(el.innerText) === mode && el.childElementCount === 0);
    if (!span) return 'missing';
    const label = span.closest('.ant-checkbox-wrapper');
    if (!label) return 'no-label';
    if (String(label.className).includes('ant-checkbox-wrapper-checked')) return 'already-on';
    const input = label.querySelector('input');
    if (input) { input.click(); } else { label.click(); }
    return 'turned-on';
}
"""

# CSS Modules 类名带构建 hash（roomTypeSwitch___vqCx0 等），必须前缀匹配。
_FLIGGY_CAL_COLLECT_JS = """/*pricing_room_rates:fliggy_cal_collect*/
() => {
    const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
    const out = {dates: [], rooms: []};
    out.dates = Array.from(document.querySelectorAll('span, div'))
        .filter(el => el.childElementCount === 0 && /^\\d{2}-\\d{2}$/.test(clean(el.innerText)))
        .map(el => clean(el.innerText));
    const roomRows = Array.from(document.querySelectorAll('div'))
        .filter(r => r.querySelector('[class*="roomTypeSwitch"]')
                  && !r.querySelector('[class*="priceCell"]')
                  && String(r.className || '').includes('items-center'));
    for (const row of roomRows) {
        const nameEl = row.querySelector('.font-bold');
        if (!nameEl) continue;
        const room = {
            name: clean(nameEl.innerText),
            cells: Array.from(row.querySelectorAll('[class*="roomTypeSwitch"]'))
                .map(sw => {
                    const btn = sw.querySelector('[class*="btn"]');
                    return btn ? clean(btn.innerText) : '';
                }),
            products: [],
        };
        let sib = row.nextElementSibling;
        while (sib && sib.querySelectorAll
               && sib.querySelectorAll('[class*="priceCell"]').length) {
            // 一个房型的全部产品在同一个容器里：以每个产品名（.font-bold）
            // 为锚向上找「包含价格格的最小祖先」= 单个产品块，避免把
            // 整个房型容器的格子都算到第一个产品头上。
            const anchors = Array.from(sib.querySelectorAll('.font-bold'));
            for (const el of anchors) {
                let holder = el;
                while (holder && holder !== sib
                       && !holder.querySelector('[class*="priceCell"]')) {
                    holder = holder.parentElement;
                }
                if (!holder || holder === sib) continue;
                const tags = Array.from(
                    holder.querySelectorAll('.ant-tag, [class*="tag"]')
                ).map(t => clean(t.innerText)).join(' ');
                room.products.push({
                    name: clean(el.innerText),
                    tags: tags,
                    cells: Array.from(
                        holder.querySelectorAll('[class*="priceCell"]')
                    ).map(pc => {
                        const cellHolder = pc.parentElement;
                        const inv = cellHolder
                            ? cellHolder.querySelector('[class*="invCell"]') : null;
                        const statusEl = inv
                            ? inv.querySelector('[class*="switchText"]') : null;
                        const numEl = inv
                            ? inv.querySelector('[class*="num"]') : null;
                        const priceEl = pc.querySelector(
                            '[class*="price"]:not([class*="priceCell"])'
                        );
                        return {
                            status: statusEl ? clean(statusEl.innerText) : '',
                            count: numEl ? clean(numEl.innerText) : '',
                            sell: priceEl ? clean(priceEl.innerText) : '',
                        };
                    }),
                });
            }
            sib = sib.nextElementSibling;
        }
        out.rooms.push(room);
    }
    return out;
}
"""


def fliggy_calendar_room_rates(
    collected: Any,
    *,
    checkin_date: str,
) -> list[dict[str, Any]]:
    """把日历页采集结果转成指定入住日的房型×无早名单。

    ``collected`` 是 ``_FLIGGY_CAL_COLLECT_JS`` 的返回：``{dates, rooms}``。
    日期格顺序与表头 MM-DD 一致；入住日不在窗口时回退首列（调用方已先
    平移窗口，这里是兜底）。产品择优沿用无早口径分级，同级优先非满房；
    页面不展示底价，如实留空。
    """

    from app.domain.browser.pricing_product_selection import (
        canonical_product_tier,
        is_unavailable_status_text,
    )

    if not isinstance(collected, dict):
        return []
    dates: list[str] = []
    for raw in collected.get("dates") or []:
        text = str(raw or "").strip()
        if text not in dates:
            dates.append(text)
    normalized = _normalize_date_text(checkin_date)
    target = normalized[5:] if normalized else ""
    target_index = dates.index(target) if target in dates else 0

    room_rates: list[dict[str, Any]] = []
    for room in collected.get("rooms") or []:
        if not isinstance(room, dict):
            continue
        room_label = str(room.get("name") or "").strip()
        if not room_label:
            continue
        products = [p for p in (room.get("products") or []) if isinstance(p, dict)]
        if not products:
            room_cells = [str(c or "") for c in (room.get("cells") or [])]
            room_status = room_cells[target_index] if 0 <= target_index < len(room_cells) else ""
            room_rates.append(
                {
                    "room_label": room_label,
                    "sell_price_yuan": None,
                    "base_price_yuan": None,
                    "status_text": room_status or None,
                }
            )
            continue
        best: dict[str, Any] | None = None
        best_rank: tuple | None = None
        fallback_status: str | None = None
        for product in products:
            cells = product.get("cells") or []
            cell = cells[target_index] if 0 <= target_index < len(cells) else {}
            status = str(cell.get("status") or "").strip()
            count = str(cell.get("count") or "").strip()
            status_text = f"{status}·{count}" if status and count else (status or count or None)
            if fallback_status is None:
                fallback_status = status_text
            tier = canonical_product_tier(
                f"{product.get('name') or ''} {product.get('tags') or ''}"
            )
            rank = (
                tier is None,
                tier if tier is not None else 9,
                is_unavailable_status_text(status),
            )
            candidate = {
                "sell_price_yuan": _to_price_number(cell.get("sell")),
                "status_text": status_text,
            }
            if best_rank is None or rank < best_rank:
                best = candidate
                best_rank = rank
        if best is not None and best_rank is not None and not best_rank[0]:
            chosen = best
        else:
            # 房型没有无早口径变体：保留房型行（房态用首个产品的），
            # 价格按 2026-09-14 口径留空，不拿单早/双早价格冒充。
            chosen = {
                "sell_price_yuan": None,
                "status_text": fallback_status,
            }
        room_rates.append(
            {
                "room_label": room_label,
                "sell_price_yuan": chosen.get("sell_price_yuan"),
                "base_price_yuan": None,
                "status_text": chosen.get("status_text"),
            }
        )
    return room_rates[:_MAX_ROOM_RATES]


async def read_fliggy_calendar_room_rates(
    page: Any,
    *,
    checkin_date: str,
) -> dict[str, Any]:
    """飞猪「房价房量管理」日历页读取指定入住日的房型现价/房态（只读）。

    2026-09-24 实测：批量修改页产品树/编辑面板之外，价库日历页
    （roomsVsManage）是纯展示页——「显示 房价/房态」开关默认已开，房型
    默认折叠，点「全部展开」后产品子行每日期格渲染 房态/房量/卖价。
    读取只切显示（开关未开时点开、展开全部），不勾选、不填价、不提交。
    """

    try:
        await page.wait_for_selector('[class*="roomTypeSwitch"]', timeout=15_000)
    except Exception:
        return {
            "status": "not_found",
            "summary": "飞猪房价房量日历没有渲染出房型行。",
            "room_rates": [],
        }

    for mode in ("房价", "房态"):
        try:
            await page.evaluate(_FLIGGY_CAL_ENSURE_TOGGLE_JS, mode)
        except Exception:
            pass

    normalized = _normalize_date_text(checkin_date)
    window_applied = False
    if normalized:
        collected_probe = await _fliggy_cal_safe_collect(page)
        dates = _unique_texts(collected_probe.get("dates"))
        if normalized[5:] not in dates:
            window_applied = await _set_fliggy_calendar_date(page, normalized)

    await _expand_fliggy_calendar_rooms(page)

    collected = await _fliggy_cal_safe_collect(page)
    room_rates = fliggy_calendar_room_rates(collected, checkin_date=normalized or "")
    if not room_rates:
        return {
            "status": "not_found",
            "summary": "飞猪房价房量日历没有读取到房型价格行。",
            "room_rates": [],
        }
    summary = f"飞猪房价房量日历读取到 {len(room_rates)} 个房型的现价（无早口径；页面未展示底价）。"
    if normalized and not window_applied and normalized[5:] not in _unique_texts(
        collected.get("dates")
    ):
        summary += "（日期窗口平移失败，按页面当前窗口读取）"
    return {"status": "success", "summary": summary, "room_rates": room_rates}


async def _fliggy_cal_safe_collect(page: Any) -> dict[str, Any]:
    try:
        collected = await page.evaluate(_FLIGGY_CAL_COLLECT_JS)
    except Exception:
        return {}
    return collected if isinstance(collected, dict) else {}


async def _set_fliggy_calendar_date(page: Any, checkin_date: str) -> bool:
    """日历窗口默认从今日起 14 天：入住日在窗口外时平移「请选择日期」。"""

    try:
        inputs = page.locator('input[placeholder="请选择日期"]')
        if await inputs.count() == 0:
            return False
        await inputs.first.click(timeout=4_000)
        await inputs.first.fill(checkin_date, timeout=4_000)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3_000)
        return True
    except Exception:
        return False


async def _expand_fliggy_calendar_rooms(page: Any) -> None:
    """房型默认折叠（产品子行不可见）：点「全部展开」，轮询到出现产品行。"""

    try:
        control = page.get_by_text("全部展开", exact=True).first
        if await control.count() > 0:
            await control.click(timeout=5_000)
    except Exception:
        return
    for _ in range(10):
        try:
            found = await page.evaluate(
                "() => document.querySelectorAll('[class*=\"priceCell\"]').length"
            )
        except Exception:
            return
        if int(found or 0) > 0:
            return
        await page.wait_for_timeout(600)


def _unique_texts(values: Any) -> list[str]:
    result: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


async def wait_for_definitive_page_state(
    page: Any,
    classify_fn: Any,
    *,
    rounds: int = 5,
    interval_ms: int = 1500,
) -> str:
    """有界等待 SPA 渲染出可判定的页面态。

    固定 600ms 等待（_settle_page）对慢渲染页不够：classify 读到半渲染壳
    （词表命中不足）误判 page_structure_changed。这里轮询到出现明确态
    （价格页/需登录/需验证）或轮次耗尽，与既有「有界等待」修复模式一致。
    """

    definitive = ("pricing_page", "login_required", "verification_required")
    url = str(getattr(page, "url", "") or "")
    state = classify_fn(url, await _page_body_text(page))
    for _ in range(rounds):
        if state in definitive:
            return state
        try:
            await page.wait_for_timeout(interval_ms)
        except Exception:
            return state
        url = str(getattr(page, "url", "") or "")
        state = classify_fn(url, await _page_body_text(page))
    return state


async def _page_body_text(page: Any) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=6_000)).strip()
    except Exception:
        return ""


def login_redirected_after_read(url: str) -> bool:
    """读取过程中登录态失效（页面被重定向到 login）的轻量后置检查。"""

    return "login" in str(url or "").lower()


def platform_read_payload(
    *,
    platform: str,
    status: str,
    summary: str,
    room_rates: list[dict[str, Any]],
    platform_account_id: str,
    browser_session_id: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "platform_label": _platform_label(platform),
        "status": status,
        "summary": summary,
        "room_rates": room_rates,
        "room_rate_count": len(room_rates),
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "read_only": True,
        "captured_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def platform_read_error_payload(
    *,
    platform: str,
    status: str,
    summary: str,
    platform_account_id: str | None = None,
    browser_session_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return platform_read_payload(
        platform=platform,
        status=status,
        summary=summary,
        room_rates=[],
        platform_account_id=str(platform_account_id or ""),
        browser_session_id=str(browser_session_id or ""),
        reason=reason,
    )


def _first_number(match: re.Match[str] | None) -> float | int | None:
    if match is None:
        return None
    try:
        number = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _money_like_values(text: str) -> list[float | int]:
    filtered = re.sub(r"\d{1,2}[-月]\d{1,2}(?:日)?", " ", text)
    values: list[float | int] = []
    for match in _MONEY_RE.finditer(filtered):
        number = _first_number(match)
        if number is not None and number >= 30:
            values.append(number)
    return values


def _platform_label(platform: str) -> str:
    return {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}.get(
        str(platform or "").strip().lower(), str(platform or "未知")
    )


def room_rate_table_rows(room_rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """房型房价行 → 飞书指标表格行（field=房型，parts=[卖价, 底价, 房态]）。"""

    rows: list[dict[str, Any]] = []
    for rate in room_rates:
        if not isinstance(rate, dict):
            continue
        label = str(rate.get("room_label") or "-").strip() or "-"
        if not rate.get("is_parent", True):
            label = f"　└ {label}"
        parts = [
            _display_yuan(rate.get("sell_price_yuan")),
            _display_yuan(rate.get("base_price_yuan")),
            str(rate.get("status_text") or "未标注"),
        ]
        rows.append({"field": label, "parts": parts})
    return rows


def _display_yuan(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"¥{value:g}"
    return "页面未展示"


__all__ = [
    "ctrip_room_rates_from_candidates",
    "extract_room_label",
    "finalize_product_tree_read",
    "fliggy_calendar_room_rates",
    "login_redirected_after_read",
    "meituan_calendar_room_rates",
    "parse_room_rate_text",
    "platform_read_error_payload",
    "platform_read_payload",
    "product_tree_rates_from_rows",
    "read_ctrip_calendar_room_rates",
    "read_fliggy_calendar_room_rates",
    "read_meituan_calendar_room_rates",
    "read_product_tree_room_rates",
    "room_rate_table_rows",
    "wait_for_definitive_page_state",
]
