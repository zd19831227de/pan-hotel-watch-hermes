from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.domain.operator_payload import sanitize_operator_payload

AgentToolRiskLevel = Literal["read_only", "state_change", "approval_only"]


class AgentToolCallContext(BaseModel):
    event_id: str
    message_id: str | None = None
    chat_id: str | None = None
    open_id: str | None = None


class AgentToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    risk_level: AgentToolRiskLevel
    safety_notes: list[str] = Field(default_factory=list)


class AgentToolResult(BaseModel):
    status: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


class EmptyToolArgs(BaseModel):
    pass


class WatchGroupToolArgs(BaseModel):
    watch_group_key: str | None = Field(default=None, description="监控组 key；为空时从当前飞书群绑定解析。")
    platform: Literal["ctrip", "meituan", "fliggy"] = Field(
        default="ctrip",
        description="EBK 平台：ctrip（默认）/meituan/fliggy。美团与飞猪的定时监控挂在携程监控组锚点下。",
    )


class EbkDatasetReadToolArgs(BaseModel):
    watch_group_key: str | None = Field(
        default=None,
        description="EBK 数据中心盯盘组 key；为空时从当前飞书群绑定解析。",
    )
    dataset_ids: list[str] | None = Field(
        default=None,
        description=(
            "要立即读取的 EBK 数据中心数据集 ID，可一次传多个。常用值："
            "business_outline=收益概览，beneficial_data=销售数据，"
            "competition_profile=经营对比，promotion_registered=已参加促销。"
        ),
    )
    metric_keys: list[str] | None = Field(
        default=None,
        description=(
            "用户关心的指标 key，可为空。出租率使用 occupancy_rate；"
            "平均卖价/房价使用 average_selling_price 或 average_price。"
        ),
    )
    time_dimension: Literal["realtime", "yesterday", "daily"] | None = Field(
        default=None,
        description="时间维度：realtime=实时，yesterday=昨日，daily=按日；为空则返回两档。",
    )

    @field_validator("dataset_ids")
    @classmethod
    def _validate_dataset_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        from app.domain.ctrip_ebooking_watch.repository import normalize_dataset_id

        normalized: list[str] = []
        for dataset_id in value:
            item = normalize_dataset_id(_normalize_ebk_dataset_alias(dataset_id))
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("metric_keys")
    @classmethod
    def _normalize_metric_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = []
        for item in value:
            key = _normalize_ebk_metric_alias(item)
            if key and key not in normalized:
                normalized.append(key)
        return normalized or None


class WatchControlToolArgs(BaseModel):
    watch_kind: Literal["ebk", "competitor", "meituan_ebk", "fliggy_ebk"] = Field(
        description=(
            "盯盘类型：ebk（携程数据中心）、competitor（竞品）、"
            "meituan_ebk（美团定时监控）、fliggy_ebk（飞猪定时监控）。"
        )
    )
    watch_group_key: str | None = Field(default=None, description="监控组 key；为空时从当前飞书群绑定解析。")


class ManualPricingApprovalToolArgs(BaseModel):
    room_type: str = Field(description="用户明确提到的房型名称，不含平台名（携程/美团/飞猪）。")
    date_label: Literal["today", "tomorrow", "specified"] = Field(description="入住日期：today、tomorrow 或 specified（用户提到具体日期如9月15日时用 specified）。")
    target_price: int = Field(gt=0, le=99999, description="用户明确输入的目标售价，单位元。")
    checkin_date: str | None = Field(default=None, description="具体入住日期 ISO（YYYY-MM-DD），仅当 date_label=specified 时必填。")
    watch_group_key: str | None = Field(default=None, description="竞品监控组 key；为空时使用默认组。")
    platform: Literal["ctrip", "meituan", "fliggy"] | None = Field(
        default=None,
        description="调价平台；用户提到携程/美团/飞猪时传对应值，省略则用当前群绑定的调价账号。",
    )


_ToolHandler = Callable[[BaseModel, AgentToolCallContext], AgentToolResult | Awaitable[AgentToolResult]]


def _normalize_ebk_dataset_alias(value: Any) -> str:
    text = str(value or "").strip()
    alias_map = {
        "收益概览": "business_outline",
        "经营报告": "business_outline",
        "经营概览": "business_outline",
        "收益": "business_outline",
        "销售数据": "beneficial_data",
        "销售": "beneficial_data",
        "出租率": "beneficial_data",
        "入住率": "beneficial_data",
        "经营对比": "competition_profile",
        "竞争圈": "competition_profile",
        "流量数据": "flow_data",
        "访客量": "flow_data",
        "流量": "flow_data",
        "流失订单": "loss_analysis",
        "流失": "loss_analysis",
        "销售排名": "competition_list",
        "排名": "competition_list",
        "促销效果": "promotion_effect",
        "促销情况": "promotion_effect",
        "已参加促销": "promotion_registered",
        "参加促销": "promotion_registered",
        "促销活动": "promotion_registered",
        "广告金字塔": "ad_pyramid",
        "金字塔": "ad_pyramid",
        "订单点评": "comment_monitor",
        "点评分析": "comment_monitor",
        "promotion_effectiveness": "promotion_effect",
        "revenue_overview": "business_outline",
        "sales_data": "beneficial_data",
        "traffic": "flow_data",
        "pyramid": "ad_pyramid",
    }
    return alias_map.get(text, text)


def _normalize_ebk_metric_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    alias_map = {
        "出租率": "occupancy_rate",
        "入住率": "occupancy_rate",
        "occ": "occupancy_rate",
        "平均卖价": "average_selling_price",
        "平均房价": "average_selling_price",
        "均价": "average_selling_price",
        "成交率": "transaction_rate",
        "转化率": "conversion_rate",
        "实时预订订单": "realtime_booking_orders",
        "实时在店间夜": "realtime_in_house_room_nights",
    }
    return alias_map.get(text, text)


class _RegisteredAgentTool(BaseModel):
    spec: AgentToolSpec
    argument_model: type[BaseModel]
    handler: _ToolHandler

    model_config = {"arbitrary_types_allowed": True}


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredAgentTool] = {}

    def register_tool(
        self,
        spec: AgentToolSpec,
        argument_model: type[BaseModel],
        handler: _ToolHandler,
    ) -> None:
        self._tools[spec.name] = _RegisteredAgentTool(
            spec=spec,
            argument_model=argument_model,
            handler=handler,
        )

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def tool_specs(self) -> list[AgentToolSpec]:
        return [registered.spec for registered in self._tools.values()]

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: AgentToolCallContext,
    ) -> AgentToolResult:
        registered = self._tools.get(name)
        if registered is None:
            return AgentToolResult(
                status="tool_not_allowed",
                summary=f"AI Agent 不允许调用工具：{name}。",
                evidence={
                    "tool_name": name,
                    "handler_invoked": False,
                    "browser_read_invoked": False,
                    "ota_write_invoked": False,
                    "webhook_called": False,
                },
                next_action="请改用已注册的安全工具，或先在后端新增白名单工具定义。",
            )

        try:
            from app.domain.config.service import resolve_feishu_role_for_chat

            role = await resolve_feishu_role_for_chat(context.chat_id)
        except Exception:
            if registered.spec.risk_level == "read_only":
                role = "operator"
            else:
                return AgentToolResult(
                    status="config_required",
                    summary="当前会话角色无法确认，系统没有继续执行工具。",
                    evidence={
                        "tool_name": name,
                        "role_resolution_failed": True,
                        "handler_invoked": False,
                        "browser_read_invoked": False,
                        "ota_write_invoked": False,
                        "webhook_called": False,
                    },
                    next_action="请检查飞书群与 Agent profile 绑定后重试。",
                )
        if role == "viewer" and registered.spec.risk_level != "read_only":
            return AgentToolResult(
                status="tool_not_allowed",
                summary="viewer 角色仅允许调用只读工具。",
                evidence={
                    "tool_name": name,
                    "role": role,
                    "risk_level": registered.spec.risk_level,
                    "handler_invoked": False,
                    "browser_read_invoked": False,
                    "ota_write_invoked": False,
                    "webhook_called": False,
                },
                next_action="请改用只读查询工具，或联系 supervisor 调整权限。",
            )
        try:
            parsed_arguments = registered.argument_model.model_validate(arguments)
        except ValidationError as exc:
            return AgentToolResult(
                status="invalid_arguments",
                summary=f"工具 {name} 的参数不完整或格式不正确。",
                data={"validation_errors": exc.errors(include_url=False)},
                evidence={
                    "tool_name": name,
                    "handler_invoked": False,
                    "browser_read_invoked": False,
                    "ota_write_invoked": False,
                    "webhook_called": False,
                },
                next_action="请补充必要信息后重试。",
            )

        try:
            raw_result = registered.handler(parsed_arguments, context)
            result = await raw_result if inspect.isawaitable(raw_result) else raw_result
        except Exception as exc:
            return AgentToolResult(
                status="failed",
                summary=f"工具 {name} 执行失败，系统没有继续执行高风险操作。",
                evidence={
                    "tool_name": name,
                    "handler_invoked": True,
                    "error_type": type(exc).__name__,
                    "ota_write_invoked": False,
                    "webhook_called": False,
                },
                next_action="请稍后重试，或在后台查看对应模块状态。",
            )

        return _sanitize_tool_result(result)


def get_default_agent_tool_registry() -> AgentToolRegistry:
    registry = AgentToolRegistry()
    for spec, argument_model, handler in _default_tool_definitions():
        registry.register_tool(spec, argument_model, handler)
    return registry


def _default_tool_definitions() -> list[
    tuple[AgentToolSpec, type[BaseModel], _ToolHandler]
]:
    from app.domain.ai_agent.tools import (
        analyze_competitor_price_change_tool,
        create_manual_pricing_approval_tool,
        get_calendar_price_latest_tool,
        get_calendar_price_watch_status_tool,
        get_competitor_latest_prices_tool,
        get_ebk_latest_result_tool,
        get_watch_scheduler_status_tool,
        pause_watch_tool,
        read_ebk_datasets_tool,
        run_calendar_price_watch_once_tool,
        run_ebk_watch_once_tool,
        resume_watch_tool,
    )

    return [
        (
            _tool_spec(
                name="get_ebk_latest_result",
                description="查询 EBK 数据中心盯盘的最新一次读取结果（默认携程；platform=meituan/fliggy 查美团/飞猪定时监控），只读，不触发浏览器。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只读取已持久化结果。",
                    "不返回 webhook、账号密码、cookie、浏览器 profile 或截图路径。",
                ],
            ),
            WatchGroupToolArgs,
            get_ebk_latest_result_tool,
        ),
        (
            _tool_spec(
                name="run_ebk_watch_once",
                description=(
                    "立即执行一次 EBK 数据中心盯盘并按现有配置推送飞书（默认携程；"
                    "platform=meituan/fliggy 跑美团/飞猪定时监控，投递走其锚定携程组的机器人）。"
                    "仅当用户明确要求盯盘推送、推送飞书、读取并推送、跑一次盯盘时使用。"
                    "如果用户是在问某个 EBK 指标并要求直接回答，不要用这个工具。"
                ),
                argument_model=WatchGroupToolArgs,
                risk_level="state_change",
                safety_notes=[
                    "只能走现有 EBK 盯盘服务。",
                    "不得直接操作 DOM，不得绕过登录、验证码或风控。",
                ],
            ),
            WatchGroupToolArgs,
            run_ebk_watch_once_tool,
        ),
        (
            _tool_spec(
                name="read_ebk_datasets",
                description=(
                    "立即读取一个或多个 EBK 数据中心数据集，把结构化指标返回给 AI 直接回复用户；"
                    "不发送飞书推送，不改变盯盘配置。用于“告诉我出租率/经营报告/销售数据/收益概览”等会话查询。"
                    "用户问出租率时优先读取 beneficial_data，并在回复里直接给出 occupancy_rate。"
                ),
                argument_model=EbkDatasetReadToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只触发浏览器读取和结构化解析，不调用 Feishu webhook。",
                    "不修改 EBK 或 OTA 数据，不执行调价、dry-run 或 execute。",
                    "不返回 webhook、cookie、token、密码、浏览器 profile 或截图路径。",
                ],
            ),
            EbkDatasetReadToolArgs,
            read_ebk_datasets_tool,
        ),
        (
            _tool_spec(
                name="get_competitor_latest_prices",
                description="查询竞品价格盯盘的最新持久化价格结果，只读，不触发浏览器。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只读取已持久化竞品价格。",
                    "不展示 raw JSON、run id 或敏感配置。",
                ],
            ),
            WatchGroupToolArgs,
            get_competitor_latest_prices_tool,
        ),
        (
            _tool_spec(
                name="get_calendar_price_latest",
                description="查询未来日历价监测的最新持久化矩阵，只读，不触发浏览器读取或调价审批。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只读取已持久化日历价矩阵。",
                    "不创建 pricing approval、dry-run、execute 或 OTA 写入。",
                    "不返回 webhook、cookie、token、浏览器 profile、截图路径或 run id。",
                ],
            ),
            WatchGroupToolArgs,
            get_calendar_price_latest_tool,
        ),
        (
            _tool_spec(
                name="get_calendar_price_watch_status",
                description="只读查询日历价配置、自动轮询开关、轮询间隔、调度心跳和上次读取结果；不创建调价审批或写价。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只读取日历价配置、最新持久化读取记录和调度器心跳。",
                    "不创建 pricing approval、dry-run、execute 或 OTA 写入。",
                    "不依赖调价 EBK 是否可写；direct/unknown 仍只读。",
                ],
            ),
            WatchGroupToolArgs,
            get_calendar_price_watch_status_tool,
        ),
        (
            _tool_spec(
                name="run_calendar_price_watch_once",
                description="用户明确要求最新、刷新、立即读或跑一次日历价时，现读未来日历价；默认不推送 webhook。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只触发日历价浏览器读取和持久化，不写 OTA。",
                    "push_to_feishu 固定为 false，Hermes 对话回复只走当前群 operator card。",
                    "不创建调价审批，不把 AI 输出当执行价格。",
                ],
            ),
            WatchGroupToolArgs,
            run_calendar_price_watch_once_tool,
        ),
        (
            _tool_spec(
                name="analyze_competitor_price_change",
                description="基于已持久化竞品价格和价格变化记录，分析当前是否需要关注。",
                argument_model=WatchGroupToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只分析已持久化可信价格。",
                    "不自动生成 OTA 写价执行。",
                ],
            ),
            WatchGroupToolArgs,
            analyze_competitor_price_change_tool,
        ),
        (
            _tool_spec(
                name="get_watch_scheduler_status",
                description="查询 EBK、竞品、飞书监听等盯盘服务状态。",
                argument_model=EmptyToolArgs,
                risk_level="read_only",
                safety_notes=[
                    "只读取心跳和运行状态。",
                    "不返回 webhook 或本地运行路径。",
                ],
            ),
            EmptyToolArgs,
            get_watch_scheduler_status_tool,
        ),
        (
            _tool_spec(
                name="pause_watch",
                description="暂停指定盯盘任务：ebk（携程）、competitor（竞品）、meituan_ebk（美团）、fliggy_ebk（飞猪）。",
                argument_model=WatchControlToolArgs,
                risk_level="state_change",
                safety_notes=[
                    "必须指定或解析到明确监控组。",
                    "不影响 OTA 价格，不执行写价。",
                ],
            ),
            WatchControlToolArgs,
            pause_watch_tool,
        ),
        (
            _tool_spec(
                name="resume_watch",
                description="恢复指定盯盘任务：ebk（携程）、competitor（竞品）、meituan_ebk（美团）、fliggy_ebk（飞猪）。",
                argument_model=WatchControlToolArgs,
                risk_level="state_change",
                safety_notes=[
                    "必须指定或解析到明确监控组。",
                    "不影响 OTA 价格，不执行写价。",
                ],
            ),
            WatchControlToolArgs,
            resume_watch_tool,
        ),
        (
            _tool_spec(
                name="create_manual_pricing_approval",
                description="根据用户明确提供的房型、日期和目标价，创建手动调价审批卡。",
                argument_model=ManualPricingApprovalToolArgs,
                risk_level="approval_only",
                safety_notes=[
                    "只能创建飞书审批卡。",
                    "不得直接 dry-run、execute 或绕过飞书审批。",
                    "目标价必须来自用户明确输入，不能由模型自由生成。",
                ],
            ),
            ManualPricingApprovalToolArgs,
            create_manual_pricing_approval_tool,
        ),
    ]


def _tool_spec(
    *,
    name: str,
    description: str,
    argument_model: type[BaseModel],
    risk_level: AgentToolRiskLevel,
    safety_notes: list[str],
) -> AgentToolSpec:
    schema = argument_model.model_json_schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return AgentToolSpec(
        name=name,
        description=description,
        parameters=schema,
        risk_level=risk_level,
        safety_notes=safety_notes,
    )


async def _not_implemented_tool(
    arguments: BaseModel,
    context: AgentToolCallContext,
) -> AgentToolResult:
    return AgentToolResult(
        status="not_implemented",
        summary="这个 AI Agent 工具已经列入白名单，但还没有接入具体业务服务。",
        evidence={
            "event_id": context.event_id,
            "handler_invoked": True,
            "ota_write_invoked": False,
            "webhook_called": False,
        },
        next_action="继续实现该工具的业务 handler，并补充对应测试。",
    )


def _sanitize_tool_result(result: AgentToolResult) -> AgentToolResult:
    sanitized = sanitize_operator_payload(result.model_dump(mode="json"))
    return AgentToolResult.model_validate(sanitized)
