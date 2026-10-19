"""携程 EBK 促销中心（活动促销）已参加促销读取与折扣调整浏览器流。

页面事实（2026-09-08 CDP 只读探针复验）：
- 促销中心 `/promotion/promotionCenter?microJump=true` 首页是促销类型目录页；
  「已参加促销」tab（`[he-click="domestic_promotionRegister"]`）展示全量表格：
  `tr[data-row-key]` 行，列 = 促销名称 | 参与房型 | 促销房量 | 促销时间 |
  促销优惠 | 订单储备金 | 操作（每行「暂停」「修改」「更多」，修改按钮带
  `he-click="pc_promotion_list_modify_click"`）。
- 点「修改」开新标签进入 `/promotion/editPromotion?promotionType=...&ruleIds=...&opera=modify`
  编辑页：促销名称标题「修改 <促销名>」、开始/结束日期输入框、折扣输入框
  （值为数字、邻近文本含 %）、按钮「保 存」「取 消」「添加不同时间的促销设置」。

安全设计（与金字塔出价写链同构）：
- URL 白名单只接受促销中心/编辑页；登录/验证页一律 `human_required`；
- 仅支持单条「立减 X%」规则的促销；多条规则/红包折扣一律阻断转人工；
- dry-run 只导航核验、绝不输入；执行时填入新折扣后回读输入框，保存后
  重新读取列表页折扣做最终校验。
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
from app.domain.promotion_adjust.commands import promotion_name_key

if TYPE_CHECKING:
    from app.domain.browser.ctrip_ebooking import CtripEbookingBrowserService

logger = logging.getLogger(__name__)

PROMOTION_CENTER_URL = "https://ebooking.ctrip.com/promotion/promotionCenter?microJump=true"
PROMOTION_EDIT_URL_PREFIX = "https://ebooking.ctrip.com/promotion/editPromotion"

_PERCENT_EPSILON = 0.005
_EDIT_LEAVE_TIMEOUT_MS = 20_000
_PROMOTION_REGISTER_TAB_SELECTOR = '[he-click="domestic_promotionRegister"]'
_PROMOTION_MODIFY_BUTTON_ATTR = "pc_promotion_list_modify_click"
# 2026-09-09 真机探针确认的编辑页底部操作栏真实结构：
# <div class="he-trip-ui-checkbox-group" id="basic_agreement">
#   <label class="he-trip-ui-checkbox-wrapper">
#     <span class="he-trip-ui-checkbox" he-click="agreement">
#       <input type="checkbox" class="he-trip-ui-checkbox-input" value="0">
#   兄弟节点文本「我已经阅读并同意促销规则」（注意带「我已经」，
#   此前搜「已阅读并同意促销规则」差一个「经」字永远匹配不上）。
# 保存按钮 he-click="footer_pageSave"，取消 he-click="footer_pageBack"。
_PROMOTION_RULE_AGREEMENT_TEXT = "我已经阅读并同意促销规则"

_COLLECT_PROMOTION_ROWS_JS = """() => {
  const seen = new Set();
  const payload = [];
  const rows = Array.from(document.querySelectorAll('tr')).filter(tr => {
    const btn = tr.querySelector('[he-click="cpc_promotion_list_modify_button"], [he-click="pc_promotion_list_modify_click"]');
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
  return inputs.slice(0, 50);
}"""

# 编辑页就绪判定（重 SPA 异步渲染）：出现编辑页标记，且折扣输入框
# （数字值 + % 语境）已渲染出值，之后才读 body/inputs 做校验。
_EDIT_PAGE_MARKER_READY_JS = (
    "() => /修改|保\\s*存|取\\s*消/.test(document.body.innerText || '')"
)
_DISCOUNT_INPUT_READY_JS = """() => {
  const all = Array.from(document.querySelectorAll('input'));
  for (const inp of all) {
    const type = (inp.getAttribute('type') || '').toLowerCase();
    if (['checkbox', 'radio', 'hidden'].includes(type)) continue;
    if (!/^\\d+(\\.\\d+)?$/.test(String(inp.value || '').trim())) continue;
    let el = inp.parentElement;
    let near = '';
    for (let k = 0; k < 4 && el; k += 1) {
      near = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (near) break;
      el = el.parentElement;
    }
    if (near.includes('%')) return true;
  }
  return false;
}"""

_PROMOTION_RULE_AGREEMENT_STATE_JS = """() => {
  const pack = (via, found, checked, disabled) => ({
    label_found: found, control_found: found, control_type: via,
    checked: checked, disabled: disabled
  });
  // 主路径：编辑页底部 basic_agreement 复选组里的原生 input（2026-09-09 真机结构）
  const input = document.querySelector('#basic_agreement input[type="checkbox"]')
    || document.querySelector('span[he-click="agreement"] input[type="checkbox"]');
  if (input) {
    return pack('input', true, Boolean(input.checked), Boolean(input.disabled));
  }
  // 次路径：无原生 input 的自定义复选框（he-click 埋点属性最稳）
  const custom = document.querySelector('span[he-click="agreement"], [he-click="agreement"]');
  if (custom) {
    const wrapper = custom.closest('label') || custom;
    const cls = ((wrapper.className || '') + ' ' + (custom.className || ''));
    const checked = /checked/i.test(cls)
      || custom.getAttribute('aria-checked') === 'true';
    return pack('custom', true, checked, wrapper.getAttribute('aria-disabled') === 'true');
  }
  // 兜底：按归一化文本「阅读并同意」定位后向上下找复选框（防结构再变）
  const strip = (s) => (s || '').replace(/[\s　《》〈〉「」『』【】〔〕（）]/g, '');
  const els = Array.from(document.querySelectorAll('body *')).filter(el => {
    const t = strip(el.innerText || '');
    return t.includes('阅读并同意') && t.length < 60;
  });
  const labelEl = els.sort((a, b) =>
    strip(a.innerText || '').length - strip(b.innerText || '').length)[0];
  if (!labelEl) return pack(null, false, false, false);
  let node = labelEl;
  for (let i = 0; node && i < 6; i += 1, node = node.parentElement) {
    const c = node.querySelector('input[type="checkbox"], span[he-click="agreement"]');
    if (c) {
      if (c.tagName === 'INPUT') return pack('text_input', true, !!c.checked, !!c.disabled);
      const wrapper = c.closest('label') || c;
      return pack('text_custom', true, /checked/i.test(wrapper.className || ''), false);
    }
  }
  return pack(null, false, false, false);
}"""


# 勾选「我已经阅读并同意促销规则」的点击候选，按命中优先级排列：
# antd 风格点 label wrapper 即可切换内部 input；原生 input/自定义 span 兜底。
_AGREEMENT_CLICK_SELECTORS = (
    '#basic_agreement label',
    'label:has(span[he-click="agreement"])',
    'span[he-click="agreement"]',
    '#basic_agreement input[type="checkbox"]',
)


# ---------------------------------------------------------------------------
# 只读：已参加促销列表
# ---------------------------------------------------------------------------

_campaign_key = promotion_name_key

_LIJIAN_PERCENT_RE = re.compile(r"立减\s*(\d+(?:\.\d+)?)\s*%")


def parse_promotion_discount(text: Any) -> tuple[float | None, str]:
    """解析促销优惠文本。

    返回 (折扣百分比, 类别)：
    - single：整行只有一条「立减 X%」（可调）
    - multi：多条「立减 X%」规则（多时段/多条件，不可自动调）
    - unsupported：9折/红包/未识别等其他形式
    """
    compact = re.sub(r"\s+", "", str(text or ""))
    matches = _LIJIAN_PERCENT_RE.findall(compact)
    if len(matches) == 1:
        return _safe_float(matches[0]), "single"
    if len(matches) > 1:
        return None, "multi"
    return None, "unsupported"


def parse_promotion_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """解析已参加促销表格行（2026-09-08 真实 DOM 结构）。

    列序：促销名称 | 参与房型 | 促销房量 | 促销时间 | 促销优惠 | 订单储备金 | 操作。
    """
    promotions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = [str(cell or "").strip() for cell in (row.get("cells") or [])]
        if len(cells) < 6 or not cells[0]:
            continue
        row_key = str(row.get("row_key") or "").strip()
        if row_key:
            if row_key in seen_keys:
                continue
            seen_keys.add(row_key)
        discount_percent, discount_kind = parse_promotion_discount(
            cells[4] if len(cells) > 4 else ""
        )
        promotions.append(
            {
                "promotion_name": cells[0],
                "promotion_row_key": row_key or None,
                "room_scope": cells[1] or None,
                "quota_text": cells[2] or None,
                "date_text": cells[3] or None,
                "discount_text": cells[4] if len(cells) > 4 else None,
                "discount_percent": discount_percent,
                "discount_kind": discount_kind,
                "multi_rule": discount_kind != "single",
                "deposit_text": cells[5] if len(cells) > 5 else None,
                "has_modify_button": bool(row.get("has_modify_button")),
            }
        )
    return promotions


def match_promotion_row(rows: list[Any], promotion_name: str) -> dict[str, Any] | None:
    """按促销名称匹配表格行；返回 {index, row_key, promotion_name}。

    页面名带分组前缀（「基础促销组 天天特价」），用户指令常只说短名
    （「天天特价」），因此做双向包含匹配；命中多行（歧义）返回 None。
    """
    target = _campaign_key(promotion_name)
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
        matched = {
            "index": index,
            "row_key": str(row.get("row_key") or "").strip() or None,
            "promotion_name": cells[0],
        }
        if target == name_key:
            exact.append(matched)
        elif target in name_key or name_key in target:
            contains.append(matched)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(contains) == 1:
        return contains[0]
    return None


def pick_promotion_discount_input(inputs: list[dict[str, Any]]) -> int | None:
    """定位折扣输入框：值是数字且邻近文本含 %。

    多个 % 输入（多规则促销）返回 None——宁可不调也不能改错规则。
    """
    candidates = [
        item
        for item in inputs
        if "%" in str(item.get("near") or "")
        and _safe_float(item.get("value")) is not None
    ]
    if len(candidates) == 1:
        return int(candidates[0]["input_index"])
    return None


async def _collect_promotion_rows_stable(
    page: Page,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = 700,
) -> list[Any]:
    """轮询采集已参加促销表格行，直到行数连续两次不再增长（同金字塔口径）。"""
    from time import monotonic

    try:
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
    """点「修改」并返回编辑页 Page（可能开新标签，也可能同页跳转）。"""
    existing = {id(p) for p in page.context.pages}
    await modify_button.first.click()
    for _ in range(30):  # 最多 15s
        await page.wait_for_timeout(500)
        try:
            if "editPromotion" in page.url:
                return page
        except Exception:  # noqa: BLE001 - 页签被站点关闭等
            break
        for candidate in page.context.pages:
            if id(candidate) in existing:
                continue
            try:
                if "editPromotion" in candidate.url:
                    return candidate
            except Exception:  # noqa: BLE001
                continue
    raise TimeoutError("click modify did not open editPromotion page")


async def read_promotion_enrollments_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
) -> dict[str, Any]:
    opened = await _open_promotion_center_page(
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
            "data": {"captured_at": opened.get("captured_at")},
            "evidence": opened.get("evidence"),
            "next_action": opened.get("next_action"),
        }
    page: Page = opened["page"]
    try:
        rows = await _collect_promotion_rows_stable(page)
        promotions = parse_promotion_rows(rows)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "summary": "读取已参加促销列表失败。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "promotions": [],
            "evidence": {"error_type": type(exc).__name__},
            "next_action": "请人工登录 EBK 查看促销中心。",
        }
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status="success",
        page_state=opened["page_state"],
    )
    return {
        "status": "success",
        "summary": f"读取到 {len(promotions)} 个已参加促销。",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "promotions": promotions,
        "data": {
            "query_type": "ctrip_promotion_enrollments_read",
            "captured_at": opened.get("captured_at"),
        },
        "evidence": {
            "page_state": opened["page_state"],
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "read_only": True,
        },
    }


# ---------------------------------------------------------------------------
# 空跑：打开编辑页核验促销与折扣输入框（不输入任何内容）
# ---------------------------------------------------------------------------


async def prepare_promotion_adjust_page_write_dry_run_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    action = _first_action(execute_request)
    promotion_name = str(action.get("promotion_name") or "")
    current_percent = _safe_float(action.get("current_discount_percent"))
    verify = await _open_promotion_edit_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        promotion_name=promotion_name,
        current_percent=current_percent,
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
            f"促销编辑页核验通过：「{promotion_name}」折扣输入框已定位，未做任何输入。"
        ),
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "ctrip_promotion_adjust_page_write_dry_run",
            "captured_at": verify.get("captured_at"),
            "promotion_read": edit_read,
        },
        "evidence": {
            "dry_run": True,
            "read_only": True,
            "input_fill_invoked": False,
            "submit_invoked": False,
            "browser_write_invoked": False,
            "promotion_name_verified": True,
            "discount_input_located": bool(edit_read.get("discount_input_index") is not None),
            "current_discount_percent": edit_read.get("current_discount_percent"),
        },
        "next_action": "等待真实折扣写入。",
    }


# ---------------------------------------------------------------------------
# 执行：填折扣 -> 保存 -> 回读校验
# ---------------------------------------------------------------------------


async def execute_promotion_adjust_write_flow(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    execute_request: dict[str, Any],
) -> dict[str, Any]:
    action = _first_action(execute_request)
    promotion_name = str(action.get("promotion_name") or "")
    target_percent = _safe_float(action.get("target_discount_percent"))
    current_percent = _safe_float(action.get("current_discount_percent"))
    if target_percent is None:
        return {
            "status": "data_required",
            "summary": "执行请求缺少目标立减百分比，已停止。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "evidence": {"browser_write_invoked": False},
        }
    verify = await _open_promotion_edit_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        promotion_name=promotion_name,
        current_percent=current_percent,
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
    discount_input_index = edit_read.get("discount_input_index")

    before_screenshot = await _safe_screenshot(service, page, platform_account_id)
    fill_result = await _fill_discount_input(page, discount_input_index, target_percent)
    if fill_result["status"] != "filled":
        await _return_to_list_page(page, list_page=verify.get("list_page"))
        status = (
            "page_structure_changed" if fill_result["status"] == "not_located" else "needs_review"
        )
        return {
            "status": status,
            "summary": f"促销折扣输入失败（{fill_result['status']}），未保存修改。",
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "captured_at": verify.get("captured_at"),
                "promotion_read": edit_read,
                "discount_fill_result": fill_result,
            },
            "evidence": {
                "browser_write_invoked": True,
                "submit_invoked": False,
                "fill_status": fill_result["status"],
                "screenshot_captured": bool(before_screenshot),
            },
            "next_action": "请人工在 EBK 促销中心确认后再操作。",
        }

    agreement_result = await _ensure_promotion_rules_agreed(page)
    if agreement_result["status"] not in {"checked", "already_checked"}:
        after_screenshot = await _safe_screenshot(service, page, platform_account_id)
        await _return_to_list_page(page, list_page=verify.get("list_page"))
        status = (
            "page_structure_changed"
            if agreement_result["status"] == "not_located"
            else "needs_review"
        )
        return {
            "status": status,
            "summary": (
                "未能确认已勾选“我已经阅读并同意促销规则”，未保存促销修改。"
            ),
            "platform_account_id": platform_account_id,
            "browser_session_id": browser_session_id,
            "data": {
                "captured_at": verify.get("captured_at"),
                "promotion_read": edit_read,
                "discount_fill_result": fill_result,
                "promotion_rules_agreement": agreement_result,
            },
            "evidence": {
                "browser_write_invoked": True,
                "submit_invoked": False,
                "fill_status": fill_result["status"],
                "promotion_rules_agreed": False,
                "agreement_status": agreement_result["status"],
                "screenshot_captured": bool(before_screenshot or after_screenshot),
            },
            "next_action": "请人工确认促销编辑页的规则同意控件后重试。",
        }

    submit_result = await _submit_promotion_edit(page)
    after_screenshot = await _safe_screenshot(service, page, platform_account_id)

    readback = await _read_promotion_discount(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        promotion_name=promotion_name,
    )
    readback_percent = _safe_float((readback.get("promotion") or {}).get("discount_percent"))
    readback_ok = (
        readback_percent is not None and abs(readback_percent - target_percent) < _PERCENT_EPSILON
    )
    if readback_ok:
        status = "executed"
        summary = (
            f"「{promotion_name}」立减已调整为 {target_percent:g}%，页面回读一致。"
        )
    elif (
        not submit_result.get("left_edit_page")
        and current_percent is not None
        and readback_percent is not None
        and abs(readback_percent - current_percent) < _PERCENT_EPSILON
    ):
        status = "submit_failed"
        summary = "保存修改未生效，页面立减仍是原值，已停止。"
    else:
        status = "mismatch"
        summary = (
            f"保存后页面回读立减 {readback_percent if readback_percent is not None else '-'}% "
            f"与目标 {target_percent:g}% 不一致，需人工复核。"
        )
    _sync_ebk_browser_session_state(
        browser_session_id=browser_session_id,
        actor_id=actor_id,
        status=status,
        page_state="authenticated",
    )
    # 执行收尾统一回列表页并关闭编辑标签：成功路径此前不关 editPromotion
    # 标签，每次成功执行都会留下一个孤儿标签。
    await _return_to_list_page(page, list_page=verify.get("list_page"))
    return {
        "status": status,
        "summary": summary,
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {
            "query_type": "ctrip_promotion_adjust_write_execute",
            "captured_at": verify.get("captured_at"),
            "promotion_read": edit_read,
            "discount_fill_result": fill_result,
            "promotion_rules_agreement": agreement_result,
            "submit_result": submit_result,
            "readback_result": {
                "promotion_name": promotion_name,
                "readback_discount_percent": readback_percent,
                "target_discount_percent": target_percent,
            },
        },
        "evidence": {
            "browser_write_invoked": True,
            "submit_invoked": bool(submit_result.get("clicked")),
            "left_edit_page": bool(submit_result.get("left_edit_page")),
            "fill_status": fill_result["status"],
            "promotion_rules_agreed": True,
            "agreement_status": agreement_result["status"],
            "readback_percent_matches_target": readback_ok,
            "screenshot_captured": bool(before_screenshot or after_screenshot),
        },
        "next_action": (
            "可在飞书发送「促销列表」复核最新立减。"
            if status == "executed"
            else "请人工登录 EBK 促销中心复核。"
        ),
    }


# ---------------------------------------------------------------------------
# 页面步骤
# ---------------------------------------------------------------------------


async def _open_promotion_center_page(
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
        await page.goto(PROMOTION_CENTER_URL, wait_until="domcontentloaded", timeout=45_000)
        await _settle_page(page)
        await _dismiss_ebk_read_blocking_popups(page)
        # 首页是促销类型目录页，可调整的实体在「已参加促销」tab 里。
        tab = page.locator(_PROMOTION_REGISTER_TAB_SELECTOR).first
        await tab.wait_for(state="visible", timeout=10_000)
        await tab.click()
        await page.wait_for_timeout(1_500)
        await _settle_page(page)
        await _dismiss_ebk_read_blocking_popups(page)
    except Exception as exc:  # noqa: BLE001
        logger.warning("promotion center page open failed (%s)", type(exc).__name__)
        profile_locked = type(exc).__name__ == "CtripBrowserProfileLockedError"
        return {
            "status": "human_required" if profile_locked else "failed",
            "summary": (
                "携程 EBK 浏览器账号正在被其他任务或登录窗口占用，本次不操作促销中心。"
                if profile_locked
                else "打开携程促销中心页失败。"
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
            "summary": "携程 EBK 需要登录或人工验证，本次不操作促销中心。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"page_state": page_state, "browser_write_invoked": False},
            "next_action": "请人工完成登录/验证后重试。",
        }
    if "ebooking.ctrip.com" not in page.url.lower():
        return {
            "status": "page_structure_changed",
            "summary": "促销中心页面跳转到了非预期地址，已停止。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"current_url_host_unexpected": True, "browser_write_invoked": False},
            "next_action": "请人工打开 EBK 促销中心确认。",
        }
    if "促销名称" not in body_text and "已参加促销" not in body_text:
        return {
            "status": "page_structure_changed",
            "summary": "已参加促销列表页结构未识别（缺少「促销名称」表头），已停止。",
            "page": page,
            "page_state": page_state,
            "captured_at": captured_at,
            "evidence": {"promotion_table_missing": True, "browser_write_invoked": False},
            "next_action": "请人工确认 EBK 促销中心页面结构后重试。",
        }
    return {
        "status": "success",
        "summary": "已参加促销列表页已打开。",
        "page": page,
        "page_state": page_state,
        "captured_at": captured_at,
        "evidence": {"page_state": page_state},
    }


async def _open_promotion_edit_page(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    actor_id: str,
    promotion_name: str,
    current_percent: float | None = None,
) -> dict[str, Any]:
    """打开列表 -> 匹配促销 -> 点「修改」-> 核验编辑页。返回 promotion_read。"""
    from datetime import UTC, datetime

    opened = await _open_promotion_center_page(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id=actor_id,
    )
    if opened["status"] != "success":
        return opened
    page: Page = opened["page"]
    rows = await _collect_promotion_rows_stable(page)
    promotions = parse_promotion_rows(rows)
    matched = match_promotion_row(rows, promotion_name)
    if matched is None:
        return {
            "status": "promotion_not_found",
            "summary": (
                f"已参加促销列表里没有找到「{promotion_name}」。当前促销："
                + "、".join(item.get("promotion_name") or "" for item in promotions[:8])
            ),
            "page": page,
            "page_state": opened["page_state"],
            "captured_at": opened["captured_at"],
            "evidence": {"promotion_count": len(promotions), "browser_write_invoked": False},
            "next_action": "请发送「促销列表」查看最新促销名称后重试。",
        }
    matched_promotion = next(
        (
            item
            for item in promotions
            if _campaign_key(item.get("promotion_name"))
            == _campaign_key(matched.get("promotion_name"))
        ),
        None,
    )
    if matched_promotion is not None and matched_promotion.get("multi_rule"):
        return {
            "status": "multi_rule_promotion",
            "summary": (
                f"「{matched_promotion.get('promotion_name')}」包含多条促销规则"
                f"（{matched_promotion.get('discount_text')}），自动调整可能改错规则，已停止。"
            ),
            "page": page,
            "page_state": opened["page_state"],
            "captured_at": opened["captured_at"],
            "evidence": {
                "discount_kind": matched_promotion.get("discount_kind"),
                "browser_write_invoked": False,
            },
            "next_action": "请人工登录 EBK 促销中心调整该促销。",
        }
    try:
        row_key = matched.get("row_key")
        modify_rows = page.locator(
            f'tr:has([he-click="{_PROMOTION_MODIFY_BUTTON_ATTR}"])'
        )
        if row_key and re.fullmatch(r"[\w-]+", str(row_key)):
            row_locator = page.locator(f'tr[data-row-key="{row_key}"]')
        else:
            row_locator = modify_rows.nth(int(matched["index"]))
        modify_button = row_locator.locator(
            f'[he-click="{_PROMOTION_MODIFY_BUTTON_ATTR}"]'
        )
        if not await modify_button.count():
            modify_button = row_locator.get_by_role("button", name="修改", exact=True)
        await modify_button.first.wait_for(state="visible", timeout=8_000)
        # 「修改」可能开新标签页进入编辑页，也可能同页跳转；两种都接住。
        edit_page = await _click_modify_and_capture_edit_page(page, modify_button)
        await edit_page.wait_for_load_state("domcontentloaded")
        await _settle_page(edit_page)
        # 编辑页表单异步渲染：等标记与折扣输入值就绪再读，避免误判结构变化。
        await edit_page.wait_for_function(_EDIT_PAGE_MARKER_READY_JS, timeout=12_000)
        await edit_page.wait_for_function(_DISCOUNT_INPUT_READY_JS, timeout=10_000)
    except Exception as exc:  # noqa: BLE001
        # 渲染超时先分类页面态：编辑页掉登录/风控时必须报 human_required，
        # 不能误判 page_structure_changed 丢失人工介入语义。
        fallback_text = await _safe_visible_text(edit_page)
        fallback_state = _classify_page_state(edit_page.url, fallback_text)
        if fallback_state in {"login_required", "verification_required"}:
            return {
                "status": "human_required",
                "summary": "携程 EBK 需要登录或人工验证，本次不操作促销中心。",
                "page": edit_page,
                "page_state": fallback_state,
                "captured_at": datetime.now(UTC).isoformat(),
                "evidence": {"page_state": fallback_state, "error_type": type(exc).__name__},
                "next_action": "请人工完成登录/验证后重试。",
            }
        return {
            "status": "page_structure_changed",
            "summary": "打开或渲染促销编辑页失败（页面未在预期时间内就绪），已停止。",
            "page": page,
            "page_state": opened["page_state"],
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"error_type": type(exc).__name__, "browser_write_invoked": False},
            "next_action": "请人工打开该促销的编辑页确认后重试。",
        }
    body_text = await _safe_visible_text(edit_page)
    page_state = _classify_page_state(edit_page.url, body_text)
    if page_state in {"login_required", "verification_required"}:
        return {
            "status": "human_required",
            "summary": "携程 EBK 需要登录或人工验证，本次不操作促销中心。",
            "page": edit_page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"page_state": page_state},
            "next_action": "请人工完成登录/验证后重试。",
        }
    if PROMOTION_EDIT_URL_PREFIX not in edit_page.url:
        return {
            "status": "page_structure_changed",
            "summary": "促销编辑页未按预期展示（URL 不是 editPromotion），已停止。",
            "page": edit_page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"edit_page_marker_missing": True},
            "next_action": "请人工确认促销编辑页结构。",
        }
    heading_name = _extract_edit_heading_name(body_text)
    if not _promotion_names_compatible(promotion_name, heading_name):
        return {
            "status": "page_structure_changed",
            "summary": (
                f"促销编辑页名称（{heading_name or '未读取到'}）与目标促销"
                f"「{promotion_name}」不一致，可能点错促销，已停止。"
            ),
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {
                "promotion_name_verified": False,
                "edit_heading_name": heading_name,
            },
            "next_action": "请人工核对促销名称后重试。",
        }
    inputs = await edit_page.evaluate(_COLLECT_EDIT_INPUTS_JS)
    inputs = inputs if isinstance(inputs, list) else []
    discount_input_index = pick_promotion_discount_input(inputs)
    if discount_input_index is None:
        return {
            "status": "discount_input_not_located",
            "summary": "未能在促销编辑页唯一定位折扣输入框（可能含多条规则），已停止。",
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {"discount_input_located": False},
            "next_action": "请人工确认促销编辑页结构；多规则促销请在 EBK 手动调整。",
        }
    current_page_discount_value = _input_value(inputs, discount_input_index)
    if not _discount_percent_matches_expected(current_page_discount_value, current_percent):
        return {
            "status": "needs_review",
            "summary": (
                "促销当前立减与审批预览不一致，可能已被其他操作修改，已停止自动调整。"
            ),
            "page": edit_page,
            "list_page": page,
            "page_state": page_state,
            "captured_at": datetime.now(UTC).isoformat(),
            "evidence": {
                "promotion_name_verified": True,
                "discount_input_located": True,
                "approved_current_discount_percent": current_percent,
                "current_page_discount_percent": _safe_float(current_page_discount_value),
                "browser_write_invoked": False,
            },
            "next_action": "请重新发送促销调整指令，以页面最新立减生成审批预览。",
        }
    return {
        "status": "edit_page_verified",
        "summary": f"已打开「{promotion_name}」的促销编辑页并核验折扣输入框。",
        "page": edit_page,
        "list_page": page,
        "page_state": page_state,
        "captured_at": datetime.now(UTC).isoformat(),
        # 键名与金字塔流程对齐：空跑/执行流读取 verify["edit_read"]。
        # （2026-09-09 线上审批后必崩根因：此键曾误写 promotion_read。）
        "edit_read": {
            "promotion_name": promotion_name,
            "edit_heading_name": heading_name,
            "current_page_discount_input_value": current_page_discount_value,
            "discount_input_index": discount_input_index,
            "current_discount_percent": _safe_float(current_page_discount_value),
            "edit_page_url_prefix": PROMOTION_EDIT_URL_PREFIX,
        },
        "evidence": {
            "promotion_name_verified": True,
            "discount_input_located": True,
        },
    }


def _extract_edit_heading_name(body_text: str) -> str | None:
    """编辑页标题形如「促销中心修改 天天特价 修改 天天特价」，取「修改」后的名称。"""
    match = re.search(r"修改\s*(?P<name>[^\s，。;；]{1,40})", str(body_text or ""))
    return match.group("name").strip() if match else None


def _promotion_names_compatible(target: str, page_heading: str | None) -> bool:
    """编辑页标题短名（「天天特价」）须包含于目标促销名（「基础促销组 天天特价」）。

    防点错促销的安全闸门只收紧不放宽，不做反向包含。
    """
    if not str(target or "") or not str(page_heading or ""):
        return False
    return _campaign_key(page_heading) in _campaign_key(target)


async def _fill_discount_input(
    page: Page,
    discount_input_index: Any,
    target_percent: float,
) -> dict[str, Any]:
    if discount_input_index is None:
        return {"status": "not_located", "filled_value": None, "verified_value": None}
    value = f"{target_percent:g}"
    try:
        handle = page.locator("input, textarea").nth(int(discount_input_index))
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
        if actual is not None and abs(actual - target_percent) < _PERCENT_EPSILON:
            return {"status": "filled", "filled_value": value, "verified_value": readback}
        return {"status": "mismatch", "filled_value": value, "verified_value": readback}
    except Exception:  # noqa: BLE001
        return {"status": "input_failed", "filled_value": None, "verified_value": None}


async def _ensure_promotion_rules_agreed(page: Page) -> dict[str, Any]:
    """勾选编辑页底部「我已经阅读并同意促销规则」（2026-09-09 真机结构）。

    antd 风格复选框：点 label wrapper 切换内部 input；逐个候选点击并
    复查 input.checked，勾不上绝不继续保存。
    """
    try:
        state = await page.evaluate(_PROMOTION_RULE_AGREEMENT_STATE_JS)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "inspect_failed",
            "label_found": False,
            "control_found": False,
            "control_type": None,
            "checked": False,
            "clicked": False,
            "error_type": type(exc).__name__,
        }
    state = state if isinstance(state, dict) else {}

    def result(status: str, *, checked: bool, clicked: bool, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "label_found": bool(state.get("label_found")),
            "control_found": bool(state.get("control_found")),
            "control_type": state.get("control_type"),
            "checked": checked,
            "clicked": clicked,
            **extra,
        }

    if not state.get("label_found"):
        return result("not_located", checked=False, clicked=False)
    if state.get("disabled"):
        return result("disabled", checked=bool(state.get("checked")), clicked=False)
    if state.get("checked"):
        return result("already_checked", checked=True, clicked=False)

    clicked_any = False
    for selector in _AGREEMENT_CLICK_SELECTORS:
        locator = page.locator(selector).first
        try:
            if await locator.count() == 0:
                continue
            await locator.click(timeout=3_000)
            clicked_any = True
        except Exception:  # noqa: BLE001 - 该候选点不动就换下一个
            continue
        await page.wait_for_timeout(400)
        try:
            verified = await page.evaluate(_PROMOTION_RULE_AGREEMENT_STATE_JS)
        except Exception as exc:  # noqa: BLE001
            return result(
                "inspect_failed",
                checked=False,
                clicked=True,
                error_type=type(exc).__name__,
            )
        if isinstance(verified, dict) and verified.get("checked"):
            return {
                "status": "checked",
                "label_found": True,
                "control_found": True,
                "control_type": verified.get("control_type"),
                "checked": True,
                "clicked": True,
            }
    return result("not_checked", checked=False, clicked=clicked_any)


async def _submit_promotion_edit(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {"clicked": False, "left_edit_page": False, "status": "not_submitted"}
    # 优先用携程埋点属性定位底部保存按钮（2026-09-09 真机结构），角色名兜底。
    submit = page.locator('button[he-click="footer_pageSave"]')
    if not await submit.count():
        submit = page.get_by_role("button", name="保 存", exact=True)
    try:
        await submit.wait_for(state="visible", timeout=8_000)
    except Exception:  # noqa: BLE001
        submit = page.get_by_role("button", name="保存", exact=True)
    try:
        await submit.wait_for(state="visible", timeout=3_000)
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
        if "editPromotion" not in current_url:
            result["left_edit_page"] = True
            result["status"] = "left_edit_page"
            return result
    result["status"] = "still_on_edit_page"
    return result


async def _read_promotion_discount(
    service: CtripEbookingBrowserService,
    *,
    platform_account_id: str,
    browser_session_id: str,
    promotion_name: str,
) -> dict[str, Any]:
    read_result = await read_promotion_enrollments_flow(
        service,
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        actor_id="promotion-readback",
    )
    promotions = read_result.get("promotions") or []
    target = _campaign_key(promotion_name)
    for item in promotions:
        name_key = _campaign_key(item.get("promotion_name"))
        if target == name_key or target in name_key or name_key in target:
            return {"status": "success", "promotion": item}
    return {"status": "promotion_not_found", "promotion": {}}


async def _return_to_list_page(page: Page, *, list_page: Page | None = None) -> None:
    """把浏览器留在已参加促销列表：编辑页是独立标签时直接关闭，否则导航回列表。"""
    try:
        if list_page is not None and page is not list_page:
            try:
                await page.close()
            except Exception:  # noqa: BLE001 - 页签可能已被站点关闭
                pass
            return
        if "editPromotion" in page.url:
            await page.goto(PROMOTION_CENTER_URL, wait_until="domcontentloaded", timeout=45_000)
            tab = page.locator(_PROMOTION_REGISTER_TAB_SELECTOR).first
            try:
                await tab.wait_for(state="visible", timeout=8_000)
                await tab.click()
                await _settle_page(page)
            except Exception:  # noqa: BLE001
                logger.warning("failed to switch back to promotion register tab")
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to return to promotion list page (%s)", type(exc).__name__)


async def _safe_screenshot(
    service: CtripEbookingBrowserService, page: Page, platform_account_id: str
) -> str | None:
    try:
        return await service._capture_screenshot(page, platform_account_id)
    except Exception:  # noqa: BLE001
        return None


def _input_value(inputs: list[dict[str, Any]], input_index: int) -> str | None:
    for item in inputs:
        if int(item.get("input_index", -1)) == int(input_index):
            return str(item.get("value") or "") or None
    return None


def _discount_percent_matches_expected(value: Any, expected: float | None) -> bool:
    if expected is None:
        return True
    current = _safe_float(value)
    return current is not None and abs(current - expected) < _PERCENT_EPSILON


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
        "summary": verify.get("summary") or "促销页面步骤未完成。",
        "platform_account_id": platform_account_id,
        "browser_session_id": browser_session_id,
        "data": {"captured_at": verify.get("captured_at")},
        "evidence": {**(verify.get("evidence") or {}), **(extra_evidence or {})},
        "next_action": verify.get("next_action"),
    }


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed
