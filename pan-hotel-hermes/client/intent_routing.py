from __future__ import annotations

from typing import Any

from client.feishu_cards import attach_ebk_intent_card
from client.watch_groups import WATCH_KIND_EBK, watch_kind_label

EBK_REVIEW_ONLY_DATASET_IDS = frozenset({"comment_monitor"})
EBK_LATEST_RESULT_TOOL = "get_ebk_latest_result"
EBK_RUN_ONCE_TOOL = "run_ebk_watch_once"

_EBK_RUN_NO_MESSAGE_ID_NOTE = (
    "EBK 浏览器读取由后台 API 触发，不依赖飞书 message_id。"
    "全部数据集启用时可能耗时数分钟；禁止对用户说 MCP 连不上或要求重发消息。"
)

INTENT_TOOL_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "key": "review_today",
        "user_intent": "今天新增几个好评",
        "required_tool": EBK_LATEST_RESULT_TOOL,
        "forbidden_tools": ("run_ebk_watch_once", "read_ebk_datasets"),
        "review_dataset_guard": "comment_monitor",
    },
    {
        "key": "ebk_run_once",
        "user_intent": "跑一次最新的EBK来确认最新数据",
        "required_tool": EBK_RUN_ONCE_TOOL,
        "forbidden_tools": (EBK_LATEST_RESULT_TOOL, "read_ebk_datasets"),
    },
    {
        "key": "occupancy_now_read",
        "user_intent": "现在读一次出租率",
        "required_tool": "read_ebk_datasets",
        "forbidden_tools": ("run_ebk_watch_once",),
        "allowed_read_datasets": ("beneficial_data",),
    },
    {
        "key": "promotion_and_pyramid",
        "user_intent": "参加了哪些促销，金字塔今天投放情况",
        "required_tool": "read_ebk_datasets",
        "forbidden_tools": ("get_ebk_latest_result", "run_ebk_watch_once"),
        "allowed_read_datasets": ("promotion_registered", "promotion_effect", "ad_pyramid"),
    },
    {
        "key": "competitor_trend_3d",
        "user_intent": "最近三天竞品价格趋势",
        "required_tool": "get_competitor_price_trend",
        "forbidden_tools": (
            "analyze_competitor_price_change",
            "get_competitor_latest_prices",
        ),
        "days": 3,
    },
    {
        "key": "competitor_trend_1d_amplitude",
        "user_intent": "竞品最近一天的价格涨幅情况",
        "required_tool": "get_competitor_price_trend",
        "forbidden_tools": (
            "get_competitor_latest_prices",
            "analyze_competitor_price_change",
        ),
        "days": 1,
    },
    {
        "key": "competitor_latest_spot",
        "user_intent": "现在竞品多少钱",
        "required_tool": "get_competitor_latest_prices",
        "forbidden_tools": (
            "get_competitor_price_trend",
            "analyze_competitor_price_change",
        ),
    },
    {
        "key": "competitor_change_alert",
        "user_intent": "竞品有没有明显变化，要不要关注",
        "required_tool": "analyze_competitor_price_change",
        "forbidden_tools": (
            "get_competitor_latest_prices",
            "get_competitor_price_trend",
        ),
    },
)


def is_ebk_review_only_dataset_read(dataset_ids: list[str]) -> bool:
    if not dataset_ids:
        return False
    return all(item in EBK_REVIEW_ONLY_DATASET_IDS for item in dataset_ids)


def build_ebk_review_misroute_response(*, watch_group_key: str) -> dict[str, Any]:
    routing_hints = [EBK_LATEST_RESULT_TOOL]
    return {
        "status": "misrouted",
        "summary": (
            "问好评/差评/评论应读取持久化 EBK 最新结果，"
            "不应通过 read_ebk_datasets 现读 comment_monitor。"
        ),
        "watch_kind": WATCH_KIND_EBK,
        "watch_group_key": watch_group_key,
        "routing_hints": routing_hints,
        "operator_next_step": (
            "请改调 get_ebk_latest_result，读取 operator_summary.review_summary；"
            "positive_reviews_yesterday 是昨日好评口径，不是今日实时新增。"
        ),
        "operator_summary": {
            "watch_kind": WATCH_KIND_EBK,
            "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
            "watch_group_key": watch_group_key,
            "routing_hints": routing_hints,
            "usage_note": (
                "数据集 enabled=false 只影响定时轮询，不影响 /latest 已持久化结果。"
                "问好评/差评/评论时不要 read_ebk_datasets(comment_monitor)。"
            ),
        },
    }


def enrich_ebk_run_response(
    result: dict[str, Any],
    *,
    latest_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """EBK run-now 结果：成功时合并 latest 摘要；只持久化，不自动推飞书卡片。"""
    from client.watch_api import enrich_ebk_latest_response

    status = str(result.get("status") or "")
    summary = str(result.get("summary") or "")
    watch_group_key = result.get("watch_group_key")

    if status == "success" and isinstance(latest_snapshot, dict):
        enriched = enrich_ebk_latest_response(latest_snapshot)
        operator = enriched.get("operator_summary") if isinstance(enriched.get("operator_summary"), dict) else {}
        enriched["operator_summary"] = {
            **operator,
            "source": "browser_run_now",
            "data_is_stale": False,
            "suggest_immediate_run": False,
            "stale_data_prompt": None,
            "run_summary": summary or "已完成一次 EBK 盯盘读取。",
            "ebk_run_note": _EBK_RUN_NO_MESSAGE_ID_NOTE,
        }
        enriched.pop("feishu_card", None)
        enriched.pop("feishu_card_kind", None)
        enriched["run_triggered"] = True
        enriched["run_result"] = {
            "status": status,
            "summary": summary,
            "push_result": result.get("feishu_push") or result.get("push_result"),
        }
        enriched["operator_reply_note"] = (
            "已通过后台完成 EBK 读取并持久化，未自动发送飞书卡片。"
            "本轮只用一句话确认数据已刷新及 latest_captured_at；"
            "禁止同一轮再调 get_ebk_joint_analysis_context、get_ebk_latest_result、get_operator_hotel_overview。"
            "用户追问出租率等经营指标时再调 read_ebk_datasets；追问好评时再调 get_ebk_latest_result。"
            "禁止提及 message_id 或要求用户重发消息。"
        )
        return enriched

    dataset_results = result.get("dataset_results")
    has_results = isinstance(dataset_results, list) and len(dataset_results) > 0
    no_enabled_datasets = status == "skipped" and "没有启用" in summary

    operator_summary: dict[str, Any] = {
        "watch_kind": WATCH_KIND_EBK,
        "watch_kind_label": watch_kind_label(WATCH_KIND_EBK),
        "watch_group_key": watch_group_key,
        "run_status": status,
        "summary": summary,
        "ebk_run_note": _EBK_RUN_NO_MESSAGE_ID_NOTE,
    }

    if no_enabled_datasets:
        routing_hints = [EBK_LATEST_RESULT_TOOL]
        operator_summary["routing_hints"] = routing_hints
        operator_summary["usage_note"] = (
            "不是 message_id 或飞书联动问题：当前 EBK 监控组没有启用的数据集（enabled=false），"
            "后台 run-now 会直接跳过，不会开浏览器。"
            "需在控制台启用数据集（如效益数据、流量、点评监控）并可选开启自动轮询。"
        )
        operator_summary["next_action"] = "控制台 → EBK 盯盘 → 启用数据集并开启自动轮询。"
    elif status == "skipped" and "已停用" in summary:
        operator_summary["usage_note"] = "EBK 监控组已停用，不是 message_id 问题。"
        routing_hints = []
    else:
        routing_hints = [EBK_LATEST_RESULT_TOOL] if not has_results else []
        operator_summary["usage_note"] = (
            "读取失败时说明真实 status/summary；禁止归咎于缺少 message_id。"
            "human_required 表示需人工处理浏览器登录或 profile 占用。"
        )

    payload: dict[str, Any] = {
        **result,
        "run_triggered": True,
        "operator_summary": operator_summary,
        "operator_reply_note": (
            "已向后台发起 EBK 读取（不依赖 message_id）。"
            "用 operator_summary 向店长说明结果；禁止要求补发消息或解释 message_id。"
        ),
    }
    if routing_hints:
        payload["routing_hints"] = routing_hints
        payload["operator_next_step"] = (
            "若本次未读到新数据，可先 get_ebk_latest_result 看已持久化结果，"
            "并说明需在控制台启用数据集/自动轮询——不是 message_id 问题。"
        )
    return payload


def fixture_by_key(key: str) -> dict[str, Any] | None:
    for item in INTENT_TOOL_FIXTURES:
        if item.get("key") == key:
            return item
    return None
