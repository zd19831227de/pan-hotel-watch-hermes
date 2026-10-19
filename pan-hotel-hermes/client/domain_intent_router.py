"""意图路由（2026-10-09 翻转 LLM 优先，2026-10-14 全量 LLM 先行）。

不再存在任何词表硬路由：词表子串匹配会劫持测算/图表追问等问法
（2026-10-14 生产实证：「房型价格提高20元的进单测算」被劫持成房价查询），
且问法无限、排除词补不完。现在：

- 一切问法统一 generic_agent：放行全部只读落库 + 按需现读 + 知识检索/
  自学习 + 文档产出 + 事件转发（dispatch_watch_feishu_event）工具，由
  LLM 判断缺什么数据、调什么工具；浏览器成本由网关插件的每轮现读预算
  控制，不靠词表白名单。
- dispatch_watch_feishu_event 在通用目录内：LLM 判定是价目表问法（房价/
  房型/房态/卖价）或固定格式指令（手动调价/促销调整/金字塔出价）时，
  转发**运营原话**给后端，由后端确定性分类器解析并回权威卡/人工审批卡
  ——目标价等字段不经 LLM 手拼。
- 写侧/执行侧工具（create_manual_pricing_approval、暂停/恢复监控、卡片
  回执）通用模式一律拒绝：调价安全契约不变，审批卡只能由后端解析运营
  原话生成，人工确认后才可能执行。

学习库先验照常参与：generic_agent 路由携带「该问法此前用过哪些工具」的
软提示（learned_tools），白名单始终是全目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


MCP_PREFIX = "mcp_pan_hotel_watch_"
CONTEXT_TOOLS = frozenset({"resolve_feishu_chat_context"})
# pan-hotel-watch MCP 服务端暴露的全部工具（与 pan_hotel_mcp/server.py 对齐）。
PAN_HOTEL_TOOLS = frozenset(
    {
        "list_watch_groups",
        "resolve_feishu_chat_context",
        "get_operator_watch_configuration",
        "get_watch_scheduler_status",
        "get_weekly_operator_briefing",
        "get_operator_hotel_overview",
        "get_operator_analysis_context",
        "get_operator_recent_activity",
        "get_ebk_latest_result",
        "get_latest_ebk_snapshot",
        "get_ebk_joint_analysis_context",
        "read_ebk_datasets",
        "run_ebk_watch_once",
        "run_competitor_watch_once",
        "get_competitor_latest_prices",
        "get_competitor_price_trend",
        "analyze_competitor_price_change",
        "get_calendar_price_latest",
        "get_calendar_price_watch_status",
        "run_calendar_price_watch_once",
        "pause_watch",
        "resume_watch",
        "create_manual_pricing_approval",
        "reply_feishu_operator_card",
        "dispatch_watch_feishu_event",
        "get_refund_status",
        "get_today_reservations",
        "get_ebk_period_read",
        "pending_review_count",
        "send_operator_document",
        "query_hotel_analytics",
        "rag_search",
        "search_group_archive",
        "distill_external_source",
        "create_distill_source",
        "list_distill_sources",
        "get_bi_daily_report",
    }
)
# 执行/写侧工具：通用模式一律拒绝。
# create_manual_pricing_approval 保持特权：目标价等审批字段只能由后端解析
# 运营原话生成（LLM 用 dispatch_watch_feishu_event 转发原文），不经 LLM
# 手拼；暂停/恢复监控会静默停掉生产推送（历史事故：锚点组停用致推送全无
# 响应）；卡片回执只在卡片事件流内用。
# dispatch_watch_feishu_event 2026-10-14 起入通用目录：转发原文给后端
# 确定性分类器，写路径全部有人工审批卡兜底。
PRIVILEGED_EXECUTION_TOOLS = frozenset(
    {
        "create_manual_pricing_approval",
        "pause_watch",
        "resume_watch",
        "reply_feishu_operator_card",
    }
)
# 知识库自学习（用户投喂/定时学习/学习源清单）：SKILL「自学习蒸馏路由」
# 的工具面，通用模式一并放行（由 LLM 按用户明确指令调用）。
# 通用 agent 工具目录：全部只读取数 + 按需现读 + 知识检索/自学习 + 文档产出。
GENERIC_AGENT_TOOLS = PAN_HOTEL_TOOLS - PRIVILEGED_EXECUTION_TOOLS
GENERIC_AGENT_REASON = "generic_agent_mode"


def _live_read_budget() -> int:
    """每轮浏览器现读预算（网关插件与 pre-LLM 提示共用同一环境变量）。"""
    raw = str(os.environ.get("HERMES_LIVE_READ_BUDGET") or "").strip()
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 2


@dataclass(frozen=True)
class DomainRoute:
    intent: str | None
    allowed_tools: frozenset[str]
    reason: str
    # generic_agent 路由携带「此前该问法实际用过的工具」先验（仅提示用，
    # 白名单始终是全目录 GENERIC_AGENT_TOOLS）。
    learned_tools: tuple[str, ...] = ()


def plain_tool_name(name: str) -> str:
    value = str(name or "").strip()
    return value[len(MCP_PREFIX) :] if value.startswith(MCP_PREFIX) else value


def route_domain_intent(user_text: str) -> DomainRoute:
    text = str(user_text or "").strip().lower()
    if not text:
        return DomainRoute("ambiguous", frozenset(), "empty")

    # ── 一切问法：LLM 通用 agent（学习库先验作软提示）──
    learned_tools = _lookup_learned_route_tools(text)
    return DomainRoute(
        "generic_agent",
        GENERIC_AGENT_TOOLS | CONTEXT_TOOLS,
        GENERIC_AGENT_REASON,
        learned_tools=learned_tools or (),
    )


def is_generic_agent_route(route: DomainRoute) -> bool:
    return getattr(route, "intent", "") == "generic_agent"


def is_tool_allowed(route: DomainRoute, tool_name: str) -> bool:
    plain = plain_tool_name(tool_name)
    if plain in route.allowed_tools:
        return True
    if route.intent in {"general", "ambiguous", "generic_agent"}:
        # Hermes 自身能力（web_search 等）不受 pan-hotel 目录限制；
        # pan-hotel MCP 工具仍必须落在白名单内。
        return not str(tool_name or "").startswith(MCP_PREFIX) and plain not in PAN_HOTEL_TOOLS
    return False


def build_misroute_message(route: DomainRoute, tool_name: str) -> str:
    plain = plain_tool_name(tool_name)
    if plain in PRIVILEGED_EXECUTION_TOOLS:
        return (
            f"{plain} 属于执行/审批类工具，通用运营助手模式不允许调用。"
            "调价请引导运营使用明确指令（如「把今天XX房型调到420」），"
            "再用 dispatch_watch_feishu_event 转发运营原话，由后端解析生成人工审批卡；"
            "暂停/恢复监控请在运营控制台操作。"
        )
    return (
        f"本轮为通用运营助手模式，{plain} 不在可用工具目录内。"
        "请改用目录内的只读取数/现读/知识工具作答；缺数据时如实说明缺口，不要编造。"
    )


def build_pre_llm_route_context(route: DomainRoute) -> str:
    if route.intent == "generic_agent":
        budget = _live_read_budget()
        prior = ""
        if route.learned_tools:
            prior = (
                f"该问法此前已成功回答过，优先复用这些工具："
                f"{'、'.join(sorted(route.learned_tools))}。\n"
            )
        return (
            "本轮为通用运营助手模式：不依赖固定问法路由，由你自主理解问题、"
            "选择并组合工具后作答。"
            "先用 resolve_feishu_chat_context 解析当前群绑定的店铺与监控组，再按需取数：\n"
            "- 今日/历史/区间经营（出租率、ADR、销售额、间夜、流量、转化、金字塔、促销）："
            "get_ebk_period_read（周期/自定义区间现读）、read_ebk_datasets（数据集现读）、"
            "get_ebk_latest_result / get_latest_ebk_snapshot / get_weekly_operator_briefing（已落库）、"
            "get_ebk_joint_analysis_context（美团/飞猪/多平台）\n"
            "- 竞品：get_competitor_latest_prices（最新落库现价）、"
            "get_competitor_price_trend（趋势/对比优先传 with_chart=true 出图卡，"
            "内容量大时即使未提图也出图）、"
            "analyze_competitor_price_change（相邻轮询变化）\n"
            "- 日历价：get_calendar_price_latest、get_calendar_price_watch_status\n"
            "- 退款/今日预订：get_refund_status、get_today_reservations（默认现读）\n"
            "- 评价：pending_review_count（各平台待回复数）、get_ebk_latest_result（点评结果）\n"
            "- 汇总与近况：get_operator_hotel_overview、get_operator_analysis_context、"
            "get_operator_recent_activity、query_hotel_analytics、get_bi_daily_report（BI日报）\n"
            "- 知识/政策/SOP：rag_search；用户明确要「学习/蒸馏这个链接」→ "
            "distill_external_source、「定时学习」→ create_distill_source、"
            "「学过什么」→ list_distill_sources；监控状态/配置：get_watch_scheduler_status、"
            "get_operator_watch_configuration、list_watch_groups\n"
            "- 群资料归档（运营在群里发的每日实施经营数据/Excel/截图会自动永久归档）："
            "search_group_archive，按日期区间/关键词/消息类型检索；问「实施数据、"
            "手工报数、群里发过的数据、积累对比」时优先用它，跨日分析把多条归档"
            "汇总后再作答，缺的日子如实说明\n"
            "- 用户明确要文件（PPT/Excel/Word/报表/周报），或回答内容量大（多平台对照/"
            "≥3模块组合/区间报告/多行明细，预计超两屏）时：先取数再 send_operator_document"
            "发文件（doc_type 按内容形态：表格xlsx/报告docx·pdf/演示pptx，content 用 markdown，"
            "数字必须来自本轮工具结果），群里只回 3 条以内结论；短问短答直接文字，"
            "不要为出文件而出文件\n"
            "- 权威卡与固定指令转发：问「房价/房型价格/房型价/房态/卖价」价目表现状 → "
            "调 dispatch_watch_feishu_event 传 text=运营原话（服务端自动组装事件并补会话 "
            "message_id/chat_id，原话一字不改），后端现读回「房价情况表」卡；运营发固定格式指令"
            "（把今天X房型调到Y / 促销列表 / 促销调整 X 立减调到N / 金字塔出价…调到N）→ "
            "同样传 text=原话转发，后端解析生成人工审批卡；含「测算/分析/影响」等"
            "开放问法不是查询指令，直接用上面的数据工具作答\n"
            f"{prior}"
            f"作答规则：优先用已落库数据并标注口径与读取时间；用户要最新、或落库缺失/过期时"
            f"再现读（浏览器现读每轮至多 {budget} 次，较慢需先告知运营稍候）；"
            "缺数据就如实说明缺口，不编数字；多工具组合时逐模块标注口径。"
            "调价只能给参考建议与建议区间，禁止任何执行性表述；改价审批卡只能由后端"
            "解析运营原话生成（转发原文即可，不要手拼审批字段），暂停/恢复监控没有"
            "通用入口，不要尝试调用。"
            "与酒店经营无关的闲聊直接用自身能力回答，不调数据工具。"
        )
    allowed = "、".join(sorted(route.allowed_tools - CONTEXT_TOOLS))
    return (
        f"本轮酒店数据意图已确定为 {route.intent}。只使用以下 pan-hotel-watch 工具：{allowed}。"
        "不得从历史回答推断 EBK 指标或酒店价格；业务数字只能来自本轮工具结果。"
    )


def _lookup_learned_route_tools(text: str) -> tuple[str, ...] | None:
    """学习库精确（规范化后）查找；未启用/读失败/未命中一律 None。"""
    try:
        from client.learned_routes import lookup_learned_tools

        return lookup_learned_tools(text)
    except Exception:
        return None
