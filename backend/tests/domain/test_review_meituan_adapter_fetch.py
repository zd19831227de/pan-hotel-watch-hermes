"""美团评价适配器 fetch_history_replies 翻页拉全回归测试。

此前只取 iframe 默认列表第一页（约 10 条近期评价，几乎全是好评已回复），
店铺历史里回复过的中/差评永远进不了模板样本。锁定：
- 默认列表翻页（mtd-pager 页码点击）逐页收集已回复评价；
- review_id 去重、limit 截断、无下一页时终止；
- 不再依赖 fetch_reviews（历史导入不触发「未回复」tab 切换）。
"""

from __future__ import annotations

import asyncio
import unittest

from app.domain.review_management.meituan_review_adapter import (
    _MT_CLICK_PAGE_JS,
    MeituanReviewAdapter,
)


def _mt_card(nick: str, score_line: str, content: str, date: str, reply: str | None) -> str:
    lines = [nick, "评价举报", score_line, "【预订】", "设施 5 卫生 5", content, f"评价时间：{date}"]
    if reply:
        lines.extend([f"酒店回复内容：{reply}", f"回复时间：{date} 12:00"])
    else:
        lines.extend(["酒店回复内容：", "发表回复"])
    return "\n".join(lines)


def _mt_body(cards: list[str]) -> str:
    # 头部统计区按解析口径截断（含店名行，供 hotel_id 提取），正文为卡片分块。
    return "立瑞酒店（云州澜江路店）\n添加回复模板下载列表内容\n" + "\n".join(cards)


class _FakeFrame:
    """双轮 iframe 模拟：先「差评」tab 轮（phase=bad），后「全部评价」轮
    （phase=all）；按页返回 body，翻页/tab 点击按当前 phase 预设决定成败。"""

    def __init__(self, *, bad: dict | None = None, all_reviews: dict | None = None) -> None:
        self._phases = {
            "bad": bad or {"bodies": [""], "click_ok": []},
            "all": all_reviews or {"bodies": [""], "click_ok": []},
        }
        self.phase = "bad"
        self.page_index = 0
        self.clicks: list[int] = []
        self.all_tab_clicked = False

    def _current(self) -> dict:
        return self._phases[self.phase]

    async def inner_text(self, selector: str) -> str:
        assert selector == "body"
        pages = self._current()["bodies"]
        return pages[min(self.page_index, len(pages) - 1)]

    async def evaluate(self, script, *args):
        if script == _MT_CLICK_PAGE_JS:
            target = args[0] if args else None
            self.clicks.append(target)
            ok_list = self._current()["click_ok"]
            ok = ok_list[self.page_index] if self.page_index < len(ok_list) else False
            if ok:
                self.page_index += 1
            return ok
        assert "mtd-tabs-item" in script, f"unexpected script: {script[:60]}"
        if "差评" in script:
            self.phase, self.page_index = "bad", 0
            return self._phases["bad"]["bodies"] != [""]
        assert "全部评价" in script
        self.all_tab_clicked = True
        self.phase, self.page_index = "all", 0
        return self._phases["all"]["bodies"] != [""]


class MeituanFetchHistoryPaginationTests(unittest.TestCase):
    def _run(self, frame: _FakeFrame, limit: int):
        adapter = MeituanReviewAdapter()

        async def fake_with_review_page(fn, **kwargs):
            return await fn(page=None, target=frame)

        adapter._with_review_page = fake_with_review_page
        return asyncio.run(adapter.fetch_history_replies(limit=limit))

    def test_decimal_score_bad_card_classifies_as_bad(self) -> None:
        """差评卡总分是独立小数行（无「很差」词形）：此前 score=None 被归
        中评，差评模板永远出不来（立瑞美团 4 条已回复差评全被错分）。"""
        from app.domain.review_management.meituan_review_adapter import (
            _parse_meituan_reviews,
        )
        from app.domain.review_management.types import classify_review_score

        body = _mt_body([
            _mt_card("王**", "2.0", "卫生差", "2026/06/19", "深感歉意，已整改"),
        ])
        items = _parse_meituan_reviews(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].score, 2.0)
        self.assertEqual(classify_review_score(items[0].score), "bad")
        self.assertNotIn("2.0", items[0].content or "")
        self.assertTrue(items[0].reply_content)


    def test_bad_tab_round_first_then_all_round_and_dedupes(self) -> None:
        bad_page = _mt_body([
            _mt_card("王**", "2.0 差评", "隔音差", "2026/08/01", "抱歉给您带来不便"),
            _mt_card("张**", "5.0 很好", "住得舒适", "2026/09/16", "感谢入住"),
        ])
        all_page = _mt_body([
            _mt_card("张**", "5.0 很好", "住得舒适", "2026/09/16", "感谢入住"),
            _mt_card("李**", "4.0 不错", "还行", "2026/09/15", "感谢反馈"),
        ])
        frame = _FakeFrame(
            bad={"bodies": [bad_page], "click_ok": [False]},
            all_reviews={"bodies": [all_page], "click_ok": [False]},
        )

        history = self._run(frame, limit=10)

        self.assertTrue(frame.all_tab_clicked)
        self.assertEqual(len(history), 3)
        self.assertEqual([h.score for h in history], [2.0, 5.0, 4.0])
        self.assertTrue(all(h.reply_content for h in history))
        # 真实店名必须落库：review_items 唯一键含 hotel_id，占位值会让同集团
        # 模板化回复在多店铺落库时撞唯一键（立瑞/惊黎曾 500）。
        self.assertTrue(all(h.hotel_id == "立瑞酒店（云州澜江路店）" for h in history))

    def test_stops_once_limit_reached_without_all_round(self) -> None:
        bad_page = _mt_body([
            _mt_card(f"客{'甲乙丙'[i]}**", "1.0 差评", f"内容{i}", f"2026/08/0{i}", "抱歉")
            for i in range(3)
        ])
        frame = _FakeFrame(bad={"bodies": [bad_page], "click_ok": [True]})

        history = self._run(frame, limit=2)

        self.assertEqual(len(history), 2)
        self.assertFalse(frame.all_tab_clicked)
        self.assertEqual(frame.clicks, [])


class SubmitPendingRepliesCircuitBreakerTests(unittest.TestCase):
    """2026-10-08 巡检 F3：浏览器掉线时批量回复连续页面失败需熔断（同携程口径）。"""

    def test_aborts_after_consecutive_page_failures(self) -> None:
        from app.domain.review_management.adapters import ReviewAdapterError

        adapter = MeituanReviewAdapter()

        async def failing_page(fn, **kwargs):
            raise ReviewAdapterError(
                "评价页访问失败（TimeoutError），请确认平台浏览器在线后重试。"
            )

        adapter._with_review_page = failing_page
        rows = asyncio.run(
            adapter.submit_pending_replies(lambda info: "感谢您的反馈")
        )
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(not row.get("ok") for row in rows))


if __name__ == "__main__":
    unittest.main()
