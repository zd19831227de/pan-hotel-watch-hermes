from __future__ import annotations

import json
import unittest

from app.domain.feishu_qa_cards import (
    QA_CARD_TEMPLATE_IDS,
    build_qa_reply_card,
    classify_qa_content,
    register_qa_card_template,
    render_qa_card_elements,
)


class QaCardContentClassificationTests(unittest.TestCase):
    def test_classify_data_report_for_metric_dense_text(self) -> None:
        text = "静里上周经营情况：\n出租率 82%，均价 ¥431，间夜 56，订单 40 单。"
        self.assertEqual(classify_qa_content(text), "qa_data_report")

    def test_classify_data_report_for_markdown_table(self) -> None:
        text = "| 指标 | 本周 | 上周 |\n| --- | --- | --- |\n| 出租率 | 82% | 78% |"
        self.assertEqual(classify_qa_content(text), "qa_data_report")

    def test_classify_price_compare_for_competitor_prices(self) -> None:
        text = "竞品价格对比：A酒店商务房 ¥420，本店同房型 ¥431，B酒店 ¥399。"
        self.assertEqual(classify_qa_content(text), "qa_price_compare")

    def test_classify_analysis_for_structured_sections(self) -> None:
        text = "**结论**：本周经营平稳。\n**关键发现**：竞品降价。\n**建议行动**：关注明日价格。"
        self.assertEqual(classify_qa_content(text), "qa_analysis")

    def test_classify_alert_for_failure_text(self) -> None:
        text = "读取失败：携程数据中心页面异常，请稍后重试。"
        self.assertEqual(classify_qa_content(text), "qa_alert")

    def test_classify_plain_for_short_answer(self) -> None:
        self.assertEqual(classify_qa_content("好的，已记录您的反馈。"), "qa_plain")
        self.assertEqual(classify_qa_content(""), "qa_plain")


class QaCardTemplateRenderTests(unittest.TestCase):
    def test_analysis_card_uses_mobile_section_icons_and_spacing(self) -> None:
        elements = render_qa_card_elements(
            "qa_analysis",
            "**结论**：本周经营平稳。\n**关键发现**：竞品降价。\n**建议行动**：关注明日价格。",
        )
        content = "\n".join(item.get("content", "") for item in elements if item.get("tag") == "markdown")
        self.assertIn("✅", content)
        self.assertIn("🔎", content)
        self.assertIn("👉", content)
        self.assertIn("\n", content)

    def test_data_report_uses_contextual_icon_without_repeating_chart_icon(self) -> None:
        elements = render_qa_card_elements("qa_data_report", "今日经营：\n出租率：82%\n均价：431 元")
        content = "\n".join(item.get("content", "") for item in elements if item.get("tag") == "markdown")
        self.assertIn("🧾", content)
        self.assertNotIn("📊", content)
        self.assertTrue(any(item.get("tag") == "table" for item in elements))
    def test_data_report_renders_table_element_from_markdown_table(self) -> None:
        text = "本周数据如下：\n| 指标 | 本周 | 上周 |\n| --- | --- | --- |\n| 出租率 | 82% | 78% |"
        elements = render_qa_card_elements("qa_data_report", text)
        tables = [element for element in elements if element.get("tag") == "table"]
        self.assertTrue(tables)
        table = tables[0]
        column_names = [column["name"] for column in table["columns"]]
        self.assertEqual(column_names, ["指标", "本周", "上周"])
        self.assertEqual(table["rows"][0], {"指标": "出租率", "本周": "82%", "上周": "78%"})

    def test_data_report_wide_table_freezes_key_column_for_mobile_scroll(self) -> None:
        text = (
            "| 指标 | 我的酒店 | 竞争圈平均 | 较昨日 | 较上周 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 列表曝光量 | 1,669 | 1,245 | +12% | +8% |"
        )

        elements = render_qa_card_elements("qa_data_report", text)
        table = next(element for element in elements if element.get("tag") == "table")

        self.assertTrue(table.get("freeze_first_column"))
        self.assertEqual(table["columns"][0]["width"], "132px")
        self.assertEqual(table["columns"][1]["width"], "120px")
        self.assertEqual(table["columns"][0]["data_type"], "lark_md")

    def test_data_report_two_column_table_is_wider_than_mobile_card(self) -> None:
        text = "| field | value |\n| --- | --- |\n| service | a long value that must remain visible |"

        elements = render_qa_card_elements("qa_data_report", text)
        table = next(element for element in elements if element.get("tag") == "table")

        total_width = sum(int(column["width"].removesuffix("px")) for column in table["columns"])
        self.assertGreaterEqual(total_width, 480)

    def test_data_report_caps_native_tables_and_keeps_overflow_content_readable(self) -> None:
        table = "| 指标 | 数值 |\n| --- | --- |\n| 出租率 | 82% |"
        text = "\n\n".join(f"第 {index + 1} 组\n{table}" for index in range(6))

        elements = render_qa_card_elements("qa_data_report", text)

        self.assertLessEqual(sum(item.get("tag") == "table" for item in elements), 5)
        markdown = "\n".join(item.get("content", "") for item in elements if item.get("tag") == "markdown")
        self.assertIn("第 6 组", markdown)

    def test_data_report_builds_field_table_from_label_value_lines(self) -> None:
        text = "今日经营速览：\n出租率：82%\n均价：431 元\n间夜：56"
        elements = render_qa_card_elements("qa_data_report", text)
        tables = [element for element in elements if element.get("tag") == "table"]
        self.assertTrue(tables)
        rows = tables[0]["rows"]
        self.assertEqual(rows[0]["field"], "出租率")
        self.assertEqual(rows[0]["value"], "82%")
        markdowns = [element for element in elements if element.get("tag") == "markdown"]
        self.assertTrue(any("今日经营速览" in element["content"] for element in markdowns))

    def test_data_report_falls_back_to_markdown_when_no_tabular_content(self) -> None:
        elements = render_qa_card_elements("qa_data_report", "今日出租率 82%，整体平稳。")
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertIn("82%", elements[0]["content"])


class DataReportFieldRowAdmissionTests(unittest.TestCase):
    """2026-09-01 实测（运营助手 · 17:21 卡片坏行）：叙述句与含时间戳的标题行
    被冒号劈进「项目/数值」表格。只有「字段：短指标值」才进表。"""

    def _render(self, text):
        elements = render_qa_card_elements("qa_data_report", text)
        rows = []
        for element in elements:
            if element.get("tag") == "table":
                rows.extend(element.get("rows") or [])
        markdowns = [
            element for element in elements if element.get("tag") == "markdown"
        ]
        return rows, markdowns

    def test_heading_with_timestamp_stays_out_of_table(self) -> None:
        # 标题里的 09:20 时间冒号不得当字段分隔符（实测坏行「…9月1日 09 | 20 读取）」）
        text = "✅ 今天最新数据（9月1日 09:20 读取）\n\n预订销售额：2680 元"
        rows, markdowns = self._render(text)
        fields = [row["field"] for row in rows]
        self.assertNotIn("✅ 今天最新数据（9月1日 09", fields)
        joined = "\n".join(item["content"] for item in markdowns)
        self.assertIn("09:20 读取）", joined)
        self.assertIn("预订销售额", fields)

    def test_narrative_sentence_after_colon_stays_out_of_table(self) -> None:
        # 「📊 结论：<叙述句>」是结论不是指标，不得被劈成 数值/说明 两列
        text = (
            "📊 结论：今天金字塔本身已经调得不错，别乱动；明天想同时解决"
            "「流量不够 + 转化不够」，转化率 22.73% 是根子。"
        )
        rows, markdowns = self._render(text)
        fields = [row["field"] for row in rows]
        self.assertNotIn("📊 结论", fields)
        joined = "\n".join(item["content"] for item in markdowns)
        self.assertIn("结论：今天金字塔", joined)

    def test_real_reply_shape_produces_no_broken_rows(self) -> None:
        # 复现 2026-09-01 17:21 真实回复结构：结论/时间标题/口径提醒不进表，指标行保留
        text = (
            "📊 结论：今天金字塔本身已经调得不错，别乱动；明天想同时解决"
            "「流量不够 + 转化不够」。\n\n"
            "✅ 今天最新数据（9月1日 09:20 读取）\n"
            "• 实时访客：176 人\n\n"
            "📌 口径提醒：金字塔是今日实时（09:20），流量访客是昨日口径。\n\n"
            "预订销售额：2680 元\n"
            "较上周同期：-30.27%"
        )
        rows, markdowns = self._render(text)
        joined_rows = "\n".join(f"{row['field']}|{row.get('value', '')}" for row in rows)
        self.assertNotIn("20 读取）", joined_rows)
        for bad in ("✅ 今天最新数据", "📊 结论", "📌 口径提醒"):
            self.assertNotIn(bad, joined_rows)
        self.assertIn("预订销售额", joined_rows)
        values = {row["field"]: row["value"] for row in rows}
        self.assertEqual(values.get("预订销售额"), "2680 元")
        self.assertEqual(values.get("较上周同期"), "-30.27%")


    def test_price_compare_renders_blocks_with_hr_separators(self) -> None:
        text = "竞品 A：¥420\n\n竞品 B：¥399\n\n本店：¥431"
        elements = render_qa_card_elements("qa_price_compare", text)
        tags = [element.get("tag") for element in elements]
        self.assertIn("hr", tags)
        self.assertGreaterEqual(tags.count("markdown"), 3)
        self.assertNotEqual(tags[-1], "hr")

    def test_dense_price_compare_uses_scrollable_native_table(self) -> None:
        text = "\n\n".join(
            [
                "商务大床房：本店 ¥431 / 竞品 ¥420",
                "高级大床房：本店 ¥468 / 竞品 ¥455",
                "豪华大床房：本店 ¥520 / 竞品 ¥499",
                "行政大床房：本店 ¥688 / 竞品 ¥650",
            ]
        )

        elements = render_qa_card_elements("qa_price_compare", text)
        table = next(element for element in elements if element.get("tag") == "table")

        self.assertTrue(table.get("freeze_first_column"))
        self.assertEqual(table["columns"][0]["display_name"], "房型")
        self.assertEqual(table["columns"][1]["display_name"], "价格对比")
        self.assertEqual(table["rows"][0]["field"], "商务大床房")

    def test_analysis_renders_three_sections(self) -> None:
        text = "**结论**：本周经营平稳。\n**关键发现**：竞品降价。\n**建议行动**：关注明日价格。"
        elements = render_qa_card_elements("qa_analysis", text)
        markdowns = [element["content"] for element in elements if element.get("tag") == "markdown"]
        joined = "\n".join(markdowns)
        self.assertIn("结论", joined)
        self.assertIn("发现", joined)
        self.assertIn("建议", joined)
        self.assertGreaterEqual(len(markdowns), 2)

    def test_plain_renders_markdown_with_note_footer(self) -> None:
        elements = render_qa_card_elements("qa_plain", "好的，已记录您的反馈。")
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertIn("已记录", elements[0]["content"])
        notes = [element for element in elements if element.get("tag") == "note"]
        self.assertTrue(notes)

    def test_alert_renders_concise_body(self) -> None:
        text = "\n".join(["读取失败：携程数据中心页面异常"] + [f"补充信息 {i}" for i in range(30)])
        elements = render_qa_card_elements("qa_alert", text)
        markdowns = [element for element in elements if element.get("tag") == "markdown"]
        self.assertTrue(markdowns)
        joined = "\n".join(element["content"] for element in markdowns)
        self.assertIn("读取失败", joined)
        self.assertLessEqual(len(joined), 5000)

    def test_render_falls_back_to_plain_for_unknown_template(self) -> None:
        elements = render_qa_card_elements("qa_nonexistent", "普通回答")
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertIn("普通回答", elements[0]["content"])

    def test_render_falls_back_to_plain_when_builder_raises(self) -> None:
        def _broken_builder(text: str) -> list[dict]:
            raise RuntimeError("boom")

        register_qa_card_template("qa_broken", _broken_builder)
        try:
            elements = render_qa_card_elements("qa_broken", "兜底内容")
        finally:
            register_qa_card_template("qa_broken", None)
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertIn("兜底内容", elements[0]["content"])

    def test_custom_template_can_be_registered(self) -> None:
        def _custom_builder(text: str) -> list[dict]:
            return [{"tag": "markdown", "content": f"CUSTOM::{text}"}]

        register_qa_card_template("qa_custom", _custom_builder)
        try:
            elements = render_qa_card_elements("qa_custom", "hello")
        finally:
            register_qa_card_template("qa_custom", None)
        self.assertEqual(elements[0]["content"], "CUSTOM::hello")


class QaCardBuildTests(unittest.TestCase):
    def test_build_qa_reply_card_keeps_envelope_shape(self) -> None:
        card = build_qa_reply_card("今日出租率 82%。", title="运营助手", status="success")
        self.assertEqual(card["config"], {"wide_screen_mode": True})
        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("运营助手", card["header"]["title"]["content"])
        self.assertTrue(card["elements"])

    def test_build_qa_reply_card_alert_overrides_header_color(self) -> None:
        card = build_qa_reply_card("读取失败：页面异常，请检查。", title="运营助手", status="success")
        self.assertEqual(card["header"]["template"], "red")

    def test_build_qa_reply_card_auto_selects_template(self) -> None:
        card = build_qa_reply_card(
            "| 指标 | 值 |\n| --- | --- |\n| 出租率 | 82% |",
            title="运营助手",
        )
        self.assertTrue(any(element.get("tag") == "table" for element in card["elements"]))

    def test_build_qa_reply_card_enforces_byte_limit(self) -> None:
        huge_text = "\n".join(f"第 {i} 行：出租率 8{i}%，均价 ¥43{i}。" for i in range(600))
        card = build_qa_reply_card(huge_text, title="运营助手")
        self.assertLessEqual(len(json.dumps(card, ensure_ascii=False).encode("utf-8")), 28000)
        self.assertTrue(card["elements"])

    def test_template_registry_contains_all_planned_templates(self) -> None:
        for template_id in (
            "qa_data_report",
            "qa_price_compare",
            "qa_analysis",
            "qa_plain",
            "qa_alert",
        ):
            self.assertIn(template_id, QA_CARD_TEMPLATE_IDS)


if __name__ == "__main__":
    unittest.main()
