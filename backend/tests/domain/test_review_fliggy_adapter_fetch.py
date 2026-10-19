"""飞猪评价适配器 fetch_history_replies 翻页拉全回归测试。

两处历史缺陷：
- 解析器 reply_content 恒为 None（已回复判定有了但从不提取回复文本），
  fetch_history_replies 的收集条件永远不满足，飞猪历史导入零产出；
- 只取首屏（滚动 3 屏），历史里回复过的中/差评进不了模板样本。

锁定：回复内容提取（含从评价内容中剔除回复段）、ant 分页翻页收集、
review_id 去重、limit 截断、无分页时单页终止。
"""

from __future__ import annotations

import asyncio
import unittest

from app.domain.review_management.fliggy_review_adapter import (
    _FLIGGY_CLICK_PAGE_JS,
    _FLIGGY_CLICK_PENDING_FILTER_JS,
    FliggyReviewAdapter,
)


def _fl_card(nick: str, score: str, content: str, date: str, reply: str | None) -> str:
    """真实卡片行序：[昵称][总分][子维度][内容][日期发表][房型][浏览/有用]
    [店家回复][回复内容][发表于:]（未回复卡无回复区）。"""
    iso = date.replace("年", "-").replace("月", "-").replace("日", "")
    lines = [
        nick, score,
        "位置便利", "4.5", "清洁程度", "4.5", "服务态度", "4.5",
        content,
        f"{date}发表{iso}发表",
        "芮享舒适房-大床",
        "浏览次数 0", "有用次数 0",
    ]
    if reply:
        lines.extend(["店家回复", reply, f"发表于：{iso} 17:50"])
    lines.extend(["修改评价", "异常评价申诉"])
    return "\n".join(lines) + "\n"


def _fl_tail() -> str:
    """页面尾部占位（切卡按总分行引导，尾部不再需要日期锚）。"""
    return "共 10 条数据\n"


class _FakeMouse:
    async def wheel(self, dx: float, dy: float) -> None:
        return None


class _FakePage:
    mouse = _FakeMouse()

    def __init__(self, *, bad: dict | None = None, all_reviews: dict | None = None) -> None:
        self._phases = {
            "bad": bad or {"bodies": [""], "click_ok": []},
            "all": all_reviews or {"bodies": [""], "click_ok": []},
        }
        self.phase = "bad"
        self.page_index = 0
        self.clicks: list[int] = []
        self.all_tab_clicked = False
        self._after_goto = False

    def _current(self) -> dict:
        return self._phases[self.phase]

    async def inner_text(self, selector: str) -> str:
        assert selector == "body"
        pages = self._current()["bodies"]
        return pages[min(self.page_index, len(pages) - 1)]

    async def evaluate(self, script, *args):
        if script == _FLIGGY_CLICK_PAGE_JS:
            target = args[0] if args else None
            self.clicks.append(target)
            ok_list = self._current()["click_ok"]
            ok = ok_list[self.page_index] if self.page_index < len(ok_list) else False
            if ok:
                self.page_index += 1
            return ok
        if "scrollHeight" in script:
            return 100
        if "scrollTo" in script:
            return None
        if "差评" in script:
            self.phase, self.page_index = "bad", 0
            return self._phases["bad"]["bodies"] != [""]
        assert "快速筛选" in script, f"unexpected script: {script[:60]}"
        # goto 重载后的第二次「快速筛选·全部」点击才切回全部视图。
        if self._after_goto:
            self.all_tab_clicked = True
            self.phase, self.page_index = "all", 0
            return self._phases["all"]["bodies"] != [""]
        return True

    async def eval_on_selector_all(self, selector: str, script: str):
        assert selector == "a,li,div,span,button"
        assert "飞猪" in script, f"unexpected tab script: {script[:60]}"
        return True

    async def goto(self, url: str, **kwargs) -> None:
        # 第二轮通过重新加载列表页回到「全部」快速筛选视图。
        self._after_goto = True

    async def wait_for_timeout(self, ms: float) -> None:
        return None


class FliggyFetchHistoryPaginationTests(unittest.TestCase):
    def _run(self, page: _FakePage, limit: int):
        adapter = FliggyReviewAdapter()

        async def fake_with_review_page(fn, **kwargs):
            return await fn(page)

        adapter._with_review_page = fake_with_review_page
        return asyncio.run(adapter.fetch_history_replies(limit=limit))

    def test_bad_filter_round_first_then_all_round(self) -> None:
        body_bad = (
            _fl_card("王二", "1", "太差了", "2026年7月1日", "非常抱歉，已整改")
            + _fl_card("张三", "5", "很好", "2026年9月16日", "感谢入住")
            + _fl_tail()
        )
        body_all = (
            "某公寓酒店\n快速筛选\n全部\n待回复\n"
            + _fl_card("张三", "5", "很好", "2026年9月16日", "感谢入住")
            + _fl_card("李四", "4", "不错", "2026年9月15日", "感谢反馈")
            + _fl_tail()
        )
        page = _FakePage(
            bad={"bodies": [body_bad], "click_ok": [False]},
            all_reviews={"bodies": [body_all], "click_ok": [False]},
        )

        history = self._run(page, limit=10)

        self.assertTrue(page.all_tab_clicked)
        self.assertEqual(len(history), 3)
        by_score = {h.score: h.reply_content for h in history}
        self.assertEqual(by_score.get(1.0), "非常抱歉，已整改")
        self.assertEqual(by_score.get(5.0), "感谢入住")
        self.assertEqual(by_score.get(4.0), "感谢反馈")
        # 回复段不能混进评价内容。
        for h in history:
            self.assertNotIn("店家回复", h.content or "")
            self.assertNotIn("芮享舒适房", h.content or "")

    def test_stops_once_limit_reached_without_all_round(self) -> None:
        body_bad = (
            _fl_card("王二", "1", "太差了", "2026年7月1日", "非常抱歉，已整改")
            + _fl_card("王三", "2", "很吵", "2026年7月2日", "深表歉意")
            + _fl_tail()
        )
        page = _FakePage(bad={"bodies": [body_bad], "click_ok": [True]})

        history = self._run(page, limit=2)

        self.assertEqual(len(history), 2)
        self.assertFalse(page.all_tab_clicked)
        self.assertEqual(page.clicks, [])


class FliggySubmitPendingFilterTests(unittest.TestCase):
    """飞猪 submit 待回复筛选定位回归。

    页面 DOM 实测（2026-09-19 CDP 探针）：「待回复(62)」与「AI帮你写」合在
    一个 ant-tag 里（innerText="AI帮你写\\n待回复(62)"，默认已选中），页面
    上独立的 startsWith('待回复') 元素只有「待回复差评(0)」。旧
    startsWith('待回复') 点中差评筛选 → 0 卡 → mark_card_js null → 空 batch
    → 不推卡（飞猪 62 条待回复却完全不处理的根因）。
    """

    def test_filter_js_does_not_match_pending_bad_reviews(self) -> None:
        # 不得用 startsWith('待回复')——会命中「待回复差评(0)」
        self.assertNotIn("startsWith('待回复')", _FLIGGY_CLICK_PENDING_FILTER_JS)
        # 必须排除差评筛选
        self.assertIn("差评", _FLIGGY_CLICK_PENDING_FILTER_JS)
        # 必须匹配「待回复(」带左括号，精确到待回复计数 tag
        self.assertIn("待回复(", _FLIGGY_CLICK_PENDING_FILTER_JS)

    def test_filter_js_skips_already_checked(self) -> None:
        # 默认已选中的待回复 tag 不得再点（点了会 toggle off）
        self.assertIn("checked", _FLIGGY_CLICK_PENDING_FILTER_JS)


class SubmitPendingRepliesCircuitBreakerTests(unittest.TestCase):
    """2026-10-08 巡检 F3：浏览器掉线时批量回复连续页面失败需熔断（同携程口径）。"""

    def test_aborts_after_consecutive_page_failures(self) -> None:
        from app.domain.review_management.adapters import ReviewAdapterError

        adapter = FliggyReviewAdapter()

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
