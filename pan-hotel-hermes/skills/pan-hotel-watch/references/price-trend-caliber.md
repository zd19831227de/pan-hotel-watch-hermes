# 竞品价格趋势口径说明

## 数据来源

价格趋势数据来自盯盘系统每次成功轮询时读取的携程 DOM 页面的**可信房价**（`room_rate_dom_display_price`），不是 API 接口数据，确保与实际展示价一致。

## 时间口径

- `granularity=run`（默认）：按每次成功轮询的时间点展示价格变化轨迹，纵轴是价格，横轴是盯盘时间点。反映的是**系统盯盘频率内**的价格波动。
- `granularity=hour`：按小时聚合，一个天内同一房型同一餐标的多次读价取均值。
- `granularity=day`：按天聚合，取当天最后一次读价作为该日的代表价格。

**重要**：趋势展示的是「每次读价的连续轨迹」，不是「N 天前 vs 今天」的两点对比。与控制台「可信 DOM 房价趋势」一致。

## date_label 参数

- 传 `date_label="today"` 只查今天（checkin=今天）的价格轨迹。
- 传 `date_label="tomorrow"` 只查明天的。
- 传具体日期（如 `2026-06-30`）查指定日期 checkin 的价格轨迹。
- 不传时默认返回全部日期的趋势，可能包含 6 个日期的数据（盯盘系统会读多天的价格）。

## 运行间隔与数据密度

盯盘轮询的间隔决定了趋势曲线的密度。间隔短（如 20 分钟一次）则曲线平滑，间隔长（如每 2 小时一次）则曲线点稀疏。如果某个日期没有轮询记录，则无法为该日期生成趋势点——这属于正常情况，不是数据丢失。

## breakfast_kind 参数

- `none`：无早餐
- `included`：含早餐
- 不传时默认同时返回两种餐标

## 用户常见问法映射

| 用户说法 | 工具参数 |
|---------|---------|
| "最近三天的趋势" | days=3, granularity=run |
| "昨天的详细曲线" | date_label=具体日期, granularity=run → 注意：若返回 0 个点，说明当天 checkin=该日期的独立数据不存在。建议退回到 `days=3, granularity=run` 或 `granularity=day` 获取更完整的趋势覆盖，并向用户解释 |
| "今天的价格变化" | date_label="today", granularity=run |
| "近一周的价格波动" | days=7, granularity=run |
| "按天看趋势" | days=N, granularity=day |
| "最近几小时的波动" | days=1, granularity=hour |
