from __future__ import annotations

import importlib
import unittest
from typing import Any


def _reload_flows():
    return importlib.import_module("app.domain.browser.ebk_period_read_flows")


class _EvaluateOnlyPage:
    """只提供 evaluate 的 stub：测选中态校验 helper 的判定逻辑。"""

    def __init__(self, evaluate_result: Any) -> None:
        self._evaluate_result = evaluate_result
        self.evaluate_calls: list[Any] = []

    async def evaluate(self, script: Any, *args: Any) -> Any:
        self.evaluate_calls.append(args)
        if isinstance(self._evaluate_result, Exception):
            raise self._evaluate_result
        return self._evaluate_result


class CtripPeriodButtonSelectedTests(unittest.TestCase):
    def test_selected_class_variants_pass(self) -> None:
        flows = _reload_flows()
        for class_name in ("btn active", "btn-selected", "btn current", "btn-primary"):
            page = _EvaluateOnlyPage(
                [{"className": class_name, "pressed": ""}]
            )
            self.assertTrue(
                asyncio_run(
                    flows._ctrip_period_button_selected(page, "按周")
                ),
                class_name,
            )

    def test_aria_pressed_true_passes(self) -> None:
        flows = _reload_flows()
        page = _EvaluateOnlyPage([{"className": "btn", "pressed": "true"}])
        self.assertTrue(asyncio_run(flows._ctrip_period_button_selected(page, "按周")))

    def test_unselected_button_fails_closed(self) -> None:
        flows = _reload_flows()
        page = _EvaluateOnlyPage([{"className": "btn", "pressed": "false"}])
        self.assertFalse(asyncio_run(flows._ctrip_period_button_selected(page, "按周")))

    def test_evaluate_error_or_empty_fails_closed(self) -> None:
        flows = _reload_flows()
        page = _EvaluateOnlyPage(RuntimeError("detached"))
        self.assertFalse(asyncio_run(flows._ctrip_period_button_selected(page, "按周")))
        page_empty = _EvaluateOnlyPage([])
        self.assertFalse(asyncio_run(flows._ctrip_period_button_selected(page, "按周")))
        page_none = _EvaluateOnlyPage(None)
        self.assertFalse(asyncio_run(flows._ctrip_period_button_selected(page, "按周")))


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
