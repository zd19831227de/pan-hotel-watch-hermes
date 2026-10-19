from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from app.domain.competitor_prices.messages import (
    FEISHU_MAX_TABLES_PER_CARD,
    build_competitor_price_detail_table_card,
    build_competitor_price_detail_table_cards,
    build_competitor_price_feishu_card,
    build_competitor_price_feishu_mobile_cards,
    build_competitor_price_feishu_message,
)
from app.domain.notifications.feishu import FeishuPushResult


FULL_SAMPLE_COMPETITOR_DATA: dict[str, Any] = {
    "snapshots": [
        {
            "role": "own",
            "hotel_name": "泉吉云州澜江路步行街高空江景酒店",
            "status": "success",
            "availability": "紧张",
            "room_rates": [
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "高级大床房",
                    "breakfast": "无早",
                    "price": {"amount": 416, "currency": "CNY", "display": "¥416"},
                    "discount_amount_yuan": 32,
                    "discount_display": "优惠32",
                },
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "高级大床房",
                    "breakfast": "含早",
                    "price": {"amount": 481, "currency": "CNY", "display": "¥481"},
                    "discount_amount_yuan": 36,
                    "discount_display": "优惠36",
                },
            ],
            "date_context": {"check_in": "2026-06-17", "check_out": "2026-06-18"},
            "tomorrow": {
                "status": "success",
                "availability": "仅剩4间",
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "高级大床房",
                        "breakfast": "无早",
                        "price": {"amount": 394, "currency": "CNY", "display": "¥394"},
                    },
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "高级大床房",
                        "breakfast": "含早",
                        "price": {"amount": 459, "currency": "CNY", "display": "¥459"},
                    },
                ],
                "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
            },
        },
        {
            "role": "competitor_1",
            "hotel_name": "泉吉云州澜江路步行街酒店",
            "status": "success",
            "availability": "正常",
            "room_rates": [
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "商务大床房",
                    "breakfast": "无早",
                    "price": {"amount": 415, "currency": "CNY", "display": "¥415"},
                },
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "商务大床房",
                    "breakfast": "含早",
                    "price": {"amount": 453, "currency": "CNY", "display": "¥453"},
                },
            ],
            "date_context": {"check_in": "2026-06-17", "check_out": "2026-06-18"},
            "tomorrow": {
                "status": "success",
                "availability": "正常",
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "商务大床房",
                        "breakfast": "无早",
                        "price": {"amount": 399, "currency": "CNY", "display": "¥399"},
                    },
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "商务大床房",
                        "breakfast": "含早",
                        "price": {"amount": 437, "currency": "CNY", "display": "¥437"},
                    },
                ],
                "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
            },
        },
        {
            "role": "competitor_2",
            "hotel_name": "泉吉云州澜江路步行街云滩江景酒店",
            "status": "success",
            "availability": "紧张",
            "room_rates": [
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "商务大床房",
                    "breakfast": "无早",
                    "price": {"amount": 423, "currency": "CNY", "display": "¥423"},
                },
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "商务大床房",
                    "breakfast": "含早",
                    "price": {"amount": 461, "currency": "CNY", "display": "¥461"},
                },
            ],
            "date_context": {"check_in": "2026-06-17", "check_out": "2026-06-18"},
            "tomorrow": {
                "status": "success",
                "availability": "紧张",
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "商务大床房",
                        "breakfast": "无早",
                        "price": {"amount": 403, "currency": "CNY", "display": "¥403"},
                    },
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "商务大床房",
                        "breakfast": "含早",
                        "price": {"amount": 440, "currency": "CNY", "display": "¥440"},
                    },
                ],
                "date_context": {"check_in": "2026-06-18", "check_out": "2026-06-19"},
            },
        },
    ],
    "comparison": {
        "breakfast_comparison": {
            "no_breakfast": {
                "own_price": 416,
                "lowest_price": {"role": "competitor_1", "amount": 415},
                "spread": 8,
            },
            "with_breakfast": {
                "own_price": 481,
                "lowest_price": {"role": "competitor_1", "amount": 453},
                "spread": 28,
            },
        }
    },
}


SAMPLE_COMPETITOR_DATA: dict[str, Any] = {
    "snapshots": [
        {
            "role": "own",
            "hotel_name": "本店",
            "label": "本店",
            "availability": "正常",
            "room_rates": [
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "高级大床房",
                    "breakfast_text": "无早",
                    "price": {"amount": 388, "currency": "CNY"},
                }
            ],
            "tomorrow": {
                "status": "success",
                "availability": "正常",
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "高级大床房",
                        "breakfast_text": "无早",
                        "price": {"amount": 399, "currency": "CNY"},
                    }
                ],
            },
        },
        {
            "role": "competitor_1",
            "hotel_name": "竞品A",
            "label": "竞品A",
            "availability": "仅剩 2 间",
            "room_rates": [
                {
                    "source": "room_rate_dom_display_price",
                    "room_type": "高级大床房",
                    "breakfast_text": "无早",
                    "price": {"amount": 368, "currency": "CNY"},
                }
            ],
            "tomorrow": {
                "status": "success",
                "availability": "正常",
                "room_rates": [
                    {
                        "source": "room_rate_dom_display_price",
                        "room_type": "高级大床房",
                        "breakfast_text": "无早",
                        "price": {"amount": 378, "currency": "CNY"},
                    }
                ],
            },
        },
    ]
}


class CompetitorWatchFeishuFormatTests(unittest.TestCase):
    def test_mobile_layout_uses_configured_alias_for_blocked_snapshot(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "configured_alias": "泉吉云州澜江路步行街高空江景酒店",
                    "hotel_name": "登录首页",
                    "label": "本店",
                    "status": "human_required",
                    "page_state": "login_required",
                    "review_reason": "login_required",
                    "date_context": {
                        "check_in": "2026-06-18",
                        "check_out": "2026-06-19",
                    },
                    "room_rates": [],
                },
                {
                    "role": "competitor_1",
                    "configured_alias": "泉吉云州澜江路步行街酒店",
                    "hotel_name": "竞品1",
                    "label": "竞品1",
                    "status": "human_required",
                    "page_state": "login_required",
                    "review_reason": "login_required",
                    "date_context": {
                        "check_in": "2026-06-18",
                        "check_out": "2026-06-19",
                    },
                    "room_rates": [],
                },
            ]
        }

        card = build_competitor_price_feishu_card(
            status="human_required",
            data=data,
            title="澜江路泉吉酒店竞对盯盘简报",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertIn("泉吉云州澜江路步行街高空江景酒店", content)
        self.assertIn("泉吉云州澜江路步行街酒店", content)
        self.assertNotIn("登录首页", content)
        self.assertNotIn("竞品1", content)

    def test_plain_fallback_message_omits_title_line(self) -> None:
        message = build_competitor_price_feishu_message(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
        )

        self.assertTrue(message.startswith("2026-06-17 ~ 2026-06-18"))
        self.assertNotIn("澜江路竞品盯盘", message)
        self.assertNotIn("静里全套房酒店竞对盯盘简报", message)

    def test_hotel_markers_are_colored_in_mobile_cards_but_plain_in_fallback(self) -> None:
        mobile_card = build_competitor_price_feishu_card(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )
        desktop_card = build_competitor_price_feishu_card(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品价格",
            layout="desktop_table",
        )
        fallback = build_competitor_price_feishu_message(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
        )

        content = _all_text(mobile_card)
        blue_own_hotel_line = (
            "<font color='blue'>🏠 **泉吉云州澜江路步行街高空江景酒店**（本店）</font>"
        )
        self.assertIn(blue_own_hotel_line, content)
        self.assertIn(
            "<font color='turquoise'>🏪 **泉吉云州澜江路步行街酒店**</font>",
            content,
        )
        self.assertIn(
            "<font color='purple'>🌉 **泉吉云州澜江路步行街云滩江景酒店**</font>",
            content,
        )
        self.assertNotIn("<font color='orange'>", content)
        self.assertNotIn("<font color='green'>🏪", content)
        self.assertNotIn(
            "**泉吉云州澜江路步行街高空江景酒店**<font color='blue'>（本店）</font>",
            content,
        )
        self.assertIn("（本店）", fallback)
        self.assertNotIn("<font", fallback)
        self.assertNotIn("<font", _all_text(desktop_card))

    def test_interactive_fallback_uses_timed_title_without_duplicate_body_title(self) -> None:
        from unittest.mock import patch

        from app.domain.notifications.feishu import FeishuPushResult, push_interactive_card_to_feishu_webhook

        post_results = [
            FeishuPushResult(status="failed", summary="card failed"),
            FeishuPushResult(status="success", summary="text sent"),
        ]

        async def fake_post(**kwargs: Any) -> FeishuPushResult:
            return post_results.pop(0)

        fallback_body = "📅 今日入住 | 2026-06-29 ▶ 2026-06-30 离店"
        with patch(
            "app.domain.notifications.feishu._post_feishu_webhook",
            side_effect=fake_post,
        ) as mock_post:
            result = asyncio.run(
                push_interactive_card_to_feishu_webhook(
                    title="静里全套房酒店竞对盯盘简报",
                    card={"header": {"title": {"content": "card"}}},
                    webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
                    fallback_text=fallback_body,
                )
            )

        self.assertEqual(result.status, "success")
        text_payload = mock_post.call_args_list[1].kwargs["payload"]
        pushed_text = text_payload["content"]["text"]
        self.assertRegex(pushed_text, r"^静里全套房酒店竞对盯盘简报 · \d{2}:\d{2}\n\n📅 今日入住")
        self.assertEqual(pushed_text.count("静里全套房酒店竞对盯盘简报"), 1)

    def test_mobile_cards_use_one_card_per_date_without_hotel_chunking(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": f"hotel_{index}",
                    "hotel_name": f"竞品酒店{index}",
                    "status": "success",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [_rate("高级大床房", "无早", 400 + index, 20)],
                    "tomorrow": {
                        "status": "success",
                        "availability": "正常",
                        "date_context": {"check_in": "2026-07-02", "check_out": "2026-07-03"},
                        "room_rates": [_rate("高级大床房", "无早", 390 + index, 20)],
                    },
                }
                for index in range(1, 7)
            ]
        }

        cards = build_competitor_price_feishu_mobile_cards(
            status="success",
            data=data,
            title="澜江路竞品盯盘",
        )

        self.assertEqual(len(cards), 2)
        content = _all_text({"cards": cards})
        self.assertIn("📅 今日入住", content)
        self.assertIn("📅 明日入住", content)
        self.assertNotRegex(content, r"全量\s+\d+/\d+")
        for index in range(1, 7):
            self.assertIn(f"竞品酒店{index}", content)

    def test_today_only_scope_omits_tomorrow_from_push_views(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": f"hotel_{index}",
                    "hotel_name": f"竞品酒店{index}",
                    "status": "success",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [_rate("高级大床房", "无早", 400 + index, 20)],
                    "tomorrow": {
                        "status": "success",
                        "availability": "正常",
                        "date_context": {"check_in": "2026-07-02", "check_out": "2026-07-03"},
                        "room_rates": [_rate("高级大床房", "无早", 390 + index, 20)],
                    },
                }
                for index in range(1, 4)
            ]
        }

        cards = build_competitor_price_feishu_mobile_cards(
            status="success",
            data=data,
            title="澜江路竞品盯盘",
            push_date_scope="today_only",
        )

        self.assertEqual(len(cards), 1)
        content = _all_text({"cards": cards})
        self.assertIn("📅 今日入住", content)
        self.assertNotIn("明日", content)
        self.assertNotIn("391", content)

        detail_cards = build_competitor_price_detail_table_cards(
            status="success",
            data=data,
            title="澜江路竞品价格",
            push_date_scope="today_only",
        )
        detail_content = _all_text({"cards": detail_cards})
        self.assertNotIn("明日", detail_content)
        self.assertNotIn("各店最低价对照", detail_content)
        self.assertNotIn("391", detail_content)

        plain = build_competitor_price_feishu_message(
            status="success",
            data=data,
            push_date_scope="today_only",
        )
        self.assertIn("今日", plain)
        self.assertNotIn("明日", plain)
        self.assertNotIn("391", plain)

    def test_mobile_layout_adapts_primary_summary_for_full_capture(self) -> None:
        cards = build_competitor_price_feishu_mobile_cards(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品盯盘",
        )
        card = cards[0]
        content = _all_text({"cards": cards})
        self.assertNotIn("table", _tags(card))
        self.assertIn("2026-06-17 ~ 2026-06-18", content)
        self.assertIn("📅 今日入住 | 2026-06-17 ▶ 2026-06-18 离店", content)
        self.assertIn("泉吉云州澜江路步行街高空江景酒店", content)
        self.assertIn("主房型", content)
        self.assertIn("高级大床房", content)
        self.assertIn("**¥416**", content)
        self.assertIn("**¥481**", content)
        self.assertIn("泉吉云州澜江路步行街酒店", content)
        self.assertIn("**¥415**", content)
        self.assertIn("2026-06-18 ~ 2026-06-19", content)
        self.assertIn("📅 明日入住 | 2026-06-18 ▶ 2026-06-19 离店", content)
        self.assertIn("**¥394**", content)
        self.assertIn("📝 简析", content)
        self.assertIn("无早：我们¥416，比最低高¥1；含早：我们¥481，比最低高¥28", content)
        self.assertNotIn("▶ 房型", content)
        today_card = cards[0]
        tomorrow_card = cards[1]
        self.assertIn("📝 简析", _all_text(today_card))
        self.assertIn("📝 简析", _all_text(tomorrow_card))
        self.assertIn("无早：我们最低，领先¥5", _all_text(tomorrow_card))
        self.assertIn("含早：我们¥459，比最低高¥22", _all_text(tomorrow_card))
        self.assertNotIn("另有", content)
        self.assertNotIn("可信来源", content)
        self.assertNotIn("room_rate_dom_display_price", content)

    def test_mobile_layout_is_block_card_without_table_component(self) -> None:
        card = build_competitor_price_feishu_card(
            status="success",
            data=SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )

        self.assertIn("澜江路竞品价格", card["header"]["title"]["content"])
        self.assertNotIn("table", _tags(card))
        content = _all_text(card)
        self.assertIn("本店", content)
        self.assertIn("竞品A", content)
        self.assertIn("主房型", content)
        self.assertNotIn("room_rate_dom_display_price", content)

    def test_mobile_layout_ignores_untrusted_visible_text_prices(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "本店",
                    "availability": "正常",
                    "room_rates": [
                        {
                            "source": "visible_text_price",
                            "room_type": "高级大床房",
                            "breakfast": "无早",
                            "price": {"amount": 199, "currency": "CNY"},
                        },
                        {
                            "source": "room_rate_dom_display_price",
                            "room_type": "高级大床房",
                            "breakfast": "无早",
                            "price": {"amount": 388, "currency": "CNY"},
                        },
                    ],
                    "date_context": {"check_in": "2026-06-17", "check_out": "2026-06-18"},
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertIn("¥388", content)
        self.assertNotIn("¥199", content)
        self.assertNotIn("visible_text_price", content)

    def test_desktop_layout_uses_feishu_table_component(self) -> None:
        card = build_competitor_price_feishu_card(
            status="success",
            data=SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品价格",
            layout="desktop_table",
        )

        self.assertIn("澜江路竞品价格", card["header"]["title"]["content"])
        self.assertIn("table", _tags(card))
        table = next(element for element in card["elements"] if element.get("tag") == "table")
        self.assertGreaterEqual(len(table["columns"]), 5)
        self.assertGreaterEqual(len(table["rows"]), 2)
        self.assertIn("hotel", [column["name"] for column in table["columns"]])
        self.assertIn("本店", str(table["rows"]))
        self.assertIn("竞品A", str(table["rows"]))

    def test_desktop_layout_keeps_discount_in_price_cells(self) -> None:
        card = build_competitor_price_feishu_card(
            status="success",
            data=FULL_SAMPLE_COMPETITOR_DATA,
            title="澜江路竞品价格",
            layout="desktop_table",
        )

        table = next(element for element in card["elements"] if element.get("tag") == "table")
        self.assertIn("'no_breakfast': '**¥416**（优惠**32**）'", str(table["rows"]))
        self.assertIn("'with_breakfast': '**¥481**（优惠**36**）'", str(table["rows"]))

    def test_detail_table_card_uses_per_hotel_feishu_tables_for_all_room_rates(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品A",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("高级大床房", "无早", 423, 29, availability="正常"),
                        _rate("高级大床房", "含早", 461, 29, availability="正常"),
                        _rate("商务大床房", "无早", 488, 31, availability="仅剩4间"),
                        _rate("商务大床房", "含早", 526, 31, availability="仅剩4间"),
                    ],
                    "tomorrow": {
                        "status": "success",
                        "availability": "仅剩4间",
                        "date_context": {"check_in": "2026-07-02", "check_out": "2026-07-03"},
                        "room_rates": [
                            _rate("高级大床房", "无早", 433, 29, availability="仅剩4间"),
                            _rate("商务大床房", "含早", 536, 31, availability="正常"),
                        ],
                    },
                }
            ]
        }

        cards = build_competitor_price_detail_table_cards(
            status="success",
            data=data,
            title="澜江路竞品价格",
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertIn("table", _tags(card))
        content = _all_text(card)
        self.assertIn("2026-07-01~2026-07-03", card["header"]["title"]["content"])
        self.assertNotIn("今日续", card["header"]["title"]["content"])
        self.assertNotIn("明日续", card["header"]["title"]["content"])
        self.assertNotIn("📝 简析", content)
        self.assertNotIn("酒店｜房型｜早餐", content)
        self.assertIn("竞品A", content)
        self.assertIn("各店最低价对照", content)
        tables = [element for element in card["elements"] if element.get("tag") == "table"]
        self.assertEqual(len(tables), 2)
        today_table = tables[0]
        self.assertEqual(today_table["page_size"], 4)
        self.assertLessEqual(len(tables), FEISHU_MAX_TABLES_PER_CARD)
        self.assertEqual(
            [column["name"] for column in today_table["columns"]],
            ["room", "breakfast", "price", "discount", "inventory"],
        )
        self.assertIn("高级大床房", str(today_table["rows"]))
        self.assertIn("仅剩4间", str(today_table["rows"]))
        self.assertNotIn("竞品A", str(today_table["rows"]))

    def test_detail_table_cards_split_by_date_to_respect_feishu_table_limit(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": f"hotel_{index}",
                    "hotel_name": f"酒店{index}",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [_rate("高级大床房", "无早", 400 + index, 20)],
                    "tomorrow": {
                        "status": "success",
                        "date_context": {"check_in": "2026-07-02", "check_out": "2026-07-03"},
                        "room_rates": [_rate("高级大床房", "无早", 390 + index, 20)],
                    },
                }
                for index in range(1, 4)
            ]
        }

        cards = build_competitor_price_detail_table_cards(status="success", data=data, title="测试")
        self.assertEqual(len(cards), 1)
        tables = [element for element in cards[0]["elements"] if element.get("tag") == "table"]
        self.assertEqual(len(tables), 4)
        self.assertLessEqual(len(tables), FEISHU_MAX_TABLES_PER_CARD)
        self.assertNotIn("续", cards[0]["header"]["title"]["content"])

    def test_mobile_layout_shows_primary_summary_for_long_own_hotel_name(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "静里全套房酒店（云州澜江路望川路地铁站店）",
                    "status": "success",
                    "availability": "紧张",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("精英套房", "无早", 246, 173, availability="仅剩2间"),
                        _rate("精英套房", "含早", 260, 183, availability="仅剩2间"),
                        _rate("悦聚套房", "无早", 260, 183),
                        _rate("悦聚套房", "含早", 274, 193),
                    ],
                },
                {
                    "role": "competitor_1",
                    "hotel_name": "菲想酒店（云州澜江路步行街云渡桥地铁站店）",
                    "status": "success",
                    "availability": "仅剩3间",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("静享景观大床房", "无早", 209, 33, availability="仅剩3间"),
                        _rate("静享景观大床房", "含早", 228, 35, availability="仅剩3间"),
                    ],
                },
            ]
        }

        cards = build_competitor_price_feishu_mobile_cards(
            status="success",
            data=data,
            title="澜江路竞品盯盘",
        )
        content = _all_text(cards[0])

        self.assertIn("主房型", content)
        self.assertIn("精英套房", content)
        self.assertIn("**¥246**", content)
        self.assertIn("**¥260**", content)
        self.assertIn("（本店）", content)
        self.assertIn("其它 1 个房型", content)
        self.assertNotIn("其它 1 个房型 ↓", content)
        self.assertNotIn("另有", content)
        self.assertNotIn("全量房型 · 4条", content)

    def test_mobile_layout_shows_all_trusted_room_rates_in_collapsible_panels(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品A",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("高级大床房", "无早", 423, 29),
                        _rate("商务大床房", "无早", 488, 31),
                        _rate("高级大床房", "含早", 461, 29),
                        _rate("商务大床房", "含早", 526, 31),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertNotIn("table", _tags(card))
        self.assertIn("collapsible_panel", _tags(card))
        self.assertIn("主房型", content)
        self.assertIn("高级大床房", content)
        self.assertIn("商务大床房", content)
        self.assertIn("**¥423**", content)
        self.assertIn("**¥461**", content)
        self.assertIn("**¥488**", content)
        self.assertIn("**¥526**", content)
        self.assertIn("其它 1 个房型", content)
        self.assertNotIn("其它 1 个房型 ↓", content)
        self.assertNotIn("另有", content)

    def test_primary_room_type_prices_ignore_cheaper_secondary_room_types(self) -> None:
        from app.domain.browser.ctrip_public_competitor_comparison import (
            _build_public_competitor_breakfast_comparison,
        )
        from app.domain.competitor_prices.messages import _competitor_rows

        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "本店",
                    "status": "success",
                    "availability": "正常",
                    "room_rates": [
                        _rate("高级大床房", "无早", 416, 32),
                        _rate("高级大床房", "含早", 481, 36),
                        _rate("豪华套房", "无早", 211, 50),
                        _rate("豪华套房", "含早", 350, 50),
                    ],
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                },
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品A",
                    "status": "success",
                    "availability": "正常",
                    "room_rates": [
                        _rate("商务大床房", "无早", 415, 29),
                        _rate("商务大床房", "含早", 453, 29),
                        _rate("经济单人间", "无早", 199, 10),
                    ],
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                },
            ]
        }

        rows = _competitor_rows(data["snapshots"])
        own_row = next(row for row in rows if row["role"] == "own")
        competitor_row = next(row for row in rows if row["role"] == "competitor_1")
        self.assertIn("416", own_row["no_breakfast"])
        self.assertIn("481", own_row["with_breakfast"])
        self.assertNotIn("211", own_row["no_breakfast"])
        self.assertIn("415", competitor_row["no_breakfast"])
        self.assertNotIn("199", competitor_row["no_breakfast"])

        comparison = _build_public_competitor_breakfast_comparison(data["snapshots"])
        no_breakfast = comparison["no_breakfast"]
        self.assertEqual(no_breakfast["own_price"], 416)
        self.assertEqual(no_breakfast["lowest_price"]["amount"], 415)
        self.assertEqual(no_breakfast["spread"], 1)

        card = build_competitor_price_feishu_card(
            status="success",
            data={**data, "comparison": {"breakfast_comparison": comparison}},
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )
        content = _all_text(card)
        self.assertIn("无早：我们¥416，比最低高¥1", content)
        self.assertNotIn("价差¥139", content)
        self.assertNotIn("价差¥204", content)

    def test_primary_room_type_uses_first_captured_room_type(self) -> None:
        from app.domain.competitor_prices.messages import _competitor_rows

        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "本店",
                    "status": "success",
                    "availability": "正常",
                    "room_rates": [
                        _rate("豪华套房", "含早", 350, 50),
                        _rate("高级大床房", "无早", 416, 32),
                        _rate("豪华套房", "无早", 211, 50),
                    ],
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                }
            ]
        }

        own_row = _competitor_rows(data["snapshots"])[0]
        self.assertIn("350", own_row["with_breakfast"])
        self.assertIn("211", own_row["no_breakfast"])
        self.assertIn("豪华套房", own_row["no_breakfast_room_type"])

    def test_summary_compares_each_hotel_first_room_type_prices(self) -> None:
        from app.domain.competitor_prices.messages import _format_compact_summary, _competitor_rows

        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "静里全套房酒店",
                    "status": "success",
                    "availability": "紧张",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("精英套房", "无早", 246, 173),
                        _rate("精英套房", "含早", 260, 183),
                        _rate("悦聚套房", "无早", 260, 183),
                    ],
                },
                {
                    "role": "competitor_1",
                    "hotel_name": "菲想酒店",
                    "status": "success",
                    "availability": "仅剩3间",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("静享景观大床房", "无早", 209, 33),
                        _rate("静享景观大床房", "含早", 228, 35),
                    ],
                },
            ]
        }
        rows = _competitor_rows(data["snapshots"])
        summary = _format_compact_summary(data, rows)
        self.assertIn("无早：我们¥246，比最低高¥37", summary)
        self.assertIn("含早：我们¥260，比最低高¥32", summary)

    def test_scheduler_ignores_desktop_table_webhook_and_sends_mobile_cards(self) -> None:
        from app.domain.competitor_prices import scheduler

        calls: list[dict[str, Any]] = []

        async def fake_push(**kwargs: Any) -> FeishuPushResult:
            calls.append(kwargs)
            return FeishuPushResult(status="success", summary="sent")

        original = scheduler.push_interactive_card_to_feishu_webhook
        scheduler.push_interactive_card_to_feishu_webhook = fake_push
        try:
            result = asyncio.run(
                scheduler._push_competitor_watch(
                    {
                        "group_key": "ops_watch",
                        "name": "默认标题",
                        "brief_title": "澜江路竞品价格",
                        "feishu_mobile_webhook_url": "https://open.feishu.cn/mobile",
                        "feishu_desktop_webhook_url": "https://open.feishu.cn/desktop",
                        "feishu_card_layout": "desktop_table",
                    },
                    SimpleNamespace(status="success", data=SAMPLE_COMPETITOR_DATA),
                )
            )
        finally:
            scheduler.push_interactive_card_to_feishu_webhook = original

        self.assertEqual(result["status"], "success")
        self.assertEqual([call["title"] for call in calls], ["澜江路竞品价格", "澜江路竞品价格"])
        self.assertNotIn("table", _tags(calls[0]["card"]))
        self.assertIn("主房型", _all_text(calls[0]["card"]))
        mobile_calls = [call for call in calls if call["webhook_url"] == "https://open.feishu.cn/mobile"]
        desktop_calls = [call for call in calls if call["webhook_url"] == "https://open.feishu.cn/desktop"]
        self.assertEqual(len(mobile_calls), 2)
        self.assertEqual(len(desktop_calls), 0)
        self.assertEqual(result["target_count"], 1)
        self.assertEqual([target["target"] for target in result["targets"]], ["mobile"])
        self.assertIsNone(mobile_calls[0]["fallback_text"])

    def test_scheduler_push_respects_today_only_scope(self) -> None:
        from app.domain.competitor_prices import scheduler

        calls: list[dict[str, Any]] = []

        async def fake_push(**kwargs: Any) -> FeishuPushResult:
            calls.append(kwargs)
            return FeishuPushResult(status="success", summary="sent")

        original = scheduler.push_interactive_card_to_feishu_webhook
        scheduler.push_interactive_card_to_feishu_webhook = fake_push
        try:
            result = asyncio.run(
                scheduler._push_competitor_watch(
                    {
                        "group_key": "ops_watch",
                        "name": "默认标题",
                        "brief_title": "澜江路竞品价格",
                        "feishu_mobile_webhook_url": "https://open.feishu.cn/mobile",
                        "push_date_scope": "today_only",
                    },
                    SimpleNamespace(status="success", data=SAMPLE_COMPETITOR_DATA),
                )
            )
        finally:
            scheduler.push_interactive_card_to_feishu_webhook = original

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 1)
        content = _all_text(calls[0]["card"])
        self.assertIn("📅 今日入住", content)
        self.assertNotIn("明日", content)

    def test_mobile_extra_room_count_groups_breakfast_variants(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品A",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("静享景观大床房", "无早", 209, 33),
                        _rate("静享景观大床房·双早", "含早", 228, 35),
                        _rate("尊享景观大床房", "无早", 250, 40),
                        _rate("尊享景观大床房·双早", "含早", 270, 42),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )
        content = _all_text(card)

        self.assertIn("其它 1 个房型", content)
        self.assertNotIn("其它 1 个房型 ↓", content)
        self.assertNotIn("另有 **3** 个", content)
        self.assertNotIn("另有 **4** 个", content)
        self.assertNotIn("另有", content)
        self.assertIn("尊享景观大床房", content)
        self.assertIn("无早 **¥250**", content)
        self.assertIn("含早 **¥270**", content)

    def test_mobile_extra_room_panel_shows_distinct_cloud_series_room_types(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "江景竞品",
                    "availability": "仅剩3间",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("景·一线江景大床房", "无早", 452, 87, availability="仅剩3间"),
                        _rate("景·一线江景大床房", "含早", 475, 92, availability="仅剩3间"),
                        _rate("云·高空江景大床房", "无早", 611, 108, availability="房量紧张"),
                        _rate("云·高空江景大床房", "含早", 658, 117, availability="房量紧张"),
                        _rate(
                            "云·高空江景双床房（零压床垫+全屋声控+mini冰箱）",
                            "无早",
                            680,
                            120,
                            availability="仅剩2间",
                        ),
                        _rate(
                            "云·高空江景双床房（零压床垫+全屋声控+mini冰箱）",
                            "含早",
                            710,
                            125,
                            availability="仅剩2间",
                        ),
                        _rate("观景·高空江景大床房", "无早", 923, 176, availability="仅剩1间"),
                        _rate("观景·高空江景大床房", "含早", 945, 182, availability="仅剩1间"),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="江景竞品",
            layout="mobile_blocks",
        )
        content = _all_text(card)

        self.assertIn("其它 3 个房型", content)
        self.assertIn("云·高空江景大床房", content)
        self.assertIn("云·高空江景双床房", content)
        self.assertIn("观景·高空江景大床房", content)

    def test_mobile_extra_room_panel_does_not_merge_cloud_xi_room_descriptors(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "云夕竞品",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("岚夕·雅致大床房", "无早", 269, 30),
                        _rate("岚夕·雅致大床房", "含早", 304, 32),
                        _rate("云夕·城景大床房", "无早", 394, 36),
                        _rate("云夕·城景大床房", "含早", 430, 38),
                        _rate("云夕·城景大床房（高楼层）", "无早", 421, 39),
                        _rate("云夕·城景大床房（高楼层）", "含早", 457, 41),
                        _rate("云夕·逸致标准房丨记忆床垫+无线投屏", "无早", 409, 35),
                        _rate("云夕·逸致标准房丨记忆床垫+无线投屏", "含早", 444, 37),
                        _rate("云夕丨逸致家庭房", "无早", 551, 42),
                        _rate("云夕丨逸致家庭房", "含早", 590, 44),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="云夕竞品",
            layout="mobile_blocks",
        )
        content = _all_text(card)

        self.assertIn("其它 4 个房型", content)
        self.assertIn("云夕·城景大床房", content)
        self.assertIn("云夕·城景大床房（高楼层）", content)
        self.assertIn("云夕·逸致标准房", content)
        self.assertIn("云夕丨逸致家庭房", content)

    def test_mobile_extra_room_panel_uses_native_icon_and_two_line_rows(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "competitor_1",
                    "hotel_name": "竞品A",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("高级大床房", "无早", 423, 29),
                        _rate("高级大床房", "含早", 461, 29),
                        _rate("豪华大床房", "无早", 517, 32),
                        _rate("豪华大床房", "含早", 554, 35),
                        _rate("江景大床房", "无早", 604, 38),
                        _rate("江景大床房", "含早", 642, 40),
                        _rate("豪华双床房", "无早", 657, 41, availability="仅剩2间"),
                        _rate("豪华双床房", "含早", 695, 43, availability="仅剩2间"),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )

        panel = next(element for element in card["elements"] if element.get("tag") == "collapsible_panel")
        header = panel["header"]
        self.assertEqual(header["icon"]["tag"], "standard_icon")
        self.assertEqual(header["icon"]["token"], "down-small-ccm_outlined")
        self.assertEqual(header["icon_position"], "right")
        self.assertEqual(header["icon_expanded_angle"], -180)
        self.assertEqual(header["title"]["content"], "**其它 3 个房型**")

        content = panel["elements"][0]["content"]
        self.assertIn(
            "豪华大床房  🟢 正常\n"
            "无早 **¥517**（优惠**32**）｜含早 **¥554**（优惠**35**）",
            content,
        )
        self.assertIn(
            "豪华双床房  🔴 仅剩2间\n"
            "无早 **¥657**（优惠**41**）｜含早 **¥695**（优惠**43**）",
            content,
        )
        self.assertNotIn("· 豪华大床房 · 无早", content)
        self.assertNotIn("↓", str(panel))

    def test_mobile_primary_room_uses_two_line_mobile_format(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "泉吉云州澜江路步行街高空江景酒店",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("商务大床房", "无早", 482, 30),
                        _rate("商务大床房", "含早", 520, 32),
                        _rate("豪华大床房", "无早", 517, 32),
                        _rate("豪华大床房", "含早", 554, 35),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="澜江路竞品价格",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertIn(
            "主房型 商务大床房  🟢 正常\n"
            "无早 **¥482**（优惠**30**）｜含早 **¥520**（优惠**32**）",
            content,
        )
        self.assertNotIn("主房型 商务大床房 · 无早", content)
        self.assertNotIn("库存 正常", content)

    def test_mobile_room_price_lines_show_counted_breakfast_labels(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "测试酒店",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("商务大床房", "无早", 482, 30),
                        _rate("商务大床房", "1份早餐", 520, 32),
                        _rate("豪华大床房", "无早", 517, 32),
                        _rate("豪华大床房", "2份早餐", 554, 35),
                        _rate("家庭套房", "无早", 604, 38),
                        _rate("家庭套房", "三早", 642, 40),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="测试",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertIn(
            "主房型 商务大床房  🟢 正常\n"
            "无早 **¥482**（优惠**30**）｜含单早 **¥520**（优惠**32**）",
            content,
        )
        self.assertIn(
            "豪华大床房  🟢 正常\n"
            "无早 **¥517**（优惠**32**）｜含双早 **¥554**（优惠**35**）",
            content,
        )
        self.assertIn(
            "家庭套房  🟢 正常\n"
            "无早 **¥604**（优惠**38**）｜含三早 **¥642**（优惠**40**）",
            content,
        )
        self.assertNotIn("｜含早 **¥520**", content)
        self.assertNotIn("｜含早 **¥554**", content)
        self.assertNotIn("｜含早 **¥642**", content)

    def test_mobile_extra_room_uses_lowest_counted_breakfast_variant(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "测试酒店",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("商务大床房", "无早", 482, 30),
                        _rate("商务大床房", "含早", 520, 32),
                        _rate("豪华大床房", "无早", 517, 32),
                        _rate("豪华大床房", "1份早餐", 530, 33),
                        _rate("豪华大床房", "2份早餐", 554, 35),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="测试",
            layout="mobile_blocks",
        )

        content = _all_text(card)
        self.assertIn(
            "豪华大床房  🟢 正常\n"
            "无早 **¥517**（优惠**32**）｜含单早 **¥530**（优惠**33**）",
            content,
        )
        self.assertNotIn("含双早 **¥554**", content)

    def test_mobile_primary_room_type_keeps_longer_display_name(self) -> None:
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "测试酒店",
                    "status": "success",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate("静享景观豪华大床房（城景落地窗）", "无早", 300, 20),
                        _rate("静享景观豪华大床房（城景落地窗）", "含早", 340, 20),
                    ],
                }
            ]
        }

        card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="测试",
            layout="mobile_blocks",
        )
        content = _all_text(card)

        self.assertIn("静享景观豪华大床房", content)
        self.assertNotIn("静享景观豪华大床…", content)

    def test_room_type_display_trims_ctrip_promo_suffixes(self) -> None:
        long_primary = "静享景观大床房『房内庭院景+全屋声控+mini冰箱+超长景观窗+亲子布置』"
        long_extra_pipe = "水夕·行政大床房丨空气净化器+软榻沙发+MINI冰箱"
        long_extra_paren = "景·一线江景大床房（零压床垫+全屋声控+mini冰箱）"
        data = {
            "snapshots": [
                {
                    "role": "own",
                    "hotel_name": "测试酒店",
                    "status": "success",
                    "availability": "正常",
                    "date_context": {"check_in": "2026-07-01", "check_out": "2026-07-02"},
                    "room_rates": [
                        _rate(long_primary, "无早", 300, 20),
                        _rate(long_primary, "含早", 340, 20),
                        _rate(long_extra_pipe, "无早", 420, 30),
                        _rate(long_extra_pipe, "含早", 460, 30),
                        _rate(long_extra_paren, "无早", 520, 40),
                        _rate(long_extra_paren, "含早", 560, 40),
                    ],
                }
            ]
        }

        mobile_card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="测试",
            layout="mobile_blocks",
        )
        desktop_card = build_competitor_price_feishu_card(
            status="success",
            data=data,
            title="测试",
            layout="desktop_table",
        )

        mobile_content = _all_text(mobile_card)
        desktop_rows = str(
            next(element for element in desktop_card["elements"] if element.get("tag") == "table")[
                "rows"
            ]
        )
        combined = f"{mobile_content}\n{desktop_rows}"
        self.assertIn("静享景观大床房", combined)
        self.assertIn("水夕·行政大床房", combined)
        self.assertIn("景·一线江景大床房", combined)
        self.assertNotIn("百寸幕布", combined)
        self.assertNotIn("空气净化器", combined)
        self.assertNotIn("静享景观大床房『", combined)
        self.assertNotIn("水夕·行政大床房丨", combined)
        self.assertIn("景·一线江景大床房（零压床垫+全屋声控+min…", combined)


def _tags(value: Any) -> list[str]:
    tags: list[str] = []
    if isinstance(value, dict):
        tag = value.get("tag")
        if isinstance(tag, str):
            tags.append(tag)
        for child in value.values():
            tags.extend(_tags(child))
    elif isinstance(value, list):
        for child in value:
            tags.extend(_tags(child))
    return tags


def _all_text(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(_all_text(child) for child in value.values())
    if isinstance(value, list):
        return "\n".join(_all_text(child) for child in value)
    return str(value)


def _rate(
    room_type: str,
    breakfast: str,
    amount: int,
    discount: int,
    *,
    availability: str = "正常",
) -> dict[str, Any]:
    return {
        "source": "room_rate_dom_display_price",
        "room_type": room_type,
        "breakfast": breakfast,
        "availability": availability,
        "price": {"amount": amount, "currency": "CNY", "display": f"¥{amount}"},
        "discount_amount_yuan": discount,
        "discount_display": f"优惠{discount}",
    }


if __name__ == "__main__":
    unittest.main()
