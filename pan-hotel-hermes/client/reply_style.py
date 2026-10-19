from __future__ import annotations

from typing import Any

# Hermes 每次读工具结果时都会看到此提示，配合 SKILL.md / SOUL.md 约束飞书文字风格。
HERMES_REPLY_STYLE_HINT = (
    "飞书文字规范：只用简体中文；禁止 Markdown 标题/代码块/表格/大量加粗；"
    "Hermes 问答一律短文字，禁止发简报/状态/价格明细卡片；response_mode=detail 无效。"
    "只回答用户当前所问；把关键数据、状态和时间口径写清楚。"
    "不要把 JSON 字段名或 success/failed 原样贴给用户。"
    "若 feishu_card_sent 为 true，说明已是调价审批/回调操作卡；文字只需说明已发卡，勿重复表格。"
    "经营分析按「结论、依据、可能原因、建议、数据缺口」压缩组织；"
    "引用 operator_summary / mobile_evidence 里的关键行即可，勿展开完整 JSON 或重复卡片表格。"
    "run_ebk_watch_once / read_ebk_datasets 由后台触发浏览器读取，不依赖 message_id；"
    "禁止对用户说需要 message_id、飞书消息 ID 或再发一条消息才能读 EBK。"
    "区分飞书群角色：运营问答上下文群用于本群问答解析，手动调价指令群用于人工目标价命令，"
    "价格变化提醒群用于竞品轮询提醒和调价参考卡投递。"
)


def attach_reply_style_hint(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("reply_style_hint"):
        return payload
    return {**payload, "reply_style_hint": HERMES_REPLY_STYLE_HINT}
