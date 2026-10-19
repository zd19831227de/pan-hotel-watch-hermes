# 查房价指令落到 Hermes 时的事件转发（2026-10-13 实测）

适用：群里 @机器人 说「查房价」「看看房态」「<房型>多少钱」等命中查价词表，但消息走到了 Hermes（route=pricing_query）。本轮只放行 `dispatch_watch_feishu_event`，由后端现读当前群绑定门店的房型价目表并回「房价查询」卡。

## 关键结论：payload 必须是原始飞书事件本体

自己拼一个近似结构（如把 chat_id / text / intent 单独塞进去）**不会**被识别，后端直接返回 ignored，不发卡。必须把落到磁盘的那份原始事件整体当 `payload` 传。

## 实测两轮对比

1. 自造 payload（`{"chat_id":..., "intent":"price_query", "text":"查房价"}`）→ 返回 `status=ignored`，说明「Feishu event was not a pricing command or pricing approval action」，`execute_triggered=false`。这**不是**权限或能力问题，是 payload 形态不对，别就此放弃或改用别的读价工具。
2. 用原始事件重试 → 返回 `status=pricing_query_card_replied`，`feishu_push.status=success`，`feishu_code=0`。这一次卡真的发出去了。

## 取原始事件的位置与方法

原始事件由归档器落盘到当前 profile 的群资料归档 spool：

- 目录：`<profile>/group-archive-spool/`（Windows 本机即 `~/AppData/Local/hermes/profiles/<profile>/group-archive-spool/`）
- 文件名形如 `<epoch_ms>-<om_message_id>-<hash>.json`，schema 2.0 完整事件（`header.event_type=im.message.receive_v1` + `event.message` / `event.sender`）
- 取最新一条并按修改时间倒序，核对 `event.message.content` 里的文本与本轮用户原话一致（群里 @机器人 的文本形如 `"{\"text\":\"@_user_1 查房价\"}"`，`message.chat_id` 要与当前群一致）

等价的检查命令（bash）：

```
cd ~/AppData/Local/hermes/profiles/<profile>/group-archive-spool/
ls -t | head -5
python -c "import json,glob,os; f=max(glob.glob('*.json'),key=os.path.getmtime); d=json.load(open(f,encoding='utf-8')); print(d['event']['message']['message_id'], d['event']['message']['content'])"
```

确认命中本轮消息后，把该 JSON 内容整体作为 `payload` 传给 `dispatch_watch_feishu_event`，同时照常透传 `chat_id` / `open_id` / `user_text`。

## 返回后怎么答

- `evidence.platforms_read` 逐平台给出 `status` 与 `room_rate_count`（如携程 5 个房型、美团 7 个房型、飞猪 not_found）。
- 文字只写一两句：已发卡、口径是哪一天入住、哪些平台读到、哪个平台本轮没读到。**不要复述卡里的房型/价格/房态表格**（卡已可见，重复即刷屏）。
- 平台 `not_found` 要明说「本轮未读到」，不能说成「该平台没有价格」或 0。
- 卡片上的读取时间不带时区后缀即北京时间，直接用，不要再加 8 小时。
- 同一轮不要再调竞品价 / 日历价工具去「补」房价——查价指令只走这一条确定性通道。

## 边界

- 这套转发只用于**查价**和卡片回调/URL 验证类事件；真正的改价仍走审批链，禁止任何执行性表述。
- 落到 spool 的原始事件只在需要 payload 时读取，禁止把其中的 message_id、chat_id 写进记忆、技能正文或回复。
