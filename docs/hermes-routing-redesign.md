# Hermes 路由重构审计

## 结论

原实现把“工具白名单”“自然语言判断”“飞书会话记忆”分散在 Agent、MCP 插件和多个专项 router 中。模型在第一轮仍可看到大量相邻工具，专项规则主要在模型选完工具后才拦截，因此错误工具调用和失败历史会互相强化。

## 新边界

1. `route_domain_intent` 是 Hermes 飞书入口的唯一领域路由。识别到 EBK、竞对或调价问题后，在首次模型调用前注入唯一允许工具约束，并在工具执行前再次硬门禁。
2. 领域问题仍可保留自然语言上下文，但 EBK/价格事实必须来自本轮后端工具结果，不得从上一轮答案推断。
3. 失败、拒绝、参数错误和超时的工具交换会在下一次模型调用前被中性化（不从 Hermes 的浅拷贝历史列表删除）；后端兼容 Agent 只过滤带 tool-call 元数据的失败摘要。
4. 明确的非酒店问题（如天气、翻译、网页搜索）禁止进入 pan-hotel-watch MCP，但继续交给 Hermes 自身能力；无法分类的酒店问题先澄清，不自动刷新浏览器。

Hermes 当前公开插件 API 的 `pre_llm_call` 只支持向本轮用户消息注入上下文，不能替换核心循环的 `tools` 数组。因此本阶段不修改外部 Hermes 核心；“首轮约束 + 执行门禁”是当前部署可验证的最小实现，后续若上游提供 tool-set hook，再将约束升级为物理 schema 裁剪。

## 目标路由

| 用户问题 | 工具 |
| --- | --- |
| 三个平台经营简报/联合汇总 | `get_ebk_joint_analysis_context` |
| EBK 指标、出租率、销售数据 | `read_ebk_datasets`、`get_ebk_latest_result` |
| 明确刷新/现读 EBK | `run_ebk_watch_once` |
| 竞对当前价格 | `get_competitor_latest_prices` |
| 竞对价格趋势、涨跌幅、历史变化 | `get_competitor_price_trend` |
| 竞对变化提醒/预警 | `analyze_competitor_price_change` |
| 用户输入房型、日期、目标价 | `create_manual_pricing_approval` |

该路由只负责选择数据入口，不生成业务事实，也不改变调价安全契约。
