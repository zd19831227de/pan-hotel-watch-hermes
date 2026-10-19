# EBK 数据集字段形状与口径笔记

三平台 EBK 快照的常见字段与口径，供回答流量/转化/金字塔/促销问题时快速对照。所有数字必须来自本轮 MCP 结果，禁止凭此笔记编造数值。

## 携程 read_ebk_datasets

- ad_pyramid（广告金字塔，date_preset=今日）：四组——流量(点击/曝光/平均曝光位次/平均点击位次)、花费(总花费/点击均价/现金花费/红包花费)、收益(预订金额/预订订单/预订间夜)、效果(ROAS/点击率/转化率)。ROAS=预订金额÷总花费。
- flow_data（流量数据）：主口径「昨日」+「实时」两套。
  - 昨日：访客量(change 较上周同期, competition_average)、转化率、曝光转化率、下单转化率、成交转化率。
  - 实时：实时访客量(competition_average)、实时下单转化率(APP)(competitive_rank，格式 N/M)，另有竞争圈排名列表(同格式)。
  - 页面会带建议：「报名促销活动，提升流量」「点评分 落后」等，可引用为 EBK 侧提示。
- promotion_registered（已参加促销）：可见活动数 + 活动列表；字段含名称/促销组(场景/基础/红包)/类型(立减/特价/连住)/房型 scope/时间/优惠规则/到期自动延期。个别活动 raw_text 里会出现第二条优惠规则（如「今夜甩卖」16-21点立减20% 之外还有 21点-次日6点立减25%），读 summary 行只给主规则，需看 raw_text 才完整。
- competition_list（销售排名/竞争排名/竞争圈榜单）：问「竞争圈排名/销售排名榜/订单量在竞争圈排第几」用。metrics 键：sales_ranking_booking_order_rank（预订订单量排名）、sales_ranking_booking_sales_rank（预订销售额排名）、sales_ranking_in_house_room_nights_rank（在店间夜排名）、sales_ranking_occupancy_rate_rank（出租率排名）、traffic_ranking_app_detail_visitor_rank（APP详情页访客量排名）、service_ranking_psi_rank（PSI分排名）、service_ranking_ctrip_review_rank（携程点评分）、service_ranking_qunar_review_rank（去哪儿点评分）。tables 三张：sales_ranking（预订订单量/预订销售额/在店间夜/出租率）、traffic_ranking（APP详情页访客量/转化率）、service_ranking（PSI/携程/去哪儿/同程/智行点评分）；每行 hotel_name="我的酒店"=本店，其余为竞争圈酒店；EBK 页面只展示部分酒店的字段，解析到空属正常，不要当缺失。
- 多数据集一次读取时，服务端会返回 multi_dataset_analysis_note 要求综合回答，禁止只答其中一个模块。

## 联合分析 get_ebk_joint_analysis_context（美团/飞猪）

- refresh_before_analysis=true 会触发后台现读并落库，返回各平台 read_times（如 meituan 08:26:10、fliggy 08:26:42），这就是「为什么是 8:26」这类问题的答案来源。
- status 可能为 partial：某平台 snapshot 缺失（如携程 no_snapshot）时仍返回美团/飞猪数据，结论注明仅供参考；不要把 partial 当失败。
- 美团 meituan_workbench：realtime_data(浏览人数/销售额/入住间夜/满房率/流失订单量)、yesterday_data 同字段、hos_score(value/grade_tip/message)。
- 美团 meituan_data_center.basic_operation_metrics：引流价/曝光量/浏览人数/支付转化率/支付订单数/销售间夜/销售均价/销售额/入住间夜/满房率，各带 week_over_week、peer_rank(如 2/11)、peer_average。tabs：data_center_trade(平均房价/成交率)、data_center_traffic(曝光-浏览转化率/浏览-支付转化率)、data_center_service(确认率/拒单/违规/新增差评好评/待回复)。
- 美团 meituan_reviews：review_score、review_total、review_dimensions(服务/位置/设施/卫生)、review_periods(30/180/365天 新增有效评价数/差评率/表现)、review_filters(未回复/差评/有图)。
- 美团 meituan_promotion_coupon：神券活动报名状态 + 用券金额/订单数/核销/客单价。
- 飞猪 fliggy_home：yesterday_data(销售额/浏览人数/预订订单量/预订间夜)；today_data 常为空。
- 飞猪 fliggy_diagnose：trade_overview(今日在店/间夜单价/昨日)、guest_review_score、mci_index。
- 飞猪 fliggy_operating：in_house_data(在店间夜/房费收入/间夜单价)、booking_data(预订间夜/收入/订单)、flow_data(列表页曝光人数/详情页曝光人数)。
- 飞猪 fliggy_promotion：joined_activities(分场景/分人群/全人群运营活动，立减力度，进行中)。
- 注意平台店名可能不同：同一门店在飞猪页面显示名可能与美团/携程不同（如「静里公寓酒店…」vs「静里全套房酒店…」），属平台展示差异，不改变数据归属，回答时可提示但不必展开。

## 口径提醒

- 携程金字塔=今日实时；携程流量=昨日+今日实时两套；美团工作台=实时+昨日两套；飞猪以昨日为主。
- 多平台金额字段定义可能不同（页面口径 vs 数据中心口径），仅作渠道表现参考，不作同口径财务对账，回答时说明 comparison_caliber_note 类似的话。
