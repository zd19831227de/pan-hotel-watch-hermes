from __future__ import annotations

import unittest

from app.domain.notifications.feishu_card_tables import (
    TABLE_PAGE_SIZE_LIMIT,
    chunk_by_units,
    display_width,
    metric_block_elements,
    metric_rows_text,
    parse_metric_line,
    render_metric_group_pages,
    rows_from_lines,
    split_value_parts,
)


class DisplayWidthTests(unittest.TestCase):
    def test_cjk_counts_double(self) -> None:
        self.assertEqual(display_width("销售均价"), 8)
        self.assertEqual(display_width("rank 8"), 6)

    def test_chunk_keeps_each_block_within_budget(self) -> None:
        text = "销售均价" * 10  # 80 units
        chunks = chunk_by_units(text, 28)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(display_width(chunk), 28)
        self.assertEqual("".join(chunks).replace(" ", ""), text)

    def test_short_text_returns_single_chunk(self) -> None:
        self.assertEqual(chunk_by_units("347.50 元（较上周 8.52%）", 48), ["347.50 元（较上周 8.52%）"])


class ParseMetricLineTests(unittest.TestCase):
    def test_field_value_line(self) -> None:
        self.assertEqual(
            parse_metric_line("销售均价：347.50 元（较上周 8.52%）"),
            ("销售均价", ["347.50 元（较上周 8.52%）"]),
        )

    def test_pipe_separated_line_becomes_multi_columns(self) -> None:
        field, parts = parse_metric_line("天天特价｜房型产品:大床房｜有效期:长期｜生效中")
        self.assertEqual(field, "天天特价")
        self.assertEqual(parts, ["房型产品:大床房", "有效期:长期", "生效中"])

    def test_newline_separated_value_splits(self) -> None:
        self.assertEqual(
            split_value_parts("5.35\nrank 8 / 21"),
            ["5.35", "rank 8 / 21"],
        )

    def test_pure_text_line_returns_none(self) -> None:
        self.assertIsNone(parse_metric_line("【实时】"))
        self.assertIsNone(parse_metric_line("美团神券（报名成功）"))

    def test_rows_from_lines_splits_rows_and_text(self) -> None:
        rows, text_lines = rows_from_lines(
            ["【实时】", "实时预订订单：10", "活动A｜生效中", "纯说明行"]
        )
        self.assertEqual(
            rows,
            [
                {"field": "实时预订订单", "parts": ["10"]},
                {"field": "活动A", "parts": ["生效中"]},
            ],
        )
        self.assertEqual(text_lines, ["【实时】", "纯说明行"])


class MetricBlockElementsTests(unittest.TestCase):
    def test_small_row_set_renders_single_non_paging_table(self) -> None:
        rows = [{"field": f"指标{index}", "value": str(index)} for index in range(6)]
        elements = metric_block_elements(rows)
        self.assertEqual(len(elements), 1)
        table = elements[0]
        self.assertEqual(table["tag"], "table")
        # page_size == 行数 → 飞书不会分页（上限 10）
        self.assertEqual(table["page_size"], 6)
        self.assertEqual(len(table["rows"]), 6)

    def test_table_slides_as_a_whole_without_frozen_column(self) -> None:
        """整表横向滑动：不冻结首列，指标列随内容一起移动。"""

        elements = metric_block_elements([{"field": "服务概览", "parts": ["5.35"]}])
        self.assertFalse(elements[0]["freeze_first_column"])

    def test_over_ten_rows_split_into_stacked_non_paging_tables(self) -> None:
        """数据多的段落全部表格化：>10 行拆成多个 ≤10 行的堆叠表格，不翻页。"""

        rows = [{"field": f"指标{index}", "value": str(index)} for index in range(23)]
        elements = metric_block_elements(rows)
        tables = [element for element in elements if element.get("tag") == "table"]
        self.assertEqual(len(tables), 3)
        self.assertEqual([len(t["rows"]) for t in tables], [10, 10, 3])
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))
            self.assertLessEqual(table["page_size"], TABLE_PAGE_SIZE_LIMIT)
        # 所有行都可见
        seen = {row["field"] for table in tables for row in table["rows"]}
        self.assertEqual(seen, {f"指标{index}" for index in range(23)})

    def test_exhausted_table_budget_falls_back_to_text(self) -> None:
        rows = [{"field": f"指标{index}", "value": str(index)} for index in range(23)]
        elements = metric_block_elements(rows, remaining_tables=1)
        tables = [element for element in elements if element.get("tag") == "table"]
        texts = [element for element in elements if element.get("tag") == "div"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(texts), 1)
        self.assertIn("指标22", texts[0]["text"]["content"])

    def test_long_value_chunks_into_extra_columns_for_sliding(self) -> None:
        long_text = "这是很长的一段说明文字" * 6  # 108 units
        elements = metric_block_elements(
            [{"field": "服务概览", "value": "5.35", "detail": long_text}]
        )
        self.assertEqual(elements[0]["tag"], "table")
        keys = [column["name"] for column in elements[0]["columns"]]
        self.assertIn("value", keys)
        self.assertIn("detail", keys)
        self.assertTrue(any(key.startswith("detail_") for key in keys))
        row = elements[0]["rows"][0]
        self.assertEqual(row["value"], "5.35")
        joined = "".join(
            str(row.get(key) or "") for key in ("detail", "detail_2", "detail_3")
        )
        self.assertTrue(joined.startswith("这是很长"))
        budget = (260 - 24) // 7
        for key in ("detail", "detail_2", "detail_3"):
            if row.get(key):
                self.assertLessEqual(display_width(row[key]), budget)
        # 列宽总和超过画布 → 横向滑动
        total_width = sum(
            int(column["width"].removesuffix("px")) for column in elements[0]["columns"]
        )
        self.assertGreaterEqual(total_width, 480)

    def test_multi_part_row_uses_per_part_columns(self) -> None:
        elements = metric_block_elements(
            [{"field": "PSI", "parts": ["5.35", "rank 8 / 21"]}]
        )
        self.assertEqual(elements[0]["rows"][0]["value"], "5.35")
        self.assertEqual(elements[0]["rows"][0]["detail"], "rank 8 / 21")
        headers = [column["display_name"] for column in elements[0]["columns"]]
        self.assertEqual(headers, ["字段", "内容", "说明"])

    def test_custom_headers(self) -> None:
        elements = metric_block_elements(
            [{"field": "销售额", "parts": ["1280"]}],
            field_header="指标",
            value_header="数据",
        )
        headers = [column["display_name"] for column in elements[0]["columns"]]
        self.assertEqual(headers, ["指标", "数据"])

    def test_empty_rows_render_empty_text(self) -> None:
        elements = metric_block_elements([])
        self.assertEqual(elements[0]["tag"], "div")
        self.assertEqual(elements[0]["text"]["content"], "暂无可展示指标")

    def test_many_columns_have_unique_keys_and_keep_all_cells(self) -> None:
        """超过 6 个数据列时键名不得重复（否则后写的单元格会覆盖数据）。"""

        parts = [f"片段{index}" for index in range(7)]
        elements = metric_block_elements([{"field": "活动", "parts": parts}])
        names = [column["name"] for column in elements[0]["columns"]]
        self.assertEqual(len(names), len(set(names)))
        row = elements[0]["rows"][0]
        values = [str(row.get(name) or "") for name in names[1:]]
        for index in range(7):
            self.assertIn(f"片段{index}", " ".join(values))

    def test_chunk_prefers_separator_boundaries(self) -> None:
        chunks = chunk_by_units("有效期:2026-08-01 至 2026-12-31", 28)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertFalse(chunk.startswith("-"))

    def test_metric_rows_text_keeps_all_values(self) -> None:
        text = metric_rows_text(
            [{"field": "A", "parts": ["1", "2"]}, {"field": "B", "parts": ["3"]}]
        )
        self.assertEqual(text.splitlines(), ["A：1 / 2", "B：3"])


class RenderMetricGroupPagesTests(unittest.TestCase):
    """分页分组渲染：每组 = 小节标题 + 表格；每页 ≤5 表；永不截断数据。

    用户 2026-08-26 反馈的硬要求：
    - 不合并板块、不放「另有 N 项未展示」——数据必须全部展示，
      一张卡放不下就由调用方拆成多张卡（每页就是一张卡的内容）。
    - 行字段只保留具体条目名；数据集内部子结构（今日/昨日、实时/按日）
      用「板块」维度列，不拼进字段。
    """

    @staticmethod
    def _tables(pages: list[list[dict]]) -> list[dict]:
        return [
            element
            for page in pages
            for element in page
            if element.get("tag") == "table"
        ]

    def test_single_group_single_page(self) -> None:
        pages = render_metric_group_pages(
            [
                {
                    "title": "📊 经营数据",
                    "rows": [
                        {"field": "在店间夜量", "parts": ["12"]},
                        {"field": "预定订单量", "parts": ["5"]},
                    ],
                }
            ]
        )
        self.assertEqual(len(pages), 1)
        tags = [element["tag"] for element in pages[0]]
        self.assertEqual(tags, ["hr", "div", "table"])
        table = pages[0][2]
        names = [column["name"] for column in table["columns"]]
        self.assertNotIn("dimension", names)
        self.assertEqual(table["rows"][0]["field"], "在店间夜量")
        self.assertEqual(table["page_size"], len(table["rows"]))
        self.assertFalse(table["freeze_first_column"])

    def test_sub_dimension_uses_dimension_column(self) -> None:
        pages = render_metric_group_pages(
            [
                {
                    "title": "📊 经营数据",
                    "rows": [
                        {"field": "间夜量", "parts": ["12"], "dimension": "在店"},
                        {"field": "订单量", "parts": ["5"], "dimension": "预定"},
                        {"field": "访客数", "parts": ["80"], "dimension": "流量"},
                    ],
                }
            ]
        )
        table = self._tables(pages)[0]
        names = [column["name"] for column in table["columns"]]
        self.assertEqual(names[0], "dimension")
        self.assertEqual(table["rows"][0]["dimension"], "在店")
        self.assertEqual(table["rows"][0]["field"], "间夜量")
        self.assertNotIn("在店·间夜量", str(table["rows"]))

    def test_big_group_splits_into_pages_without_truncation(self) -> None:
        rows = [
            {"field": f"指标{index}", "parts": [str(index)], "dimension": "在店"}
            for index in range(24)
        ]
        small_groups = [
            {"title": f"组{index}", "rows": [{"field": "项", "parts": ["1"]}]}
            for index in range(3)
        ]
        pages = render_metric_group_pages(
            [{"title": "📊 经营数据", "rows": rows}, *small_groups]
        )
        tables = self._tables(pages)
        for page in pages:
            page_tables = [e for e in page if e.get("tag") == "table"]
            self.assertLessEqual(len(page_tables), 5)
        for table in tables:
            self.assertEqual(table["page_size"], len(table["rows"]))
            self.assertLessEqual(table["page_size"], TABLE_PAGE_SIZE_LIMIT)
        blob = str(pages)
        # 全部 24 行都展示，绝不出现截断提示。
        for index in range(24):
            self.assertIn(f"指标{index}", blob)
        self.assertNotIn("未展示", blob)
        self.assertNotIn("另有", blob)

    def test_group_crossing_page_gets_continuation_title(self) -> None:
        groups = [
            {
                "title": "📊 大板块",
                "subtitle": "子板块说明",
                "rows": [
                    {"field": f"行{index}", "parts": [str(index)]} for index in range(55)
                ],
            }
        ]
        pages = render_metric_group_pages(groups)
        # 55 行 = 6 张表 → 每页 5 张 → 2 页；第二页以「（续）」小节开头。
        tables = self._tables(pages)
        self.assertEqual(len(tables), 6)
        self.assertLessEqual(
            max(
                len([e for e in page if e.get("tag") == "table"]) for page in pages
            ),
            5,
        )
        headers = [
            element["text"]["content"]
            for page in pages
            for element in page
            if element.get("tag") == "div"
        ]
        self.assertIn("**📊 大板块**", headers[0])
        self.assertTrue(any("（续）" in header for header in headers[1:]))
        blob = str(pages)
        for index in (0, 30, 54):
            self.assertIn(f"行{index}", blob)

    def test_many_groups_flow_across_pages_all_visible(self) -> None:
        groups = [
            {
                "title": f"组{index}",
                "rows": [{"field": f"行{row}", "parts": [str(row)]} for row in range(12)],
            }
            for index in range(5)
        ]
        pages = render_metric_group_pages(groups)
        blob = str(pages)
        for index in range(5):
            self.assertIn(f"组{index}", blob)
        for row in range(12):
            self.assertIn(f"行{row}", blob)
        self.assertNotIn("未展示", blob)
        for page in pages:
            self.assertLessEqual(
                len([e for e in page if e.get("tag") == "table"]), 5
            )

    def test_group_subtitle_lists_member_sections(self) -> None:
        pages = render_metric_group_pages(
            [
                {
                    "title": "📈 流量与竞争",
                    "subtitle": "流量数据 · 经营对比",
                    "rows": [{"field": "曝光量", "parts": ["100"]}],
                }
            ]
        )
        header_text = pages[0][1]["text"]["content"]
        self.assertIn("**📈 流量与竞争**", header_text)
        self.assertIn("流量数据 · 经营对比", header_text)

    def test_empty_group_renders_nothing(self) -> None:
        self.assertEqual(render_metric_group_pages([{"title": "空组", "rows": []}]), [])
        self.assertEqual(render_metric_group_pages([]), [])

    def test_group_field_header_override_applies_to_first_column(self) -> None:
        """组级 field_header 覆盖：金字塔出价表首列是「推广计划」不是通用「指标」。

        不带 field_header 的组必须维持原默认（「指标」），行为逐字节不变。
        """
        pages = render_metric_group_pages(
            [
                {
                    "title": "✈️ 携程·金字塔出价",
                    "field_header": "推广计划",
                    "part_headers": ["点击出价", "今日预算", "今日花费", "状态"],
                    "rows": [
                        {"field": "周内0-2", "parts": ["¥6.5", "¥300", "¥45.2", "推广中"]},
                        {"field": "周末14-16", "parts": ["¥5", "¥400", "¥12", "推广中"]},
                    ],
                },
                {
                    "title": "📊 对照组",
                    "rows": [{"field": "销售额", "parts": ["1280"]}],
                },
            ]
        )
        tables = self._tables(pages)
        self.assertEqual(len(tables), 2)
        override_headers = [
            column["display_name"] for column in tables[0]["columns"]
        ]
        self.assertEqual(override_headers, ["推广计划", "点击出价", "今日预算", "今日花费", "状态"])
        self.assertEqual(tables[0]["rows"][0]["field"], "周内0-2")
        default_headers = [
            column["display_name"] for column in tables[1]["columns"]
        ]
        self.assertEqual(default_headers, ["指标", "内容"])


if __name__ == "__main__":
    unittest.main()
