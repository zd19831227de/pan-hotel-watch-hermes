"""V7 Step 1 — 页面变更检测与主动报警域。

采集后校验层（不改采集逻辑）：
- selector/解析健康检查：同一 (platform, target) 连续 N 次结构失败 → hard_fail 红色告警；
- 页面结构指纹对比：成功读取时对比结构指纹 → layout_changed 蓝色通知；
- 部分失败：partial 状态 → partial_fail 橙色提醒；
- 告警去重：同一 (platform, target, problem) 1 小时内最多 1 次；
- selector/健康阈值配置文件化（page_health_config.json），mtime 热更新。
"""
