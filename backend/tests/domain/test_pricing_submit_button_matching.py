from __future__ import annotations

import unittest

from app.domain.browser.pricing_submit_buttons import (
    choose_submit_button,
    resolve_pricing_button_config,
)

# B5.1 实测按钮全集。
FLIGGY_BUTTONS = ["增加", "提交并继续", "提交并查看", "取 消"]
MEITUAN_BUTTONS = [
    "请选择",
    "取消选择",
    "反选",
    "卖价(佣金率)",
    "预览修改结果",
    "提交并前往日历页",
    "提交并继续修改",
]


def _buttons(texts: list[str]) -> list[dict]:
    return [{"text": text, "selector": f"#btn-{i}"} for i, text in enumerate(texts)]


class ChooseSubmitButtonTests(unittest.TestCase):
    """B6 — 完整文案白名单优先的提交按钮匹配。"""

    def test_fliggy_prefers_configured_target_label(self) -> None:
        choice = choose_submit_button(
            _buttons(FLIGGY_BUTTONS),
            submit_labels=["提交并继续", "提交并查看"],
            non_submit_labels=["取 消", "增加", "取消"],
            preferred="提交并继续",
        )
        self.assertIsNotNone(choice)
        self.assertEqual(choice.label, "提交并继续")
        self.assertEqual(choice.button["text"], "提交并继续")

    def test_meituan_prefers_configured_target_label(self) -> None:
        choice = choose_submit_button(
            _buttons(MEITUAN_BUTTONS),
            submit_labels=["提交并前往日历页", "提交并继续修改"],
            non_submit_labels=["预览修改结果", "卖价(佣金率)", "取消选择"],
            preferred="提交并继续修改",
        )
        self.assertIsNotNone(choice)
        self.assertEqual(choice.label, "提交并继续修改")

    def test_non_submit_labels_never_match_even_with_prefix_fallback(self) -> None:
        # 「预览修改结果」不以提交开头，这里验证已知非提交词被显式排除
        buttons = _buttons(["预览修改结果", "卖价(佣金率)"])
        choice = choose_submit_button(
            buttons,
            submit_labels=["提交并前往日历页", "提交并继续修改"],
            non_submit_labels=["预览修改结果", "卖价(佣金率)"],
            preferred=None,
        )
        self.assertIsNone(choice)

    def test_space_inside_label_is_normalized(self) -> None:
        """「取 消」去空白后等于「取消」，必须被排除，不得进入前缀兜底。"""
        buttons = _buttons(["取 消", "提交并继续"])
        choice = choose_submit_button(
            buttons,
            submit_labels=["提交并继续"],
            non_submit_labels=["取 消", "取消"],
            preferred="提交并继续",
        )
        self.assertIsNotNone(choice)
        self.assertEqual(choice.label, "提交并继续")

    def test_multiple_submit_buttons_without_preferred_returns_none(self) -> None:
        """无 preferred 且命中多个提交按钮：不猜（不做最靠下/最右侧猜测）。"""
        choice = choose_submit_button(
            _buttons(FLIGGY_BUTTONS),
            submit_labels=["提交并继续", "提交并查看"],
            non_submit_labels=["取 消", "增加"],
            preferred=None,
        )
        self.assertIsNone(choice)

    def test_prefix_fallback_picks_unique_submit_prefixed_button(self) -> None:
        choice = choose_submit_button(
            _buttons(["保存", "提交修改"]),
            submit_labels=["提交并前往日历页"],
            non_submit_labels=[],
            preferred=None,
        )
        self.assertIsNotNone(choice)
        self.assertEqual(choice.label, "提交修改")

    def test_missing_preferred_button_returns_none_not_fallback(self) -> None:
        """配置的目标按钮不在页面上：诚实失败，不退回其它提交按钮。"""
        choice = choose_submit_button(
            _buttons(["提交并查看", "取 消"]),
            submit_labels=["提交并继续", "提交并查看"],
            non_submit_labels=["取 消"],
            preferred="提交并继续",
        )
        self.assertIsNone(choice)

    def test_no_submit_button_returns_none(self) -> None:
        self.assertIsNone(
            choose_submit_button(
                _buttons(["增加", "取 消"]),
                submit_labels=["提交并继续"],
                non_submit_labels=["取 消", "增加"],
                preferred="提交并继续",
            )
        )


class PricingButtonConfigTests(unittest.TestCase):
    """B6 — 按钮词表走 page_health_config.json 热更新。"""

    def test_fliggy_config_has_full_labels_and_preferred(self) -> None:
        config = resolve_pricing_button_config("fliggy")
        self.assertIn("提交并继续", config.submit_labels)
        self.assertIn("提交并查看", config.submit_labels)
        self.assertIn("取 消", config.non_submit_labels)
        self.assertEqual(config.preferred_label, "提交并继续")

    def test_meituan_config_has_full_labels_and_preview(self) -> None:
        config = resolve_pricing_button_config("meituan")
        self.assertIn("提交并前往日历页", config.submit_labels)
        self.assertIn("提交并继续修改", config.submit_labels)
        self.assertIn("预览修改结果", config.non_submit_labels)
        self.assertEqual(config.preferred_label, "提交并继续修改")
        self.assertEqual(config.preview_label, "预览修改结果")

    def test_fliggy_has_no_preview_label(self) -> None:
        config = resolve_pricing_button_config("fliggy")
        self.assertIsNone(config.preview_label)


if __name__ == "__main__":
    unittest.main()
