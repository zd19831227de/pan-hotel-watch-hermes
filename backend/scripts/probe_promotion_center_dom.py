"""只读探针 v4：列表行优惠值 + 折扣输入框完整语境（不提交）。"""
from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright

PROMOTION_CENTER_URL = "https://ebooking.ctrip.com/promotion/promotionCenter?microJump=true"
CDP_PORT = 9221
TARGET_PROMOTION = "门店新客立减"

ROW_CELLS_JS = """() => {
  const rows = Array.from(document.querySelectorAll('table tbody tr')).map(tr => ({
    row_key: tr.getAttribute('data-row-key') || '',
    cells: Array.from(tr.querySelectorAll('td'))
      .map(td => (td.innerText || '').replace(/\\s+/g, ' ').trim()).slice(0, 8),
  }));
  return rows;
}"""

DISCOUNT_CONTEXT_JS = """() => {
  const out = [];
  const all = Array.from(document.querySelectorAll('input'));
  for (let i = 0; i < all.length; i += 1) {
    const inp = all[i];
    const type = (inp.getAttribute('type') || '').toLowerCase();
    if (['checkbox', 'radio', 'hidden'].includes(type)) continue;
    const rect = inp.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    let el = inp.closest('div');
    let section = '';
    for (let k = 0; k < 6 && el; k += 1) {
      section = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300);
      if (section.length > 40) break;
      el = el.parentElement;
    }
    out.push({
      input_index: i,
      value: String(inp.value || '').slice(0, 30),
      placeholder: inp.getAttribute('placeholder') || '',
      section: section,
    });
  }
  return out.filter(x => x.value || x.placeholder).slice(0, 30);
}"""


async def main() -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{CDP_PORT}", timeout=3_000
        )
        context = browser.contexts[0]
        probe = await context.new_page()
        existing = {id(p) for p in context.pages}
        try:
            await probe.goto(PROMOTION_CENTER_URL, wait_until="domcontentloaded", timeout=45_000)
            await probe.wait_for_timeout(3_000)
            tab = probe.locator('[he-click="domestic_promotionRegister"]').first
            await tab.click()
            await probe.wait_for_timeout(3_500)
            rows = await probe.evaluate(ROW_CELLS_JS)
            for r in rows:
                print("ROW", r["row_key"], "|", r["cells"][0] if r["cells"] else "", "|", r["cells"][4] if len(r["cells"]) > 4 else "")
            target = next(
                (r for r in rows if TARGET_PROMOTION in str(r.get("cells") or [""])[0:1] or TARGET_PROMOTION in (r["cells"][0] if r["cells"] else "")),
                None,
            )
            print("target:", json.dumps(target, ensure_ascii=False))
            if target is None:
                return 1
            row = probe.locator(f'tr[data-row-key="{target[chr(34) if False else "row_key"]}"]')
            modify = row.locator('[he-click="pc_promotion_list_modify_click"]')
            if not await modify.count():
                modify = row.get_by_role("button", name="修改", exact=True)
            await modify.first.click()
            edit_page = None
            for _ in range(30):
                await probe.wait_for_timeout(500)
                for candidate in context.pages:
                    if id(candidate) in existing and candidate is not probe:
                        continue
                    try:
                        if "editPromotion" in candidate.url:
                            edit_page = candidate
                            break
                    except Exception:
                        continue
                if edit_page is not None:
                    break
            if edit_page is None:
                print("no edit page; url:", probe.url)
                return 1
            await edit_page.wait_for_load_state("domcontentloaded")
            await edit_page.wait_for_timeout(4_000)
            print("edit url:", edit_page.url)
            ctx_inputs = await edit_page.evaluate(DISCOUNT_CONTEXT_JS)
            print("=== INPUT CONTEXT ===")
            print(json.dumps(ctx_inputs, ensure_ascii=False, indent=1)[:10_000])
        finally:
            for p in list(context.pages):
                if id(p) in existing:
                    continue
                try:
                    await p.close()
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
