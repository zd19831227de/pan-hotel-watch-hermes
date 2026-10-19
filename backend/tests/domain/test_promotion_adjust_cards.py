from __future__ import annotations

import unittest
from typing import Any

from app.domain.promotion_adjust.cards import (
    build_promotion_adjust_approval_card,
    build_promotion_adjust_list_card,
    build_promotion_adjust_list_fallback_text,
    build_promotion_adjust_confirmation_card,
    build_promotion_execution_blocked_card,
    build_promotion_execution_result_card,
)


def _promotions() -> list[dict[str, Any]]:
    return [
        {
            "promotion_name": "基础促销组 天天特价",
            "room_scope": "全房型（包括新增）",
            "date_text": "2026年9月6日-2026年9月12日 到期前自动延期",
            "discount_text": "立减 1%",
            "discount_percent": 1.0,
            "discount_kind": "single",
            "multi_rule": False,
        },
        {
            "promotion_name": "场景促销组 提前预订",
            "room_scope": "预露台双卧套房",
            "date_text": "2026年9月3日-2026年9月23日",
            "discount_text": "提前3天预订 立减 35% 提前1天预订 立减 38%",
            "discount_percent": None,
            "discount_kind": "multi",
            "multi_rule": True,
        },
    ]


def _preview_payload() -> dict[str, Any]:
    return {
        "preview_id": 7,
        "status": "preview_ready",
        "connection_mode": "non_direct",
        "can_auto_write_price": True,
        "write_policy_message": "",
        "preview_candidate": {
            "actions": [
                {
                    "promotion_name": "基础促销组 天天特价",
                    "current_discount_percent": 1.0,
                    "proposed_discount_percent": 5.0,
                    "date_text": "2026年9月6日-2026年9月12日",
                    "trigger_reason": "飞书手动促销调整指令。",
                    "risk_flags": ["manual_promotion_adjustment"],
                }
            ]
        },
    }


def _card_text(card: dict[str, Any]) -> str:
    parts = []
    for element in card.get("elements") or []:
        if not isinstance(element, dict):
            continue
        text = element.get("text") if isinstance(element.get("text"), dict) else None
        if text is not None:
            parts.append(str(text.get("content") or ""))
        actions = element.get("actions") if isinstance(element.get("actions"), list) else None
        if actions is not None:
            parts.append(str(actions))
        if element.get("tag") == "table":
            columns = element.get("columns") or []
            parts.append(
                " | ".join(str(column.get("display_name") or "") for column in columns)
            )
            for row in element.get("rows") or []:
                parts.append(
                    " | ".join(str(value) for _key, value in row.items() if value)
                )
    return "\n".join(parts)


def _card_table(card: dict[str, Any]) -> dict[str, Any] | None:
    for element in card.get("elements") or []:
        if isinstance(element, dict) and element.get("tag") == "table":
            return element
    return None


def _card_title(card: dict[str, Any]) -> str:
    header = card.get("header") if isinstance(card.get("header"), dict) else {}
    title = header.get("title") if isinstance(header.get("title"), dict) else {}
    return str(title.get("content") or "")


class PromotionListCardTests(unittest.TestCase):
    def test_all_promotions_rendered_with_discount(self) -> None:
        card = build_promotion_adjust_list_card({"promotions": _promotions()})
        text = _card_text(card)
        self.assertIn("天天特价", text)
        self.assertIn("立减 1%", text)
        self.assertIn("提前预订", text)

    def test_promotions_rendered_as_table_with_columns(self) -> None:
        card = build_promotion_adjust_list_card({"promotions": _promotions()})
        table = _card_table(card)
        self.assertIsNotNone(table)
        column_names = [
            str(column.get("display_name") or "")
            for column in table.get("columns") or []
        ]
        self.assertEqual(column_names, ["促销名称", "立减优惠", "促销时间"])
        self.assertEqual(len(table.get("rows") or []), 2)
        self.assertLessEqual(table.get("page_size"), 10)
        # 首列不冻结：整表一起横向滑动，手机端后两列才不会被挤没空间。
        self.assertFalse(table.get("freeze_first_column"))

    def test_title_is_prominent_with_promotion_count(self) -> None:
        card = build_promotion_adjust_list_card({"promotions": _promotions()})
        title = _card_title(card)
        self.assertIn("活动促销", title)
        self.assertIn("共 2 个", title)

    def test_multi_rule_promotion_gets_manual_note(self) -> None:
        card = build_promotion_adjust_list_card({"promotions": _promotions()})
        text = _card_text(card)
        self.assertIn("多条规则", text)

    def test_empty_read_falls_back_to_summary_without_table(self) -> None:
        card = build_promotion_adjust_list_card(
            {"promotions": [], "summary": "读取失败"}, read_status="failed"
        )
        text = _card_text(card)
        self.assertIn("读取失败", text)
        self.assertIsNone(_card_table(card))

    def test_fallback_text(self) -> None:
        fallback = build_promotion_adjust_list_fallback_text({"promotions": _promotions()})
        self.assertIn("天天特价", fallback)


class PromotionApprovalCardTests(unittest.TestCase):
    def test_buttons_and_workflow(self) -> None:
        card = build_promotion_adjust_approval_card(_preview_payload())
        text = _card_text(card)
        self.assertIn("确认调整促销", text)
        self.assertIn("暂不调整", text)
        self.assertIn("promotion_adjust_approval", text)
        self.assertIn("1%", text)
        self.assertIn("5%", text)

    def test_risk_flags_shown_in_plain_language_not_raw_tokens(self) -> None:
        payload = _preview_payload()
        action = payload["preview_candidate"]["actions"][0]
        action["risk_flags"] = [
            "manual_promotion_adjustment",
            "requires_preflight_revalidation",
            "current_discount_readback_missing",
        ]
        card = build_promotion_adjust_approval_card(payload)
        text = _card_text(card)
        self.assertNotIn("manual_promotion_adjustment", text)
        self.assertNotIn("requires_preflight_revalidation", text)
        self.assertNotIn("current_discount_readback_missing", text)
        self.assertIn("执行前会重新核验", text)

    def test_non_writable_account_has_no_buttons(self) -> None:
        payload = _preview_payload()
        payload["can_auto_write_price"] = False
        payload["connection_mode"] = "direct"
        payload["write_policy_message"] = "当前账号仅允许生成调价建议。"
        card = build_promotion_adjust_approval_card(payload)
        text = _card_text(card)
        self.assertNotIn("确认调整促销", text)
        self.assertIn("当前账号仅允许生成调价建议", text)


class PromotionConfirmationCardTests(unittest.TestCase):
    def test_summary_and_examples(self) -> None:
        card = build_promotion_adjust_confirmation_card(
            {"summary": "促销调整信息不完整。", "examples": ["促销调整 天天特价 立减调到5"]}
        )
        text = _card_text(card)
        self.assertIn("促销调整信息不完整", text)
        self.assertIn("促销调整 天天特价 立减调到5", text)


class PromotionExecutionCardTests(unittest.TestCase):
    def test_result_card_shows_percent_and_readback(self) -> None:
        card = build_promotion_execution_result_card(
            {
                "status": "executed",
                "summary": "「天天特价」立减已调整为 5%。",
                "data": {
                    "promotion_read": {"promotion_name": "基础促销组 天天特价"},
                    "discount_fill_result": {"filled_value": "5", "verified_value": "5"},
                    "readback_result": {"readback_discount_percent": 5.0},
                },
            }
        )
        text = _card_text(card)
        self.assertIn("已执行", text)
        self.assertIn("天天特价", text)
        self.assertIn("5%", text)

    def test_blocked_card_renders_chinese_labels(self) -> None:
        cases = {
            "multi_rule_promotion": "多条促销规则",
            "promotion_not_found": "未找到该促销",
            "discount_input_not_located": "未定位折扣输入框",
            "submit_failed": "提交未生效",
            "pipeline_failed": "执行链异常",
        }
        for status, label in cases.items():
            with self.subTest(status=status):
                card = build_promotion_execution_blocked_card(
                    blocked_step="促销出价写入",
                    blocked_status=status,
                    blocked_summary="测试摘要",
                )
                text = _card_text(card)
                self.assertIn(label, text)
                self.assertNotIn(status, text)

    def test_blocked_card_hides_internal_audit_flags_and_shows_plain_evidence(self) -> None:
        card = build_promotion_execution_blocked_card(
            blocked_step="促销页面核验空跑",
            blocked_status="failed",
            blocked_summary="携程 EBK 促销页面步骤失败。",
            next_action="检查浏览器登录态后重试。",
            evidence={
                "browser_write_invoked": False,
                "ota_submit_invoked": False,
                "execute_triggered": False,
                "connection_mode_checked": True,
                "error_type": "KeyError",
                "page_state": "login_required",
                "current_page_discount_percent": 5.0,
                "approved_current_discount_percent": 5.0,
            },
        )
        text = _card_text(card)
        self.assertNotIn("browser_write_invoked", text)
        self.assertNotIn("ota_submit_invoked", text)
        self.assertNotIn("execute_triggered", text)
        self.assertNotIn("connection_mode_checked", text)
        self.assertNotIn("error_type", text)
        self.assertIn("页面状态：需要重新登录", text)
        self.assertIn("页面当前立减：5%", text)
        self.assertIn("审批时立减：5%", text)


if __name__ == "__main__":
    unittest.main()
