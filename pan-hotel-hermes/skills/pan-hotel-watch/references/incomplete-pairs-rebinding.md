# 不完整配对陷阱（incomplete_pairs / partial rebinding）

## 症状

用户修改群绑定后，`resolve_feishu_chat_context` 返回 `binding_mode=multi_store`，但 `resolution.status=ambiguous` 且 `reason=incomplete_pairs`。

## 产生原因

- 之前这个群绑的是 A 店（例如静里），用户在控制台改成了 B 店（例如泉吉）
- 但改的是部分字段：比如只改了竞品组 `default_competitor_watch`，而 EBK 组 `ebk_1782812373623` 是新建的另一条记录，未与竞品组关联到同一门店
- 结果形成两条孤立的记录：①竞品组无配对的 EBK，②EBK 组无配对的竞品

## 常见陷阱：chat_id 填错群

用户修改绑定后，`resolve_feishu_chat_context` 返回 `binding_mode=unbound`，尽管用户声称已经填了 chat_id。

典型场景：用户把**另一个飞书群**的 chat_id 填到了控制台，而不是当前对话群的 chat_id。

- 用户的群 ID 可能是 `oc_22222222222222222222222222222222`
- 但当前对话的 chat_id 可能是 `oc_44444444444444444444444444444444`
- 系统检查的是当前群的 chat_id，不匹配时就显示未绑定

**正确处理方式**：
1. 告诉用户当前群的准确 chat_id（从会话上下文可知，如 `oc_xxxxxx...`）
2. 让用户在控制台门店的「运营问答上下文群」字段填入这个值
3. 不要笼统说「检查控制台配置」或「重新绑定」，用户已经试过了

## 表现特征（来自 operator_summary）

- `store_catalog` 数组有 2 条或更多记录，但各自 `pair_complete=false` 或 `pair_score=0`
- 一条有 `competitor_watch_group_key` 但 `ebk_watch_group_key=null`
- 另一条有 `ebk_watch_group_key` 但 `competitor_watch_group_key=null`
- `resolution.reason=incomplete_pairs`

## 正确处理方式

1. **判断依据**：检查 `store_catalog[].pair_complete` 是否全部为 false，以及 `pair_score` 是否为 0
2. **向用户说明现状**：哪些组件有值、哪些缺失，不要说「本群绑了 2 家酒店」——实际是未完成配对的半截记录
3. **引导**：让用户在控制台找到**同一家门店**，把 EBK 监控组和竞品监控组都填写到该门店下，并把本群的 chat_id 填到「运营问答上下文群」字段
4. 注意：如果之前在别的 chat_id 下有完整的绑定，改成新 chat_id 后旧记录可能成为 orphan，需要检查控制台清理

## 典型对话示例

用户：「我修改了绑定，你再看看」
助手解析到 incomplete_pairs。

应该回复类似：

> 绑定已识别到，但目前 EBK 和竞品组没有配对到同一家门店。控制台里有两条记录：一条有竞品组（竞品可查但 EBK 数据读不到），另一条有 EBK 组（有数据中心但竞争品数据读不到）。它们还没关联到同一家门店。您需要在控制台里，在同一门店配置下，同时把 EBK 监控组和竞品监控组都选好，再把本群的 chat_id 填到「运营问答上下文群」字段。

## 与正常多店的区别

| 特征 | 正常多店（resolved） | 不完全配对（incomplete_pairs） |
|------|-------------------|-------------------------------|
| pair_complete | true（至少一家店） | 全部 false |
| 可查询 | 带 store_hint 即可查 | 即使带店名也无法查全数据 |
| 需用户操作 | 只需指明是哪家店 | 需去控制台完成配对 |
