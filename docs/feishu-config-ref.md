# 飞书Session插件 & Config数据模型 参考文档

**项目：** pan-hotel-watch / hermes  
**整理日期：** 2026-08-04  
**整理人：** 天童凯伊

---

## 一、飞书Session插件架构

### 1.1 文件位置
```
pan-hotel-hermes/hermes_plugin/pan-hotel-feishu-session/__init__.py
```

### 1.2 核心机制

插件通过Hermes的Hook系统注册了5个钩子，覆盖飞书消息从接入到回复的全生命周期：

| Hook | 函数 | 触发时机 | 作用 |
|------|------|----------|------|
| `pre_gateway_dispatch` | `_on_pre_gateway_dispatch` | 消息进入网关前 | 判断是否为酒店业务事件，转发watch事件到后端 |
| `pre_llm_call` | `_on_pre_llm_call` | LLM调用前 | 注入飞书session上下文（chat_id/message_id/open_id）、清理失败工具调用历史、域意图路由 |
| `pre_tool_call` | `_on_pre_tool_call` | MCP工具调用前 | 校验域路由匹配、防止重复发卡、注入session到工具参数 |
| `transform_tool_result` | `_on_transform_tool_result` | 工具返回后 | 检测工具是否已发送飞书卡片，标记卡片发送状态 |
| `transform_llm_output` | `_on_transform_llm_output` | LLM输出后 | 清理Markdown为飞书原生文本格式、抑制工具前导语、处理零宽确认 |

### 1.3 Session桥接机制

插件用本地JSON文件做session桥接，存储在：
```
$HERMES_HOME/feishu-session-bridge/{chat_id}.json
$HERMES_HOME/feishu-session-bridge/{session_id}.json
```

每个桥接文件内容：
```json
{
  "chat_id": "oc_xxxx",
  "session_id": "xxx",
  "message_id": "om_xxxx",
  "open_id": "ou_xxxx"
}
```

### 1.4 消息路由流程

```
飞书消息 → pre_gateway_dispatch
  ├─ 是watch事件(调价/价格建议)? → 转发到后端 /api/v1/feishu/events
  ├─ chat_id不在白名单? → skip
  └─ 其他 → 正常进入LLM流程

→ pre_llm_call
  ├─ 提取用户原文，记住chat_id↔text映射
  ├─ 清理失败的tool调用历史
  ├─ 写session桥接文件
  └─ 域意图路由(domain_intent_router) → 注入路由上下文给LLM

→ pre_tool_call
  ├─ 本轮已发卡? → 阻止重复发卡
  ├─ 检查域路由是否匹配该工具
  ├─ 不匹配? → 返回misroute阻断信息
  └─ 匹配? → 注入session字段到工具参数

→ transform_tool_result
  └─ 检测 feishu_card_sent / response_mode=text → 更新session状态

→ transform_llm_output
  ├─ 本轮已发卡? → 返回零宽字符(不显示)
  ├─ text模式? → 清理Markdown输出纯文本
  ├─ 看起来像工具前导语? → 抑制
  └─ 普通回复 → 清理Markdown后输出
```

### 1.5 关键配置

| 环境变量 | 作用 | 默认值 |
|----------|------|--------|
| `FEISHU_ALLOWED_CHATS` | 允许的chat_id白名单(逗号分隔) | 空=全部允许 |
| `WATCH_API_BASE_URL` | 后端API地址 | http://127.0.0.1:8100 |
| `HERMES_HOME` | Hermes根目录 | $LOCALAPPDATA/hermes |

### 1.6 多租户改造要点

当前架构**不支持**多机器人多店铺。要支持需改造：

1. **飞书消息入口**：`_on_pre_gateway_dispatch`中提取的`chat_id`目前只做白名单校验，需增加`chat_id → shop_id`映射
2. **Session桥接**：桥接文件需扩展存储`shop_id`字段
3. **工具参数注入**：`_inject_feishu_session`需把`shop_id`注入到MCP工具调用参数
4. **后端API**：`/api/v1/feishu/events`需接收`shop_id`参数，按店铺路由配置

改造路径：
```
飞书机器人A (App ID: cli_xxx1) → chat_id: oc_aaa → shop_id: shop_001
飞书机器人B (App ID: cli_xxx2) → chat_id: oc_bbb → shop_id: shop_002
```

在config表加`shop_id`字段，`config_key`改为`{shop_id}:{config_name}`格式即可实现按店铺隔离配置。

---

## 二、Config数据模型

### 2.1 数据库表结构

表名：`runtime_integration_configs`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BigInt (PK) | 自增主键 |
| `config_key` | String(64) | 配置键名，唯一 |
| `llm_provider` | String(64) | 模型提供商，默认deepseek |
| `llm_base_url` | Text | API地址 |
| `llm_model` | String(128) | 模型名称，默认deepseek-chat |
| `llm_api_key` | Text (nullable) | API密钥 |
| `created_at` | DateTime | 创建时间 |
| `updated_at` | DateTime | 更新时间 |

### 2.2 配置加载机制

```
RuntimeIntegrationConfig (单例)
  ├─ __init__: 从settings读取默认值
  ├─ _refresh_from_store: 从DB读取config_key="default"的记录覆盖内存
  ├─ _runtime_for_request: 
  │   ├─ update=None → 用当前内存配置
  │   └─ update=有值 → 构建临时配置(不落库)
  ├─ test_llm_connection: 测试连通性
  ├─ list_llm_models: 拉取可用模型列表
  └─ update: 持久化到DB
```

### 2.3 配置数据流

```
settings.py (环境变量/配置文件)
  ↓ 初始化
RuntimeIntegrationConfig (内存单例 _RUNTIME_CONFIG)
  ↓ _refresh_from_store()
runtime_integration_configs 表 (config_key="default")
  ↓ update()
持久化到DB
```

### 2.4 API端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/config/integrations/llm` | GET | 获取当前LLM配置快照 |
| `/api/v1/config/integrations/llm` | PUT | 更新LLM配置(持久化) |
| `/api/v1/config/integrations/llm/test` | POST | 测试LLM连接(可传临时配置) |
| `/api/v1/config/integrations/llm/models` | POST | 拉取可用模型列表(可传临时配置) |

---

## 三、模型回退(Fallback)方案

### 3.1 现状

当前**不支持**模型回退。每个配置只有一组`llm_provider + llm_model + llm_api_key`，主模型挂了请求直接失败。

### 3.2 改造方案

**方案A：字段扩展（简单）**

在config表加fallback字段：
```
llm_fallback_provider  VARCHAR(64)
llm_fallback_base_url  TEXT
llm_fallback_model     VARCHAR(128)
llm_fallback_api_key   TEXT
```

`IntegrationConfigUpdate`和`LlmRuntimeConfig`同步加字段。LLM调用时捕获异常，fallback字段有值就重试一次。

优点：改动小，现有逻辑几乎不动。
缺点：只支持两级回退，要三级就得再加字段。

**方案B：JSON模型链（灵活，推荐）**

加一个`llm_fallback_chain`字段存JSON数组：
```json
[
  {"provider": "deepseek", "model": "deepseek-v4-pro", "base_url": "https://...", "api_key": "sk-xxx"},
  {"provider": "kimi", "model": "kimi-k2.7", "base_url": "https://...", "api_key": "sk-yyy"},
  {"provider": "glm", "model": "glm-5.2-fast", "base_url": "https://...", "api_key": "sk-zzz"}
]
```

LLM调用时按链顺序尝试，第一个成功就返回，全部失败才报错。

优点：支持N级回退，灵活配置每个店铺不同的回退链。
缺点：需要改LLM调用层的重试逻辑，工作量稍大。

### 3.3 推荐改造路径

结合多租户需求一起做：

1. config表加`shop_id`字段（多租户）
2. config表加`llm_fallback_chain`字段（JSON，模型回退链）
3. `RuntimeIntegrationConfig`从单例改为按`shop_id`查询
4. LLM调用层加重试逻辑：主模型失败 → 按fallback_chain顺序重试
5. 飞书session插件入口加`chat_id → shop_id`映射，注入到LLM调用上下文

这样每个店铺可以配置自己的主模型和回退链，飞书机器人消息进来自动路由到对应店铺配置。

---

## 四、改造优先级建议

| 优先级 | 改造项 | 工作量 | 说明 |
|--------|--------|--------|------|
| P0 | config表加shop_id | 小 | 加字段+改查询 |
| P0 | 飞书入口chat_id→shop_id映射 | 中 | 改gateway dispatch |
| P1 | RuntimeConfig按shop_id查询 | 中 | 单例改多实例/字典 |
| P1 | llm_fallback_chain字段+重试逻辑 | 中 | JSON字段+LLM层重试 |
| P2 | 数据表加shop_id隔离业务数据 | 大 | 竞品/价格/日历等表都要改 |
| P2 | 前端按店铺切换配置UI | 中 | configApi + App.tsx改造 |

---

*文档生成时间：2026-08-04 16:20 CST*
*数据来源：SSH只读访问公司电脑项目代码*
