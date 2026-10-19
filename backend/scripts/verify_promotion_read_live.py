"""只读真机验证：用 promotion_adjust 模块函数直连 CDP 读已参加促销列表。

验证点：tab 切换、稳定采集、parse_promotion_rows、match_promotion_row、
pick_promotion_discount_input（折扣输入定位需打开编辑页，属 dry-run 段，
由审批后真实执行链验证）。只导航 + 读取，不点击修改、不输入。
"""
from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright

sys.path.insert(0, "backend")

from app.domain.browser.ctrip_promotion_adjust_flow import (  # noqa: E402
    PROMOTION_CENTER_URL,
    _collect_promotion_rows_stable,
    _PROMOTION_REGISTER_TAB_SELECTOR,
    match_promotion_row,
    parse_promotion_rows,
    parse_promotion_discount,
)

CDP_PORT = 9221


async def main() -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{CDP_PORT}", timeout=3_000
        )
        context = browser.contexts[0]
        probe = await context.new_page()
        try:
            await probe.goto(
                PROMOTION_CENTER_URL, wait_until="domcontentloaded", timeout=45_000
            )
            await probe.wait_for_timeout(3_000)
            tab = probe.locator(_PROMOTION_REGISTER_TAB_SELECTOR).first
            await tab.wait_for(state="visible", timeout=10_000)
            await tab.click()
            await probe.wait_for_timeout(1_500)
            rows = await _collect_promotion_rows_stable(probe)
            promotions = parse_promotion_rows(rows)
            print(f"stable rows: {len(rows)}, parsed promotions: {len(promotions)}")
            for item in promotions:
                print(
                    f"- {item['promotion_name']} | {item['discount_text']} | "
                    f"percent={item['discount_percent']} kind={item['discount_kind']}"
                )
            matched = match_promotion_row(rows, "天天特价")
            print("match 天天特价 ->", json.dumps(matched, ensure_ascii=False))
            multi = match_promotion_row(rows, "提前预订")
            print("match 提前预订 ->", json.dumps(multi, ensure_ascii=False))
            for text in ("立减 35%", "连住2天 立减 2% 连住3天 立减 3%", "9折"):
                print("discount parse:", text, "->", parse_promotion_discount(text))
        finally:
            await probe.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
