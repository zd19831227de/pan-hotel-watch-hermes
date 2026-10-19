"""EBK 全局推送三分类卡 builder 单测（设计文档 §5.5/§6）。"""

from __future__ import annotations

import unittest

from app.domain.ebk_global_push.global_push_cards import build_global_push_cards
from app.domain.ebk_global_push.projection import extract_ctrip_score_summary


def _row(platform, dataset_id, label, value, *, freshness="realtime",
         value_type="integer", unit="", dimension=""):
    return {
        "platform": platform,
        "dataset_id": dataset_id,
        "field_key": label,
        "label": label,
        "value": value,
        "value_type": value_type,
        "unit": unit,
        "time_scope": freshness,
        "freshness": freshness,
        "captured_at": "2026-08-28T08:00:32+08:00",
        "source_path": f"datasets.{dataset_id}.data.metrics.x",
        "dimension": dimension,
    }


def _score_summary(psi=None, review=None):
    rows = []
    if psi is not None:
        row = _row("ctrip", "business_outline", "PSI 服务质量分", psi,
                   freshness="score_snapshot", value_type="score")
        row["field_key"] = "psi_service_score"
        rows.append(row)
    if review is not None:
        row = _row("ctrip", "business_outline", "酒店点评分", review,
                   freshness="score_snapshot", value_type="score")
        row["field_key"] = "hotel_review_score"
        rows.append(row)
    return extract_ctrip_score_summary(rows)


FULL_SECTIONS = [
    {
        "platform": "ctrip",
        "status": "success",
        "rows": [
            _row("ctrip", "business_outline", "实时预订订单", 12, unit="单"),
            _row("ctrip", "business_outline", "实时在店间夜", 8, unit="间夜"),
            _row("ctrip", "promotion_registered", "某某促销活动", "进行中",
                 freshness="current_state", value_type="text"),
            _row("ctrip", "ad_pyramid", "今日花费", 128.5,
                 value_type="amount", unit="元"),
        ],
    },
    {
        "platform": "meituan",
        "status": "success",
        "rows": [
            _row("meituan", "workbench_home", "今日实时销售额", 18650.5,
                 value_type="amount", unit="元"),
            _row("meituan", "promotion_coupon", "神券活动A", "进行中",
                 freshness="current_state", value_type="text"),
        ],
    },
    {
        "platform": "fliggy",
        "status": "success",
        "rows": [
            _row("fliggy", "home_v1", "今日销售额", 9876.0,
                 value_type="amount", unit="元"),
        ],
    },
]


class BuildGlobalPushCardsTests(unittest.TestCase):
    def _build(self, sections=None, score_summary=None, prefixes=None):
        return build_global_push_cards(
            store_name="示例门店",
            read_time="2026-08-28T08:00:32+08:00",
            overall_status="success",
            overall_summary="三平台读取成功",
            score_summary=score_summary or _score_summary(4.72, 4.6),
            platform_sections=sections if sections is not None else FULL_SECTIONS,
            title_prefixes=prefixes,
        )

    def test_three_categories_always_present(self):
        cards = self._build()
        self.assertEqual(
            sorted(cards.keys()), ["advertising", "business", "promotion"]
        )
        for category, card_list in cards.items():
            self.assertTrue(card_list, f"{category} 不应为空")

    def test_business_title_contains_ctrip_score_suffix(self):
        cards = self._build()
        title = cards["business"][0]["header"]["title"]["content"]
        self.assertTrue(title.startswith("经营数据｜携程 PSI 4.72｜携程店铺评分 4.6"))

    def test_business_title_placeholder_when_scores_missing(self):
        cards = self._build(score_summary=_score_summary())
        title = cards["business"][0]["header"]["title"]["content"]
        self.assertTrue(title.startswith("经营数据｜携程 PSI 未读取｜携程店铺评分 未读取"))

    def test_custom_prefix_replaces_only_prefix_keeps_suffix(self):
        cards = self._build(prefixes={"business": "早晚盯盘"})
        title = cards["business"][0]["header"]["title"]["content"]
        self.assertTrue(title.startswith("早晚盯盘｜携程 PSI 4.72｜携程店铺评分 4.6"))

    def test_promotion_and_advertising_titles(self):
        cards = self._build()
        self.assertEqual(cards["promotion"][0]["header"]["title"]["content"], "活动促销")
        self.assertEqual(cards["advertising"][0]["header"]["title"]["content"], "广告营销")

    def test_header_block_contains_store_read_time_status(self):
        cards = self._build()
        first_elements = cards["business"][0]["elements"]
        header_text = first_elements[0]["text"]["content"]
        self.assertIn("示例门店", header_text)
        self.assertIn("2026-08-28 08:00", header_text)
        self.assertIn("成功", header_text)

    def test_card_envelope_structure(self):
        cards = self._build()
        card = cards["business"][0]
        self.assertEqual(card["config"], {"wide_screen_mode": True})
        self.assertIn(card["header"]["template"], {"green", "orange", "red", "blue"})

    def test_advertising_placeholder_for_meituan_and_fliggy(self):
        cards = self._build()
        adv_elements = _all_elements(cards["advertising"])
        joined = _elements_text(joined_elements(adv_elements))
        self.assertIn("未投放", joined)
        # 占位按平台各出现一次
        self.assertEqual(joined.count("未投放"), 2)

    def test_advertising_card_renders_pyramid_bid_module_table(self):
        """2026-09-10 需求：广告营销卡新增「携程·金字塔出价」模块（表格化）。

        列契约与指令「金字塔出价」卡一致；排在「携程·广告金字塔」之后。
        """
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    _row(
                        "ctrip", "ad_pyramid", "今日花费", 128.5,
                        value_type="amount", unit="元",
                    ),
                    {
                        **_row("ctrip", "pyramid_bid", "周内0-2", "¥6.5"),
                        "parts": ["¥6.5", "¥300", "¥45.2", "推广中"],
                    },
                    {
                        **_row("ctrip", "pyramid_bid", "周末14-16", "¥5"),
                        "parts": ["¥5", "¥400", "¥12", "推广中"],
                    },
                ],
            },
        ]
        cards = self._build(sections=sections)
        adv_cards = cards["advertising"]
        joined = _elements_text(joined_elements(_all_elements(adv_cards)))
        self.assertIn("携程·广告金字塔", joined)
        self.assertIn("携程·金字塔出价", joined)
        tables = [
            element
            for card in adv_cards
            for element in card["elements"]
            if isinstance(element, dict) and element.get("tag") == "table"
        ]
        pyramid_tables = [
            table
            for table in tables
            if any(
                column.get("display_name") == "推广计划"
                for column in table["columns"]
            )
        ]
        self.assertEqual(len(pyramid_tables), 1)
        headers = [
            column["display_name"] for column in pyramid_tables[0]["columns"]
        ]
        self.assertEqual(
            headers, ["推广计划", "点击出价", "今日预算", "今日花费", "状态"]
        )
        first = pyramid_tables[0]["rows"][0]
        self.assertEqual(first["field"], "周内0-2")
        self.assertEqual(first["value"], "¥6.5")
        self.assertEqual(first["detail"], "¥300")
        self.assertEqual(first["detail_2"], "¥45.2")
        self.assertEqual(first["detail_3"], "推广中")

    def test_advertising_card_pyramid_placeholder_row_without_bid_columns(self):
        """读取成功但无推广计划：占位行不套出价列头（指标/内容两列即可）。"""
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    _row("ctrip", "pyramid_bid", "推广计划", "暂无金字塔推广计划",
                         freshness="current_state", value_type="text"),
                ],
            },
        ]
        cards = self._build(sections=sections)
        joined = _elements_text(joined_elements(_all_elements(cards["advertising"])))
        self.assertIn("携程·金字塔出价", joined)
        self.assertIn("暂无金字塔推广计划", joined)
        tables = [
            element
            for card in cards["advertising"]
            for element in card["elements"]
            if isinstance(element, dict) and element.get("tag") == "table"
        ]
        for table in tables:
            headers = [
                column.get("display_name") for column in table["columns"]
            ]
            self.assertNotIn("点击出价", headers)

    def test_failed_platform_shows_failure_row_not_empty_success(self):
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [_row("ctrip", "business_outline", "实时预订订单", 12, unit="单")],
            },
            {
                "platform": "fliggy",
                "status": "human_required",
                "rows": [],
                "summary": "登录失效",
            },
        ]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("读取失败", text)
        self.assertIn("飞猪", text)
        # 成功平台数据不丢失（展示标签已按规则去掉「实时」前缀）
        self.assertIn("预订订单", text)

    def test_config_required_platform_overrides_advertising_placeholder(self):
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [_row("ctrip", "business_outline", "实时预订订单", 12, unit="单")],
            },
            {
                "platform": "fliggy",
                "status": "config_required",
                "rows": [],
                "summary": "飞猪监控组缺少账号绑定。",
            },
        ]
        cards = self._build(sections=sections)
        for category in ("business", "promotion", "advertising"):
            text = _elements_text(joined_elements(_all_elements(cards[category])))
            self.assertIn("飞猪", text)
            self.assertIn("需配置", text)
        advertising = _elements_text(joined_elements(_all_elements(cards["advertising"])))
        self.assertNotIn("🧳 飞猪·广告", advertising)

    def test_empty_category_shows_placeholder_not_fake_data(self):
        sections = [{
            "platform": "ctrip",
            "status": "success",
            "rows": [_row("ctrip", "business_outline", "实时预订订单", 12, unit="单")],
        }]
        cards = self._build(sections=sections)
        promo_text = _elements_text(joined_elements(_all_elements(cards["promotion"])))
        self.assertIn("当前无可展示活动", promo_text)

    def test_pagination_when_over_five_tables(self):
        """同类超 5 表 → 分页连发 (i/N)，组跨页（续），分类边界不变。"""
        # 每平台一个 section（API 语义），数据集在 rows 里区分；
        # 6 个白名单 (platform, dataset) 组合 → 6 张表 > 每卡 5 表。
        combos = [
            ("ctrip", "business_outline"),
            ("ctrip", "competition_profile"),
            ("meituan", "workbench_home"),
            ("meituan", "data_center_home"),
            ("meituan", "data_center_trade"),
            ("fliggy", "home_v1"),
        ]
        rows_by_platform: dict[str, list[dict]] = {}
        for platform, dataset_id in combos:
            rows_by_platform.setdefault(platform, []).extend(
                _row(platform, dataset_id, f"{dataset_id}·指标{index}", index)
                for index in range(3)
            )
        sections = [
            {"platform": platform, "status": "success", "rows": rows}
            for platform, rows in rows_by_platform.items()
        ]
        cards = self._build(sections=sections)
        business_cards = cards["business"]
        self.assertGreater(len(business_cards), 1)
        titles = [c["header"]["title"]["content"] for c in business_cards]
        self.assertIn("（2/2）", titles[-1])
        # 单卡表格数不超 5
        for card in business_cards:
            table_count = sum(
                1 for el in card["elements"] if el.get("tag") == "table"
            )
            self.assertLessEqual(table_count, 5)

    def test_realtime_prefix_stripped_from_labels_and_dimensions(self):
        """卡片即默认口径：标签与板块不出现「实时」「今日」「活动」字样。"""
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    _row("ctrip", "business_outline", "实时预订订单", 12, unit="单"),
                    _row("ctrip", "business_outline", "实时访客排名", 3),
                    _row("ctrip", "competition_profile", "竞争圈排名", 2, dimension="实时"),
                ],
            },
            {
                "platform": "meituan",
                "status": "success",
                "rows": [
                    _row("meituan", "workbench_home", "实时销售额", 18650.5,
                         value_type="amount", unit="元", dimension="今日实时"),
                    _row("meituan", "promotion_coupon", "今日核销", 128, unit="张",
                         freshness="current_state", dimension="活动"),
                ],
            },
            {
                "platform": "fliggy",
                "status": "success",
                "rows": [
                    _row("fliggy", "home_v1", "销售额", 9876,
                         value_type="amount", unit="元", dimension="今日"),
                    _row("fliggy", "operating_data", "在店间夜", 5, unit="间夜",
                         dimension="在店"),
                ],
            },
        ]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        promotion_text = _elements_text(
            joined_elements(_all_elements(cards["promotion"]))
        )
        self.assertIn("预订订单", text)
        self.assertIn("访客排名", text)
        self.assertIn("核销", promotion_text)
        self.assertNotIn("实时", text + promotion_text)
        self.assertNotIn("今日", text + promotion_text)
        # 活动卡本身即活动口径，「活动」板块列被剥离 → 促销卡表格无「板块」维度列
        for el in _all_elements(cards["promotion"]):
            if el.get("tag") == "table":
                column_names = [col.get("name") for col in el.get("columns", [])]
                self.assertNotIn("dimension", column_names)
        # 有信息量的板块（如飞猪「在店」）保留
        self.assertIn("在店", text)

    def test_advertising_dimension_column_stripped(self):
        """问题 1（实测）：广告卡整类即广告口径，ad_pyramid 行的「广告」板块前缀
        与分类重复，须剥离（同「活动」规则），表格不出现「板块」维度列。"""
        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    _row("ctrip", "ad_pyramid", "点击(次)", 47,
                         dimension="广告"),
                    _row("ctrip", "ad_pyramid", "总花费(CNY)", "193.78",
                         value_type="amount", unit="元", dimension="广告"),
                ],
            },
        ]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["advertising"])))
        self.assertIn("点击(次)", text)
        self.assertIn("总花费(CNY)", text)
        # 数据集名「广告金字塔」合法保留；行内不再出现独立的「广告」板块单元
        self.assertNotIn(" 广告 ", text)
        for el in _all_elements(cards["advertising"]):
            if el.get("tag") == "table":
                column_names = [col.get("name") for col in el.get("columns", [])]
                self.assertNotIn("dimension", column_names)
                for row in el.get("rows", []):
                    self.assertNotIn("dimension", row)
                    self.assertNotIn("广告", row.get("parts", []))

    def test_group_titles_use_platform_specific_icons(self):
        """板块标题前用平台特有标志（携程✈️ 美团🦘 飞猪🐷），不再用通用数据集图标。"""
        sections = [
            {"platform": "ctrip", "status": "success",
             "rows": [_row("ctrip", "business_outline", "预订订单", 12, unit="单")]},
            {"platform": "meituan", "status": "success",
             "rows": [_row("meituan", "workbench_home", "销售额", 100,
                           value_type="amount", unit="元")]},
            {"platform": "fliggy", "status": "success",
             "rows": [_row("fliggy", "home_v1", "销售额", 200,
                           value_type="amount", unit="元")]},
        ]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("✈️ 携程·收益概览", text)
        self.assertIn("🦘 美团·收益概览", text)
        self.assertIn("🧳 飞猪·收益概览", text)

        adv_text = _elements_text(joined_elements(_all_elements(cards["advertising"])))
        self.assertIn("🦘 美团·广告", adv_text)
        self.assertIn("🧳 飞猪·广告", adv_text)

    def test_failed_platform_title_keeps_platform_icon_with_status(self):
        sections = [
            {"platform": "ctrip", "status": "success",
             "rows": [_row("ctrip", "business_outline", "预订订单", 12, unit="单")]},
            {"platform": "fliggy", "status": "human_required", "rows": [],
             "summary": "登录失效"},
        ]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("🧳⚠️ 飞猪：读取失败（需人工处理）", text)

    def test_business_pages_split_ctrip_first_and_scores_on_page2(self):
        """经营卡（2026-09-04 运营确认）：1/N 只携程标题带 PSI/店铺评分；
        2/N 标题带美团点评分 + 飞猪点评得分；无表格形式点评分小节；无评分总览。"""
        def _score_row(platform, dataset_id, label, value, field_key, vtype="score"):
            row = _row(platform, dataset_id, label, value,
                       freshness="score_snapshot", value_type=vtype)
            row["field_key"] = field_key
            return row

        sections = [
            {
                "platform": "ctrip",
                "status": "success",
                "rows": [
                    _row("ctrip", "business_outline", "预订订单", 12, unit="单"),
                    _score_row("ctrip", "business_outline", "PSI 服务质量分", 4.72,
                               "psi_service_score"),
                ],
            },
            {
                "platform": "meituan",
                "status": "success",
                "rows": [
                    _score_row("meituan", "workbench_home", "点评分", 4.6,
                               "review_score"),
                    _row("meituan", "workbench_home", "销售额", 100,
                         value_type="amount", unit="元"),
                ],
            },
            {
                "platform": "fliggy",
                "status": "success",
                "rows": [
                    _score_row("fliggy", "diagnose_report", "客人点评得分", 4.7,
                               "guest_review_score"),
                ],
            },
        ]
        cards = self._build(sections=sections)
        pages = cards["business"]
        self.assertGreaterEqual(len(pages), 2, "经营卡应至少分两页（携程页 + 美团/飞猪页）")
        # 1/N 标题带携程 PSI + 店铺评分。
        title1 = pages[0]["header"]["title"]["content"]
        self.assertIn("携程 PSI 4.72", title1)
        # 2/N 标题带美团点评分 + 飞猪点评得分（不再重复携程 PSI）。
        title2 = pages[1]["header"]["title"]["content"]
        self.assertIn("美团点评分 4.6", title2)
        self.assertIn("飞猪点评得分 4.7", title2)
        self.assertNotIn("携程 PSI", title2)
        # 无表格形式「⭐ 点评分」小节、无「评分总览」。
        all_text = _elements_text(joined_elements(_all_elements(pages)))
        self.assertNotIn("点评分", all_text.replace("美团点评分", "").replace("飞猪点评得分", ""))
        self.assertNotIn("评分总览", all_text)
        # 1/N 只含携程内容：美团/飞猪指标不在第一页。
        page1 = _elements_text(joined_elements(_all_elements([pages[0]])))
        self.assertIn("预订订单", page1)
        self.assertNotIn("销售额", page1.replace("实时销售额", ""))

    def test_business_page_headers_show_platform_store_names(self):
        cards = build_global_push_cards(
            store_name="锚点携程组",
            read_time="2026-08-28T08:00:32+08:00",
            overall_status="success",
            overall_summary="ok",
            score_summary=_score_summary(4.72, 4.6),
            platform_sections=FULL_SECTIONS,
            platform_store_names={
                "ctrip": "携程静里店",
                "meituan": "美团静里店",
                "fliggy": "飞猪静里店",
            },
        )
        pages = cards["business"]
        page1_header = pages[0]["elements"][0]["text"]["content"]
        page2_header = pages[1]["elements"][0]["text"]["content"]
        self.assertIn("携程静里店", page1_header)
        self.assertNotIn("美团静里店", page1_header)
        self.assertIn("美团静里店", page2_header)
        self.assertIn("飞猪静里店", page2_header)
        self.assertNotIn("携程静里店", page2_header)

    def test_score_overview_absent_when_no_scores(self):
        sections = [{
            "platform": "ctrip",
            "status": "success",
            "rows": [_row("ctrip", "business_outline", "预订订单", 12, unit="单")],
        }]
        cards = self._build(sections=sections)
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertNotIn("评分总览", text)
        self.assertNotIn("点评分", text)

    def test_table_cells_not_truncated_and_full_width_scroll(self):
        cards = self._build()
        table = next(
            el for el in cards["business"][0]["elements"] if el.get("tag") == "table"
        )
        self.assertFalse(table["freeze_first_column"])
        self.assertLessEqual(table["page_size"], 10)

    def test_all_tables_use_shared_renderer_contract(self):
        """所有表格走共享渲染器契约：page_size==行数、列宽 px、lark_md。"""
        cards = self._build()
        for category, card_list in cards.items():
            for card in card_list:
                for el in card["elements"]:
                    if el.get("tag") != "table":
                        continue
                    self.assertEqual(el["page_size"], len(el["rows"]))
                    for column in el["columns"]:
                        self.assertTrue(column["width"].endswith("px"))
                        self.assertEqual(column["data_type"], "lark_md")






    def test_percent_value_no_duplicate_unit(self):
        from app.domain.ebk_global_push.projection import format_row_value
        row = {"value": "23.43%", "value_type": "percent", "unit": "%"}
        self.assertEqual(format_row_value(row), "23.43%")

    def test_percent_value_without_percent_in_value_keeps_unit(self):
        from app.domain.ebk_global_push.projection import format_row_value
        row = {"value": "23.43", "value_type": "percent", "unit": "%"}
        self.assertEqual(format_row_value(row), "23.43 %")

    def test_value_already_contains_unit_not_duplicated(self):
        from app.domain.ebk_global_push.projection import format_row_value
        # 值自带中文单位（如美团「7142.00 元」）时不再重复拼 unit
        row = {"value": "7142.00 元", "value_type": "text", "unit": "元"}
        self.assertEqual(format_row_value(row), "7142.00 元")
        row2 = {"value": "25 单", "value_type": "text", "unit": "单"}
        self.assertEqual(format_row_value(row2), "25 单")


class CtripCardRestructureTests(unittest.TestCase):
    """2026-09-01 卡片结构调整：换序/收益概览/经营对比/流量曝光。"""

    def _build(self, sections):
        return build_global_push_cards(
            store_name="示例门店",
            read_time="2026-09-01T12:00:00+08:00",
            overall_status="success",
            overall_summary="ok",
            score_summary={},
            platform_sections=sections,
            title_prefixes=None,
        )

    def test_category_order_business_promotion_advertising(self):
        cards = self._build(FULL_SECTIONS)
        self.assertEqual(list(cards.keys()), ["business", "promotion", "advertising"])

    def test_competition_profile_moves_traffic_and_adds_rank(self):
        rows = [
            {"platform": "ctrip", "dataset_id": "competition_profile",
             "field_key": "booking_orders", "label": "预订订单量", "value": 57,
             "value_type": "integer", "unit": "单", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T12:00:00+08:00",
             "source_path": "x", "dimension": "竞争圈", "competitive_rank": "14"},
            {"platform": "ctrip", "dataset_id": "competition_profile",
             "field_key": "ctrip_app_visitors", "label": "携程APP访客", "value": 341,
             "value_type": "integer", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T12:00:00+08:00",
             "source_path": "x", "dimension": "流量", "competitive_rank": "19"},
        ]
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("经营对比", text)
        self.assertIn("预订订单量", text)
        self.assertIn("14", text)
        self.assertIn("流量曝光", text)
        self.assertIn("携程APP访客", text)
        comp_tables = [el for el in _all_elements(cards["business"]) if el.get("tag") == "table"]
        for tb in comp_tables:
            cols = [c.get("display_name") for c in tb.get("columns", [])]
            self.assertNotIn("竞争圈", cols)

    def test_departure_sales_joins_revenue_overview(self):
        """2026-09-21 需求：离店销售额进携程收益概览（值+较上次幅度），
        不落「其他数据集」分组另起一表。"""
        rows = [
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "realtime_booking_orders", "label": "实时预订订单", "value": 18,
             "value_type": "integer", "unit": "单", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-21T12:12:00+08:00",
             "source_path": "x", "dimension": ""},
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "departure_sales_amount", "label": "离店销售额",
             "value": "13976.98", "value_type": "amount", "unit": "元",
             "freshness": "realtime", "time_scope": "realtime",
             "captured_at": "2026-09-21T12:12:00+08:00",
             "source_path": "x", "dimension": "", "comparison": "+500.00 (+3.7%)"},
        ]
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        tables = [el for el in elements if el.get("tag") == "table"]
        self.assertEqual(len(tables), 1)
        revenue_tb = tables[0]
        cols = [c.get("display_name") for c in revenue_tb.get("columns", [])]
        self.assertEqual(cols, ["指标", "内容", "较上次"])
        rows_text = str(revenue_tb.get("rows", []))
        self.assertIn("离店销售额", rows_text)
        self.assertIn("13,976.98", rows_text)
        self.assertIn("+500.00 (+3.7%)", rows_text)
        self.assertIn("预订订单", rows_text)

    def test_traffic_exposure_block_units_and_rank(self):
        rows = [
            {"platform": "ctrip", "dataset_id": "flow_data",
             "field_key": "realtime_visitors", "label": "实时访客量", "value": 235,
             "value_type": "integer", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T12:00:00+08:00",
             "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
            {"platform": "ctrip", "dataset_id": "flow_data",
             "field_key": "realtime_visitor_rank", "label": "实时排名", "value": 3,
             "value_type": "rank", "unit": "", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T12:00:00+08:00",
             "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
            {"platform": "ctrip", "dataset_id": "flow_data",
             "field_key": "realtime_starting_price", "label": "实时起价", "value": 328,
             "value_type": "amount", "unit": "元", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T12:00:00+08:00",
             "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
        ]
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("流量曝光", text)
        self.assertIn("访客量", text)
        self.assertIn("实时排名", text)
        self.assertIn("实时起价", text)
        self.assertIn("竞争圈排名", text)

    def test_ad_click_rate_no_double_percent(self):
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": [
            _row("ctrip", "ad_pyramid", "点击率", "2.83%", value_type="percent",
                 unit="", dimension="广告"),
        ]}])
        text = _elements_text(joined_elements(_all_elements(cards["advertising"])))
        self.assertIn("2.83%", text)
        self.assertNotIn("2.83% %", text)

    def test_outline_traffic_fields_dedupe_against_flow_data(self):
        """2026-09-01 实测：经营概览页与流量页都有实时访客量/实时排名；
        流量曝光以 flow_data 行为准，business_outline 同名字段去重不重复展示。"""
        rows = [
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "realtime_visitors", "label": "实时访客量", "value": 202,
             "value_type": "integer", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": ""},
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "realtime_visitor_rank", "label": "实时排名", "value": 240,
             "value_type": "rank", "unit": "", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": ""},
            {"platform": "ctrip", "dataset_id": "flow_data",
             "field_key": "realtime_visitors", "label": "实时访客量", "value": 202,
             "value_type": "integer", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
            {"platform": "ctrip", "dataset_id": "flow_data",
             "field_key": "realtime_visitor_rank", "label": "实时排名", "value": 240,
             "value_type": "rank", "unit": "", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": "", "competitive_rank": "13/21"},
        ]
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertEqual(text.count("访客量"), 1, text)
        self.assertNotIn("经营概览", text)

    def test_outline_traffic_fields_used_when_flow_data_absent(self):
        """flow_data 缺失时经营概览的实时访客量/实时排名仍进流量曝光，不丢数据。"""
        rows = [
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "realtime_visitors", "label": "实时访客量", "value": 202,
             "value_type": "integer", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": ""},
            {"platform": "ctrip", "dataset_id": "business_outline",
             "field_key": "realtime_visitor_rank", "label": "实时排名", "value": 240,
             "value_type": "rank", "unit": "", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:04:00+08:00",
             "source_path": "x", "dimension": ""},
        ]
        cards = self._build([{"platform": "ctrip", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("流量曝光", text)
        self.assertIn("访客量", text)
        self.assertIn("实时排名", text)


class FliggyCardRestructureTests(unittest.TestCase):
    def _build(self, sections):
        return build_global_push_cards(
            store_name="示例门店", read_time="2026-09-01T12:00:00+08:00",
            overall_status="success", overall_summary="ok", score_summary={},
            platform_sections=sections, title_prefixes=None,
        )

    def test_fliggy_traffic_exposure_block(self):
        rows = [
            {"platform": "fliggy", "dataset_id": "operating_data",
             "field_key": "flow_data.详情页曝光人数",
             "label": "详情页曝光人数",
             "value": 532, "value_type": "integer", "unit": "人",
             "freshness": "realtime", "time_scope": "realtime",
             "captured_at": "2026-09-01T12:00:00+08:00", "source_path": "x",
             "dimension": "流量"},
            {"platform": "fliggy", "dataset_id": "operating_data",
             "field_key": "flow_data.预订转化率",
             "label": "预订转化率",
             "value": "4.5%", "value_type": "percent", "unit": "",
             "freshness": "realtime", "time_scope": "realtime",
             "captured_at": "2026-09-01T12:00:00+08:00", "source_path": "x",
             "dimension": "流量"},
        ]
        cards = self._build([{"platform": "fliggy", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("流量曝光", text)
        self.assertIn("详情页曝光人数", text)
        self.assertIn("预订转化率", text)

    def test_fliggy_home_visitors_routed_to_traffic_exposure(self):
        """首页「浏览人数」是流量指标，进流量曝光而非收益概览。
        注：飞猪首页该指标为定性徽章（良好/中等/落后），解析层拒收无数字值，
        只有出现数值时才会进卡。"""
        rows = [
            {"platform": "fliggy", "dataset_id": "home_v1",
             "field_key": "销售额", "label": "销售额", "value": "979 元",
             "value_type": "text", "unit": "元", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "今日"},
            {"platform": "fliggy", "dataset_id": "home_v1",
             "field_key": "浏览人数", "label": "浏览人数", "value": "356人",
             "value_type": "text", "unit": "人", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "今日"},
            {"platform": "fliggy", "dataset_id": "home_v1",
             "field_key": "预订间夜", "label": "预订间夜", "value": "2间夜",
             "value_type": "text", "unit": "间夜", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "今日"},
        ]
        cards = self._build([{"platform": "fliggy", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        text = _elements_text(joined_elements(elements))
        self.assertIn("收益概览", text)
        self.assertIn("流量曝光", text)
        tables = [el for el in elements if el.get("tag") == "table"]
        revenue_tb = next(t for t in tables if "销售额" in str(t.get("rows", [])))
        revenue_fields = [row.get("field") for row in revenue_tb.get("rows", [])]
        self.assertIn("预订间夜", revenue_fields)
        self.assertNotIn("浏览人数", revenue_fields)
        traffic_tb = next(
            (t for t in tables if "浏览人数" in str(t.get("rows", []))), None
        )
        self.assertIsNotNone(traffic_tb)

    def test_fliggy_revenue_dedupes_home_and_operating_same_metric(self):
        """首页与经营数据页都产「预订间夜」（订单量/订单同名互为别名），收益概览去重展示。"""
        rows = [
            {"platform": "fliggy", "dataset_id": "home_v1",
             "field_key": "预订订单量", "label": "预订订单量", "value": "6单",
             "value_type": "text", "unit": "单", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "今日"},
            {"platform": "fliggy", "dataset_id": "home_v1",
             "field_key": "预订间夜", "label": "预订间夜", "value": "7间夜",
             "value_type": "text", "unit": "间夜", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "今日"},
            {"platform": "fliggy", "dataset_id": "operating_data",
             "field_key": "booking_data.预订间夜", "label": "预订间夜", "value": "7间夜",
             "value_type": "text", "unit": "间夜", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "预定"},
            {"platform": "fliggy", "dataset_id": "operating_data",
             "field_key": "booking_data.预订订单", "label": "预订订单", "value": "6单",
             "value_type": "text", "unit": "单", "freshness": "realtime",
             "time_scope": "realtime", "captured_at": "2026-09-01T19:06:00+08:00",
             "source_path": "x", "dimension": "预定"},
        ]
        cards = self._build([{"platform": "fliggy", "status": "success", "rows": rows}])
        tables = [el for el in _all_elements(cards["business"]) if el.get("tag") == "table"]
        revenue_tb = next(t for t in tables if "预订间夜" in str(t.get("rows", [])))
        fields = [row.get("field") for row in revenue_tb.get("rows", [])]
        self.assertEqual(fields.count("预订间夜"), 1)
        self.assertIn("预订订单量", fields)
        self.assertNotIn("预订订单", fields)


class MeituanBusinessGroupTests(unittest.TestCase):
    """2026-09-01 复审：收益概览第一位（值+较上期+同行排名N/M），流量曝光同口径；
    工作台实时行仅在数据中心无收益行时兜底，避免销售额重复展示。"""

    def _build(self, sections):
        return build_global_push_cards(
            store_name="示例门店", read_time="2026-09-01T12:00:00+08:00",
            overall_status="success", overall_summary="ok", score_summary={},
            platform_sections=sections, title_prefixes=None,
        )

    def _dc_row(self, label, value, *, rank="", comparison="", value_type="text", unit=""):
        row = _row("meituan", "data_center_home", label, value,
                   value_type=value_type, unit=unit)
        if rank:
            row["competitive_rank"] = rank
        if comparison:
            row["comparison"] = comparison
        return row

    def test_revenue_overview_first_with_jiaoshangqi_and_rank_columns(self):
        rows = [
            self._dc_row("销售额", "5437.00 元", rank="4/11",
                         comparison="+36.00 (+0.7%)", unit="元"),
            self._dc_row("支付订单数", "10 单", rank="4/11",
                         comparison="+8 (+47.1%)"),
            self._dc_row("浏览人数", "184 人", rank="4/11",
                         comparison="+40 (+27.8%)"),
        ]
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        tables = [el for el in elements if el.get("tag") == "table"]
        revenue_tb = next(t for t in tables if "销售额" in str(t.get("rows", [])))
        cols = [c.get("display_name") for c in revenue_tb.get("columns", [])]
        self.assertEqual(cols, ["指标", "内容", "较上期", "同行排名"])
        self.assertIn("4/11", _elements_text(joined_elements(elements)))
        # 收益概览是美团经营卡第一个板块
        titles = [
            el.get("text", {}).get("content", "")
            for el in elements
            if el.get("tag") == "div" and "收益概览" in el.get("text", {}).get("content", "")
        ]
        self.assertTrue(titles)

    def test_revenue_shows_checkin_nights_not_sales_nights(self):
        """2026-09-01 复审#1：收益概览显示入住间夜，不显示销售间夜；满房率不展示。"""
        rows = [
            self._dc_row("销售额", "5437.00 元", rank="4/11", unit="元"),
            self._dc_row("支付订单数", "10 单", rank="4/11"),
            self._dc_row("入住间夜", "- 间夜"),
            self._dc_row("销售间夜", "18 间夜", rank="4/11"),
            self._dc_row("满房率", "30.00%", rank="2/11"),
        ]
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("入住间夜", text)
        self.assertNotIn("销售间夜", text)
        self.assertNotIn("满房率", text)

    def test_revenue_dash_placeholder_rendered_as_em_dash(self):
        """2026-09-02 复审#1：入住间夜当日暂无数据（页面「- 间夜」）时，
        内容列显示「—」而非「- 间夜」，整行三列统一为占位样式。"""
        rows = [
            self._dc_row("销售额", "5437.00 元", rank="4/11", unit="元"),
            self._dc_row("入住间夜", "- 间夜"),
        ]
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertNotIn("- 间夜", text)
        self.assertIn("入住间夜 — — —", text)

    def test_peer_boards_module_between_revenue_and_traffic(self):
        """2026-09-01 复审#2：同行榜单四榜（N/M）模块，位于收益概览之后。"""
        rows = [
            self._dc_row("销售额", "5437.00 元", rank="4/11", unit="元"),
        ]
        for board, value in (("入住榜", "5/11"), ("销售榜", "4/11"),
                             ("流量榜", "6/11"), ("转化榜", "6/11")):
            r = _row("meituan", "data_center_home", board, value)
            r["dimension"] = "同行榜单"
            rows.append(r)
        rows.append(self._dc_row("浏览人数", "184 人", rank="4/11",
                                 comparison="+40 (+27.8%)"))
        rows[-1]["dimension"] = "流量趋势"
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        text = _elements_text(joined_elements(elements))
        self.assertIn("同行榜单", text)
        for value in ("5/11", "4/11", "6/11"):
            self.assertIn(value, text)
        # 顺序：收益概览 → 同行榜单 → 流量曝光
        titles = [
            el.get("text", {}).get("content", "")
            for el in elements
            if el.get("tag") == "div" and "美团·" in el.get("text", {}).get("content", "")
        ]
        joined = "|".join(titles)
        self.assertIn("收益概览", joined.split("|")[0])
        self.assertLess(joined.index("收益概览"), joined.index("同行榜单"))
        self.assertLess(joined.index("同行榜单"), joined.index("流量曝光"))

    def test_traffic_exposure_shows_jiaoshangqi_and_rank(self):
        """流量曝光 2 项（2026-09-02 需求）：浏览人数 + 浏览-支付转化率；
        曝光-浏览转化率等旧流量字段被抑制不进卡。"""
        rows = [
            self._dc_row("浏览人数", "184 人", rank="4/11",
                         comparison="+40 (+27.8%)"),
            self._dc_row("曝光-浏览转化率", "14.93%", rank="1/11",
                         comparison="+0.6 (+4.2%)"),
            self._dc_row("浏览-支付转化率", "5.47%", rank="6/11",
                         comparison="+0.4 (+7.9%)"),
        ]
        for r in rows:
            r["dimension"] = "流量趋势"
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        text = _elements_text(joined_elements(elements))
        self.assertIn("流量曝光", text)
        self.assertIn("较上期", text)
        self.assertIn("4/11", text)
        self.assertIn("6/11", text)
        self.assertNotIn("曝光-浏览转化率", text)
        tables = [el for el in elements if el.get("tag") == "table"]
        traffic_tb = next(t for t in tables if "浏览人数" in str(t.get("rows", [])))
        cols = [c.get("display_name") for c in traffic_tb.get("columns", [])]
        self.assertEqual(cols, ["指标", "内容", "较上期", "同行排名"])

    def test_traffic_rows_dedupe_home_and_traffic_dataset(self):
        """首页与流量分析的「浏览人数」同名行去重，保留流量分析页签值。"""
        home = self._dc_row("浏览人数", "273 人", rank="3/11")
        home["dimension"] = "流量趋势"
        traffic = self._dc_row("浏览人数", "274 人", rank="3/11")
        traffic["dimension"] = "流量趋势"
        cards = self._build([{"platform": "meituan", "status": "success", "rows": [home, traffic]}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertIn("274 人", text)
        self.assertNotIn("273 人", text)
        self.assertEqual(text.count("浏览人数"), 1, text)

    def test_workbench_rows_do_not_duplicate_data_center_revenue(self):
        """数据中心已有收益行时，工作台同名实时行（无对比/排名的子集）不再进收益概览。"""
        rows = [
            _row("meituan", "workbench_home", "销售额", "5437元"),
            _row("meituan", "workbench_home", "入住间夜", "10间夜"),
            self._dc_row("销售额", "5437.00 元", rank="4/11",
                         comparison="+36.00 (+0.7%)", unit="元"),
        ]
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        text = _elements_text(joined_elements(_all_elements(cards["business"])))
        self.assertEqual(text.count("销售额"), 1, text)

    def test_workbench_only_rows_still_form_revenue_overview(self):
        """数据中心读取失败时，工作台实时行兜底组成收益概览（仍放第一位）。"""
        rows = [
            _row("meituan", "workbench_home", "销售额", "5437元"),
            _row("meituan", "workbench_home", "入住间夜", "10间夜"),
        ]
        cards = self._build([{"platform": "meituan", "status": "success", "rows": rows}])
        elements = list(_all_elements(cards["business"]))
        text = _elements_text(joined_elements(elements))
        self.assertIn("收益概览", text)
        self.assertIn("销售额", text)
        tables = [el for el in elements if el.get("tag") == "table"]
        first_tb_titles = [
            el.get("text", {}).get("content", "")
            for el in elements
            if el.get("tag") == "div" and "·" in el.get("text", {}).get("content", "")
        ]
        self.assertTrue(first_tb_titles)
        self.assertIn("收益概览", first_tb_titles[0])


def _all_elements(cards):
    for card in cards:
        for el in card["elements"]:
            yield el


def joined_elements(elements):
    return list(elements)


def _elements_text(elements):
    parts = []
    for el in elements:
        if el.get("tag") == "div":
            parts.append(el.get("text", {}).get("content", ""))
        elif el.get("tag") == "table":
            for col in el.get("columns", []):
                parts.append(str(col.get("display_name") or ""))
            for row in el.get("rows", []):
                parts.append(" ".join(str(v) for v in row.values()))
    return "\n".join(parts)


if __name__ == "__main__":
    unittest.main()
