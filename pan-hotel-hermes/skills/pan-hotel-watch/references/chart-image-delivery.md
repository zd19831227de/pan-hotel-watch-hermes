# 非价格模块的图表交付（本地出图 + MEDIA，2026-10-13 复核）

适用：店长要「曲线图 / 出图 / 图表展示」，但问的不是竞品价格（价格类一律用
`get_competitor_price_trend` 传 `with_chart=true` 的图卡）。典型场景：金字塔投放、
竞争圈排名、分时订单、退单分布等**没有图卡能力**的模块。

## 结论：本地 matplotlib 出真图，用 MEDIA 标签交付（已在本 profile 验证可用）

1. 图卡能力只覆盖竞品房价趋势。其余模块要图，就走「取数 → 本地脚本渲染 PNG → 回复里带
   `MEDIA:<绝对路径>`」。
2. 本群插件对**带真实附件**的回复有明确放行（读 `plugins/pan-hotel-feishu-session/__init__.py`
   的 `_on_transform_llm_output`，约 1181-1188 行）：`MEDIA:` 指向存在的文件时，回复交给网关
   原生管线，网关剥掉 MEDIA 行、上传附件、再发清洗后的正文；**不走**「文字换运营助手卡」那条路
   （那条会把 MEDIA 标签吞掉）。所以图能真发出去，正文只需短文字结论。
3. 因此「不要写临时脚本出图」只约束**价格趋势**（那里有 with_chart 图卡，自绘会与系统口径打架）。
   金字塔 / 排名 / 订单类无图卡模块，自绘真图 + MEDIA 正是正确做法；切勿回答「不支持生成图表」。
4. 数值仍必须来自本轮 MCP 结果；图上要标注口径与读取时间，禁止把示意图画成「看起来像读数」。

## 生产配方（本 profile 已成型）

- 脚本落 `~/.hermes/profiles/<profile>/scripts/plot_*.py`，PNG 落
  `~/.hermes/profiles/<profile>/charts/<name>_<yyyymmdd>.png`（同目录已有历史图与脚本，如
  `plot_ctrip_ranking_bars.py`、`plot_own_vs_comp_hourly.py`、`plot_trend_min_chart.py`）。
- 字体（否则中文方块）：`plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun"]`；
  `matplotlib.use("Agg")`。`Agg` + 后台跑，不弹窗。
- 存图固定加 `bbox_inches="tight", pad_inches=0.35`：底部脚注（口径/来源/边界说明）用 `fig.text`
  放在 y≈0.01~0.08 时，不加 tight 会被画布下沿裁掉半行（2026-10-13 实测被裁、改 tight 后正常）。
- 双轴组合图（金字塔这类「花费 vs 出价」）：柱=今日花费，右轴折线=点击出价，柱顶标「xx.xx元 / n次点击」，
  已暂停计划柱置灰并在柱底标「已暂停」、未到时段标「待投放」，避免店长把预设出价当成已花钱。
- 出图后**先用 `vision_analyze` 自检**（中文有无方块、脚注有无裁切、图例有无遮挡、柱和是否等于总量），
  再带 MEDIA 回消息。自检还顺带复核了自洽性（柱和=总花费、点击率=点击/曝光、ROAS=金额/花费）。

## 交付话术

- 正文只写 2~3 句结论 + 图上口径，不逐条复述图中数字（图已可见）。
- 结尾或说明里点名口径边界，例如金字塔：仅广告流量不含自然流量；成交金额只有全天总量、不能按小时拆；
  折线上的出价是系统预设出价上限，不代表已消耗。

## 取数补充：金字塔「曲线」能取到什么

- 今日（`read_ebk_datasets(ad_pyramid + pyramid_bid)`）：全天总量 + 10 个时段计划的
  花费/曝光/点击/出价 → 可画「分时段花费 + 出价」。
- 近 7 天/30 天（`get_ebk_period_read(data_kind=pyramid, platform=ctrip, period=week/month)`）：
  只返回**区间汇总**（一个合计块），**没有逐日序列**——不要承诺「按天曲线」。要逐日曲线只能用
  EBK 页面自带趋势表，或逐日多次现读累积。
