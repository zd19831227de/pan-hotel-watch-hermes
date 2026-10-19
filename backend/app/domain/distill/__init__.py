"""V6 Step 2 — 自学习与数据蒸馏域。

流程：数据源（text / image_url / video_url）→ 豆包/千问 chat 提取文字
（系统不做视觉理解，URL 直接转交外部多模态 AI）→ 准入硬约束校验
（只允许知识类内容，拒绝实时数据）→ RAG 入库（复用 app.domain.rag）。
"""
