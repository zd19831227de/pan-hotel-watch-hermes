"""携程评价适配器 fetch_reviews 快照构建回归测试。

2026-09-03 回归（9f2746b）：pending 对账改造在 fetch_reviews 返回快照时引用了
未定义的 body_text，页面读取成功后直接 NameError，携程抓取全量失败
（service 层兜底成「评价抓取异常（NameError）」）。本模块锁定：
- fetch_reviews 成功路径返回快照（不再 NameError）；
- pending_total 按页面「待回复(N)」解析；
- 已回复/未回复卡片区分不受影响。
"""

from __future__ import annotations

import asyncio
import unittest

from app.domain.review_management.ctrip_review_adapter import (
    _CARD_JS,
    _CLICK_PAGE_JS,
    _HOTEL_NAME_JS,
    CtripReviewAdapter,
)

_BODY_TEXT = """
全部点评
某某酒店
待回复 (1)
"""


class _FakePage:
    """evaluate 按 JS 片段返回固定页面数据。"""

    @staticmethod
    async def evaluate(script, *args):
        if script == _HOTEL_NAME_JS:
            return "某某酒店"
        if script == _CARD_JS:
            return [
                "\n".join(
                    [
                        "匿名用户",
                        "5 超棒",
                        "房间干净",
                        "发表于:2026年9月2日 10:00",
                        "酒店回复内容:感谢入住",
                        "回复员工:前台",
                    ]
                ),
                "\n".join(
                    [
                        "匿名用户",
                        "4 很好",
                        "位置不错",
                        "发表于:2026年9月1日 09:30",
                    ]
                ),
            ]
        return _BODY_TEXT


class CtripFetchSnapshotTests(unittest.TestCase):
    def test_fetch_reviews_returns_snapshot_with_pending_total(self) -> None:
        adapter = CtripReviewAdapter()

        async def fake_with_review_page(fn, **kwargs):
            return await fn(_FakePage())

        adapter._with_review_page = fake_with_review_page
        snapshot = asyncio.run(adapter.fetch_reviews())
        self.assertEqual(snapshot.hotel_id, "某某酒店")
        self.assertEqual(snapshot.pending_total, 1)
        statuses = {item.reply_status for item in snapshot.reviews}
        self.assertEqual(statuses, {"replied", "pending"})


def _card(nick: str, score_line: str, content: str, day: str, reply: str | None) -> str:
    lines = [nick, score_line, content, f"发表于:{day} 10:0{nick[-1]}"]
    if reply:
        lines.append(f"酒店回复内容:{reply}")
        lines.append("回复员工:前台")
    return "\n".join(lines)


class CtripBadCardScoreParseTests(unittest.TestCase):
    """差评卡片总分是独立小数行（2.7/0.6），无「N 很差」词形；
    此前解析为 None 被归成中评，差评样本全军覆没。"""

    def test_decimal_score_line_parses(self) -> None:
        from app.domain.review_management.ctrip_review_adapter import (
            _parse_ctrip_review_card,
        )
        from app.domain.review_management.types import classify_review_score

        card = _card("M13***", "2.7", "卫生很差 沙发上有头发", "2026年8月1日", "抱歉给您带来不便")
        parsed = _parse_ctrip_review_card(card)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.score, 2.7)
        self.assertEqual(classify_review_score(parsed.score), "bad")

    def test_integer_word_score(self) -> None:
        from app.domain.review_management.ctrip_review_adapter import (
            _parse_ctrip_review_card,
        )

        card = _card("匿名用户", "5 超棒", "很好", "2026年9月16日", "感谢入住")
        parsed = _parse_ctrip_review_card(card)
        self.assertEqual(parsed.score, 5.0)


class _PagingFakePage:
    """双轮页面模拟：先「差评」筛选轮（phase=bad），后「全部点评」轮
    （phase=all）；按页返回卡片，翻页按当前 phase 预设决定成败。"""

    def __init__(self, *, bad: dict | None = None, all_reviews: dict | None = None,
                 bad_tab_ok: bool = True) -> None:
        self._phases = {
            "bad": bad or {"cards": [[]], "click_ok": []},
            "all": all_reviews or {"cards": [[]], "click_ok": []},
        }
        self._bad_tab_ok = bad_tab_ok
        self.phase = "bad"
        self.page_index = 0
        self.clicks: list[int] = []
        self.all_tab_clicked = False

    def _current(self) -> dict:
        return self._phases[self.phase]

    async def evaluate(self, script, *args):
        if script == _HOTEL_NAME_JS:
            return "某某酒店"
        if script == _CARD_JS:
            pages = self._current()["cards"]
            return pages[min(self.page_index, len(pages) - 1)]
        if script == _CLICK_PAGE_JS:
            target = args[0] if args else None
            self.clicks.append(target)
            ok_list = self._current()["click_ok"]
            ok = ok_list[self.page_index] if self.page_index < len(ok_list) else False
            if ok:
                self.page_index += 1
            return ok
        raise AssertionError(f"unexpected script: {script[:60]}")

    async def eval_on_selector_all(self, selector: str, script: str):
        assert selector == "a,li,div,span,button"
        assert "差评" in script
        if self._bad_tab_ok:
            self.phase = "bad"
            self.page_index = 0
            return True
        return False

    async def goto(self, url: str, **kwargs) -> None:
        # 第二轮通过重新加载列表页回到「全部点评」第 1 页。
        self.all_tab_clicked = True
        self.phase = "all"
        self.page_index = 0

    async def wait_for_timeout(self, ms: float) -> None:
        return None


class CtripFetchHistoryPaginationTests(unittest.TestCase):
    def _adapter_with(self, page: _PagingFakePage) -> CtripReviewAdapter:
        adapter = CtripReviewAdapter()

        async def fake_with_review_page(fn, **kwargs):
            return await fn(page)

        adapter._with_review_page = fake_with_review_page
        return adapter

    def test_bad_filter_round_first_then_all_round_and_dedupes(self) -> None:
        bad_page = [
            _card("M13***", "2 较差", "太吵", "2026年8月1日", "抱歉给您带来不便"),
            # 差评轮与全部轮重复的评价（跨轮去重）。
            _card("匿名用户", "5 超棒", "很好", "2026年9月16日", "感谢入住"),
        ]
        all_page = [
            _card("匿名用户", "5 超棒", "很好", "2026年9月16日", "感谢入住"),
            _card("M12***", "4 很好", "不错", "2026年9月15日", "感谢反馈"),
        ]
        fake = _PagingFakePage(
            bad={"cards": [bad_page], "click_ok": [False]},
            all_reviews={"cards": [all_page], "click_ok": [False]},
        )
        adapter = self._adapter_with(fake)

        history = asyncio.run(adapter.fetch_history_replies(limit=10))

        self.assertTrue(fake.all_tab_clicked)
        self.assertEqual(len(history), 3)
        self.assertEqual([h.score for h in history], [2.0, 5.0, 4.0])
        self.assertTrue(all(h.reply_content for h in history))

    def test_stops_once_limit_reached_without_all_round(self) -> None:
        bad_page = [_card(f"M1{i}***", "1 很差", f"内容{i}", "2026年8月1{i}日", "抱歉") for i in range(3)]
        fake = _PagingFakePage(
            bad={"cards": [bad_page], "click_ok": [True]},
        )
        adapter = self._adapter_with(fake)

        history = asyncio.run(adapter.fetch_history_replies(limit=2))

        self.assertEqual(len(history), 2)
        # 差评轮已收满 limit，不应再切回全部点评轮。
        self.assertFalse(fake.all_tab_clicked)
        self.assertEqual(fake.clicks, [])


class SubmitPendingRepliesCircuitBreakerTests(unittest.TestCase):
    """2026-10-08 巡检 F3：浏览器掉线/假死时批量回复需熔断。

    每条回复一个独立页面会话（goto networkidle 40s）：浏览器不在线时
    max_count*3=60 次全部撞墙约 40 分钟且零提交。连续 3 次页面会话失败
    即放弃本轮，失败行如实带回（日报卡转橙提醒）。
    """

    def test_aborts_after_consecutive_page_failures(self) -> None:
        from app.domain.review_management.adapters import ReviewAdapterError

        adapter = CtripReviewAdapter()

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

    def test_page_failure_counter_resets_after_success(self) -> None:
        # 间歇单次失败不累计：成功会话后计数清零，偶发抖动不触发熔断。
        from app.domain.review_management.adapters import ReviewAdapterError

        adapter = CtripReviewAdapter()
        calls = {"n": 0}

        async def flaky_page(fn, **kwargs):
            calls["n"] += 1
            if calls["n"] % 2 == 1:
                raise ReviewAdapterError("评价页访问失败（TimeoutError）")
            return None  # 页面正常但没有待回复卡片 → 正常终止

        adapter._with_review_page = flaky_page
        rows = asyncio.run(
            adapter.submit_pending_replies(lambda info: "感谢您的反馈")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
