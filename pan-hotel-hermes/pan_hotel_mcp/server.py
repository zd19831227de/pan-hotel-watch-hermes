from __future__ import annotations

import json
import os
import sys
from typing import Any, Awaitable, Callable

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from client.feishu_cards import attach_feishu_card
from client.feishu_session_bridge import merge_feishu_session
from client.operator_summary_contract import normalize_operator_summary
from client.operator_reply import finalize_operator_payload
from client.reply_policy import resolve_tool_response_mode
from client.reply_style import attach_reply_style_hint
from client.watch_api import WatchApiClient
from client.feishu_store_resolver import resolve_overview_watch_groups, resolve_watch_group_keys
from client.watch_groups import (
    DEFAULT_COMPETITOR_GROUP as DEFAULT_COMPETITOR_GROUP_KEY,
    DEFAULT_EBK_GROUP as DEFAULT_EBK_GROUP_KEY,
)

DEFAULT_EBK_GROUP = os.environ.get("WATCH_DEFAULT_EBK_GROUP", DEFAULT_EBK_GROUP_KEY)
DEFAULT_COMPETITOR_GROUP = os.environ.get("WATCH_DEFAULT_COMPETITOR_GROUP", DEFAULT_COMPETITOR_GROUP_KEY)

_MCP_SKILL_REF = "读取 operator_summary。"
_MCP_CARD_HINT = "问答短文字；调价审批/回调/价格曲线图(with_chart)可发卡。"

MESSAGE_ID_SCHEMA = {
    "message_id": {
        "type": "string",
        "description": "飞书触发消息 ID（om_ 开头，可选）；缺省时用 chat_id 发卡，EBK 现读不依赖此项。",
    }
}

FEISHU_SESSION_SCHEMA = {
    "chat_id": {
        "type": "string",
        "description": "飞书群 chat_id（oc_ 开头，建议透传）",
    },
    "open_id": {
        "type": "string",
        "description": "用户 open_id（ou_ 开头，建议透传）",
    },
    # Hermes pan-hotel-feishu-session 在 pre_tool_call 注入；closed schema 必须声明。
    "user_text": {
        "type": "string",
        "description": "本轮用户原文（插件注入；可选，供回复上下文）",
    },
}

# message_id + 飞书会话字段（含 user_text）。优先用本 schema，避免漏声明。
MCP_TOOL_CONTEXT_SCHEMA = {
    **MESSAGE_ID_SCHEMA,
    **FEISHU_SESSION_SCHEMA,
}

MCP_DATE_RANGE_SCHEMA = {
    "date_from": {
        "type": "string",
        "description": "查询起始日期（ISO YYYY-MM-DD）；用于历史范围查询，不传则默认今日口径",
    },
    "date_to": {
        "type": "string",
        "description": "查询结束日期（ISO YYYY-MM-DD）；与 date_from 配合做历史范围查询",
    },
}

STORE_HINT_SCHEMA = {
    "store_hint": {
        "type": "string",
        "description": "店名提示；一群多店时从用户问题提取，例如「静里」「澜江路泉吉」",
    },
}

RESPONSE_MODE_SCHEMA = {
    "response_mode": {
        "type": "string",
        "enum": ["text", "detail", "summary"],
        "default": "text",
        "description": (
            "text=默认；Hermes 问答不发简报卡，detail/summary 会被忽略。"
            "仅 create_manual_pricing_approval / dispatch_watch_feishu_event 可发卡；"
            "价格趋势曲线图用 get_competitor_price_trend 的 with_chart=true。"
        ),
    },
}

def _merged_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return merge_feishu_session(arguments)


def _session_kwargs(arguments: dict[str, Any]) -> dict[str, str | None]:
    merged = _merged_arguments(arguments)
    return {
        "chat_id": str(merged.get("chat_id") or "").strip() or None,
        "operator_id": str(merged.get("open_id") or "").strip() or None,
    }

server = Server("pan-hotel-watch")
_client_instance: WatchApiClient | None = None


def _get_client() -> WatchApiClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = WatchApiClient()
    return _client_instance


def _text_result(payload: dict[str, Any]) -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=json.dumps(attach_reply_style_hint(payload), ensure_ascii=False, indent=2),
        )
    ]


def _normalized_text_result(payload: dict[str, Any], *, tool_name: str) -> list[TextContent]:
    return _text_result(normalize_operator_summary(payload, tool_name=tool_name))


def _normalize_tool_result(result: list[TextContent], *, tool_name: str) -> list[TextContent]:
    if not result or not isinstance(result[0], TextContent):
        return result
    try:
        payload = json.loads(result[0].text)
    except (TypeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    return _normalized_text_result(payload, tool_name=tool_name)


def _resolve_message_id(arguments: dict[str, Any]) -> str:
    merged = merge_feishu_session(arguments)
    return str(merged.get("message_id") or "").strip()


async def _with_auto_feishu_card(
    client: WatchApiClient,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> dict[str, Any]:
    merged = merge_feishu_session(arguments)
    resolved_tool = str(
        tool_name
        or merged.get("_hermes_tool_name")
        or arguments.get("_hermes_tool_name")
        or ""
    ).strip()
    session_kwargs = {
        "chat_id": str(merged.get("chat_id") or "").strip() or None,
        "message_id": str(merged.get("message_id") or "").strip() or None,
        "store_hint": str(merged.get("store_hint") or "").strip() or None,
    }
    explicit = str(merged.get("response_mode") or payload.get("response_mode") or "").strip().lower()
    user_text = str(merged.get("user_text") or arguments.get("user_text") or "").strip()
    # Hard ban: Q&A tools never send briefing cards, even if the model asks for detail.
    response_mode = resolve_tool_response_mode(
        resolved_tool,
        requested_mode=explicit or None,
        user_text=user_text,
    )
    # 2026-10-08 价格办公软件技能：价格趋势曲线图是唯一放行的问答图表卡
    # （with_chart 显式请求；卡片由后端上传 PNG 图片，链路已验证）。
    if (
        resolved_tool == "get_competitor_price_trend"
        and merged.get("with_chart") is True
        and response_mode == "text"
    ):
        response_mode = "detail"
    return await finalize_operator_payload(
        client,
        payload,
        response_mode=response_mode,
        **session_kwargs,
    )


async def _resolve_tool_watch_group_key(
    client: WatchApiClient,
    arguments: dict[str, Any],
    *,
    watch_kind: str,
    explicit_key: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    session = _session_kwargs(arguments)
    return await resolve_watch_group_keys(
        client,
        chat_id=session["chat_id"],
        store_hint=arguments.get("store_hint"),
        watch_kind=watch_kind,
        explicit_key=explicit_key,
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="list_watch_groups",
            description="当前飞书群授权的 EBK/竞品监控组 key 与轮询状态（只读，需 chat_id）。",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="resolve_feishu_chat_context",
            description=(
                "查询当前飞书群绑定的门店、EBK 组、竞品组与账号关系（只读，需 chat_id）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "chat_id": {
                        "type": "string",
                        "description": "当前飞书群 chat_id（oc_ 开头，从会话上下文读取，必填）",
                    },
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="query_hotel_analytics",
            description=(
                "跨店数据分析总览（只读，需 chat_id）。supervisor 可查全局/指定店/多店对比，"
                "operator 仅返回本店。响应只回当前群，不串群；权限不足时返回明确提示而非空数据。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "chat_id": {
                        "type": "string",
                        "description": "当前飞书群 chat_id（oc_ 开头，从会话上下文读取，必填）",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["own", "all", "selected"],
                        "description": "查询范围：own=本店（默认）、all=全部店铺、selected=指定店铺",
                    },
                    "profile_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "scope=selected 时指定要对比的 profile_key 列表",
                    },
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="rag_search",
            description=(
                "Read-only search of the RAG knowledge base. Returns matching "
                "text chunks from uploaded documents."
            ),
            inputSchema={
                "type": "object",
                # 会话字段可选声明：pan-hotel-feishu-session 在飞书会话里会向
                # 工具参数注入 chat_id/message_id/open_id/user_text，闭 schema
                # 未声明时会因 additionalProperties=False 校验失败（2026-10-10
                # 生产实证）。只传 query/top_k 的调用方不受影响。
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "query": {
                        "type": "string",
                        "description": "RAG knowledge-base search query or question",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 3,
                        "description": "Number of matching text chunks to return",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_group_archive",
            description=(
                "群资料归档查询（只读）：群里发的每日实施数据/文档/截图永久"
                "积累，按日期区间/关键词/消息类型检索归档记录与附件明细。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "query": {
                        "type": "string",
                        "description": "文本关键词（如「出租率」「实施」），空则不过滤",
                    },
                    "date_start": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD（可选）",
                    },
                    "date_end": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD（可选）",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "限定飞书群 chat_id（插件会注入当前群；跨群对比时才显式传）",
                    },
                    "message_type": {
                        "type": "string",
                        "enum": ["text", "file", "image", "post", "media", "audio"],
                        "description": "消息类型过滤（可选）",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                        "description": "返回条数上限",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_bi_daily_report",
            description=(
                "经营 BI 日报（只读 read-only，需 chat_id）：未来在手/竞争圈胜负、"
                "逐日同比完成系数、按星期同比、预算与需求预测四板块。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "chat_id": {
                        "type": "string",
                        "description": "当前飞书群 chat_id（oc_ 开头，从会话上下文读取，必填）",
                    },
                    "section": {
                        "type": "string",
                        "enum": ["forward", "performance", "weekday_profile", "quality"],
                        "default": "forward",
                        "description": (
                            "板块：forward=未来 horizon_days 天在手预订/出租率/预埋价"
                            "+竞争圈胜负判定+市场热度+去年同星期参照；"
                            "performance=过去 lookback_days 天逐日实绩与去年同期完成系数、"
                            "预算/需求预测完成度；weekday_profile=按星期几聚合的今年 vs "
                            "去年同期（间夜/出租率/过夜均价）与完成系数；quality=各板块"
                            "数据覆盖状态与口径说明。出租率/完成系数为小数（0.166=16.6%，1.94=194%）。"
                            "问「看星期不看日期」的去年同比用 weekday_profile。"
                        ),
                    },
                    "horizon_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 14,
                        "description": "forward 板块展望天数",
                    },
                    "lookback_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "default": 30,
                        "description": "performance 板块回看天数",
                    },
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="distill_external_source",
            description=(
                "Trigger a one-off knowledge distillation run into the RAG "
                "knowledge base via the Doubao/Qianwen chat browser."
            ),
            inputSchema={
                "type": "object",
                # 同 rag_search：声明会话字段，飞书会话注入不再触发闭 schema 拒绝。
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "source_type": {
                        "type": "string",
                        "enum": ["text", "image_url", "video_url"],
                        "description": "Distillation source type",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content, or the image/video URL to hand over to the chat AI",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["doubao", "qwen"],
                        "description": "Which chat platform performs the distillation",
                    },
                    "distill_prompt": {
                        "type": "string",
                        "description": "Optional custom distillation instruction (e.g. 提取运营规则要点)",
                    },
                },
                "required": ["source_type", "content", "platform"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_distill_source",
            description=(
                "创建定时蒸馏数据源（每 N 分钟自动学习一次，委托后端 /api/v1/distill/sources；"
                "间隔低于 30 分钟时按 30 落）。与操作台自学习面板同源同表。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "source_type": {
                        "type": "string",
                        "enum": ["text", "image_url", "video_url"],
                        "description": "Distillation source type",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content, or the image/video URL to learn on schedule",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["doubao", "qwen"],
                        "description": "Which chat platform performs the distillation",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional source title shown in the ops console",
                    },
                    "distill_prompt": {
                        "type": "string",
                        "description": "Optional custom distillation instruction",
                    },
                    "schedule_interval_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 10080,
                        "default": 120,
                        "description": "Schedule interval in minutes (default 120; 0 = manual only; >=30 required)",
                    },
                    "enabled": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether the scheduled source starts enabled",
                    },
                },
                "required": ["source_type", "content", "platform"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="list_distill_sources",
            description=(
                "查询当前蒸馏数据源列表（委托后端 /api/v1/distill/sources），"
                "供运营在飞书里问「我现在有哪些学习源」。"
            ),
            inputSchema={
                "type": "object",
                "properties": {**MCP_TOOL_CONTEXT_SCHEMA},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_operator_watch_configuration",
            description=(
                "查询当前飞书群授权的盯盘组、账号与酒店配置总览（只读，需 chat_id）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_watch_scheduler_status",
            description=(
                f"查询盯盘调度器心跳与模块健康状态（只读，不读取酒店经营数据）。{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": dict(MCP_TOOL_CONTEXT_SCHEMA) | RESPONSE_MODE_SCHEMA,
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_weekly_operator_briefing",
            description=f"上周经营简报（只读，基于持久化 run，不含同比编造）。{_MCP_CARD_HINT} {_MCP_SKILL_REF}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "chat_id": {
                        "type": "string",
                        "description": "当前飞书群 chat_id（oc_ 开头，必填）",
                    },
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_operator_hotel_overview",
            description=(
                f"查询酒店今日综合概览：EBK、竞品、价格变化与调度状态（只读）。"
                f"{_MCP_CARD_HINT} {_MCP_SKILL_REF}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "ebk_watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；多店群省略时按 chat_id/store_hint 解析",
                    },
                    "competitor_watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；多店群省略时按 chat_id/store_hint 解析",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_operator_analysis_context",
            description="经营分析上下文（只读，证据+建议+缺口）。读 operator_summary。",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "ebk_watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；省略时按 chat_id/store_hint 解析",
                    },
                    "competitor_watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按 chat_id/store_hint 解析",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_operator_recent_activity",
            description=f"最近读取、价格告警与审批摘要（只读）。{_MCP_CARD_HINT}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "ebk_watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；省略时按当前 chat_id 绑定解析",
                    },
                    "competitor_watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id 绑定解析",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_ebk_latest_result",
            description=f"EBK 最新持久化读取（只读，不跑浏览器）。{_MCP_CARD_HINT} {_MCP_SKILL_REF}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_refund_status",
            description=(
                "当日退单数量与房型分布（只读）。「今天退单多少」。"
                "platform 可省（三平台汇总，并发现抓三平台）。每次自动现抓最新"
                "（按群绑定店铺账号，自动拉起浏览器）。"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **MCP_DATE_RANGE_SCHEMA,
                    "platform": {
                        "type": "string",
                        "enum": ["ctrip", "meituan", "fliggy"],
                        "description": "可选；不传返回三平台汇总。",
                    },
                    "refresh": {
                        "type": "boolean",
                        "default": True,
                        "description": "默认 true：每次先触发后端抓取最新再读（实时口径）。传了 date_from/date_to 则跳过抓取走历史范围。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_today_reservations",
            description=(
                "当日今日预定数量与房型分布（只读）。「今天订了多少/今日预订」。"
                "platform 可省（三平台汇总，并发现抓三平台）。"
                "每次自动现抓最新（按群绑定账号，自动拉起浏览器）。"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **MCP_DATE_RANGE_SCHEMA,
                    "platform": {
                        "type": "string",
                        "enum": ["ctrip", "fliggy", "meituan"],
                        "description": "可选；不传返回携程+飞猪+美团汇总。",
                    },
                    "refresh": {
                        "type": "boolean",
                        "default": True,
                        "description": "默认 true：每次先触发后端抓取最新再读（实时口径）。传了 date_from/date_to 则跳过抓取走历史范围。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_ebk_period_read",
            description=(
                "经营/排名/金字塔/流量（只读）。昨日=period=day；上周/上月=range_preset；"
                "日期区间=date_from/date_to（现读自定义时间不落库）"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **MCP_DATE_RANGE_SCHEMA,
                    "platform": {
                        "type": "string",
                        "enum": ["ctrip", "meituan", "fliggy"],
                        "description": "可选；不传返回三平台汇总。",
                    },
                    "data_kind": {
                        "type": "string",
                        "enum": ["business", "ranking", "pyramid", "flow"],
                        "description": (
                            "business=经营数据；ranking=排名数据；"
                            "pyramid=携程金字塔投放（仅 ctrip，今天/过去7天/过去30天+自定义区间）；"
                            "flow=携程流量数据（仅 ctrip，昨天/过去7天/过去30天+自定义区间，"
                            "访客量/转化率/漏斗三段转化率含竞争圈平均）。"
                        ),
                    },
                    "period": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": (
                            "day=昨日（页面昨日块）；week/month=本周/本月或平台预设口径。"
                            "传 range_preset 或 date_from/date_to 时 period 仅作标签。"
                        ),
                    },
                    "range_preset": {
                        "type": "string",
                        "enum": ["last_week", "last_month"],
                        "description": (
                            "「上周数据」→last_week（自动换算上周一~上周日）；"
                            "「上月数据」→last_month（自动换算上个自然月）。"
                            "按平台自定义时间现读；无自定义控件的平台回落页面预设口径。"
                        ),
                    },
                    "date_from": {
                        "type": "string",
                        "description": (
                            "自定义区间起始（YYYY-MM-DD，需早于今日；区间最多31天）；"
                            "「x月x日到x月x日」问法使用，实时读取平台自定义时间"
                        ),
                    },
                    "date_to": {
                        "type": "string",
                        "description": "自定义区间结束（YYYY-MM-DD），与 date_from 配合",
                    },
                },
                "required": ["data_kind", "period"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="pending_review_count",
            description=(
                "查某平台未回复评价数量（不返回正文），按群绑监控组解析，请透传 chat_id；"
                "说「刷新/重新读」传 refresh=true：先真实抓取（与自动回复开关无关）。"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "platform": {
                        "type": "string",
                        "enum": ["ctrip", "meituan", "fliggy"],
                    },
                    "ctrip_watch_group_key": {
                        "type": "string",
                    },
                    "refresh": {
                        "type": "boolean",
                        "default": False,
                        "description": "true=先触发一次真实抓取再返回数量；false=只读最近抓取记录",
                    },
                },
                "required": ["platform"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_latest_ebk_snapshot",
            description=(
                "统一 EBK snapshot 最新落库（只读，不跑 Playwright）；"
                "platform=ctrip|meituan；仅分析汇报。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "platform": {
                        "type": "string",
                        "description": "ctrip 或 meituan",
                    },
                    "platform_account_id": {
                        "type": "string",
                        "description": "对应平台的 browser platform_account_id",
                    },
                },
                "required": ["platform", "platform_account_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_ebk_joint_analysis_context",
            description=(
                "读取携程、美团、飞猪单平台或多平台 EBK 经营证据并汇总分析；可选先刷新（只读）。"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "ctrip_platform_account_id": {
                        "type": "string",
                        "description": "携程 EBK platform_account_id；联合或仅携程时需要",
                    },
                    "meituan_platform_account_id": {
                        "type": "string",
                        "description": "美团 EBK platform_account_id；联合或仅美团时需要",
                    },
                    "fliggy_platform_account_id": {
                        "type": "string",
                        "description": "飞猪 EBK platform_account_id；联合或仅飞猪时需要",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选：ctrip / meituan / fliggy / all；省略时自动分析三平台",
                    },
                    "refresh_before_analysis": {
                        "type": "boolean",
                        "description": "可读取已保存 snapshot，也可立即触发 EBK 读取：true 表示先调用后台 read-all 现读并保存，再汇总分析；false 表示只读取已保存 snapshot。",
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="read_ebk_datasets",
            description=(
                "EBK 按需现读数据集，不看 enabled_datasets；ID 看 operator_summary.dataset_read_note；点评用 latest。"
                f"{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                    "dataset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要立即读取的 EBK 数据集，可传多个。"
                            "business_outline=收益概览，beneficial_data=销售数据，flow_data=流量，"
                            "competition_profile=经营对比，loss_analysis=流失订单，competition_list=销售排名，"
                            "promotion_registered=已参加促销，promotion_effect=促销效果，ad_pyramid=广告金字塔。"
                            "中文别名如「已参加促销」「金字塔」也可。"
                        ),
                    },
                    "metric_keys": {"type": "array", "items": {"type": "string"}},
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="run_ebk_watch_once",
            description=f"刷新 EBK：现读已启用数据集并持久化，不自动推卡；耗时数分钟，不需 message_id。{_MCP_SKILL_REF}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "EBK 监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="run_competitor_watch_once",
            description=(
                "仅用户明确说「刷新竞品/现刷/现爬/立即读」时调用；"
                "变化分析用 analyze_competitor_price_change；Hermes 不推 webhook。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                    "push_to_feishu": {
                        "type": "boolean",
                        "description": (
                            "Hermes 问答一律忽略并强制 false，不推 webhook/简报卡；"
                            "定时盯盘推送只走后台调度，不走本工具。"
                        ),
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_competitor_latest_prices",
            description=(
                "查询最近一次已持久化的公开页现价（含本店 own 与竞品房型/早餐/价格；只读不刷新）。"
                "用于：今天本店房型价格、上一轮房价、现在竞品多少钱。"
                f"不要用于日历价/未来15天。{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_calendar_price_latest",
            description=(
                "未来日历价矩阵（只读）；仅日历价/未来15天问题使用，"
                "今天本店房型现价改用竞品现价工具。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_calendar_price_watch_status",
            description=f"日历价状态/配置/轮询间隔（只读）；读 operator_summary。{_MCP_CARD_HINT}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="run_calendar_price_watch_once",
            description=f"刷新日历价：现读未来日历价，默认不推 webhook；读 operator_summary。{_MCP_SKILL_REF}",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                    "push_to_feishu": {
                        "type": "boolean",
                        "description": (
                            "Hermes 问答一律忽略并强制 false；定时盯盘推送不走本工具。"
                        ),
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_competitor_price_trend",
            description=(
                "查竞品历史价格序列与趋势（只读）。要图传 with_chart=true。"
                "最低价对比配方：days=1+granularity=hour+metric=min"
                "+hotels=『本店,竞店名』+with_chart=true。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3650,
                        "default": 3,
                    },
                    "granularity": {
                        "type": "string",
                        "enum": ["run", "hour", "day"],
                        "default": "run",
                    },
                    "date_label": {"type": "string"},
                    "breakfast_kind": {"type": "string"},
                    "metric": {
                        "type": "string",
                        "enum": ["series", "min"],
                        "default": "series",
                        "description": (
                            "series=按房型/早次分序列（默认）；min=每家酒店一条"
                            "「各时段最低价」线（同桶跨早次/房型取最低）。用户问"
                            "『最低价对比』『本店和某竞店谁低』时用 min。"
                        ),
                    },
                    "hotels": {
                        "type": "string",
                        "description": (
                            "只看指定酒店，逗号分隔店名关键词，『本店』代表本店"
                            "（如『本店,水云岸』）。省略=全部酒店。"
                        ),
                    },
                    "with_chart": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "true=向群里发一张价格趋势曲线图卡片（PNG 图片）；"
                            "false=只用文字与数据回答。用户提到「曲线/波动图/"
                            "折线图/走势图/出图」时传 true。"
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="analyze_competitor_price_change",
            description=(
                "分析相邻两次成功竞品轮询的价格变化（只读，不现爬）。"
                f"用户问「竞品最近变化/变化情况如何」优先用本工具。{_MCP_CARD_HINT}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MESSAGE_ID_SCHEMA,
                    **FEISHU_SESSION_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    **RESPONSE_MODE_SCHEMA,
                    "watch_group_key": {
                        "type": "string",
                        "description": "竞品监控组 key；省略时按当前 chat_id/store_hint 解析，显式 key 也必须属于当前群",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="pause_watch",
            description=(
                "暂停指定 EBK 或竞品盯盘任务。"
                "一群多店时每家店有独立竞品组；用户说「两家/全部」时设 all_bound_groups=true。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_kind": {"type": "string", "enum": ["ebk", "competitor"]},
                    "watch_group_key": {
                        "type": "string",
                        "description": "单个监控组 key；省略则按 chat_id/store_hint 解析，禁止在多店群直接用 default_competitor_watch",
                    },
                    "all_bound_groups": {
                        "type": "boolean",
                        "description": "为 true 时对本群绑定的全部竞品组（或全部 EBK 组）逐个暂停/恢复",
                        "default": False,
                    },
                },
                "required": ["watch_kind"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="resume_watch",
            description=(
                "恢复指定 EBK 或竞品盯盘任务。"
                "一群多店时每家店有独立竞品组；用户说「两家/全部」时设 all_bound_groups=true。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "watch_kind": {"type": "string", "enum": ["ebk", "competitor"]},
                    "watch_group_key": {
                        "type": "string",
                        "description": "单个监控组 key；省略则按 chat_id/store_hint 解析，禁止在多店群直接用 default_competitor_watch",
                    },
                    "all_bound_groups": {
                        "type": "boolean",
                        "description": "为 true 时对本群绑定的全部竞品组（或全部 EBK 组）逐个暂停/恢复",
                        "default": False,
                    },
                },
                "required": ["watch_kind"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="send_operator_document",
            description=(
                "把运营内容渲染成办公文档发到当前群（xlsx/docx/pptx/pdf，只发文件不执行调价）。"
                "价格铺排/经营汇报要文件时使用；content 传 markdown（#/##标题、-要点、|表格|）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "doc_type": {
                        "type": "string",
                        "enum": ["xlsx", "docx", "pptx", "pdf"],
                        "description": "xlsx=表格（铺排表/明细），docx=文档（报告/说明），pptx=演示（汇报/评审），pdf=定稿分发",
                    },
                    "title": {
                        "type": "string",
                        "description": "文档标题（用于文件名与封面）",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "文档内容 markdown：'# 主标题'、'## 章节'、'- 要点'、"
                            "'| 列1 | 列2 |' 表格行（首行为表头）。数字须来自本轮工具结果。"
                        ),
                    },
                },
                "required": ["doc_type", "title", "content"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_manual_pricing_approval",
            description="创建手动调价审批卡（仅审批，不直写 OTA）。缺字段时后端发确认卡。",
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    **STORE_HINT_SCHEMA,
                    "room_type": {
                        "type": "string",
                        "description": "房型名称本身；不要把平台名（携程/美团/飞猪）拼进房型，平台请用 platform 参数",
                    },
                    "date_label": {
                        "type": "string",
                        "enum": ["today", "tomorrow", "specified"],
                        "description": "入住日期：today/tomorrow，或用户提到具体日期（如9月20日）时用 specified 并填 checkin_date",
                    },
                    "checkin_date": {
                        "type": "string",
                        "description": "具体入住日期 ISO（YYYY-MM-DD）；仅 date_label=specified 时必填",
                    },
                    "target_price": {"type": "integer", "minimum": 1},
                    "platform": {
                        "type": "string",
                        "enum": ["ctrip", "meituan", "fliggy"],
                        "description": "调价平台；用户提到携程/美团/飞猪时传对应值，省略则用当前群绑定的调价账号",
                    },
                    "watch_group_key": {
                        "type": "string",
                        "description": "监控组 key；ebk 用 EBK 组，competitor 用竞品组，省略则走各自默认",
                    },
                },
                "required": ["room_type", "date_label", "target_price", "chat_id", "message_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="dispatch_watch_feishu_event",
            description=(
                "转发运营原话给后端出权威卡/审批卡（价目表/调价/促销/金字塔指令必用）；"
                "优先传 text=原话，服务端自动组装事件并补会话 message_id/chat_id，禁止改写。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **MCP_TOOL_CONTEXT_SCHEMA,
                    "text": {"type": "string", "description": "运营原话（一字不改）"},
                    "payload": {"type": "object"},
                },
                "additionalProperties": False,
            },
        ),
    ]
    return tools


ToolHandler = Callable[[WatchApiClient, dict[str, Any]], Awaitable[list[TextContent]]]


async def _tool_list_watch_groups(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload(action="列出盯盘组")
    else:
        payload = await client.get_watch_group_catalog(
            chat_id=chat_id,
            store_hint=merged.get("store_hint"),
        )
    return _text_result(payload)


async def _tool_resolve_feishu_chat_context(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload()
    else:
        payload = await client.get_feishu_chat_context(
            chat_id,
            store_hint=merged.get("store_hint"),
            include_runtime_health=True,
        )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_query_hotel_analytics(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """V5 Step 2 — cross-profile analytics, scoped by the caller's chat_id role.

    The backend enforces operator=own / supervisor=all|selected and returns a
    clear error on permission failure (fail-closed). The reply always goes back
    through ``_with_auto_feishu_card`` so it lands in the inbound chat_id only
    (no cross-group leak).
    """

    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    scope = str(merged.get("scope") or "").strip().lower() or None
    raw_profile_keys = merged.get("profile_keys")
    profile_keys: list[str] | None = None
    if isinstance(raw_profile_keys, list):
        profile_keys = [str(item).strip() for item in raw_profile_keys if str(item).strip()]
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload()
    else:
        payload = await client.get_hotel_analytics_overview(
            chat_id=chat_id,
            scope=scope,
            profile_keys=profile_keys,
        )
        # V5 Step 5 — pour the structured backend report into the fixed-format
        # analytics report card. AI only ever supplies analysis text (left empty
        # here; the agent may fill operator_summary.analysis before sending).
        # The card byte-limit guard keeps the push under the webhook ceiling.
        if isinstance(payload, dict) and payload.get("hotels") is not None:
            from client.feishu_cards import attach_feishu_card

            payload = attach_feishu_card(
                {"report": payload, "operator_summary": {}}, "analytics_report"
            )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_rag_search(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """Context-free read-only RAG retrieval for all agents."""

    query = str(arguments.get("query") or "").strip()
    raw_top_k = arguments.get("top_k")
    top_k = 3
    if isinstance(raw_top_k, int) and 1 <= raw_top_k <= 20:
        top_k = raw_top_k
    payload = await client.rag_search(query=query, top_k=top_k)
    return _text_result(payload)


async def _tool_search_group_archive(
    client: WatchApiClient, arguments: dict[str, Any]
) -> list[TextContent]:
    """群资料归档查询：每日实施经营数据/文档的永久积累库。"""

    raw_limit = arguments.get("limit")
    limit = 50
    if isinstance(raw_limit, int) and 1 <= raw_limit <= 200:
        limit = raw_limit
    payload = await client.search_group_archive(
        query=str(arguments.get("query") or "").strip(),
        date_start=str(arguments.get("date_start") or "").strip(),
        date_end=str(arguments.get("date_end") or "").strip(),
        chat_id=str(arguments.get("chat_id") or "").strip(),
        message_type=str(arguments.get("message_type") or "").strip(),
        limit=limit,
    )
    return _text_result(payload)


_BI_WEEKDAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _bi_metric_value(metric: Any) -> Any:
    """解包 backend 的 {value,status,source} 指标包装，只留值。"""
    if isinstance(metric, dict):
        return metric.get("value")
    return metric


def _bi_completion(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {"value": None, "status": "unavailable", "reason": None}
    return {
        "value": block.get("value"),
        "status": block.get("status"),
        "reason": block.get("reason"),
    }


def _compact_bi_daily_report(report: dict[str, Any], section: str) -> dict[str, Any]:
    """把 backend 完整 BI 日报裁成单板块紧凑载荷（丢 source/长文案，保判定与完成系数）。"""
    quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
    dimensions = ((quality.get("coverage") or {}).get("dimensions")) or {}
    out: dict[str, Any] = {
        "status": "ok",
        "tool": "get_bi_daily_report",
        "section": section,
        "as_of": report.get("as_of"),
        "coverage": {
            str(key): (value or {}).get("status") if isinstance(value, dict) else value
            for key, value in dimensions.items()
        },
    }
    if section == "forward":
        days = []
        for day in report.get("forward") or []:
            own = day.get("own") or {}
            circle = day.get("circle") or {}
            market = day.get("market") or {}
            prior = day.get("prior_year") or {}
            prior_actual = prior.get("actual") or {}
            days.append(
                {
                    "date": day.get("stay_date"),
                    "weekday": _BI_WEEKDAY_LABELS.get(day.get("weekday"), day.get("weekday")),
                    "own": {
                        "booked_rooms": _bi_metric_value(own.get("booked_room_nights")),
                        "occupancy": _bi_metric_value(own.get("occupancy_rate")),
                        "pre_set_price": _bi_metric_value(own.get("pre_set_price")),
                    },
                    "circle": {
                        "my_occ": circle.get("my_occupancy_rate"),
                        "circle_occ": circle.get("competitor_occupancy_rate"),
                        "verdict": circle.get("verdict_label") or circle.get("status"),
                    },
                    "market_city_heat": _bi_metric_value(market.get("city_heat")),
                    "prior_year_same_weekday": {
                        "date": prior.get("mapped_date"),
                        "rule": prior.get("matching_rule"),
                        "booked_rooms": prior_actual.get("booked_room_nights"),
                        "occupancy": prior_actual.get("occupancy_rate"),
                        "adr": prior_actual.get("adr"),
                    },
                }
            )
        out["days"] = days
    elif section == "performance":
        days = []
        for day in report.get("performance") or []:
            totals = day.get("totals") or {}
            prior = day.get("prior_year") or {}
            prior_actual = prior.get("actual") or {}
            budget = day.get("budget") or {}
            forecast = day.get("forecast") or {}
            days.append(
                {
                    "date": day.get("stay_date"),
                    "weekday": _BI_WEEKDAY_LABELS.get(day.get("weekday"), day.get("weekday")),
                    "actual": {
                        "room_nights": totals.get("booked_room_nights"),
                        "revenue": totals.get("room_revenue"),
                        "platforms_missing_nights": totals.get("platforms_missing_nights") or [],
                    },
                    "prior_year": {
                        "date": prior.get("mapped_date"),
                        "room_nights": prior_actual.get("booked_room_nights"),
                        "occupancy": prior_actual.get("occupancy_rate"),
                        "adr": prior_actual.get("adr"),
                        "completion": (prior.get("completion") or {}).get("value"),
                        "completion_status": (prior.get("completion") or {}).get("status"),
                    },
                    "budget_completion": _bi_completion(budget.get("completion")),
                    "forecast_completion": _bi_completion(forecast.get("completion")),
                }
            )
        out["days"] = days
    elif section == "weekday_profile":
        rows = []
        for row in report.get("weekday_profile") or []:
            rows.append(
                {
                    "weekday": row.get("weekday_label") or row.get("weekday"),
                    "current": row.get("current") or {},
                    "prior_year": row.get("prior_year") or {},
                    "completion": _bi_completion(row.get("completion")),
                }
            )
        out["weekday_profile"] = rows
    else:
        out["quality"] = quality
    out["notes"] = quality.get("metric_notes") or []
    return out


async def _tool_get_bi_daily_report(
    client: WatchApiClient, arguments: dict[str, Any]
) -> list[TextContent]:
    """经营 BI 四板块：chat_id 解析本群店铺后读 bi-daily 报告并按板块紧凑化。"""

    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        return _text_result(missing_session_chat_id_payload())
    section = str(merged.get("section") or "forward").strip().lower()
    if section not in {"forward", "performance", "weekday_profile", "quality"}:
        return _text_result(
            {
                "status": "invalid_request",
                "summary": "section 仅支持 forward、performance、weekday_profile、quality。",
            }
        )
    horizon_days = merged.get("horizon_days")
    lookback_days = merged.get("lookback_days")
    payload = await client.get_bi_daily_report(
        chat_id=chat_id,
        store_hint=merged.get("store_hint"),
        horizon_days=horizon_days if isinstance(horizon_days, int) else 14,
        lookback_days=lookback_days if isinstance(lookback_days, int) else 30,
    )
    if not isinstance(payload, dict) or payload.get("status") not in {None, "ok"}:
        # fail-closed 载荷（session/shop/backend）原样透传，不冒充有数。
        return _text_result(payload if isinstance(payload, dict) else {"status": "failed", "summary": str(payload)})
    if section == "quality":
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        dimensions = ((quality.get("coverage") or {}).get("dimensions")) or {}
        return _text_result(
            {
                "status": "ok",
                "tool": "get_bi_daily_report",
                "section": "quality",
                "as_of": payload.get("as_of"),
                "coverage": dimensions,
                "notes": quality.get("metric_notes") or [],
            }
        )
    return _text_result(_compact_bi_daily_report(payload, section))


async def _tool_distill_external_source(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """V6 Step 2 — 触发一次性蒸馏（Hermes 只触发，不做 DOM）。"""

    payload = await client.distill_external_source(
        source_type=str(arguments.get("source_type") or "").strip(),
        content=str(arguments.get("content") or "").strip(),
        platform=str(arguments.get("platform") or "").strip(),
        distill_prompt=str(arguments.get("distill_prompt") or "").strip(),
    )
    return _text_result(payload)


async def _tool_create_distill_source(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """UI-Fix-F4F — 创建定时蒸馏数据源（委托后端，飞书 @机器人 定时学习入口）。"""

    raw_interval = arguments.get("schedule_interval_minutes")
    interval = 120
    if isinstance(raw_interval, int):
        interval = raw_interval
    elif isinstance(raw_interval, str) and raw_interval.strip().isdigit():
        interval = int(raw_interval.strip())
    payload = await client.create_distill_source(
        source_type=str(arguments.get("source_type") or "").strip(),
        content=str(arguments.get("content") or "").strip(),
        platform=str(arguments.get("platform") or "").strip(),
        title=str(arguments.get("title") or "").strip(),
        distill_prompt=str(arguments.get("distill_prompt") or "").strip(),
        schedule_interval_minutes=interval,
        enabled=bool(arguments.get("enabled", True)),
    )
    return _text_result(payload)


async def _tool_list_distill_sources(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """UI-Fix-F4F — 查询蒸馏数据源列表。"""
    return _text_result(await client.list_distill_sources())


async def _tool_get_operator_watch_configuration(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    session = _session_kwargs(merged)
    if not session["chat_id"]:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload(action="读取盯盘配置")
    else:
        payload = await client.get_operator_watch_configuration(
            chat_id=session["chat_id"],
            store_hint=merged.get("store_hint"),
            include_runtime_health=True,
        )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_watch_scheduler_status(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    payload = await client.runtime_health()
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_weekly_operator_briefing(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload(action="生成上周经营简报")
    else:
        payload = await client.get_weekly_operator_briefing(
            chat_id=chat_id,
            store_hint=merged.get("store_hint"),
        )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_operator_hotel_overview(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    session = _session_kwargs(merged)
    keys, blocked = await resolve_overview_watch_groups(
        client,
        chat_id=session["chat_id"],
        store_hint=merged.get("store_hint"),
        ebk_watch_group_key=merged.get("ebk_watch_group_key"),
        competitor_watch_group_key=merged.get("competitor_watch_group_key"),
    )
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, merged, blocked))
    ebk_key, competitor_key = keys
    payload = await client.get_operator_hotel_overview(
        ebk_watch_group_key=ebk_key,
        competitor_watch_group_key=competitor_key,
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_operator_analysis_context(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    session = _session_kwargs(merged)
    payload = await client.get_operator_analysis_context(
        chat_id=session["chat_id"],
        store_hint=merged.get("store_hint"),
        ebk_watch_group_key=merged.get("ebk_watch_group_key"),
        competitor_watch_group_key=merged.get("competitor_watch_group_key"),
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_operator_recent_activity(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    session = _session_kwargs(merged)
    keys, blocked = await resolve_overview_watch_groups(
        client,
        chat_id=session["chat_id"],
        store_hint=merged.get("store_hint"),
        ebk_watch_group_key=merged.get("ebk_watch_group_key"),
        competitor_watch_group_key=merged.get("competitor_watch_group_key"),
    )
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, merged, blocked))
    ebk_key, competitor_key = keys
    payload = await client.get_operator_recent_activity(
        ebk_watch_group_key=ebk_key,
        competitor_watch_group_key=competitor_key,
        allow_default_groups=False,
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _ebk_watch_group(client: WatchApiClient, arguments: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    return await _resolve_tool_watch_group_key(
        client,
        arguments,
        watch_kind="ebk",
        explicit_key=arguments.get("watch_group_key"),
    )


async def _competitor_watch_group(client: WatchApiClient, arguments: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    return await _resolve_tool_watch_group_key(
        client,
        arguments,
        watch_kind="competitor",
        explicit_key=arguments.get("watch_group_key"),
    )


async def _tool_get_ebk_latest_result(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _ebk_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_ebk_latest(
        watch_group_key,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_refund_status(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    platform = str(merged.get("platform") or "").strip() or None
    chat_id = str(merged.get("chat_id") or "").strip() or None
    date_from = str(merged.get("date_from") or "").strip() or None
    date_to = str(merged.get("date_to") or "").strip() or None
    payload = await client.get_refund_status(
        platform=platform,
        refresh=not (date_from or date_to),
        chat_id=chat_id,
        date_from=date_from,
        date_to=date_to,
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_today_reservations(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    platform = str(merged.get("platform") or "").strip() or None
    chat_id = str(merged.get("chat_id") or "").strip() or None
    date_from = str(merged.get("date_from") or "").strip() or None
    date_to = str(merged.get("date_to") or "").strip() or None
    payload = await client.get_today_reservations(
        platform=platform,
        refresh=not (date_from or date_to),
        chat_id=chat_id,
        date_from=date_from,
        date_to=date_to,
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_ebk_period_read(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    platform = str(merged.get("platform") or "").strip() or None
    data_kind = str(merged.get("data_kind") or "").strip() or "business"
    period = str(merged.get("period") or "").strip() or "week"
    chat_id = str(merged.get("chat_id") or "").strip() or None
    date_from = str(merged.get("date_from") or "").strip() or None
    date_to = str(merged.get("date_to") or "").strip() or None
    range_preset = str(merged.get("range_preset") or "").strip() or None
    payload = await client.get_ebk_period_read(
        platform=platform,
        data_kind=data_kind,
        period=period,
        chat_id=chat_id,
        refresh=False,
        date_from=date_from,
        date_to=date_to,
        range_preset=range_preset,
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_pending_review_count(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    payload = await client.pending_review_count(
        platform=str(merged.get("platform") or ""),
        ctrip_watch_group_key=merged.get("ctrip_watch_group_key"),
        chat_id=str(merged.get("chat_id") or "").strip() or None,
        refresh=bool(merged.get("refresh")),
    )
    return _text_result(await _with_auto_feishu_card(client, merged, payload))


async def _tool_get_latest_ebk_snapshot(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    payload = await client.get_latest_ebk_snapshot(
        platform=str(arguments.get("platform") or ""),
        platform_account_id=str(arguments.get("platform_account_id") or ""),
    )
    return _text_result(payload)


async def _tool_get_ebk_joint_analysis_context(
    client: WatchApiClient,
    arguments: dict[str, Any],
) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    if not chat_id:
        from client.feishu_store_resolver import missing_session_chat_id_payload

        payload = missing_session_chat_id_payload(action="生成 EBK 联合经营分析")
        return _text_result(await _with_auto_feishu_card(client, merged, payload))

    _, blocked = await _ebk_watch_group(client, merged)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, merged, blocked))

    context = await client.get_feishu_chat_context(
        chat_id,
        store_hint=merged.get("store_hint"),
    )
    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    if resolution.get("status") != "resolved":
        from client.feishu_store_resolver import ambiguous_store_payload

        payload = ambiguous_store_payload(context)
        return _text_result(await _with_auto_feishu_card(client, merged, payload))

    user_store_hint = str(merged.get("store_hint") or "").strip()
    resolved_store_name = str(
        resolution.get("store_name")
        or resolution.get("matched_store_name")
        or resolution.get("store_label")
        or ""
    ).strip()
    # P6：三平台账号以后端 chat-context 的 ota_ebk_accounts 为准——店铺已绑
    # 店铺时后端按 session.shop_key 精确过滤；未绑定时后端用锚定账号/唯一性
    # 兜底。客户端品牌词模糊匹配已退役，禁止再按店名猜浏览器账号。
    ota_accounts = (
        context.get("ota_ebk_accounts")
        if isinstance(context.get("ota_ebk_accounts"), dict)
        else {}
    )
    resolved: dict[str, str | None] = {}
    for platform in ("ctrip", "meituan", "fliggy"):
        entry = ota_accounts.get(platform)
        account = (
            str(entry.get("platform_account_id") or "").strip()
            if isinstance(entry, dict)
            else ""
        )
        resolved[platform] = account or None
    # 锚点挂载兜底（2026-09-16 链路，确定性）：飞书群绑定的携程 EBK 组即店铺
    # 锚点，挂载其上的平台监控组就是该店铺账号——与自动推送同款链路。
    if not resolved.get("meituan") or not resolved.get("fliggy"):
        ebk_groups = (
            context.get("ebk_groups") if isinstance(context.get("ebk_groups"), list) else []
        )
        anchor_key = ""
        for group in ebk_groups:
            if isinstance(group, dict):
                anchor_key = str(group.get("group_key") or "").strip()
                if anchor_key:
                    break
        if anchor_key:
            from client.watch_api import resolve_mounted_ebk_account_ids

            mounted = await resolve_mounted_ebk_account_ids(
                client, ctrip_watch_group_key=anchor_key
            )
            resolved = {
                "ctrip": resolved.get("ctrip"),
                "meituan": resolved.get("meituan") or mounted.get("meituan"),
                "fliggy": resolved.get("fliggy") or mounted.get("fliggy"),
            }
    ctrip_id = str(merged.get("ctrip_platform_account_id") or "").strip() or resolved["ctrip"]
    meituan_id = str(merged.get("meituan_platform_account_id") or "").strip() or resolved["meituan"]
    fliggy_id = str(merged.get("fliggy_platform_account_id") or "").strip() or resolved["fliggy"]
    platforms = merged.get("platforms")
    platform_list = [str(item) for item in platforms] if isinstance(platforms, list) else None
    if platform_list is None and any((ctrip_id, meituan_id, fliggy_id)):
        platform_list = ["ctrip", "meituan", "fliggy"]
    payload = await client.get_ebk_joint_analysis_context(
        ctrip_platform_account_id=ctrip_id,
        meituan_platform_account_id=meituan_id,
        fliggy_platform_account_id=fliggy_id,
        platforms=platform_list,
        refresh_before_analysis=bool(merged.get("refresh_before_analysis")),
        actor_id=str(merged.get("open_id") or os.environ.get("HERMES_ACTOR_ID", "hermes")),
    )
    operator = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    store_label = resolved_store_name or user_store_hint
    enriched = {
        **payload,
        "operator_summary": {
            **operator,
            **({"store_label": store_label} if store_label else {}),
        },
    }
    enriched = attach_feishu_card(enriched, "ebk_joint_brief")
    return _text_result(await _with_auto_feishu_card(client, merged, enriched))


async def _tool_read_ebk_datasets(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _ebk_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    from client.watch_api import expand_pyramid_bid_dataset_ids

    payload = await client.read_ebk_datasets(
        watch_group_key=watch_group_key,
        dataset_ids=expand_pyramid_bid_dataset_ids(
            arguments.get("dataset_ids"),
            user_text=str(arguments.get("user_text") or ""),
        ),
        metric_keys=arguments.get("metric_keys"),
        actor_id=arguments.get("open_id") or os.environ.get("HERMES_ACTOR_ID", "hermes"),
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_run_ebk_watch_once(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _ebk_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.run_ebk_watch_once(
        watch_group_key,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_run_competitor_watch_once(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    from client.domain_intent_router import is_tool_allowed, route_domain_intent

    user_text = str(arguments.get("user_text") or "").strip()
    route = route_domain_intent(user_text)
    if not is_tool_allowed(route, "run_competitor_watch_once"):
        return _normalized_text_result(
            {
                "status": "tool_not_allowed",
                "summary": "只有用户本轮明确要求刷新、现爬或立即读取竞品时，才允许启动竞品浏览器读取。",
            },
            tool_name="run_competitor_watch_once",
        )
    # Hermes Q&A hard ban: never push webhook/briefing cards from chat-triggered runs.
    push_to_feishu = False
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.run_competitor_watch_once(
        watch_group_key,
        push_to_feishu=push_to_feishu,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_competitor_latest_prices(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_competitor_latest(
        watch_group_key,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_calendar_price_latest(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_calendar_price_latest(
        watch_group_key,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_calendar_price_watch_status(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_calendar_price_watch_status(
        watch_group_key,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_run_calendar_price_watch_once(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    # Hermes Q&A hard ban: never push webhook cards from chat-triggered calendar runs.
    push_to_feishu = False
    session = _session_kwargs(arguments)
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.run_calendar_price_watch_once(
        watch_group_key,
        push_to_feishu=push_to_feishu,
        chat_id=session["chat_id"],
        operator_id=session["operator_id"],
    )
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_get_competitor_price_trend(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_competitor_price_trend(
        watch_group_key,
        days=int(arguments.get("days") or 3),
        granularity=str(arguments.get("granularity") or "run"),
        date_label=arguments.get("date_label"),
        breakfast_kind=arguments.get("breakfast_kind"),
    )
    # 2026-10-08 对比图能力：hotels 过滤（『本店』匹配 role=own）+ metric=min
    # 归并（每店一条「各时段最低价」线）。生产实证（立瑞）：本店公开页常只有
    # 含早序列、水云岸只有无早序列，按早次拆面板两家永不同框；min 口径才
    # 同图可比。归并在 MCP 侧做（后端 API 不动），卡片/图表据此渲染。
    if isinstance(payload, dict):
        from client.competitor_trend_model import (
            build_min_comparison_series,
            filter_trend_series_by_hotels,
        )

        hotels_raw = str(arguments.get("hotels") or "").strip()
        if hotels_raw:
            payload["series"] = filter_trend_series_by_hotels(
                payload.get("series") if isinstance(payload.get("series"), list) else [],
                [token for token in hotels_raw.replace("，", ",").split(",")],
            )
        if str(arguments.get("metric") or "series").strip().lower() == "min":
            payload["metric"] = "min"
            payload["series"] = build_min_comparison_series(
                payload.get("series") if isinstance(payload.get("series"), list) else []
            )
            payload["point_count"] = sum(
                len(series.get("points") or [])
                for series in payload.get("series") or []
                if isinstance(series, dict)
            )
            payload.setdefault("operator_summary", {})
            if isinstance(payload.get("operator_summary"), dict):
                payload["operator_summary"]["intro"] = "各时段最低价对比（不分早，按读价时间）"
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_analyze_competitor_price_change(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    watch_group_key, blocked = await _competitor_watch_group(client, arguments)
    if blocked is not None:
        return _text_result(await _with_auto_feishu_card(client, arguments, blocked))
    payload = await client.get_price_change_alert_latest(watch_group_key)
    return _text_result(await _with_auto_feishu_card(client, arguments, payload))


async def _tool_pause_watch(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    return _text_result(await _set_watch_enabled(client, arguments, enabled=False))


async def _tool_resume_watch(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    return _text_result(await _set_watch_enabled(client, arguments, enabled=True))


async def _tool_send_operator_document(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    """渲染 markdown 为 xlsx/docx/pptx 并发到当前群；渲染失败回退提示，不抛原始异常。"""
    merged = merge_feishu_session(arguments)
    chat_id = str(merged.get("chat_id") or "").strip()
    doc_type = str(merged.get("doc_type") or "").strip().lower()
    title = str(merged.get("title") or "").strip()
    content = str(merged.get("content") or "").strip()
    if not chat_id:
        return _text_result(
            {
                "status": "failed",
                "summary": "缺少 chat_id，无法发送文档。",
                "next_action": "请透传当前群 chat_id。",
            }
        )
    if doc_type not in {"xlsx", "docx", "pptx", "pdf"} or not title or not content:
        return _text_result(
            {
                "status": "failed",
                "summary": "doc_type/title/content 均为必填（doc_type=xlsx|docx|pptx）。",
                "next_action": "补全参数后重试，或改用文字回复。",
            }
        )
    try:
        from client.document_render import render_document, suggested_filename

        payload = render_document(content, doc_type=doc_type, title=title)
    except Exception as exc:
        return _text_result(
            {
                "status": "failed",
                "summary": f"文档渲染失败：{exc}",
                "next_action": "请简化内容后重试，或改用文字回复。",
            }
        )
    import base64 as _base64

    reply = await client.send_operator_feishu_file(
        chat_id=chat_id,
        doc_type=doc_type,
        data_base64=_base64.b64encode(payload).decode("ascii"),
        title=title,
        filename=suggested_filename(title, doc_type),
        store_hint=str(merged.get("store_hint") or "").strip() or None,
    )
    sent = reply.get("status") == "success"
    return _text_result(
        {
            "status": "success" if sent else "failed",
            "summary": (
                f"已向本群发送文档《{title}》（{doc_type}）。"
                if sent
                else f"文档发送失败：{reply.get('summary') or '未知错误'}"
            ),
            "doc_type": doc_type,
            "reply": reply,
            "operator_reply_note": (
                "文件已发到群里；请用一两句话说明文件内容与口径，勿长文复述。"
                if sent
                else "文件未发出，请向用户说明失败并改用文字总结。"
            ),
        }
    )


async def _tool_create_manual_pricing_approval(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    checkin_date = arguments.get("checkin_date")
    return _text_result(
        await client.create_manual_pricing_approval(
            room_type=str(arguments["room_type"]),
            date_label=str(arguments["date_label"]),
            target_price=int(arguments["target_price"]),
            watch_group_key=arguments.get("watch_group_key"),
            chat_id=str(arguments["chat_id"]),
            message_id=str(arguments["message_id"]),
            open_id=arguments.get("open_id"),
            store_hint=arguments.get("store_hint"),
            platform=arguments.get("platform"),
            checkin_date=str(checkin_date).strip() or None if checkin_date is not None else None,
        )
    )


async def _tool_reply_feishu_operator_card(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    return _text_result(
        {
            "status": "misrouted",
            "summary": (
                "reply_feishu_operator_card 只能回复已经发送的飞书运营卡片；"
                "请提供需要回复的 message_id 后再调用。"
            ),
            "routing_hints": ["get_ebk_latest_result", "get_competitor_latest_prices"],
        }
    )


def _dispatch_session_message_fields(merged: dict[str, Any]) -> tuple[str, str]:
    return (
        str(merged.get("message_id") or "").strip(),
        str(merged.get("chat_id") or "").strip(),
    )


def _compose_dispatch_event_payload(text: str, merged: dict[str, Any]) -> dict[str, Any] | None:
    # 后端 parse_command_context 硬性要求 event.message 的 message_id 与
    # chat_id；LLM 自组 payload 拿不到 message_id（2026-10-14 生产实证：
    # 缺失即被忽略，查房价卡/调价审批卡链路全断）。text 入口由服务端
    # 从会话桥参数组装完整事件。
    message_id, chat_id = _dispatch_session_message_fields(merged)
    if not message_id or not chat_id:
        return None
    message: dict[str, Any] = {
        "message_id": message_id,
        "chat_id": chat_id,
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    event: dict[str, Any] = {"message": message}
    open_id = str(merged.get("open_id") or "").strip()
    if open_id:
        event["sender"] = {"sender_id": {"open_id": open_id}}
    return {"header": {"event_type": "im.message.receive_v1"}, "event": event}


def _normalize_dispatch_event_payload(
    payload: dict[str, Any], text_arg: str, merged: dict[str, Any]
) -> dict[str, Any]:
    if str((payload.get("header") or {}).get("event_type") or "").strip() != "im.message.receive_v1":
        # 卡片回调/URL 验证等非文本事件原样转发。
        return payload
    normalized = dict(payload)
    normalized["header"] = dict(payload.get("header") or {})
    normalized["header"]["event_type"] = "im.message.receive_v1"
    event = dict(payload.get("event") or {})
    message = dict(event.get("message") or {})
    content = message.get("content")
    if isinstance(content, dict):
        message["content"] = json.dumps(content, ensure_ascii=False)
    message_id, chat_id = _dispatch_session_message_fields(merged)
    if not str(message.get("message_id") or "").strip() and message_id:
        message["message_id"] = message_id
    if not str(message.get("chat_id") or "").strip() and chat_id:
        message["chat_id"] = chat_id
    if text_arg and not str(message.get("content") or "").strip():
        message["content"] = json.dumps({"text": text_arg}, ensure_ascii=False)
    event["message"] = message
    normalized["event"] = event
    return normalized


async def _tool_dispatch_watch_feishu_event(client: WatchApiClient, arguments: dict[str, Any]) -> list[TextContent]:
    merged = _merged_arguments(arguments)
    text_arg = str(arguments.get("text") or "").strip()
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        event_payload = _normalize_dispatch_event_payload(payload, text_arg, merged)
    elif text_arg:
        event_payload = _compose_dispatch_event_payload(text_arg, merged)
    else:
        event_payload = None
    if not event_payload:
        return _text_result(
            {
                "status": "invalid_arguments",
                "summary": (
                    "需要 text=运营原话，或含 event.message 的 payload；"
                    "缺会话 message_id/chat_id 时先调 resolve_feishu_chat_context 再重试。"
                ),
            }
        )
    return _text_result(await client.forward_feishu_event(event_payload))


_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "list_watch_groups": _tool_list_watch_groups,
    "resolve_feishu_chat_context": _tool_resolve_feishu_chat_context,
    "get_refund_status": _tool_get_refund_status,
    "get_today_reservations": _tool_get_today_reservations,
    "get_ebk_period_read": _tool_get_ebk_period_read,
    "pending_review_count": _tool_pending_review_count,
    "query_hotel_analytics": _tool_query_hotel_analytics,
    "rag_search": _tool_rag_search,
    "search_group_archive": _tool_search_group_archive,
    "get_bi_daily_report": _tool_get_bi_daily_report,
    "distill_external_source": _tool_distill_external_source,
    "create_distill_source": _tool_create_distill_source,
    "list_distill_sources": _tool_list_distill_sources,
    "get_operator_watch_configuration": _tool_get_operator_watch_configuration,
    "get_watch_scheduler_status": _tool_get_watch_scheduler_status,
    "get_weekly_operator_briefing": _tool_get_weekly_operator_briefing,
    "get_operator_hotel_overview": _tool_get_operator_hotel_overview,
    "get_operator_analysis_context": _tool_get_operator_analysis_context,
    "get_operator_recent_activity": _tool_get_operator_recent_activity,
    "get_ebk_latest_result": _tool_get_ebk_latest_result,
    "get_latest_ebk_snapshot": _tool_get_latest_ebk_snapshot,
    "get_ebk_joint_analysis_context": _tool_get_ebk_joint_analysis_context,
    "read_ebk_datasets": _tool_read_ebk_datasets,
    "run_ebk_watch_once": _tool_run_ebk_watch_once,
    "run_competitor_watch_once": _tool_run_competitor_watch_once,
    "get_competitor_latest_prices": _tool_get_competitor_latest_prices,
    "get_calendar_price_latest": _tool_get_calendar_price_latest,
    "get_calendar_price_watch_status": _tool_get_calendar_price_watch_status,
    "run_calendar_price_watch_once": _tool_run_calendar_price_watch_once,
    "get_competitor_price_trend": _tool_get_competitor_price_trend,
    "analyze_competitor_price_change": _tool_analyze_competitor_price_change,
    "pause_watch": _tool_pause_watch,
    "resume_watch": _tool_resume_watch,
    "create_manual_pricing_approval": _tool_create_manual_pricing_approval,
    "send_operator_document": _tool_send_operator_document,
    "reply_feishu_operator_card": _tool_reply_feishu_operator_card,
    "dispatch_watch_feishu_event": _tool_dispatch_watch_feishu_event,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    arguments = merge_feishu_session(arguments or {})
    arguments["_hermes_tool_name"] = name
    client = _get_client()
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _normalized_text_result({"status": "tool_not_allowed", "summary": f"未知工具：{name}"}, tool_name=name)
    try:
        return _normalize_tool_result(await handler(client, arguments), tool_name=name)
    except Exception as exc:
        return _normalized_text_result(_safe_tool_error(name, exc), tool_name=name)


def _http_error_summary(status_code: int | None, exc: Exception) -> str:
    """HTTP 错误文案；422 带上后端校验详情，避免「参数校验错误」无从排查。"""
    base = f"后台 API 返回 HTTP {status_code}；请稍后按本轮原问题重试。"
    if status_code != 422:
        return base
    detail = ""
    try:
        payload = exc.response.json()  # type: ignore[attr-defined]
        raw = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            raw = raw[0].get("msg") or str(raw[0])
        detail = str(raw or "").strip()
    except Exception:  # noqa: BLE001 - 详情提取失败退回通用文案
        return base
    if not detail:
        return base
    return f"后台 API 校验失败（HTTP 422）：{detail[:160]}"


def _safe_tool_error(name: str, exc: Exception) -> dict[str, Any]:
    browser_timeout_routes = {
        "run_ebk_watch_once": ["get_ebk_latest_result", "list_watch_groups"],
        "read_ebk_datasets": ["get_ebk_latest_result", "list_watch_groups"],
        "run_competitor_watch_once": ["get_competitor_latest_prices", "list_watch_groups"],
        "run_calendar_price_watch_once": [
            "get_calendar_price_latest",
            "get_calendar_price_watch_status",
            "list_watch_groups",
        ],
    }
    if isinstance(exc, httpx.ReadTimeout):
        if name in browser_timeout_routes:
            latest_tool = browser_timeout_routes[name][0]
            return {
                "status": "failed",
                "summary": (
                    # HTTP timeout only means Hermes stopped waiting; run completion is unknown.
                    "浏览器读取耗时较长，本次等待超时，无法确认本次刷新是否完成或是否已经入库。"
                    f"请勿向用户说「MCP 连不上」或要求重发消息；可稍后调用 {latest_tool} "
                    "查看最近一次已入库结果，必要时再重试刷新。"
                ),
                "error_type": "ReadTimeout",
                "routing_hints": browser_timeout_routes[name],
            }
        return {
            "status": "failed",
            "summary": "后台 API 响应超时，请稍后重试。",
            "error_type": "ReadTimeout",
        }
    if isinstance(exc, httpx.ConnectError):
        return {
            "status": "failed",
            "summary": (
                "无法连接 pan-hotel-watch 后台 API；请确认 backend 已启动且 WATCH_API_BASE_URL 正确。"
            ),
            "error_type": "ConnectError",
            "routing_hints": ["list_watch_groups"],
        }
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else None
        if name.startswith("get_competitor_") or name == "analyze_competitor_price_change":
            routing_hints = [name]
        elif name == "run_competitor_watch_once":
            routing_hints = ["get_competitor_latest_prices"]
        elif "calendar_price" in name:
            routing_hints = ["get_calendar_price_latest"]
        else:
            routing_hints = ["get_ebk_latest_result", "run_ebk_watch_once"]
        return {
            "status": "failed",
            "summary": _http_error_summary(status_code, exc),
            "error_type": "HTTPStatusError",
            "routing_hints": routing_hints,
        }
    raw = str(exc)
    lowered = raw.lower()
    looks_sensitive = any(
        token in lowered
        for token in (
            "playwright",
            "cursor-sandbox",
            "webhook",
            "profile",
            "chrome.exe",
            "appdata",
        )
    ) or ("\\" in raw and len(raw) > 120)
    return {
        "status": "failed",
        "summary": f"工具 {name} 调用失败，请稍后重试或联系运维。",
        "error_type": type(exc).__name__,
        **({} if looks_sensitive else {"error": raw[:240]}),
    }


async def _set_watch_enabled(
    client: WatchApiClient,
    arguments: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    watch_kind = str(arguments.get("watch_kind") or "")
    action_label = "恢复" if enabled else "暂停"
    body = {"enabled": enabled, "auto_poll_enabled": enabled}

    if bool(arguments.get("all_bound_groups")):
        session = _session_kwargs(arguments)
        chat_id = session.get("chat_id")
        if not chat_id:
            return {
                "status": "invalid_arguments",
                "summary": f"all_bound_groups 需要 chat_id；无法{action_label}本群全部监控组。",
            }
        context = await client.get_feishu_chat_context(
            chat_id,
            store_hint=str(arguments.get("store_hint") or "").strip() or None,
        )
        group_field = "competitor_groups" if watch_kind == "competitor" else "ebk_groups"
        groups = context.get(group_field) if isinstance(context.get(group_field), list) else []
        keys: list[tuple[str, str]] = []
        for item in groups:
            if not isinstance(item, dict):
                continue
            key = str(item.get("group_key") or "").strip()
            if not key:
                continue
            name = str(item.get("name") or item.get("display_name") or key).strip()
            keys.append((key, name))
        if not keys:
            return {
                "status": "config_required",
                "summary": f"本群未绑定可{action_label}的{watch_kind}监控组。",
                "binding_mode": context.get("binding_mode"),
            }
        results: list[dict[str, Any]] = []
        for key, name in keys:
            if watch_kind == "ebk":
                patch_result = await client.patch_ebk_watch_group(key, body=body)
            elif watch_kind == "competitor":
                patch_result = await client.patch_competitor_watch_group(key, body=body)
            else:
                return {"status": "invalid_arguments", "summary": "watch_kind 必须是 ebk 或 competitor"}
            results.append(
                {
                    "watch_group_key": key,
                    "watch_group_name": name,
                    "patch_result": patch_result,
                }
            )
        return _enrich_watch_toggle_response(
            enabled=enabled,
            watch_kind=watch_kind,
            results=results,
            multi_group=True,
        )

    if watch_kind == "ebk":
        watch_group_key, blocked = await _ebk_watch_group(client, arguments)
        if blocked is not None:
            return blocked
        patch_result = await client.patch_ebk_watch_group(watch_group_key, body=body)
    elif watch_kind == "competitor":
        watch_group_key, blocked = await _competitor_watch_group(client, arguments)
        if blocked is not None:
            return blocked
        patch_result = await client.patch_competitor_watch_group(watch_group_key, body=body)
    else:
        return {"status": "invalid_arguments", "summary": "watch_kind 必须是 ebk 或 competitor"}

    group_name = watch_group_key or ""
    watch_group = patch_result.get("watch_group") if isinstance(patch_result.get("watch_group"), dict) else {}
    if watch_group.get("name"):
        group_name = str(watch_group.get("name"))
    return _enrich_watch_toggle_response(
        enabled=enabled,
        watch_kind=watch_kind,
        results=[
            {
                "watch_group_key": watch_group_key,
                "watch_group_name": group_name,
                "patch_result": patch_result,
            }
        ],
        multi_group=False,
    )


def _enrich_watch_toggle_response(
    *,
    enabled: bool,
    watch_kind: str,
    results: list[dict[str, Any]],
    multi_group: bool,
) -> dict[str, Any]:
    action_label = "恢复" if enabled else "暂停"
    kind_label = "EBK 数据中心" if watch_kind == "ebk" else "竞品价格"
    rows = [
        {
            "field": str(item.get("watch_group_name") or item.get("watch_group_key") or "监控组"),
            "value": f"{'已开启' if enabled else '已暂停'} · {item.get('watch_group_key')}",
        }
        for item in results
    ]
    if len(results) == 1:
        only = results[0]
        summary = (
            f"已{action_label}{kind_label}监控组："
            f"{only.get('watch_group_name') or only.get('watch_group_key')}。"
        )
    else:
        summary = f"已对本群 {len(results)} 个{kind_label}监控组执行{action_label}。"
    return {
        "status": "success",
        "summary": summary,
        "watch_kind": watch_kind,
        "enabled": enabled,
        "multi_group": multi_group,
        "group_results": results,
        "operator_summary": {
            "intro": summary,
            "overview_rows": rows,
            "usage_note": (
                "一群多店时，每家门店通常有独立的竞品监控组，不是「一个组覆盖多家」。"
                "用户说两家/全部时，应 all_bound_groups=true 或分别指定 watch_group_key。"
                "禁止把 default_competitor_watch 描述成覆盖泉吉+静里。"
            ),
        },
    }


def _force_utf8_stdio() -> None:
    """mcp 1.2.0 以 sys.stdout 文本编码写 JSON-RPC 帧；Windows 管道默认 GBK，
    中文载荷会坏帧（客户端按 UTF-8 解析出 {"error": 乱码}）。入口强制 UTF-8，
    对已配置 UTF-8 的环境（网关）幂等无副作用。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # 已关闭/不可重配的流保持原样
            pass


async def main() -> None:
    _force_utf8_stdio()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
