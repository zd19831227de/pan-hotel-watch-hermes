# 运营问答上下文群绑定：chat_id 格式踩坑记录

## 症状

用户在控制台「运营问答上下文群」字段填写后，`resolve_feishu_chat_context` 反复返回 `unbound` / `chat_id_configured=false`。

## 根因

**填成了「群会话 ID」（session-level identifier），而非「群 chat_id」（oc_ 开头）**。

- **群 chat_id**（正确）：以 `oc_` 开头，是飞书群的唯一标识，后端通过它定位绑定的 EBK/竞品组。
- **群会话 ID**（错误）：长串数字或其它格式，是飞书会话层的临时标识，后端不认识。

店长/运营在飞书群信息页看到的「群 ID」通常是会话 ID，不是后端需要的 chat_id。

## 正确值

正确的 `chat_id` 格式示例：`oc_44444444444444444444444444444444`

## 验证方法

填好后在群里 @机器人 发任意查询（如「查一下本群绑了哪家店」），后台会自动执行 `resolve_feishu_chat_context`。返回 `binding_mode=single_store` 且 `resolution.status=resolved` 即绑定成功。

## 调试排查树

resolve_feishu_chat_context 返回 unbound

- chat_id_configured = false
  原因：后端未找到该 chat_id 绑定记录
  - 填错 ID（填了会话ID → 改成 oc_ 开头的 chat_id）
  - 填到了「手动调价指令群」而非「运营问答上下文群」
  - 填到了竞品组的「价格变化提醒群」而非 EBK/竞品组的「运营问答上下文群」

- chat_id_configured = true 但 resolution != resolved
  原因：chat_id 已记录但尚未完成解析
  - 一群多店未带 store_hint → 发澄清卡片
  - 后端配置同步延迟 → 稍等几分钟重试

## 给用户的解释语术

当用户说「我填过了但还查不到」时：

> 您填的是群会话 ID，还是群 chat_id（oc_ 开头）？后端需要的是群 chat_id，不是群会话 ID。通常在飞书管理后台或群信息页能看到，格式类似 oc_xxxxxx。您也可以告诉我这个群信息，我帮查 chat_id。
