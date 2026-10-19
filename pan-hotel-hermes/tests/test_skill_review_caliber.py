from __future__ import annotations

import unittest
from pathlib import Path

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "pan-hotel-watch" / "SKILL.md"
SOUL_PATH = Path(__file__).resolve().parent.parent / "config" / "hermes.SOUL.md"
MANXIN_SOUL = (
    Path(__file__).resolve().parent.parent / "config" / "profiles" / "manxin-ai" / "SOUL.md"
)
JINGLI_SOUL = (
    Path(__file__).resolve().parent.parent / "config" / "profiles" / "jingli-ai" / "SOUL.md"
)


class SkillReviewCaliberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.soul = SOUL_PATH.read_text(encoding="utf-8")

    def test_skill_has_four_core_sections(self) -> None:
        self.assertIn("## 1. 数据源边界", self.text)
        self.assertIn("## 2. 工具能力目录", self.text)
        self.assertIn("## 3. 回复决策", self.text)
        self.assertIn("## 4. 写价安全", self.text)

    def test_text_first_and_no_mobile_summary_fence(self) -> None:
        self.assertIn("短文字", self.text)
        self.assertIn("MCP 工具不直接发简报卡", self.text)
        self.assertIn("response_mode=detail", self.text)
        self.assertIn("禁止输出 `MOBILE_SUMMARY_JSON`", self.text)
        self.assertNotIn("<<<MOBILE_SUMMARY_JSON>>>", self.text)

    def test_soul_text_first_not_auto_card_wrap(self) -> None:
        self.assertIn("短文字优先", self.soul)
        self.assertIn("禁止**发状态卡", self.soul)
        self.assertIn("`response_mode=detail` 对问答工具无效", self.soul)
        self.assertNotIn("都会自动包装为简报式卡片", self.soul)
        self.assertIn("简体中文", self.soul)
        self.assertIn("`#`", self.soul)
        self.assertIn("持久记忆边界", self.soul)
        self.assertIn("有证据边界的经营分析师", self.soul)
        self.assertIn("直连或未确认账号不能自动改价", self.soul)
        self.assertIn("促销调整 <促销名> 立减调到X", self.soul)
        self.assertNotIn("不支持自动化调整促销、报名活动、取消活动或改活动价格", self.soul)
        self.assertIn("适度使用 emoji", self.soul)

    def test_review_caliber_is_yesterday_not_today_new(self) -> None:
        self.assertIn("comment_monitor", self.text)
        self.assertIn("昨日好评", self.text)
        self.assertIn("今日新增", self.text)
        self.assertIn("positive_reviews_yesterday", self.text)
        self.assertIn("operator_summary.review_summary", self.text)
        self.assertIn("run_ebk_watch_once", self.text)
        self.assertIn("enabled=false", self.text)

    def test_forbids_example_data_center_key(self) -> None:
        self.assertIn("default_data_center_watch", self.text)

    def test_requires_simplified_chinese_and_mobile_format(self) -> None:
        self.assertIn("飞书文字输出规范", self.text)
        self.assertIn("只用简体中文", self.text)
        self.assertIn("禁止整段英文", self.text)
        self.assertIn("不是 Markdown 文档", self.text)
        self.assertIn("代码块", self.text)
        self.assertIn("表格", self.text)
        self.assertIn("适度使用 emoji", self.text)
        self.assertIn("📊", self.text)
        self.assertIn("emoji 不能替代关键数字、状态、时间和风险说明", self.text)
        self.assertIn("信息要完整", self.text)
        self.assertIn("手机上看", self.text)
        self.assertIn("先结论", self.text)
        self.assertIn("不要展示 run id", self.text)

    def test_operator_summary_contract_documented(self) -> None:
        self.assertIn("operator_summary.v1", self.text)
        self.assertIn("结论", self.text)
        self.assertIn("关键证据", self.text)
        self.assertIn("数据时间", self.text)
        self.assertIn("不要向用户展示 contract_version", self.text)
        self.assertIn("不要向用户展示 safety_boundary", self.text)
        self.assertIn("不要向用户展示 next_actions", self.text)
        self.assertIn("requires_confirmation=true", self.text)
        self.assertIn("先问用户确认", self.text)
        self.assertIn("feishu_card_sent=true", self.text)
        self.assertIn("不要重复整张表", self.text)
        self.assertIn("已通过操作卡回复", self.text)

    def test_pricing_safety_and_feishu_roles(self) -> None:
        self.assertIn("运营问答上下文群", self.text)
        self.assertIn("手动调价指令群", self.text)
        self.assertIn("价格变化提醒群", self.text)
        self.assertIn("Hermes home channel", self.text)
        self.assertIn("不要把 `/sethome`", self.text)
        self.assertIn("竞品涨价仅提醒", self.text)
        self.assertIn("立即调价", self.text)
        self.assertIn("写入携程", self.text)
        self.assertIn("dispatch_watch_feishu_event", self.text)
        self.assertIn("create_manual_pricing_approval", self.text)
        self.assertIn("禁止猜", self.text)

    def test_group_binding_and_memory(self) -> None:
        self.assertIn("resolve_feishu_chat_context", self.text)
        self.assertIn("一个飞书群默认管理一家酒店", self.text)
        self.assertIn("调价 EBK 账号只决定写价链路", self.text)
        self.assertIn("direct/unknown 仍允许只读查询", self.text)
        self.assertIn("禁止写价", self.text)
        self.assertIn("持久记忆治理", self.text)
        self.assertIn("禁止写入", self.text)
        self.assertIn("出租率", self.text)
        self.assertIn("竞品价", self.text)
        self.assertIn("共享 Skill", self.text)
        self.assertIn("profile SOUL", self.text)
        self.assertIn("不要在共享 Skill 里写死门店身份", self.text)
        self.assertIn("ota_ebk_accounts", self.text)
        self.assertIn("不等于系统没配美团/飞猪", self.text)
        self.assertIn("未配置美团/飞猪", self.text)
        self.assertIn("需要绑定登录信息", self.text)

    def test_capability_self_description_three_layers(self) -> None:
        self.assertIn("本轮对话", self.text)
        self.assertIn("长期偏好", self.text)
        self.assertIn("酒店真实数据", self.text)

    def test_ebk_and_competitor_tool_catalog(self) -> None:
        self.assertIn("read_ebk_datasets", self.text)
        self.assertIn("run_ebk_watch_once", self.text)
        self.assertIn("完整 EBK 盯盘", self.text)
        self.assertIn("现在读一次", self.text)
        self.assertIn("今天出租率多少", self.text)
        self.assertIn("enabled_datasets", self.text)
        self.assertIn("不要告诉店长去开启数据集", self.text)
        self.assertIn("get_operator_recent_activity", self.text)
        self.assertIn("get_ebk_joint_analysis_context", self.text)
        self.assertIn("各平台", self.text)
        self.assertIn("综合分析今日经营", self.text)
        self.assertIn("refresh_before_analysis=true", self.text)
        self.assertIn("get_operator_hotel_overview", self.text)
        self.assertIn("get_operator_analysis_context", self.text)
        self.assertIn("为什么订单弱", self.text)
        self.assertIn("要不要跟", self.text)

    def test_ota_operations_heuristics_are_rag_reference_not_skill_authority(self) -> None:
        self.assertIn("OTA 运营经验、阈值和分析方法", self.text)
        self.assertIn("rag_search", self.text)
        self.assertIn("知识库运营参考", self.text)
        self.assertIn("不能覆盖后端实时配置", self.text)
        self.assertIn("不能覆盖调价安全契约", self.text)
        self.assertIn("单体私联", self.text)
        self.assertIn("connection_mode=non_direct", self.text)
        self.assertIn("连锁直连", self.text)
        self.assertIn("connection_mode=direct", self.text)

    def test_competitor_and_calendar_tools(self) -> None:
        self.assertIn("get_competitor_latest_prices", self.text)
        self.assertIn("get_competitor_price_trend", self.text)
        self.assertIn("analyze_competitor_price_change", self.text)
        self.assertIn("run_competitor_watch_once", self.text)
        self.assertIn("suggest_immediate_run", self.text)
        self.assertIn("最近三天", self.text)
        self.assertIn("波动图", self.text)
        # 2026-10-08 价格办公软件技能：趋势问法放行曲线图卡（with_chart），
        # 推翻旧「只用文字、禁止发趋势卡/图片」口径。
        self.assertIn("with_chart=true", self.text)
        self.assertIn("PNG曲线图卡片", self.text)
        self.assertIn("不支持生成价格趋势", self.text)
        self.assertIn("相邻两次成功竞品轮询", self.text)
        self.assertIn("早上 6 点", self.text)
        self.assertIn("日历价", self.text)
        self.assertIn("未来15天", self.text)
        self.assertIn("get_calendar_price_latest", self.text)
        self.assertIn("get_calendar_price_watch_status", self.text)
        self.assertIn("run_calendar_price_watch_once", self.text)
        self.assertIn("push_to_feishu=false", self.text)
        self.assertIn("不创建调价审批", self.text)
        self.assertIn("auto_poll_enabled", self.text)
        self.assertIn("schedule_interval_minutes", self.text)
        forbidden_status_phrase = "日历价没有独立" + "设轮询间隔"
        self.assertNotIn(forbidden_status_phrase, self.text)

    def test_datasets_and_promotion_readonly(self) -> None:
        self.assertIn("promotion_registered", self.text)
        self.assertIn("promotion_effect", self.text)
        self.assertIn("ad_pyramid", self.text)
        self.assertIn("flow_data", self.text)
        self.assertIn("beneficial_data", self.text)
        self.assertIn("business_outline", self.text)
        self.assertIn("dataset_summaries", self.text)
        self.assertIn("multi_dataset_analysis_note", self.text)
        self.assertIn("promotion_effectiveness", self.text)
        self.assertIn("read_ebk_datasets(comment_monitor)", self.text)
        self.assertIn("促销调整 <促销名> 立减调到5", self.text)
        self.assertIn("仅支持携程且仅单条「立减 X%」规则", self.text)
        self.assertNotIn("不支持自动化调整促销、报名活动、取消活动或改活动价格", self.text)
        self.assertNotIn("不要承诺自动调活动", self.text)

    def test_analysis_mode_and_unbound_rules(self) -> None:
        self.assertIn("已证实数据", self.text)
        self.assertIn("可能原因", self.text)
        self.assertIn("建议动作", self.text)
        self.assertIn("数据缺口", self.text)
        self.assertIn("禁止编造经营数字", self.text)
        self.assertIn("binding_mode=multi_store", self.text)
        self.assertIn("store_hint", self.text)
        self.assertIn("default_competitor_watch", self.text)
        self.assertIn("routing_hints", self.text)
        self.assertIn("经营简报 / 周报", self.text)
        self.assertIn("禁止编造", self.text)
        self.assertIn("暂无数据", self.text)
        self.assertIn("get_weekly_operator_briefing", self.text)
        self.assertIn("pending_operator_action", self.text)
        self.assertIn("run_tool_name", self.text)
        self.assertIn("长会话", self.text)
        self.assertIn("不要猜", self.text)
        self.assertIn("skill_view", self.text)
        self.assertIn("mcp_", self.text)

    def test_both_profiles_share_text_first_policy(self) -> None:
        manxin = MANXIN_SOUL.read_text(encoding="utf-8")
        jingli = JINGLI_SOUL.read_text(encoding="utf-8")
        for soul_text, label in ((manxin, "manxin"), (jingli, "jingli")):
            with self.subTest(profile=label):
                self.assertIn("reply_policy", soul_text)
                self.assertIn("禁止简报卡", soul_text)
                self.assertIn("短文字", soul_text)
                self.assertIn("不编造", soul_text)
                self.assertIn("正确调用工具", soul_text)
                self.assertIn("chat_id", soul_text)
                self.assertIn("MEMORY", soul_text)
                self.assertIn("connection_mode", soul_text)
                self.assertIn("domain_intent_router", soul_text)
                self.assertIn("不维护第二套路由关键词", soul_text)
                self.assertNotIn("美团今日经营简报", soul_text)
                self.assertNotIn("<<<MOBILE_SUMMARY_JSON>>>", soul_text)

    def test_manxin_uses_backend_connection_mode(self) -> None:
        soul_text = MANXIN_SOUL.read_text(encoding="utf-8")
        self.assertIn("后端当前绑定", soul_text)
        self.assertIn("direct/unknown", soul_text)
        self.assertNotIn("曼馨当前按直连账号处理", soul_text)

    def test_soul_forbids_business_numbers_in_memory(self) -> None:
        self.assertIn("禁止", self.soul)
        self.assertIn("出租率", self.soul)


class HermesDocsCurrentCaliberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.hermes_readme = (self.root / "pan-hotel-hermes" / "README.md").read_text(encoding="utf-8")
        self.main_readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.operator_guide = (self.root / "docs" / "operator-user-guide.md").read_text(encoding="utf-8")
        self.acceptance = (self.root / "docs" / "hermes-e2e-acceptance-checklist.md").read_text(encoding="utf-8")

    def test_hermes_readme_uses_contract_not_stale_fixed_tool_count(self) -> None:
        self.assertIn("get_calendar_price_latest", self.hermes_readme)
        self.assertIn("get_calendar_price_watch_status", self.hermes_readme)
        self.assertIn("run_calendar_price_watch_once", self.hermes_readme)
        self.assertIn("tests/test_mcp_tool_contract.py", self.hermes_readme)
        self.assertIn("EXPECTED_TOOL_NAMES", self.hermes_readme)
        self.assertNotIn("共 **18 个**工具", self.hermes_readme)
        self.assertNotIn("stdio，18 工具", self.hermes_readme)

    def test_operator_docs_include_calendar_queries(self) -> None:
        combined = "\n".join([self.main_readme, self.operator_guide])
        self.assertIn("日历价", combined)
        self.assertIn("未来15天", combined)
        self.assertIn("get_calendar_price_latest", combined)
        self.assertIn("run_calendar_price_watch_once", combined)

    def test_operator_docs_calendar_status_uses_real_config_fields(self) -> None:
        forbidden_status_phrase = "日历价没有独立" + "设轮询间隔"
        combined = "\n".join([self.operator_guide, self.acceptance])
        self.assertIn("get_calendar_price_watch_status", combined)
        self.assertIn("auto_poll_enabled", combined)
        self.assertIn("schedule_interval_minutes", combined)
        self.assertNotIn(forbidden_status_phrase, combined)

    def test_acceptance_checklist_does_not_present_old_pid_snapshot_as_current(self) -> None:
        self.assertIn("历史快照", self.acceptance)
        self.assertIn("get_calendar_price_latest", self.acceptance)
        self.assertIn("run_calendar_price_watch_once", self.acceptance)
        self.assertNotIn("PID 20192", self.acceptance)
        self.assertNotIn("MCP 18 工具已注册", self.acceptance)


if __name__ == "__main__":
    unittest.main()
