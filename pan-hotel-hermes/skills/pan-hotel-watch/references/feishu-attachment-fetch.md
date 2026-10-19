# 取回群里消息的原始附件（Excel/Word/PDF）

## 什么时候需要

用户**回复**某条带附件的消息提问（例：回复 xlsx 说「分析一下这份报表」），或附件刚发出来、
`search_group_archive` 还查不到该条记录（`total: 0`）——这时归档拿不到 `saved_path`，
但消息本身在飞书侧是拿得到的，走开放接口把原件取回来即可。

## 定位被回复消息的 message_id

- 当前那条 @机器人 的消息，其 `root_id` / `parent_id` 就是被回复的带附件消息 ID。
- 原始事件 JSON 在 `<profile>/group-archive-spool/*.json`（文件名形如
  `<ts>-<message_id>-<hash>.json`）；`event.message.root_id` / `parent_id` 直接读得到。
- 群里最近几条消息的附件 `file_key` / `file_name` 也能在同一批 spool 文件里看到
  （`event.message.message_type` = `file` 时，`content` 是 `{"file_key": "...", "file_name": "..."}`；
  图片消息是 `image_key`，图片会另存到 `<profile>/image_cache/`，用 `vision_analyze` 读）。

## 调用步骤（实测可用）

凭证在 `<profile>/.env`：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`。
注意 `FEISHU_DOMAIN` 的值是**选择器**而不是 URL：`feishu` → `https://open.feishu.cn`，
`lark` → `https://open.larksuite.com`。直接把它当域名拼 URL 会报
`unknown url type`。

1. `POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`
   body `{"app_id": ..., "app_secret": ...}` → 取 `tenant_access_token`（返回 `code: 0` 即成功）。
2. `GET /open-apis/im/v1/messages/{message_id}`（`Authorization: Bearer <token>`）
   → `data.items[0].body.content` 是 JSON 字符串，里面有 `file_key`、`file_name`；
   `msg_type` 为 `file`。
3. `GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file`
   → 响应体就是文件二进制，直接写盘。
4. 落盘后用 `read_file` 读内容（.xlsx/.docx/.ipynb 有内置抽取器，直接给路径即可）；
   多 sheet 的 xlsx 用 `openpyxl`（`data_only=True` 取计算值）逐 sheet 遍历更完整。

现成脚本：`scripts/fetch_feishu_message_file.py <message_id>`（可 `--list` 只看附件清单）。

## 实测样例（2026-10-10）

- 用户回复的消息 = `om_x100b63943eadf4b0df93e322d19130e`，`search_group_archive`
  按 `message_type=file` 查当天本群返回 0 条（消息刚发十几分钟，归档还没落）。
- 步骤 2 拿到 `file_key=file_v3_0016b_7d0d64b3-...`、
  `file_name=10月OTA每日目标铺排表（云州澜江路立瑞酒店）.xlsx`。
- 步骤 3 下载 10,163 字节，`openpyxl` 读到 4 个 sheet：标题页、测算口径、每日目标明细（31 行）、关键提醒。

## 注意

- 附件里的数字是**运营提供的计划/实施数据**，不是 EBK 读数：引用时要标来源文件与文件日期，
  不要和 MCP 经营数字混称同一来源。
- 不要把 `.env`、token、本地绝对路径回贴给用户；技能/记忆里也不写 token 值。
- 这套是**兜底路径**，正常优先用 `search_group_archive` 的归档记录（带 `saved_path` 时直接读）。
