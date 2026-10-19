from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any
from unittest import mock

from app.domain.notifications.pricing_query_command import (
    classify_pricing_query_command,
)


class PricingQueryCommandClassifierTests(unittest.TestCase):
    def test_basic_query_variants(self) -> None:
        for text in ("查房价", "查询房价", "房价查询", "房价情况", "房价列表", "查一下房价"):
            result = classify_pricing_query_command(text)
            self.assertIsNotNone(result, text)
            assert result is not None
            self.assertEqual(result["status"], "ready", text)
            self.assertEqual(result["date_label"], "today", text)
            self.assertIsNone(result["platform"], text)

    def test_platform_and_date_in_any_order(self) -> None:
        result = classify_pricing_query_command("查一下明天携程房价")
        self.assertEqual(result["date_label"], "tomorrow")
        self.assertEqual(result["platform"], "ctrip")

        result = classify_pricing_query_command("查一下今天美团房价情况")
        self.assertEqual(result["date_label"], "today")
        self.assertEqual(result["platform"], "meituan")

        result = classify_pricing_query_command("看看飞猪的房价")
        self.assertEqual(result["platform"], "fliggy")
        self.assertEqual(result["date_label"], "today")

    def test_adjust_command_is_not_a_query(self) -> None:
        self.assertIsNone(
            classify_pricing_query_command("把明天携程奢华三卧套房调整到1100元")
        )
        self.assertIsNone(classify_pricing_query_command("今天高级大床房调到420"))

    def test_chart_followup_phrasing_is_not_a_query(self) -> None:
        # 2026-10-08 晚生产实证（立瑞 21:16）：「图上需要加上本店，并且要准确到
        # 房价个位数」是图表追问，不是价目表查询；须与网关词表同步排除。
        for text in (
            "图上需要加上本店，并且要准确到房价个位数",
            "重新出图，标注精确到个位",
            "把这张对比图重画一下",
        ):
            self.assertIsNone(classify_pricing_query_command(text), text)

    def test_methodology_phrasing_is_not_a_query(self) -> None:
        # 2026-10-08：方法论问法（怎么做/铺排/操盘）不是查价指令，不应触发
        # 房态卡转发；词表与 pan-hotel-hermes/router/pricing_query_routing.py 同步。
        for text in (
            "房价铺排价格怎么做",
            "房价铺排怎么做",
            "价格铺排怎么做",
            "OTA平台操盘怎么做",
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_pricing_query_command(text), text)
        # 正向对照：真实查价不受影响。
        self.assertEqual(
            classify_pricing_query_command("查一下今天房价")["status"], "ready"
        )

    def test_unrelated_text_is_not_a_query(self) -> None:
        self.assertIsNone(classify_pricing_query_command("今天竞品价格怎么样"))
        self.assertIsNone(classify_pricing_query_command(""))
        self.assertIsNone(classify_pricing_query_command("房价预测分析报告"))

    def test_room_state_and_selling_price_tokens(self) -> None:
        # 2026-09-22 第二批：房态/卖价/售价/房型+多少钱 也是价目表口径
        # （卡片含 房型/卖价/底价/房态 列）。词表必须与
        # pan-hotel-hermes/router/pricing_query_routing.py 同步。
        for text in ("看下房态", "明天还有房吗", "携程大床房多少钱", "本店套房卖价多少"):
            with self.subTest(text=text):
                result = classify_pricing_query_command(text)
                self.assertIsNotNone(result, text)
                assert result is not None
                self.assertEqual(result["status"], "ready", text)
        # 退款语境的「多少钱」不当查价。
        self.assertIsNone(classify_pricing_query_command("今天退了多少钱"))

    def test_mention_is_stripped(self) -> None:
        result = classify_pricing_query_command("@机器人 查房价")
        self.assertEqual(result["status"], "ready")

    def test_room_price_wording_variants(self) -> None:
        # 2026-10-14 生产实证（17:55-18:03 三连问）：运营说「各房间价格」
        # 「各个房型的价格」——不含连续「房价/房型价格」token，dispatch
        # 转发到后端被忽略，拿不到「房价情况表」卡。补「房间价格/房型
        # 的价格/房间的价格」口径。
        for text in (
            "查下携程房价情况",
            "查本店各房间价格情况",
            "查本店携程各个房型的价格",
            "房间的价格怎么样",
        ):
            with self.subTest(text=text):
                result = classify_pricing_query_command(text)
                self.assertIsNotNone(result, text)
                assert result is not None
                self.assertEqual(result["status"], "ready", text)

    def test_broad_own_hotel_and_room_phrasings(self) -> None:
        # 2026-09-14 运营反馈：「携程本店今日房价」这类句中夹「本店」或房型名的
        # 问法被旧 fullmatch 正则拒掉，Hermes 转发后端也识别不出，无法回房价卡。
        # 与 hermes domain_intent_router._is_pricing_query 同步放宽为包含式命中。
        cases = (
            ("携程本店今日房价", "today", "ctrip"),
            ("携程豪华套房今日房价", "today", "ctrip"),
            ("本店房价", "today", None),
            ("本店豪华套房房价", "today", None),
            ("最新房价", "today", None),
            ("今天本店房型价格", "today", None),
            ("看看本店明天的房型价", "tomorrow", None),
        )
        for text, date_label, platform in cases:
            with self.subTest(text=text):
                result = classify_pricing_query_command(text)
                self.assertIsNotNone(result, text)
                assert result is not None
                self.assertEqual(result["status"], "ready", text)
                self.assertEqual(result["date_label"], date_label, text)
                self.assertEqual(result["platform"], platform, text)

    def test_now_words_map_to_today(self) -> None:
        # 「现在/当前/今晚」都按今天入住口径现读房价情况表。
        for text in ("现在房价", "当前携程房价", "查一下现在的房价", "今晚房价多少"):
            with self.subTest(text=text):
                result = classify_pricing_query_command(text)
                self.assertIsNotNone(result, text)
                assert result is not None
                self.assertEqual(result["date_label"], "today", text)

    def test_multiple_platform_words_query_all_bound_platforms(self) -> None:
        # 同时点名多个平台（对比问法）不筛选，返回全部已绑定平台。
        result = classify_pricing_query_command("对比携程和美团的房价")
        self.assertEqual(result["platform"], None)
        result = classify_pricing_query_command("携程飞猪房价")
        self.assertEqual(result["platform"], None)

    def test_explicit_today_wins_over_stray_tomorrow_word(self) -> None:
        result = classify_pricing_query_command("今天和明天的房价")
        self.assertEqual(result["date_label"], "today")

    def test_broad_match_exclusions(self) -> None:
        # 竞品对象、日历价、趋势、ADR 指标、历史区间、概念/预测、调价意图
        # 都不是房价情况表查询。
        for text in (
            "今天竞品价格怎么样",
            "竞品房价",
            "未来房价",
            "日历价",
            "房价趋势",
            "今日平均房价",
            "上周房价",
            "上一轮房价",
            "你读取上一轮的数据，展示今天本店的房型价格",
            "房价是什么意思",
            "房价预测分析报告",
            "房价改一下",
            "把房价降一点",
            "怎么改房价",
            "如何调整房价",
            "把明天携程奢华三卧套房调整到1100元",
            "今天高级大床房调到420",
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_pricing_query_command(text), text)


class PricingQueryHandlerTests(unittest.TestCase):
    def test_query_replies_card_with_platform_sections(self) -> None:
        async def scenario() -> None:
            bot = importlib.reload(
                importlib.import_module("app.domain.notifications.feishu_bot")
            )
            calls: list[dict[str, Any]] = []

            class FakeClient:
                async def reply_interactive_card(
                    self,
                    message_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    calls.append({"card": card, "fallback_text": fallback_text})
                    return {"status": "success", "summary": "sent"}

                async def send_interactive_card(
                    self,
                    chat_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    calls.append({"card": card, "send": True})
                    return {"status": "success", "summary": "sent"}

            bot._feishu_client_factory = lambda chat_id=None: FakeClient()

            async def fake_context(chat_id: Any, store_hint: Any = None) -> dict[str, Any]:
                return {
                    "ota_ebk_accounts": {
                        "ctrip": {
                            "platform": "ctrip",
                            "platform_account_id": "ebk-ctrip",
                            "browser_session_id": "browser-ctrip",
                        },
                        "meituan": {
                            "platform": "meituan",
                            "platform_account_id": "ebk-mt",
                            "browser_session_id": "browser-mt",
                        },
                    },
                    "resolution": {"store_name": "静里酒店"},
                }

            async def fake_read(
                *,
                platform: str,
                platform_account_id: str,
                browser_session_id: str,
                actor_id: str,
                checkin_date: str,
            ) -> dict[str, Any]:
                if platform == "ctrip":
                    return {
                        "platform": "ctrip",
                        "platform_label": "携程",
                        "status": "success",
                        "summary": "ok",
                        "room_rates": [
                            {
                                "room_label": "奢华三卧套房",
                                "sell_price_yuan": 1100,
                                "base_price_yuan": 970,
                                "status_text": "已开房",
                            }
                        ],
                    }
                return {
                    "platform": platform,
                    "platform_label": "美团",
                    "status": "human_required",
                    "summary": "需要登录",
                    "room_rates": [],
                }

            with mock.patch.object(
                bot, "build_feishu_chat_context", new=fake_context
            ), mock.patch.object(
                bot, "_read_platform_room_rates", new=fake_read
            ):
                context = bot.FeishuCommandContext(
                    event_id="evt-query",
                    message_id="om_query",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="查房价",
                )
                result = await bot.handle_pricing_query_command(
                    context,
                    query={"status": "ready", "date_label": "today", "platform": None},
                )

            self.assertEqual(result["status"], "pricing_query_card_replied")
            self.assertEqual(len(calls), 1)
            card = calls[0]["card"]
            self.assertEqual(
                card["header"]["title"]["content"], "Pan Hotel Watch 房价查询"
            )
            card_text = str(card)
            self.assertIn("奢华三卧套房", card_text)
            self.assertIn("携程", card_text)
            self.assertIn("美团", card_text)
            self.assertEqual(result["evidence"]["platforms_read"][0]["status"], "success")

        asyncio.run(scenario())

    def test_single_platform_query_only_reads_requested_platform(self) -> None:
        async def scenario() -> None:
            bot = importlib.reload(
                importlib.import_module("app.domain.notifications.feishu_bot")
            )
            calls: list[dict[str, Any]] = []

            class FakeClient:
                async def reply_interactive_card(
                    self,
                    message_id: str,
                    card: dict[str, Any],
                    *,
                    fallback_text: str | None = None,
                ) -> dict[str, Any]:
                    calls.append({"card": card})
                    return {"status": "success", "summary": "sent"}

                async def send_interactive_card(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                    return {"status": "success", "summary": "sent"}

            bot._feishu_client_factory = lambda chat_id=None: FakeClient()

            async def fake_context(chat_id: Any, store_hint: Any = None) -> dict[str, Any]:
                return {
                    "ota_ebk_accounts": {
                        "meituan": {
                            "platform": "meituan",
                            "platform_account_id": "ebk-mt",
                            "browser_session_id": "browser-mt",
                        },
                    },
                    "resolution": {},
                }

            async def fake_read(**kwargs: Any) -> dict[str, Any]:
                calls.append({"read": kwargs["platform"]})
                return {
                    "platform": kwargs["platform"],
                    "status": "success",
                    "summary": "ok",
                    "room_rates": [],
                }

            with mock.patch.object(
                bot, "build_feishu_chat_context", new=fake_context
            ), mock.patch.object(
                bot, "_read_platform_room_rates", new=fake_read
            ):
                context = bot.FeishuCommandContext(
                    event_id="evt-query-mt",
                    message_id="om_query",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="查一下美团房价",
                )
                result = await bot.handle_pricing_query_command(
                    context,
                    query={
                        "status": "ready",
                        "date_label": "today",
                        "platform": "meituan",
                    },
                )

            self.assertEqual(result["evidence"]["platforms_read"][0]["platform"], "meituan")
            self.assertEqual(len(result["evidence"]["platforms_read"]), 1)

        asyncio.run(scenario())


    def test_unbound_platforms_visible_in_card_without_request(self) -> None:
        # 2026-09-23 修复：未点名平台时，账号未绑定的平台此前被静默跳过，
        # 卡片只剩携程——店长无法区分「没绑定」「读取失败」。现在一律
        # 渲染 account_missing 状态行。
        async def scenario() -> None:
            bot = importlib.reload(
                importlib.import_module("app.domain.notifications.feishu_bot")
            )
            calls: list[dict[str, Any]] = []

            class FakeClient:
                async def reply_interactive_card(
                    self, message_id, card, *, fallback_text=None
                ) -> dict[str, Any]:
                    calls.append({"card": card})
                    return {"status": "success", "summary": "sent"}

                async def send_interactive_card(
                    self, chat_id, card, *, fallback_text=None
                ) -> dict[str, Any]:
                    return {"status": "success", "summary": "sent"}

            bot._feishu_client_factory = lambda chat_id=None: FakeClient()

            async def fake_context(chat_id: Any, store_hint: Any = None) -> dict[str, Any]:
                return {
                    "ota_ebk_accounts": {
                        "ctrip": {
                            "platform": "ctrip",
                            "platform_account_id": "ebk-ctrip",
                            "browser_session_id": "browser-ctrip",
                        },
                    },
                    "resolution": {"store_name": "静里酒店"},
                }

            async def fake_read(
                *, platform, platform_account_id, browser_session_id, actor_id, checkin_date
            ) -> dict[str, Any]:
                return {
                    "platform": "ctrip",
                    "platform_label": "携程",
                    "status": "success",
                    "summary": "ok",
                    "room_rates": [],
                }

            with mock.patch.object(
                bot, "build_feishu_chat_context", new=fake_context
            ), mock.patch.object(
                bot, "_read_platform_room_rates", new=fake_read
            ):
                context = bot.FeishuCommandContext(
                    event_id="evt-query-unbound",
                    message_id="om_query",
                    chat_id="oc_group",
                    open_id="ou_operator",
                    command="看下房态",
                )
                result = await bot.handle_pricing_query_command(
                    context,
                    query={"status": "ready", "date_label": "today", "platform": None},
                )

            platforms = {
                item["platform"]: item["status"]
                for item in result["evidence"]["platforms_read"]
            }
            self.assertEqual(platforms.get("ctrip"), "success")
            self.assertEqual(platforms.get("meituan"), "account_missing")
            self.assertEqual(platforms.get("fliggy"), "account_missing")
            card_text = str(calls[0]["card"])
            self.assertIn("没有绑定美团", card_text)
            self.assertIn("没有绑定飞猪", card_text)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
