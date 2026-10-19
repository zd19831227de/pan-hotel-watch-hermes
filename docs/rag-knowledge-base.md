# Pan Hotel Watch 知识库

## 1. 系统概述

### 1.1 什么是 Pan Hotel Watch

Pan Hotel Watch 是一套酒店智能盯盘系统，通过飞书机器人接入，为酒店运营人员提供实时的经营数据查询、竞品价格监控、调价审批和运营分析服务。系统核心能力包括：携程 EBK 经营数据读取、竞品 OTA 价格监控、日历价矩阵查询、手动调价审批、运营简报和周报生成。

系统架构分为三层：
- 飞书网关层：接收飞书群消息，路由到 Hermes agent
- Hermes agent 层：大模型理解用户意图，调用 MCP 工具获取数据，生成回复
- 后端服务层：提供 EBK 数据读取、竞品价格抓取、调价审批等 API 接口，端口 8100

### 1.2 支持的 OTA 平台

系统支持携程、美团、飞猪三大 OTA 平台的 EBK 经营数据读取。携程 EBK 通过浏览器自动化方式抓取；美团和飞猪通过对应的平台接口获取。系统通过 `ota_ebk_accounts` 配置各平台账号绑定关系。

判断系统是否配置了某平台：以 `ota_ebk_accounts` 中对应平台是否有 `platform_account_id` 为准。如果美团或飞猪已配置，可以直接调用 `get_ebk_joint_analysis_context` 并传入 `platforms` 参数查询该平台数据。禁止以 EBK 监控组只挂携程来判断系统不支持美团或飞猪。

### 1.3 飞书群绑定机制

一个飞书群默认管理一家酒店。系统通过 `chat_id` 将飞书群与酒店绑定关系关联。绑定模式包括：
- 单店绑定（single_store）：一个群绑定一家酒店
- 多店绑定（multi_store）：一个群管理多家酒店，需指定店名查询

系统中有三类飞书群角色：
- 运营问答上下文群：用户 @机器人 提问的群，用于问答和回复
- 手动调价指令群：绑定 EBK 浏览器账号，用于"把某房型调到420"类调价命令
- 价格变化提醒群：绑定竞品监控组，用于轮询跨阈值提醒和参考卡投递

如果群未绑定（`chat_binding_status=unbound` 且 `chat_id_configured=false`），用户提问时系统会提示本群未绑定。禁止回落到未绑定的默认全局 key。

### 1.4 账号类型与权限

系统通过 `connection_mode` 判断账号类型，分为直连（direct）和非直连（non-direct/unknown）。权限分层：
- 直连账号：可以直接执行写价操作
- 非直连/未确认账号：允许只读查询，但禁止自动改价
- 未配置调价账号：允许只读查询，禁止写价

调价安全边界由后端给出，分为四个级别：`read_only`（只读）、`approval_only`（仅审批）、`write_blocked`（写价阻止）、`execution_guarded`（执行守护）。向用户说明时用中文业务含义，不要展示英文字段名。

## 2. EBK 经营数据查询

### 2.1 EBK 数据集说明

系统支持读取携程 EBK 的多个数据集模块：

| 数据集 | 说明 |
|--------|------|
| business_outline | 经营概览：出租率、入住率、ADR、房晚数等核心指标 |
| beneficial_data | 效益数据：收入、间夜量、均价等 |
| flow_data | 流量数据：曝光、点击、转化率等 |
| competition_profile | 竞争画像：市场份额、排名对比 |
| loss_analysis | 流失分析：订单流失原因分析 |
| competition_list | 竞品列表：同商圈竞品概况 |
| promotion_registered | 促销报名：已报名促销活动列表 |
| promotion_effect | 促销效果：促销活动效果数据 |
| ad_pyramid | 金字塔：广告投放金字塔数据 |

多模块可一次传入多个，系统返回 `dataset_summaries` 和 `multi_dataset_analysis_note`。禁止编造不存在的数据集名（如 `promotion_effectiveness`）。

### 2.2 点评数据口径

EBK 真实字段是 `positive_reviews_yesterday`（昨日好评）和 `negative_reviews_yesterday`（昨日差评）。用户问"今天新增"时，应说明这是昨日口径，不要改写成"今日新增"。点评数据通过 `get_ebk_latest_result` 读取 `operator_summary.review_summary`，不要用 `read_ebk_datasets` 的 `comment_monitor`。

如果点评的 `enabled=false`，不要认为没有数据，也不需要为此调用 `run_ebk_watch_once` 刷新。

### 2.3 多平台经营分析

需要查询美团或飞猪经营数据时，使用 `get_ebk_joint_analysis_context`，可传入 `platforms=["meituan"]` 或 `["fliggy"]` 指定平台。三端或多平台综合分析时同样使用此工具。禁止用 `read_ebk_datasets` 回答美团或飞猪的数据，也不要因此说"美团促销暂不支持"。

如需刷新数据再分析，传 `refresh_before_analysis=true`，但该操作可能耗时数分钟。

### 2.4 EBK 完整刷新

`run_ebk_watch_once` 读取监控组内全部已启用数据集，可能耗时数分钟。仅在用户明确要求"跑一次""刷新 EBK"时使用。等待期间不要说"MCP 连不上"或要求用户"再发一条消息"。成功后回复一句确认，包含数据时间。

## 3. 竞品价格监控

### 3.1 竞品现价查询

查询本店和竞品最新房型价格使用 `get_competitor_latest_prices`，返回持久化的公开页房型价格，包含本店 `own` 的价格。这是默认的竞品价格查询方式。

首次查询竞品现价时先调用此工具。仅在用户明确说"刷新竞品""现刷""现爬"时才使用 `run_competitor_watch_once`，该操作较慢。

### 3.2 竞品价格变化分析

查询竞品最近价格变化使用 `analyze_competitor_price_change`，基于相邻两次成功竞品轮询的数据对比。这不是任意时段对比，而是最近两次轮询之间的变化。

### 3.3 竞品价格趋势

查询竞品历史价格序列和趋势使用 `get_competitor_price_trend`，可查看最近三天的价格走势。回复时只用文字说明涨跌和关键点，可以提及折线走势，但不要发趋势卡或图片。

### 3.4 日历价

日历价是未来15天的房型价格矩阵，通过 `get_calendar_price_latest` 查询。日历价是只读的，不能创建调价审批，不能 dry-run 或 execute。

日历价轮询状态通过 `get_calendar_price_watch_status` 查询，包含 `auto_poll_enabled` 和 `schedule_interval_minutes`。刷新日历价使用 `run_calendar_price_watch_once`，Hermes 强制 `push_to_feishu=false`，不会创建调价审批。

禁止用日历价矩阵回答"今天本店房型价格"的问题，那是 `get_competitor_latest_prices` 的职责。

## 4. 调价安全规则

### 4.1 调价基本原则

- 禁止直接执行 OTA 写价、dry-run 或 execute
- 目标价必须来自用户明确输入或确认，模型参考价不能直接进入写价
- 竞品涨价仅提醒，不建议本店跟涨，不发参考或审批卡
- 竞品降价参考卡仅限非直连且已绑定调价 EBK 的账号；直连/未确认/未绑定时只提醒
- 日历价只读：不创建调价审批、不 dry-run、不 execute

### 4.2 手动调价流程

用户在飞书群发送调价指令时，系统根据指令完整性分流：
- 完整调价句式（如"把今天大床房调到420"）且字段齐全 → `create_manual_pricing_approval`，创建调价审批卡
- 字段不全或意图不明确 → `dispatch_watch_feishu_event`，转发给后端处理
- 卡片回调、URL验证、结构化命令（如"调价建议""调价"）→ `dispatch_watch_feishu_event`，立即转发，不要先推理

### 4.3 竞品自动触发

竞对轮询发现价格变化时，系统会自动创建飞书调价参考卡，但不会一键"通过"执行。用户收到参考卡后可以确认或忽略。竞品涨价时仅提醒，不生成参考卡。竞品降价时生成参考卡的前提是非直连且已绑调价 EBK。

### 4.4 安全边界说明

用户问"非直连时降价参考卡会怎样"等规则类问题时，通过 `resolve_feishu_chat_context` 读取 `pricing_reference_policy_rows` 回答。不要对这类自然语言问题调用 `dispatch_watch_feishu_event`。

完整的安全契约以项目 AGENTS.md 的"调价安全契约"为准。

## 5. 运营简报与周报

### 5.1 今日综合概览

`get_operator_hotel_overview` 提供今日综合概览，包含竞品和调度信息。这不是三端 EBK 简报，是整体运营概览。

### 5.2 周报

`get_weekly_operator_briefing` 提供上周运营简报。如果数据暂无，直接说"暂无数据"，禁止编造同比数据。

### 5.3 原因分析与建议

用户问"为什么订单弱""要不要跟价"等分析类问题时，使用 `get_operator_analysis_context` 获取原因和建议证据包。未点名多平台时使用此工具。

### 5.4 最近活动

`get_operator_recent_activity` 展示最近的轮询和审批活动记录。

## 6. 监控调度管理

### 6.1 调度状态

`get_watch_scheduler_status` 查询调度心跳，这不是经营数据，是系统运行状态。

### 6.2 暂停与恢复

`pause_watch` 和 `resume_watch` 用于暂停和恢复监控，需传入 `watch_kind`。用户说"两家/全部竞品都暂停或恢复"时，必须传 `all_bound_groups=true` 或分别对每个 `competitor_groups[].group_key` 调一次。禁止省略 key 后落到 `default_competitor_watch` 并声称已覆盖全群所有店。

## 7. 飞书群使用指南

### 7.1 如何提问

在飞书群中 @机器人 后输入问题即可。常见问法：
- "今天出租率多少" → 读取 EBK 经营概览
- "竞品现在什么价" → 读取竞品最新价格
- "最近竞品价格有什么变化" → 分析竞品价格变化
- "未来15天日历价" → 读取日历价矩阵
- "上周经营情况" → 生成周报
- "把今天大床房调到420" → 创建调价审批
- "美团今天经营怎么样" → 多平台经营分析

### 7.2 回复格式

系统回复以飞书交互卡片形式呈现，内容是简短的文字结论。回复遵守以下规范：
- 只用简体中文
- 先给结论，再补充关键数据
- 不展示工具名、run id、JSON 等技术细节
- 不展示字段名如 `contract_version`、`safety_boundary`、`next_actions` 等
- 可适度使用 emoji（📊、✅、⚠️、💡）

### 7.3 需要确认的操作

当 `requires_confirmation=true` 时，系统会先问用户确认再执行。用户回复"要"或"好的"确认。如果意图不明确（如竞品还是 EBK），系统会一句澄清，不会默认跑浏览器。

### 7.4 多店场景

一个群绑定多家酒店时，提问需指定店名。系统会澄清"请说哪家店"，带上 `store_hint` 后续查询。禁止在多店场景下猜测酒店名。

## 8. 常见问题

### 8.1 机器人不回复怎么办

检查以下几点：
1. 确认飞书群已绑定酒店（@机器人问"本群绑了哪家店"）
2. 确认 Hermes gateway 进程正常运行
3. 确认后端服务（端口 8100）已启动
4. 确认飞书 app_id 没有冲突（多个 profile 不能共用同一 app_id）
5. 确认 DeepSeek API key 未过期

### 8.2 数据显示"暂无"怎么办

如果工具返回"暂无数据"或"未读到"：
1. 说明当前确实没有持久化数据，不要用搜索或记忆填补
2. 可以建议用户手动刷新（如"跑一次 EBK"或"刷新竞品"）
3. 刷新操作可能耗时数分钟，耐心等待

### 8.3 竞品价格和日历价的区别

竞品价格（`get_competitor_latest_prices`）是当前最新房型价格，来自公开页爬取并持久化。日历价（`get_calendar_price_latest`）是未来15天的价格矩阵。用户问"今天房价"用竞品现价，问"未来几天价格"用日历价。

### 8.4 能不能自动调价

系统不支持自动调价。所有调价操作都需要人工确认。竞品价格变化时系统会推送参考卡，但需要用户确认才会执行。模型给出的参考价不能直接进入写价流程，必须由用户明确输入目标价。

### 8.5 促销和活动能自动调整吗

不能。促销和活动当前只支持读取、分析和人工操作建议。不支持自动化调整促销、报名活动、取消活动或改活动价格。

## 9. 数据安全规范

### 9.1 禁止泄露的信息

系统在回复中禁止泄露：
- webhook 地址
- cookie、token、密码
- 浏览器 profile 路径
- 本地文件路径和截图路径
- MCP 工具名和 run id
- raw JSON 数据

### 9.2 持久记忆治理

系统可以写入用户的称呼偏好和汇报风格。禁止写入以下内容作为事实源：
- 出租率、竞品价、好评条数等经营数字
- 酒店名
- webhook、chat_id
- cookie、token
- 浏览器 profile 路径

价格、指标、审批状态等数据每次通过 MCP 现查，不依赖记忆。

### 9.3 三层记忆说明

用户问"你有什么功能"或"有没有记忆"时，按三层说明：
1. 本轮对话：当前会话上下文
2. 长期偏好：称呼、汇报风格等个人设置
3. 酒店真实数据：永远通过 MCP 现查，不依赖记忆

系统不承诺 AI 自动决策调价或模型自我进化。

## 10. 系统环境信息

| 项目 | 值 |
|------|-----|
| 后端服务端口 | 8100 |
| 主模型 | deepseek-v4-flash |
| MCP 工具数量 | 26 个 |
| Hermes 安装路径 | C:\Users\Administrator\AppData\Local\hermes |
| 项目根目录 | D:\work\pan-hotel-watch |
| 飞书应用 | jingli-ai（静里全套房酒店）、manxin-ai（曼馨酒店） |
| 支持的 OTA | 携程、美团、飞猪 |
| 日历价窗口 | 未来15天 |
| 竞品轮询间隔 | 由后端配置决定 |
| EBK 刷新方式 | 浏览器自动化，可能耗时数分钟 |
