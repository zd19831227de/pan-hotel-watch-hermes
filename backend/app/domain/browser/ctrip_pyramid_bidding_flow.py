"""携程 EBK 金字塔（广告金字塔 CPC）出价读取与调整浏览器流。

页面事实（2026-09-08 CDP 只读探针复验）：
- 推广列表页 `/advertise/cpc/extensionList?microJump=true`（业务核对入口，
  完整表格）：`tr[data-row-key=<campaignId>]` 行，列 = 推广名称 | 状态 |
  推广日期 | 普通日/高峰日预算 | 点击出价 | 今日花费 | 今日曝光 | 今日点击 |
  操作（每行「修改」「暂停」「更多」，修改按钮带
  `he-click="cpc_extension_list_action_modify_button"`）。
  旧入口 `/toolcenter/cpc/pyramid` 为卡片视图且只展示前几个计划，已弃用。
- 点「修改」跳转整页编辑 `/toolcenter/cpc/createCampaign?campaignId=...`，
  表单含 推广名称 / 单次点击出价 CNY（含 `起价X.X，随季节有变动` 下限提示）/
  每日花费上限 / 智能调价（0.7~1.3 倍浮动）等，保存按钮文案「提交修改」。

安全设计（与房价写链同构）：
- URL 白名单只接受金字塔列表/编辑页；
- 登录/验证页一律 `human_required`，结构不符 `page_structure_changed`；
- dry-run 只导航核验、绝不输入；执行时填价后回读输入框，提交后重新读取
  列表页出价做最终校验。
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from playwright.async_api import Page

from app.domain.browser.ctrip_ebooking_session_helpers import (
    _classify_page_state,
    _dismiss_ebk_read_blocking_popups,
    _safe_visible_text,
    _settle_page,
    _sync_ebk_browser_session_state,
)
from app.domain.pyramid_bidding.commands import pyramid_campaign_key

if TYPE_CHECKING:
    from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService

logger = logging.getLogger(__name__)

PYRAMID_LIST_URL = "https://ebooking.ctrip.com/advertise/cpc/extensionList?microJump=true"
PYRAMID_EDIT_URL_PREFIX = "https://ebooking.ctrip.com/toolcenter/cpc/createCampaign"

_BID_EPSILON = 0.005
_EDIT_LEAVE_TIMEOUT_MS = 20_000

_COLLECT_PROMOTION_ROWS_JS = """() => {
  const seen = new Set();
  const payload = [];
  const rows = Array.from(document.querySelectorAll('tr')).filter(tr => {
    const btn = tr.querySelector('button[he-click="cpc_extension_list_action_modify_button"]');
    if (!btn) return false;
    const key = tr.getAttribute('data-row-key') || '';
    if (key && seen.has(key)) return false;
    if (key) seen.add(key);
    return true;
  });
  for (const tr of rows) {
    const cells = Array.from(tr.querySelectorAll('td'))
      .map(td => (td.innerText || '').replace(/\\s+/g, ' ').trim());
    payload.push({
      row_key: tr.getAttribute('data-row-key') || '',
      cells: cells.slice(0, 12),
      has_modify_button: true,
    });
  }
  return payload.slice(0, 30);
}"""

_COLLECT_EDIT_INPUTS_JS = """() => {
  const inputs = [];
  const all = Array.from(document.querySelectorAll('input, textarea'));
  for (let i = 0; i < all.length; i += 1) {
    const inp = all[i];
    const rect = inp.getBoundingClientRect();
    const type = (inp.getAttribute('type') || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio' || type === 'hidden') continue;
    if (rect.width <= 0 || rect.height <= 0) continue;
    let near = '';
    let el = inp.parentElement;
    for (let k = 0; k < 4 && el; k += 1) {
      near = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
      if (near) break;
      el = el.parentElement;
    }
    inputs.push({input_index: i, value: String(inp.value || '').slice(0, 40), near: near});
  }
  return inputs.slice(0, 40);
}"""

# 编辑页就绪判定（2026-09-08 执行#159：重 SPA 600ms 未渲染出「修改推广」标记
# 即被判 page_structure_changed）。两个条件：出现任一编辑页标记；推广名称
# 输入框已有值（计划数据拉回完成），之后才读 body/inputs 做校验。
_EDIT_PAGE_MARKER_READY_JS = (
    "() => /修改推广|提交修改|单次点击出价/.test(document.body.innerText || '')"
)
_EDIT_NAME_INPUT_READY_JS = """() => {
  const all = Array.from(document.querySelectorAll('input'));
  for (const inp of all) {
    const type = (inp.getAttribute('type') || '').toLowerCase();
    if (['checkbox', 'radio', 'hidden'].includes(type)) continue;
    if (!String(inp.value || '').trim()) continue;
    let el = inp.parentElement;
    let near = '';
    for (let k = 0; k < 4 && el; k++) {
      near = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (near) break;
      el = el.parentElement;
    }
    if (near.includes('推广名称')) return true;
  }
  return false;
}"""


# ---------------------------------------------------------------------------
# 只读：金字塔推广列表
# ---------------------------------------------------------------------------


_campaign_key = pyramid_campaign_key


def _campaign_names_equivalent(target: str, page_value: str | None) -> bool:
    """编辑页推广名称与目标计划名等价判断（横线/空格变体视为同名）。

    沿用原校验方向：目标名（归一化后）须包含于页面名——防点错计划的
    安全闸门只收紧不放宽，不做反向包含。
    """
    if not str(target or "") or not str(page_value or ""):
        return False
    return _campaign_key(target) in _campaign_key(page_value)


async def _collect_promotion_rows_stable(
    page: Page,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = 700,
) -> list[Any]:
    """轮询采集推广表格行，直到行数连续两次不再增长。

    2026-09-08 线上问题：表格异步渲染，settle 600ms 时可能只渲染出部分行
    （实测 8/10 甚至 0 行），必须等行数稳定后再匹配/点击。
    """
    from time import monotonic

    try:
        # 触发首屏以下的懒渲染行
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    except Exception:  # noqa: BLE001
        pass
    best: list[Any] = []
    stable_polls = 0
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        rows = await page.evaluate(_COLLECT_PROMOTION_ROWS_JS)
        if isinstance(rows, list) and len(rows) > len(best):
            best = rows
            stable_polls = 0
        else:
            stable_polls += 1
            if stable_polls >= 2 and best:
                break
        await page.wait_for_timeout(poll_ms)
    return best


async def _click_modify_and_capture_edit_page(page: Page, modify_button: Any) -> Page:
    """点「修改」并返回编辑页 Page。

    2026-09-08 线上问题：extensionList 表格的「修改」可能**开新标签页**进入
    编辑页（旧卡片视图是同页跳转）。两种形态都兼容：点击后轮询当前页 URL
    与新建页，谁出现 createCampaign 用谁；超时抛 TimeoutError。
    """
    existing = {id(p) for p in page.context.pages}
    await modify_button.first.click()
    for _ in range(30):  # 最多 15s
        await page.wait_for_timeout(500)
        try:
            if "createCampaign" in page.url:
                return page
        except Exception:  # noqa: BLE001 - 页签被站点关闭等
            break
        for candidate in page.context.pages:
            if id(candidate) in existing:
                continue
            try:
                if "createCampaign" in candidate.url:
                    return candidate
            except Exception:  # noqa: BLE001
                continue
    raise TimeoutError("click modify did not open createCampaign page")


async def read_pyramid_promotions_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
) -> dict[str, Any]:
    opened = await _open_pyramid_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
    )
    if opened["status"] != "success":
        return {
            "status": opened["status"],
            "summary": opened["summary"],
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "promotions": [],
            "data": {"captured_at": opened.get("captured_at"), "promotions": []},
            "evidence": opened.get("evidence"),
            "next_action": opened.get("next_action"),
        }
    page: Page = opened["page"]
    try:
        rows = await _collect_promotion_rows_stable(page)
        promotions = parse_pyramid_promotion_rows(rows)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "summary": "读取金字塔推广列表失败。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "promotions": [],
            "evidence": {"error_type": type(exc).__name__},
            "next_action": "请人工登录 EBK 查看金字塔页面。",
        }
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status="success",
        page_state=opened["page_state"],
    )
    return {
        "status": "success",
        "summary": f"读取到 {len(promotions)} 个金字塔推广计划。",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "promotions": promotions,
        "data": {
            "query_type": "ctrip_pyramid_promotions_read",
            "captured_at": opened.get("captured_at"),
            # promotions 冗余落 data：按需单数据集读取（/read-dataset）的调用方
            # （Hermes read_ebk_datasets）只透传 data 字段。
            "promotions": promotions,
        },
        "evidence": {
            "page_state": opened["page_state"],
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "read_only": True,
        },
    }


# ---------------------------------------------------------------------------
# 空跑：打开编辑页核验计划与出价输入框（不输入任何内容）
# ---------------------------------------------------------------------------


async def prepare_pyramid_bid_page_write_dry_run_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    action = _first_action(execute_request)
    campaign_name = str(action.get("campaign_name") or "")
    target_bid = _safe_float(action.get("target_bid_cny"))
    current_bid = _safe_float(action.get("current_bid_cny"))
    verify = await _open_campaign_edit_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        campaign_name=campaign_name,
        current_bid=current_bid,
    )
    if verify["status"] != "edit_page_verified":
        return _stage_result(
            verify,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            extra_evidence={"dry_run": True, "input_fill_invoked": False, "submit_invoked": False},
        )
    page: Page = verify["page"]
    edit_read = verify["edit_read"]

    floor = _safe_float(edit_read.get("floor_price_cny"))
    if floor is not None and target_bid is not None and target_bid < floor - _BID_EPSILON:
        await _return_to_list_page(page, list_page=verify.get("list_page"))
        return {
            "status": "floor_price_violation",
            "summary": f"目标出价 ¥{_display(target_bid)} 低于页面起价 ¥{_display(floor)}，已停止调整。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {"captured_at": verify.get("captured_at"), "campaign_read": edit_read},
            "evidence": {
                "dry_run": True,
                "input_fill_invoked": False,
                "submit_invoked": False,
                "floor_price_cny": floor,
                "target_bid_cny": target_bid,
            },
            "next_action": "请把目标出价提高到页面起价之上后重新发起。",
        }

    await _return_to_list_page(page, list_page=verify.get("list_page"))
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status="page_write_preview_ready",
        page_state="authenticated",
    )
    return {
        "status": "page_write_preview_ready",
        "summary": (
            f"金字塔页面核验通过：「{campaign_name}」出价输入框已定位，未做任何输入。"
        ),
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "ctrip_pyramid_bid_page_write_dry_run",
            "captured_at": verify.get("captured_at"),
            "campaign_read": edit_read,
        },
        "evidence": {
            "dry_run": True,
            "read_only": True,
            "input_fill_invoked": False,
            "submit_invoked": False,
            "browser_write_invoked": False,
            "campaign_name_verified": True,
            "bid_input_located": bool(edit_read.get("bid_input_index") is not None),
            "floor_price_cny": floor,
            "smart_bidding_detected": bool(edit_read.get("smart_bidding_detected")),
        },
        "next_action": "等待真实出价写入。",
    }


# ---------------------------------------------------------------------------
# 执行：填价 -> 提交 -> 回读校验
# ---------------------------------------------------------------------------


async def execute_pyramid_bid_write_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    action = _first_action(execute_request)
    campaign_name = str(action.get("campaign_name") or "")
    target_bid = _safe_float(action.get("target_bid_cny"))
    current_bid = _safe_float(action.get("current_bid_cny"))
    if target_bid is None:
        return {
            "status": "data_required",
            "summary": "执行请求缺少目标出价，已停止。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "evidence": {"browser_write_invoked": False},
        }
    verify = await _open_campaign_edit_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        campaign_name=campaign_name,
        current_bid=current_bid,
    )
    if verify["status"] != "edit_page_verified":
        return _stage_result(
            verify,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            extra_evidence={"browser_write_invoked": True},
        )
    page: Page = verify["page"]
    edit_read = verify["edit_read"]
    bid_input_index = edit_read.get("bid_input_index")

    floor = _safe_float(edit_read.get("floor_price_cny"))
    if floor is not None and target_bid < floor - _BID_EPSILON:
        await _return_to_list_page(page, list_page=verify.get("list_page"))
        return {
            "status": "floor_price_violation",
            "summary": f"目标出价 ¥{_display(target_bid)} 低于页面起价 ¥{_display(floor)}，已停止调整。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {"captured_at": verify.get("captured_at"), "campaign_read": edit_read},
            "evidence": {"browser_write_invoked": True, "submit_invoked": False, "floor_price_cny": floor},
            "next_action": "请把目标出价提高到页面起价之上后重新发起。",
        }

    before_screenshot = await _safe_screenshot(service, page, platform_account_id)
    fill_result = await _fill_bid_input(page, bid_input_index, target_bid)
    if fill_result["status"] != "filled":
        await _return_to_list_page(page, list_page=verify.get("list_page"))
        status = (
            "page_structure_changed" if fill_result["status"] == "not_located" else "needs_review"
        )
        return {
            "status": status,
            "summary": f"金字塔出价输入失败（{fill_result['status']}），未提交修改。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "captured_at": verify.get("captured_at"),
                "campaign_read": edit_read,
                "bid_fill_result": fill_result,
            },
            "evidence": {
                "browser_write_invoked": True,
                "submit_invoked": False,
                "fill_status": fill_result["status"],
                "screenshot_captured": bool(before_screenshot),
            },
            "next_action": "请人工在 EBK 金字塔页面确认后再操作。",
        }

    submit_result = await _submit_campaign_edit(page)
    after_screenshot = await _safe_screenshot(service, page, platform_account_id)

    readback = await _read_promotion_bid(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        campaign_name=campaign_name,
    )
    readback_bid = _safe_float((readback.get("promotion") or {}).get("click_bid_cny"))
    readback_ok = readback_bid is not None and abs(readback_bid - target_bid) < _BID_EPSILON
    if readback_ok:
        status = "executed"
        summary = (
            f"「{campaign_name}」点击出价已调整为 ¥{_display(target_bid)}，页面回读一致。"
        )
    elif (
        not submit_result.get("left_edit_page")
        and current_bid is not None
        and readback_bid is not None
        and abs(readback_bid - current_bid) < _BID_EPSILON
    ):
        status = "submit_failed"
        summary = "提交修改未生效，页面出价仍是原值，已停止。"
    else:
        status = "mismatch"
        summary = (
            f"提交后页面回读出价 ¥{_display(readback_bid)} 与目标 ¥{_display(target_bid)} 不一致，需人工复核。"
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state="authenticated",
    )
    # 执行收尾统一回列表页并关闭编辑标签：成功路径此前不关 createCampaign
    # 标签，每次成功执行都会留下一个孤儿标签。
    await _return_to_list_page(page, list_page=verify.get("list_page"))
    return {
        "status": status,
        "summary": summary,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "ctrip_pyramid_bid_write_execute",
            "captured_at": verify.get("captured_at"),
            "campaign_read": edit_read,
            "bid_fill_result": fill_result,
            "submit_result": submit_result,
            "readback_result": {
                "campaign_name": campaign_name,
                "readback_bid_cny": readback_bid,
                "target_bid_cny": target_bid,
            },
        },
        "evidence": {
            "browser_write_invoked": True,
            "submit_invoked": bool(submit_result.get("clicked")),
            "left_edit_page": bool(submit_result.get("left_edit_page")),
            "fill_status": fill_result["status"],
            "readback_bid_matches_target": readback_ok,
            "screenshot_captured": bool(before_screenshot or after_screenshot),
        },
        "next_action": (
            "可在飞书发送「金字塔出价」复核最新出价。"
            if status == "executed"
            else "请人工登录 EBK 金字塔页面复核。"
        ),
    }


# ---------------------------------------------------------------------------
# 纯解析函数（可单测）
# ---------------------------------------------------------------------------


def parse_pyramid_promotion_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """解析 extensionList 表格行（2026-09-08 真实 DOM 结构）。

    列序：推广名称 | 状态 | 推广日期 | 普通日/高峰日预算 | 点击出价 | 今日花费 |
    今日曝光 | 今日点击 | 操作。智能投放计划无固定出价（单元格非数字）。
    """
    promotions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = [str(cell or "").strip() for cell in (row.get("cells") or [])]
        if len(cells) < 6 or not cells[0]:
            continue
        campaign_id = str(row.get("row_key") or "").strip()
        if campaign_id:
            # antd 固定列可能重复渲染同一行；按 data-row-key 去重。
            if campaign_id in seen_ids:
                continue
            seen_ids.add(campaign_id)
        bid = _safe_float(cells[4])
        promotions.append(
            {
                "campaign_name": cells[0],
                "campaign_id": campaign_id or None,
                "campaign_status": cells[1] or None,
                "promotion_date_text": cells[2] or None,
                "click_bid_cny": bid,
                "daily_budget_cny": _parse_budget_pair(cells[3]),
                "today_spend_cny": _safe_float(cells[5]),
                "today_impressions": _safe_int(cells[6]) if len(cells) > 6 else None,
                "today_clicks": _safe_int(cells[7]) if len(cells) > 7 else None,
                "has_modify_button": bool(row.get("has_modify_button")),
                "smart_campaign": bid is None,
            }
        )
    return promotions


def extract_pyramid_floor_price(text: str) -> float | None:
    import re

    match = re.search(r"起价\s*(\d+(?:\.\d+)?)", str(text or ""))
    return _safe_float(match.group(1)) if match else None


def pick_bid_input(
    inputs: list[dict[str, Any]],
    *,
    current_bid: float | None,
) -> int | None:
    """定位单次点击出价输入框：优先 值==当前出价 且 邻近文本含出价语境。"""
    textual = [
        item
        for item in inputs
        if any(token in str(item.get("near") or "") for token in ("点击出价", "起价", "建议出价"))
    ]
    if current_bid is not None:
        for item in textual:
            if _safe_float(item.get("value")) is not None and abs(
                _safe_float(item.get("value")) - current_bid
            ) < _BID_EPSILON:
                return int(item["input_index"])
    if textual:
        return int(textual[0]["input_index"])
    if current_bid is not None:
        for item in inputs:
            if _safe_float(item.get("value")) is not None and abs(
                _safe_float(item.get("value")) - current_bid
            ) < _BID_EPSILON:
                return int(item["input_index"])
    return None


def pick_name_input(inputs: list[dict[str, Any]]) -> int | None:
    for item in inputs:
        if "推广名称" in str(item.get("near") or ""):
            return int(item["input_index"])
    return None


def _parse_budget_pair(text: str) -> float | None:
    """「普通日/高峰日预算」形如 `400/400`，取普通日预算。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", str(text or ""))
    if match:
        return _safe_float(match.group(1))
    return _safe_float(text)


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else None


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _bid_matches_expected(value: Any, expected_bid: float | None) -> bool:
    current_bid = _safe_float(value)
    return (
        expected_bid is not None
        and current_bid is not None
        and abs(current_bid - expected_bid) < _BID_EPSILON
    )


def _display(value: Any) -> str:
    parsed = _safe_float(value)
    return "-" if parsed is None else f"{parsed:g}"


# ---------------------------------------------------------------------------
# 页面步骤
# ---------------------------------------------------------------------------


async def _open_pyramid_page(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
) -> dict[str, Any]:
    from datetime import UTC, datetime

    try:
        page = await service._get_or_create_page(
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            headless=False,
        )
        await page.goto(PYRAMID_LIST_URL, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)
        await _dismiss_ebk_read_blocking_popups(page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pyramid page open failed (%s)", type(exc).__name__)
        profile_locked = type(exc).__name__ == "CtripBrowserProfileLockedError"
        return {
            "status": "human_required" if profile_locked else "failed",
            "summary": (
                "携程 EBK 浏览器账号正在被其他任务或登录窗口占用，本次不操作金字塔页面。"
                if profile_locked
                else "打开携程金字塔推广页失败。"
            ),
            "page": None,
            "page_state": "",
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"error_type": type(exc).__name__},
            "next_action": (
                "请等待当前任务结束，或关闭占用该账号的 Chrome 后重试。"
                if profile_locked
                else "请确认 EBK 浏览器可用后重试。"
            ),
        }
    body_text = await _safe_visible_text(page)
    page_state = _classify_page_state(page.url, body_text)
    captured_at = datetime.now(UTC).isoformat()
    if page_state in {"login_required", "verification_required"}:
        _sync_ebk_browser_session_state(
            browser_session_id=browser_session_id,
            actor_id=actor_id,
            status="human_required",
            page_state=page_state,
        )
        return {
            "status": "human_required",
            "summary": "携程 EBK 需要登录或人工验证，本次不操作金字塔页面。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"page_state": page_state, "browser_write_invoked": False},
            "next_action": "请人工完成登录/验证后重试。",
        }
    if "ebooking.ctrip.com" not in page.url.lower():
        return {
            "status": "page_structure_changed",
            "summary": "金字塔页面跳转到了非预期地址，已停止。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"current_url_host_unexpected": True, "browser_write_invoked": False},
            "next_action": "请人工打开 EBK 金字塔页面确认。",
        }
    if "我的推广" not in body_text and "金字塔" not in body_text:
        return {
            "status": "page_structure_changed",
            "summary": "金字塔推广列表页结构未识别（缺少「我的推广」区块），已停止。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"promotion_section_missing": True, "browser_write_invoked": False},
            "next_action": "请人工确认 EBK 金字塔页面结构后重试。",
        }
    return {
        "status": "success",
        "summary": "金字塔推广页已打开。",
        "page": page,
        "page_state": page_state,
        "captured_at": captured_at,
        "evidence": {"page_state": page_state},
    }


async def _open_campaign_edit_page(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    campaign_name: str,
    current_bid: float | None = None,
) -> dict[str, Any]:
    """打开列表 -> 匹配计划 -> 点「修改」-> 核验编辑页。返回 edit_read。"""
    from datetime import UTC, datetime

    opened = await _open_pyramid_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
    )
    if opened["status"] != "success":
        return opened
    page: Page = opened["page"]
    rows = await _collect_promotion_rows_stable(page)
    promotions = parse_pyramid_promotion_rows(rows)
    matched = _match_promotion_row(rows, campaign_name)
    if matched is None:
        return {
            "status": "campaign_not_found",
            "summary": (
                f"金字塔推广列表里没有找到「{campaign_name}」。当前计划："
                + "、".join(item.get("campaign_name") or "" for item in promotions[:8])
            ),
            "page": page,
            "page_state": opened["page_state"],
            "captured_at": opened["captured_at"],
            "evidence": {"campaign_count": len(promotions), "browser_write_invoked": False},
            "next_action": "请发送「金字塔出价」查看最新计划名称后重试。",
        }
    try:
        # 行定位：优先 data-row-key 精确锁定；无 row_key 时回退到「与采集器同口径」
        # 的过滤序号（只数含修改按钮的行），绝不能用裸 tr 序号——页面含表头行和
        # 空占位行，序号会错位点到别的计划。
        campaign_id = matched.get("campaign_id")
        modify_rows = page.locator(
            'tr:has(button[he-click="cpc_extension_list_action_modify_button"])'
        )
        if campaign_id and re.fullmatch(r"[\w-]+", campaign_id):
            row_locator = page.locator(f'tr[data-row-key="{campaign_id}"]')
        else:
            row_locator = modify_rows.nth(int(matched["index"]))
        modify_button = row_locator.locator(
            'button[he-click="cpc_extension_list_action_modify_button"]'
        )
        if not await modify_button.count():
            modify_button = row_locator.get_by_role("button", name="修改", exact=True)
        await modify_button.first.wait_for(state="visible", timeout=8_000)
        # 「修改」可能开新标签页进入编辑页，也可能同页跳转；两种都接住。
        edit_page = await _click_modify_and_capture_edit_page(page, modify_button)
        await edit_page.wait_for_load_state("domcontentloaded")
        await _settle_page(edit_page)
        # 编辑页表单异步渲染：等标记与推广名称值就绪再读，避免 600ms 内误判结构变化。
        await edit_page.wait_for_function(_EDIT_PAGE_MARKER_READY_JS, timeout=12_000)
        await edit_page.wait_for_function(_EDIT_NAME_INPUT_READY_JS, timeout=10_000)
    except Exception as exc:  # noqa: BLE001
        # 渲染超时先分类页面态：编辑页掉登录/风控时必须报 human_required，
        # 不能误判 page_structure_changed 丢失人工介入语义。
        fallback_text = await _safe_visible_text(edit_page)
        fallback_state = _classify_page_state(edit_page.url, fallback_text)
        if fallback_state in {"login_required", "verification_required"}:
            return {
                "status": "human_required",
                "summary": "携程 EBK 需要登录或人工验证，本次不操作金字塔页面。",
                "page": edit_page,
                "page_state": fallback_state,
                "captured_at": datetime.now(UTC).isoformat(),
                "evidence": {"page_state": fallback_state, "error_type": type(exc).__name__},
                "next_action": "请人工完成登录/验证后重试。",
            }
        return {
            "status": "page_structure_changed",
            "summary": "打开或渲染金字塔推广编辑页失败（页面未在预期时间内就绪），已停止。",
            "page": page,
            "page_state": opened["page_state"],
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"error_type": type(exc).__name__, "browser_write_invoked": False},
            "next_action": "请人工打开该计划的编辑页确认后重试。",
        }
    body_text = await _safe_visible_text(edit_page)
    page_state = _classify_page_state(edit_page.url, body_text)
    if page_state in {"login_required", "verification_required"}:
        return {
            "status": "human_required",
            "summary": "携程 EBK 需要登录或人工验证，本次不操作金字塔页面。",
            "page": edit_page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"page_state": page_state},
            "next_action": "请人工完成登录/验证后重试。",
        }
    if PYRAMID_EDIT_URL_PREFIX not in edit_page.url or "修改推广" not in body_text:
        return {
            "status": "page_structure_changed",
            "summary": "金字塔编辑页未按预期展示（缺少「修改推广」标识），已停止。",
            "page": edit_page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"edit_page_marker_missing": True},
            "next_action": "请人工确认金字塔编辑页结构。",
        }
    inputs = await edit_page.evaluate(_COLLECT_EDIT_INPUTS_JS)
    inputs = inputs if isinstance(inputs, list) else []
    name_input_value = None
    name_index = pick_name_input(inputs)
    if name_index is not None:
        for item in inputs:
            if int(item.get("input_index", -1)) == name_index:
                name_input_value = str(item.get("value") or "")
                break
    if not _campaign_names_equivalent(campaign_name, name_input_value):
        return {
            "status": "page_structure_changed",
            "summary": (
                f"编辑页推广名称（{name_input_value or '未读取到'}）与目标计划"
                f"「{campaign_name}」不一致，可能点错计划，已停止。"
            ),
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {
                "campaign_name_verified": False,
                "name_input_value": name_input_value,
            },
            "next_action": "请人工核对计划名称后重试。",
        }
    bid_input_index = pick_bid_input(inputs, current_bid=current_bid)
    if bid_input_index is None:
        return {
            "status": "bid_input_not_located",
            "summary": "未能在编辑页定位「单次点击出价」输入框，已停止。",
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"bid_input_located": False},
            "next_action": "请人工确认金字塔编辑页结构。",
        }
    current_page_bid_input_value = _input_value(inputs, bid_input_index)
    if not _bid_matches_expected(current_page_bid_input_value, current_bid):
        return {
            "status": "needs_review",
            "summary": "金字塔当前出价与审批预览不一致，可能已被其他操作修改，已停止自动调整。",
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {
                "campaign_name_verified": True,
                "bid_input_located": True,
                "approved_current_bid_cny": current_bid,
                "current_page_bid_cny": _safe_float(current_page_bid_input_value),
                "browser_write_invoked": False,
            },
            "next_action": "请重新发送金字塔调价指令，以页面最新出价生成审批预览。",
        }
    return {
        "status": "edit_page_verified",
        "summary": f"已打开「{campaign_name}」的修改推广页并核验输入框。",
        "page": edit_page,
        "list_page": page,
        "page_state": page_state,
        "captured_at": datetime.now(UTC).isoformat(),
        "edit_read": {
            "campaign_name": campaign_name,
            "campaign_name_input_value": name_input_value,
            "current_page_bid_input_value": current_page_bid_input_value,
            "bid_input_index": bid_input_index,
            "floor_price_cny": extract_pyramid_floor_price(body_text),
            "smart_bidding_detected": ("智能调价" in body_text and "已开启" in body_text),
            "edit_page_url_prefix": PYRAMID_EDIT_URL_PREFIX,
        },
        "evidence": {
            "campaign_name_verified": True,
            "bid_input_located": True,
        },
    }


def _match_promotion_row(rows: list[Any], campaign_name: str) -> dict[str, Any] | None:
    """按推广名称匹配表格行；返回 {index, campaign_id}。重名/未找到返回 None。

    匹配键用 `pyramid_campaign_key`（剔除空格与各类横线变体）：
    「周内0--2」与页面「周内0-2」、「22-24」与页面「22--24」互为同名。
    """
    target = _campaign_key(campaign_name)
    if not target:
        return None
    exact: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        cells = [str(cell or "") for cell in (row.get("cells") or [])]
        name_key = _campaign_key(cells[0]) if cells else ""
        if not name_key:
            continue
        matched = {"index": index, "campaign_id": str(row.get("row_key") or "").strip() or None}
        if target == name_key:
            exact.append(matched)
        elif target in name_key:
            contains.append(matched)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(contains) == 1:
        return contains[0]
    return None


async def _fill_bid_input(
    page: Page,
    bid_input_index: Any,
    target_bid: float,
) -> dict[str, Any]:
    if bid_input_index is None:
        return {"status": "not_located", "filled_value": None, "verified_value": None}
    value = f"{target_bid:g}"
    try:
        handle = page.locator("input, textarea").nth(int(bid_input_index))
        await handle.wait_for(state="visible", timeout=10_000)
        await handle.click(click_count=3)
        await page.wait_for_timeout(250)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(value, delay=80)
        await page.wait_for_timeout(500)
        readback = None
        try:
            readback = await handle.input_value()
        except Exception:  # noqa: BLE001
            pass
        actual = _safe_float(readback)
        if actual is not None and abs(actual - target_bid) < _BID_EPSILON:
            return {"status": "filled", "filled_value": value, "verified_value": readback}
        return {"status": "mismatch", "filled_value": value, "verified_value": readback}
    except Exception:  # noqa: BLE001
        return {"status": "input_failed", "filled_value": None, "verified_value": None}


async def _submit_campaign_edit(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {"clicked": False, "left_edit_page": False, "status": "not_submitted"}
    try:
        submit = page.get_by_role("button", name="提交修改", exact=True)
        await submit.wait_for(state="visible", timeout=8_000)
        await submit.click()
        result["clicked"] = True
    except Exception as exc:  # noqa: BLE001
        result["error_type"] = type(exc).__name__
        return result
    waited = 0.0
    while waited < _EDIT_LEAVE_TIMEOUT_MS:
        try:
            await page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 - 页签被站点自行关闭
            result["left_edit_page"] = True
            result["status"] = "edit_page_closed"
            return result
        waited += 500
        try:
            current_url = page.url
        except Exception:  # noqa: BLE001 - 页签已关闭
            result["left_edit_page"] = True
            result["status"] = "edit_page_closed"
            return result
        if "createCampaign" not in current_url:
            result["left_edit_page"] = True
            result["status"] = "left_edit_page"
            return result
    result["status"] = "still_on_edit_page"
    return result


async def _read_promotion_bid(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    campaign_name: str,
) -> dict[str, Any]:
    read_result = await read_pyramid_promotions_flow(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id="pyramid-readback",
    )
    promotions = read_result.get("promotions") or []
    target_key = _campaign_key(campaign_name)
    for item in promotions:
        if _campaign_key(item.get("campaign_name")) == target_key:
            return {"status": "success", "promotion": item}
    return {"status": "campaign_not_found", "promotion": {}}


async def _return_to_list_page(page: Page, *, list_page: Page | None = None) -> None:
    """把浏览器留在推广列表页：编辑页是独立标签时直接关闭，否则导航回列表。"""
    try:
        if list_page is not None and page is not list_page:
            try:
                await page.close()
            except Exception:  # noqa: BLE001 - 页签可能已被站点关闭
                pass
            return
        if "createCampaign" in page.url:
            await page.goto(PYRAMID_LIST_URL, wait_until="domcontentloaded", timeout=45_000)
            await _settle_page(page)
    except Exception:  # noqa: BLE001
        logger.warning("failed to return to pyramid list page", exc_info=True)


async def _safe_screenshot(service: CtripEbookingBrowserService, page: Page, platform_account_id: str) -> str | None:
    try:
        return await service._capture_screenshot(page, platform_account_id)
    except Exception:  # noqa: BLE001
        return None


def _input_value(inputs: list[dict[str, Any]], input_index: int) -> str | None:
    for item in inputs:
        if int(item.get("input_index", -1)) == int(input_index):
            return str(item.get("value") or "") or None
    return None


def _first_action(execute_request: dict[str, Any]) -> dict[str, Any]:
    actions = execute_request.get("actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        return actions[0]
    return {}


def _stage_result(
    verify: dict[str, Any],
    *,
    platform_account_id: str,
    browser_session_id: str,
    extra_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": verify["status"],
        "summary": verify.get("summary") or "金字塔页面步骤未完成。",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {"captured_at": verify.get("captured_at")},
        "evidence": {**(verify.get("evidence") or {}), **(extra_evidence or {})},
        "next_action": verify.get("next_action"),
    }
