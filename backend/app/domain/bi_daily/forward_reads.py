"""bi_daily 前瞻现读：携程「房价房态日历」+「市场分析·市场热度」两页只读采集。

数据源（2026-10-10 远端 CDP 实测确认）：
- 日历页 ``/ebkovsroom/inventory/calendar``：未来 14 天逐日房型组「已售 N」（携程
  渠道在手预订，来自 ``getbasicroomstatushtml``）、售卖计划「限量 剩 N / 卖价 /
  底价」（页面渲染文本）、房型组「共 N 间」（总房量）。
- 市场热度页 ``/ebkgrowth/datacenter/marketanalysis/marketheat``：页面加载即请求
  SOA2 24588 系列 JSON——``getMarketHeats``（城市/商圈热度）、``getTravelHeats``
  （游客出行热度，含去年同期）、``getCompetitorOccupiedRoomTrend``（我的 vs 竞争圈
  在手间夜/出租率）、``getPriceChangeTrend``（竞争圈调价趋势）、
  ``queryHotCalendarInfo``（热点事件）。

硬规则：只读，不点击、不输入；解析失败如实返回 not_parsed，不猜数值；
竞品出租率/预订数据只取携程市场分析页面明示的竞争圈口径，不由价格推断。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

CTRP_CALENDAR_URL = "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar?microJump=true"
CTRP_MARKET_HEAT_URL = (
    "https://ebooking.ctrip.com/ebkgrowth/datacenter/marketanalysis/marketheat?microJump=true"
)

# 日历页/市场页 JSON API 的 URL 末段（捕获用；带 query 也按末段匹配）。
_API_BASIC_ROOM_STATUS = "/inventory/getbasicroomstatushtml"
_API_ROOM_INVENTORY = "/api/inventory/getRoomInventoryInfo"
_API_RC_PRODUCT_LIST = "/api/inventory/getRcProductList"
_API_MARKET_HEATS = "/getMarketHeats"
_API_TRAVEL_HEATS = "/getTravelHeats"
_API_COMPETITOR_OCCUPIED = "/getCompetitorOccupiedRoomTrend"
_API_PRICE_CHANGE_TREND = "/getPriceChangeTrend"
_API_HOT_CALENDAR = "/queryHotCalendarInfo"

FORWARD_SOURCE_CALENDAR = "bi_forward"
FORWARD_SOURCE_DEMAND = "bi_demand"

_SOLD_RE = re.compile(r"已售(\d+)")
_TOTAL_ROOMS_RE = re.compile(r"^共(\d+)间$")
_RATE_PLAN_ID_RE = re.compile(r"\((\d+)\)\s*$")
_LIMIT_REMAIN_RE = re.compile(r"^(限量|不限)\s*剩\s*(\d+)")
_WEEKDAY_RE = re.compile(r"^星期[一二三四五六日]$")
_MMDD_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")
_PRICE_RE = re.compile(r"^\d+(\.\d+)?$")
_INT_RE = re.compile(r"^\d+$")


def _clean_lines(text: str) -> list[str]:
    # innerText 每行间有空行（EBK 渲染惯例）；结构解析只关心非空行。
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def parse_calendar_dates(lines: list[str], *, today: date) -> list[date]:
    """表头「星期X / MM-DD」交替序列 → 日期列表（年份按 today 跨年推断）。"""
    dates: list[date] = []
    index = 0
    while index < len(lines) - 1:
        if _WEEKDAY_RE.match(lines[index]) and _MMDD_RE.match(lines[index + 1]):
            month, day = (int(part) for part in lines[index + 1].split("-"))
            # 页面从今天起展示；12 月底跨年时 1~2 月属于明年。
            year = today.year
            candidate = date(year, month, day)
            if (candidate - today).days < -180:
                candidate = date(year + 1, month, day)
            elif (candidate - today).days > 180:
                candidate = date(year - 1, month, day)
            dates.append(candidate)
            index += 2
            continue
        index += 1
    return dates


def parse_calendar_rate_plans(lines: list[str], dates: list[date]) -> list[dict[str, Any]]:
    """售卖计划块解析：计划名(含ID) → 状态/剩量/卖价/底价/餐食 × 日期。

    结构：每个计划名行之后跟「直连…/ID」两行 + 固定列头 5 行（房态/房量/
    卖价(元)/底价(元)/餐食）+ 每日期一格的单元格序列。实现按「先定位列头、
    回溯找计划名」配对；单元格数与日期数不齐时该计划整块弃收，不错位输出。
    """
    plans: list[dict[str, Any]] = []
    header_starts = [
        index
        for index in range(len(lines) - 4)
        if lines[index : index + 5] == ["房态", "房量", "卖价(元)", "底价(元)", "餐食"]
    ]
    for header_start in header_starts:
        # 回溯找计划名与 ID：名字行可能带 (ID) 尾巴，也可能 ID 单独成行
        # （立瑞等酒店版本）；中间夹「直连-量态 预付 / 预付 礼盒」等元信息行。
        name_index = header_start - 1
        plan_id: int | None = None
        while name_index >= 0:
            line = lines[name_index]
            id_match = _RATE_PLAN_ID_RE.search(line)
            if id_match:
                plan_id = int(id_match.group(1))
                break
            if _INT_RE.match(line):
                plan_id = int(line)
                name_index -= 1
                if name_index >= 0 and not _looks_like_rate_plan_meta(lines[name_index]):
                    break
                continue
            if _looks_like_rate_plan_meta(line):
                name_index -= 1
                continue
            break
        if name_index < 0 or plan_id is None:
            continue
        cells: list[dict[str, Any]] = []
        cursor = header_start + 5
        while cursor < len(lines) and len(cells) < len(dates):
            parsed = _parse_one_cell(lines, cursor)
            if parsed is None:
                break
            cells.append(parsed["cell"])
            cursor = parsed["next"]
        if len(cells) != len(dates):
            continue
        plans.append(
            {
                "rate_plan_id": plan_id,
                "rate_plan_name": lines[name_index],
                "cells": [
                    {"stay_date": stay.isoformat(), **cell}
                    for stay, cell in zip(dates, cells, strict=False)
                ],
            }
        )
    return plans


def _looks_like_rate_plan_meta(line: str) -> bool:
    """计划名与列头之间的元信息行（支付方式/直连标识/礼盒等）。"""
    return any(token in line for token in ("预付", "现付", "礼盒", "直连")) or line in (
        "预付",
        "现付",
        "",
    )


def _find_price_header(lines: list[str]) -> int | None:
    """列头块（房态/房量/卖价(元)/底价(元)/餐食）结束位置；无则 None。"""
    for index in range(len(lines) - 4):
        if lines[index : index + 5] == ["房态", "房量", "卖价(元)", "底价(元)", "餐食"]:
            return index + 5
    return None


def _parse_one_cell(lines: list[str], start: int) -> dict[str, Any] | None:
    """一个日期单元格：已开房/已关房 → 限量 剩N / 不限 剩N / FS → 卖价 → 底价 → 餐食。

    已关房或未铺价时价格行可能缺失（空串）：价格记 None，不整体失败。
    FS=FreeSale（不限量），remaining 记 None 不猜数。
    """
    if start >= len(lines) or lines[start] not in ("已开房", "已关房"):
        return None
    room_status = lines[start]
    cursor = start + 1
    remaining: int | None = None
    limit_kind: str | None = None
    if cursor < len(lines):
        if lines[cursor] == "FS":
            limit_kind = "FS"
            cursor += 1
        else:
            limit_match = _LIMIT_REMAIN_RE.match(lines[cursor])
            if limit_match:
                limit_match_kind = limit_match.group(1)
                # 「不限 剩 N」在部分版本里只有「不限」+省略数：remaining 如实保留。
                limit_kind = "不限" if limit_match_kind == "不限" else "限量"
                remaining = int(limit_match.group(2))
                cursor += 1
    sale_price: float | None = None
    floor_price: float | None = None
    for expected_index, key in ((0, "sale"), (1, "floor")):
        if cursor < len(lines) and _PRICE_RE.match(lines[cursor]):
            value = float(lines[cursor])
            if key == "sale":
                sale_price = value
            else:
                floor_price = value
            cursor += 1
        elif key == "sale":
            # 卖价缺失（未铺价）时整个单元格仍有效；底价也不强求。
            break
    breakfast: int | None = None
    if cursor < len(lines) and _INT_RE.match(lines[cursor]):
        breakfast = int(lines[cursor])
        cursor += 1
    return {
        "cell": {
            "room_status": room_status,
            "limit_kind": limit_kind,
            "remaining": remaining,
            "sale_price": sale_price,
            "floor_price": floor_price,
            "breakfast": breakfast,
        },
        "next": cursor,
    }


def parse_calendar_room_groups(lines: list[str]) -> list[dict[str, Any]]:
    """房型组块：组名（「共N间」前一行，跳过「热卖」等角标）。"""
    groups: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = _TOTAL_ROOMS_RE.match(line)
        if not match:
            continue
        name_index = index - 1
        while name_index >= 0 and lines[name_index] in ("热卖", "NEW", ""):
            name_index -= 1
        if name_index < 0:
            continue
        groups.append(
            {
                "room_group_name": lines[name_index],
                "total_rooms": int(match.group(1)),
            }
        )
    return groups


def parse_basic_room_status_html(payload: Any) -> dict[str, dict[str, int]]:
    """getbasicroomstatushtml：{basicRoomTypeID: {date: 已售N}}（无已售即缺键）。"""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    result: dict[str, dict[str, int]] = {}
    for room_id, by_date in data.items():
        if not isinstance(by_date, dict):
            continue
        sold_by_date: dict[str, int] = {}
        for stay_date, html in by_date.items():
            match = _SOLD_RE.search(str(html or ""))
            if match:
                sold_by_date[str(stay_date)] = int(match.group(1))
        result[str(room_id)] = sold_by_date
    return result


def parse_room_inventory_info(payload: Any) -> list[dict[str, Any]]:
    """getRoomInventoryInfo：售卖计划逐日 totalQuantity/canUsedQuantity。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows = data.get("roomStatusResult")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            result.append(
                {
                    "rate_plan_id": int(row["roomTypeID"]),
                    "stay_date": str(row["effectDate"]),
                    "total_quantity": row.get("totalQuantity"),
                    "can_used_quantity": row.get("canUsedQuantity"),
                    "room_status": row.get("roomStatus"),
                    "limit_sale": row.get("limitSale"),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def parse_rc_product_list(payload: Any) -> dict[str, dict[str, Any]]:
    """getRcProductList：售卖计划ID → 所属房型组（basicRoomTypeID/组名/计划名）。"""
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return {}
    registry: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            group_id = int(row["basicRoomTypeID"])
        except (KeyError, TypeError, ValueError):
            continue
        group_name = str(row.get("roomNameDesc") or row.get("roomEnglishName") or "")
        for info in row.get("roomInfos") or []:
            if not isinstance(info, dict):
                continue
            plan_id = info.get("roomTypeID")
            if plan_id is None:
                continue
            registry[str(plan_id)] = {
                "basic_room_type_id": group_id,
                "room_group_name": group_name or str(row.get("roomName") or ""),
                "rate_plan_name": str(info.get("roomNameDesc") or info.get("roomName") or ""),
                "breakfast_hint": _breakfast_hint(str(info.get("roomNameDesc") or "")),
            }
    return registry


def _breakfast_hint(name: str) -> str:
    if "双早" in name or "含双早" in name:
        return "double"
    if "单早" in name or "含早" in name or "早餐" in name:
        return "single"
    if "无早" in name or "<无>" in name:
        return "none"
    return "unknown"


def _effect_date(value: Any) -> str | None:
    """日期字段兼容两种格式：ISO 文本（含 dateDes）与 .NET /Date(epoch+0800)/。"""
    if not isinstance(value, str) or not value:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    match = re.search(r"/Date\((\d+)", value)
    if match:
        try:
            moment = datetime.fromtimestamp(
                int(match.group(1)) / 1000, tz=timezone(timedelta(hours=8))
            )
        except (ValueError, OverflowError, OSError):
            return None
        return moment.date().isoformat()
    return None


def _heat_rows(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        stay = _effect_date(row.get("effectDate") or row.get("dateDes") or row.get("date"))
        if stay is None:
            continue
        result.append(
            {
                "stay_date": stay,
                "heat": row.get("heat"),
                "heat_label": row.get("heatLabel"),
            }
        )
    return result


def parse_market_heats(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """getMarketHeats：cityMarketHeats / zoneMarketHeats（及各自 LastYear 组）。"""
    if not isinstance(payload, dict):
        return {}
    body = payload.get("marketHeatsBo")
    if not isinstance(body, dict):
        return {}
    return {
        "city": _heat_rows(body.get("cityMarketHeats")),
        "city_last_year": _heat_rows(body.get("cityMarketHeatsLastYear")),
        "zone": _heat_rows(body.get("zoneMarketHeats")),
        "zone_last_year": _heat_rows(body.get("zoneMarketHeatsLastYear")),
    }


def parse_travel_heats(payload: Any) -> list[dict[str, Any]]:
    """getTravelHeats：[{stay_date, heat, heat_last_year}]。"""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("inlandMarketHeatBo")
    result: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            stay = _effect_date(row.get("effectDate"))
            if stay is None:
                continue
            result.append(
                {
                    "stay_date": stay,
                    "heat": row.get("heat"),
                    "heat_last_year": row.get("heatLastYear"),
                }
            )
    return result


def _number_list(values: Any) -> list[float | None]:
    if not isinstance(values, list):
        return []
    result: list[float | None] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            result.append(None)
    return result


def parse_competitor_occupied_trend(payload: Any) -> dict[str, Any]:
    """getCompetitorOccupiedRoomTrend：我的 vs 竞争圈在手间夜/出租率（页面口径）。"""
    if not isinstance(payload, dict):
        return {}
    date_list = [value for value in payload.get("dateList") or [] if isinstance(value, str)]
    my_rooms = _number_list(payload.get("nocturnalAmountList"))
    my_occ = _number_list(payload.get("rentalRateList"))
    comp_rooms = _number_list(payload.get("competitorNocturnalAmountList"))
    comp_occ = _number_list(payload.get("competitorRentalRateList"))
    rows: list[dict[str, Any]] = []
    for index, stay in enumerate(date_list):
        rows.append(
            {
                "stay_date": stay,
                "my_booked_rooms": my_rooms[index] if index < len(my_rooms) else None,
                "my_occupancy_rate": (
                    my_occ[index] / 100 if index < len(my_occ) and my_occ[index] is not None else None
                ),
                "competitor_booked_rooms": (
                    comp_rooms[index] if index < len(comp_rooms) else None
                ),
                "competitor_occupancy_rate": (
                    comp_occ[index] / 100
                    if index < len(comp_occ) and comp_occ[index] is not None
                    else None
                ),
            }
        )
    return {"rows": rows}


def parse_price_change_trend(payload: Any) -> list[dict[str, Any]]:
    """getPriceChangeTrend：竞争圈调价趋势（今年 + 去年同期）。"""
    if not isinstance(payload, dict):
        return []
    body = payload.get("competitorPriceChangeTrendBo")
    if not isinstance(body, dict):
        return []
    last_year = {
        _effect_date(row.get("effectDate")): row.get("trend")
        for row in body.get("priceChangeTrendLastYear") or []
        if isinstance(row, dict)
    }
    result: list[dict[str, Any]] = []
    for row in body.get("priceChangeTrend") or []:
        if not isinstance(row, dict):
            continue
        stay = _effect_date(row.get("effectDate"))
        if stay is None:
            continue
        result.append(
            {
                "stay_date": stay,
                "trend": row.get("trend"),
                "trend_last_year": last_year.get(stay),
            }
        )
    return result


def parse_hot_calendar_events(payload: Any) -> list[dict[str, Any]]:
    """queryHotCalendarInfo：热点事件（名称/起止）。"""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("otherDataList")
    result: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            result.append(
                {
                    "name": str(row.get("hotSpotName") or ""),
                    "start_date": _effect_date(row.get("startDate")),
                    "end_date": _effect_date(row.get("endDate")),
                }
            )
    return [item for item in result if item["name"] and item["start_date"]]


class _ResponseHarvester:
    """按 URL 末段捕获 JSON 响应体（页面自行发起请求，监听收集）。"""

    def __init__(self, suffixes: tuple[str, ...]) -> None:
        self._suffixes = suffixes
        self.captured: dict[str, Any] = {}

    async def on_response(self, response: Any) -> None:
        try:
            url = str(response.url or "")
            for suffix in self._suffixes:
                if url.split("?")[0].endswith(suffix):
                    self.captured[suffix] = await response.json()
                    return
        except Exception:  # noqa: BLE001 - 捕获失败按缺数处理
            pass


async def read_ctrip_calendar_payload(page: Any, *, today: date) -> dict[str, Any]:
    """导航日历页一次：收割已售/房量 JSON + 渲染文本解析价格。只读。"""
    harvester = _ResponseHarvester(
        (_API_BASIC_ROOM_STATUS, _API_ROOM_INVENTORY, _API_RC_PRODUCT_LIST)
    )
    page.on("response", harvester.on_response)
    try:
        await page.goto(CTRP_CALENDAR_URL, wait_until="domcontentloaded", timeout=45000)
        # 有界等待：卖价列头出现即认为渲染完成（EBK 慢渲染固定等待必误判）。
        try:
            await page.wait_for_function(
                "() => document.body && document.body.innerText.includes('卖价(元)')",
                timeout=20000,
            )
        except Exception:  # noqa: BLE001 - 超时继续用现有内容尽力解析
            logger.warning("ctrip calendar wait_for 卖价(元) timeout; parse anyway")
        await page.wait_for_timeout(2500)
        body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    finally:
        page.remove_listener("response", harvester.on_response)

    lines = _clean_lines(str(body_text or ""))
    dates = parse_calendar_dates(lines, today=today)
    plans = parse_calendar_rate_plans(lines, dates)
    groups = parse_calendar_room_groups(lines)
    if not groups and dates:
        # 「共N间」行偶发晚渲染：补等一次再重解析，仍缺由 ingest 回退旧快照。
        await page.wait_for_timeout(3000)
        retry_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        retry_lines = _clean_lines(str(retry_text or ""))
        groups = parse_calendar_room_groups(retry_lines)
        if groups:
            dates = parse_calendar_dates(retry_lines, today=today) or dates
            plans = parse_calendar_rate_plans(retry_lines, dates) or plans
    if not dates or not plans:
        return {
            "status": "not_parsed",
            "reason": f"日历页解析不完整（日期 {len(dates)}、计划 {len(plans)}）。",
            "dates": [stay.isoformat() for stay in dates],
        }
    sold = parse_basic_room_status_html(harvester.captured.get(_API_BASIC_ROOM_STATUS))
    inventory = parse_room_inventory_info(harvester.captured.get(_API_ROOM_INVENTORY))
    registry = parse_rc_product_list(harvester.captured.get(_API_RC_PRODUCT_LIST))
    return {
        "status": "ok",
        "dates": [stay.isoformat() for stay in dates],
        "room_groups": groups,
        "rate_plans": plans,
        "sold_by_room": sold,
        "inventory_rows": inventory,
        "rate_plan_registry": registry,
    }


async def read_ctrip_market_demand_payload(page: Any) -> dict[str, Any]:
    """导航市场热度页一次：收割 5 个 SOA JSON。只读。"""
    harvester = _ResponseHarvester(
        (
            _API_MARKET_HEATS,
            _API_TRAVEL_HEATS,
            _API_COMPETITOR_OCCUPIED,
            _API_PRICE_CHANGE_TREND,
            _API_HOT_CALENDAR,
        )
    )
    page.on("response", harvester.on_response)
    try:
        await page.goto(CTRP_MARKET_HEAT_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_function(
                "() => document.body && document.body.innerText.includes('市场热度')",
                timeout=20000,
            )
        except Exception:  # noqa: BLE001
            logger.warning("ctrip market heat wait timeout; harvest anyway")
        await page.wait_for_timeout(2500)
    finally:
        page.remove_listener("response", harvester.on_response)

    captured = harvester.captured
    competitor = parse_competitor_occupied_trend(captured.get(_API_COMPETITOR_OCCUPIED))
    if not competitor.get("rows"):
        return {
            "status": "not_parsed",
            "reason": "市场热度页未捕获竞争圈在手数据（接口未返回或页面改版）。",
        }
    return {
        "status": "ok",
        "market_heats": parse_market_heats(captured.get(_API_MARKET_HEATS)),
        "travel_heats": parse_travel_heats(captured.get(_API_TRAVEL_HEATS)),
        "competitor_occupied": competitor,
        "price_change_trend": parse_price_change_trend(
            captured.get(_API_PRICE_CHANGE_TREND)
        ),
        "events": parse_hot_calendar_events(captured.get(_API_HOT_CALENDAR)),
    }


async def run_ctrip_forward_reads_for_binding(
    *, platform_account_id: str, browser_session_id: str | None, now: datetime
) -> dict[str, Any]:
    """一个携程账号绑定：拉起页面 → 日历页 + 市场页两读 → 落快照（不 ingest）。

    返回 {status, calendar?, demand?, snapshots: int}；页面拿不到时
    human_required/not_ready 如实透传，不猜数据。
    """
    from app.domain.ebk_period_reads import repository as period_repository
    from app.domain.order_browser import acquire_order_page_for_binding

    binding = {
        "platform": "ctrip",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
    }
    page = await acquire_order_page_for_binding("ctrip", binding)
    if page is None:
        return {"status": "not_ready", "reason": "无法获取携程 EBK 页面（浏览器或登录态）。"}

    today = now.date()
    calendar_payload = await read_ctrip_calendar_payload(page, today=today)
    demand_payload = await read_ctrip_market_demand_payload(page)

    written = 0
    if calendar_payload.get("status") == "ok":
        await period_repository.append_period_read_snapshot(
            platform="ctrip",
            data_kind=FORWARD_SOURCE_CALENDAR,
            period="day",
            status="ok",
            caliber="房价房态日历·未来14天在手/房量/预埋价",
            summary="携程房价房态日历前瞻读取（只读）。",
            payload=calendar_payload,
            source=FORWARD_SOURCE_CALENDAR,
            platform_account_id=platform_account_id,
        )
        written += 1
    if demand_payload.get("status") == "ok":
        await period_repository.append_period_read_snapshot(
            platform="ctrip",
            data_kind=FORWARD_SOURCE_DEMAND,
            period="day",
            status="ok",
            caliber="市场分析·市场热度/竞争圈在手/调价趋势",
            summary="携程市场分析热度读取（只读）。",
            payload=demand_payload,
            source=FORWARD_SOURCE_DEMAND,
            platform_account_id=platform_account_id,
        )
        written += 1
    return {
        "status": "ok" if written else "not_parsed",
        "calendar_status": calendar_payload.get("status"),
        "demand_status": demand_payload.get("status"),
        "snapshots": written,
    }


YESTERDAY_ACTUALS_SOURCE = "bi_daily_yesterday"
PRIOR_YEAR_BOUNDARY_SOURCE = "bi_prior_year_boundary"


async def run_ctrip_prior_year_boundary_read(
    *, shop_key: str, platform_account_id: str, browser_session_id: str | None, now: datetime
) -> dict[str, Any]:
    """读「昨天 - 364 天」单日：该日期恰在携程 12 个月自定义区间边界上。

    携程自定义区间仅允许近 ~12 个月，D-364 映射日只有在边界当天可读；
    每日采集轮读一次，让去年同期基线（同比完成系数）逐日累积。
    已有该日事实（如批量回填已覆盖）即跳过，不重复拉浏览器。
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.session import ensure_db_ready, session_scope
    from app.domain.bi_daily.models import BookingPositionFact

    boundary = now.date() - timedelta(days=365)
    await ensure_db_ready()
    async with session_scope() as session:
        exists = (
            await session.execute(
                select(BookingPositionFact.id)
                .where(
                    BookingPositionFact.shop_key == shop_key,
                    BookingPositionFact.platform == "ctrip",
                    BookingPositionFact.stay_date == boundary,
                )
                .limit(1)
            )
        ).first()
    if exists:
        return {"status": "skipped_existing", "business_date": boundary.isoformat()}

    binding = {
        "platform": "ctrip",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
    }
    result = await fetch_period_read_quietly(binding, boundary)
    if result.get("status") != "ok":
        return {"status": result.get("status"), "business_date": boundary.isoformat()}
    return {
        "status": "ok",
        "business_date": boundary.isoformat(),
        "persisted": await _persist_single_day_snapshot(
            binding, boundary, result, PRIOR_YEAR_BOUNDARY_SOURCE
        ),
    }


async def fetch_period_read_quietly(binding: dict, target_date) -> dict[str, Any]:
    from app.domain.ebk_period_reads.service import fetch_period_read

    return await fetch_period_read(
        platform="ctrip",
        data_kind="business",
        period="day",
        binding=dict(binding),
        date_from=target_date.isoformat(),
        date_to=target_date.isoformat(),
        source=PRIOR_YEAR_BOUNDARY_SOURCE,
    )


async def _persist_single_day_snapshot(
    binding: dict, target_date, result: dict[str, Any], source: str
) -> bool:
    """把自定义单日现读结果按回填同款归一后落快照（含在店出租率）。"""
    from app.domain.bi_daily.backfill import normalize_custom_range_sections
    from app.domain.bi_daily.ingest import extract_day_business_metrics
    from app.domain.ebk_period_reads import repository as period_repository

    normalized = normalize_custom_range_sections(result.get("sections"), "ctrip")
    metrics = extract_day_business_metrics({"sections": normalized}, "ctrip")
    if (
        metrics.get("booked_room_nights") is None
        and metrics.get("room_revenue") is None
        and metrics.get("occupancy_rate") is None
    ):
        return False
    await period_repository.append_period_read_snapshot(
        platform="ctrip",
        data_kind="business",
        period="day",
        status="ok",
        caliber=f"预订销售·自定义单日 {target_date.isoformat()}（去年同期基线）",
        summary=str(result.get("summary") or ""),
        payload={
            "sections": normalized,
            "data_period_label": target_date.isoformat(),
            "backfill_date_range": [target_date.isoformat(), target_date.isoformat()],
        },
        source=source,
        business_date=target_date,
        platform_account_id=binding["platform_account_id"],
    )
    return True


async def run_ctrip_yesterday_actuals_read(
    *, platform_account_id: str, browser_session_id: str | None, now: datetime
) -> dict[str, Any]:
    """携程昨日单日现读（自定义区间 D~D）：落带「在店·出租率」的按日快照。

    与 09:30 经营概览昨日块互补：该块只有离店三指标；本读多得 在店出租率/
    在店间夜/预订组。幂等：同账号同 business_date 已有本源快照即跳过。
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.session import ensure_db_ready, session_scope
    from app.domain.ebk_period_reads.models import EbkPeriodReadSnapshot
    from app.domain.ebk_period_reads.service import fetch_period_read

    yesterday = now.date() - timedelta(days=1)
    await ensure_db_ready()
    async with session_scope() as session:
        exists = (
            await session.execute(
                select(EbkPeriodReadSnapshot.id)
                .where(
                    EbkPeriodReadSnapshot.platform == "ctrip",
                    EbkPeriodReadSnapshot.platform_account_id == platform_account_id,
                    EbkPeriodReadSnapshot.source == YESTERDAY_ACTUALS_SOURCE,
                    EbkPeriodReadSnapshot.business_date == yesterday,
                )
                .limit(1)
            )
        ).first()
    if exists:
        return {"status": "skipped_existing", "business_date": yesterday.isoformat()}

    binding = {
        "platform": "ctrip",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
    }
    result = await fetch_period_read(
        platform="ctrip",
        data_kind="business",
        period="day",
        binding=binding,
        date_from=yesterday.isoformat(),
        date_to=yesterday.isoformat(),
        source=YESTERDAY_ACTUALS_SOURCE,
    )
    if result.get("status") != "ok":
        return {"status": result.get("status"), "reason": result.get("reason")}
    persisted = await _persist_single_day_snapshot(binding, yesterday, result, YESTERDAY_ACTUALS_SOURCE)
    if not persisted:
        return {"status": "no_metrics", "reason": "页面返回成功但未解析到任何经营指标。"}
    return {"status": "ok", "business_date": yesterday.isoformat()}
