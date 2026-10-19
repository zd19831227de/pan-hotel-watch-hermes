"""Seed reply templates for the review management domain.

两步构建初始模板库（幂等，可重复执行）：
1. 真实历史回复导入：携程/美团 adapter 的 fetch_history_replies → 按好/中/差
   归类去重存为 imported 模板（内容全部来自酒店真实已发回复）。
2. 精选样板补充：好/中/差各 3 条运营话术，写入三平台，确保每类都有可用模板
   （真实样本以好评回复为主，中/差评样板为通用酒店运营话术）。

用法（需真实浏览器已登录且 CDP 在线）：
    REVIEW_CTRIP_REAL_ADAPTER_ENABLED=true \
    REVIEW_MEITUAN_REAL_ADAPTER_ENABLED=true \
    PYTHONPATH=backend python scripts/seed_review_reply_templates.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须在导入 app 之前设置：真实 adapter 按 settings 开关注册。
os.environ.setdefault("REVIEW_CTRIP_REAL_ADAPTER_ENABLED", "true")
os.environ.setdefault("REVIEW_MEITUAN_REAL_ADAPTER_ENABLED", "true")

from app.core.settings import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db.session import ensure_db_ready  # noqa: E402
from app.domain.review_management import repository, service  # noqa: E402
from app.domain.review_management.types import ReplyTemplateInput  # noqa: E402

PLATFORMS = ("ctrip", "meituan", "fliggy")

# 精选样板：好/中/差各 3 条，通用运营话术（非编造业务数据，用于回复生成）。
CURATED_SAMPLES: dict[str, list[str]] = {
    "good": [
        "亲爱的贵宾，非常感谢您的认可与好评！您的鼓励是我们前进的最大动力，我们会继续保持高标准的服务与设施，期待您的再次光临，祝您生活愉快！",
        "感谢您抽出时间分享入住体验！很高兴我们的服务让您满意，团队每一位成员都会继续努力，欢迎您下次再来，祝您旅途顺利！",
        "谢谢您的五星好评！能为您带来舒适的入住体验是我们的荣幸，我们会一直保持，期待下次为您服务！",
    ],
    "medium": [
        "感谢您的反馈与建议。很抱歉本次入住未能完全达到您的期待，您提到的问题我们已记录并逐项改进，期待您下次光临时能有更好的体验。",
        "尊敬的宾客，感谢您中肯的评价。对于您提到的不便之处我们深表歉意，已反馈相关部门优化，希望有机会再次为您服务，给您带来更好的体验。",
        "感谢您的分享。我们将认真对待您提出的每一条建议，持续改善卫生、设施与服务细节。若下次入住有任何需要，欢迎随时联系前台，我们会第一时间为您处理。",
    ],
    "bad": [
        "尊敬的宾客，非常抱歉给您带来不好的入住体验。您反映的问题我们高度重视，已第一时间核查并整改。如您方便，欢迎通过平台或致电前台与我们联系，我们希望有机会为您弥补。",
        "非常抱歉此次入住让您失望了。针对您提到的问题，我们已加强员工培训与现场管理，确保同类问题不再发生。诚挚邀请您再次光临检验我们的改进，祝您生活愉快。",
        "尊敬的宾客，对此次给您带来的不便我们深表歉意。您的每一条反馈我们都已逐项落实改进，也非常希望能进一步了解详情，为您妥善处理。期待有机会再次为您服务，弥补此次的遗憾。",
    ],
}


async def main() -> None:
    await ensure_db_ready()

    # 1) 真实历史回复导入（携程/美团已实现 fetch_history_replies）。
    for platform in ("ctrip", "meituan"):
        result = await service.import_history_as_templates(platform=platform)
        print(
            f"[history] {platform}: status={result.get('status')} "
            f"created={result.get('templates_created')} "
            f"by_category={result.get('by_category')}"
        )

    # 2) 精选样板（幂等：内容已存在则跳过）。
    for platform in PLATFORMS:
        platform_rows = await repository.list_templates(platform=platform)
        seen = {"".join((row["content"] or "").split()) for row in platform_rows}
        added = 0
        for category, contents in CURATED_SAMPLES.items():
            counter = 0
            for content in contents:
                normalized = "".join(content.split())
                if normalized in seen:
                    continue
                seen.add(normalized)
                counter += 1
                await repository.save_template(
                    template=ReplyTemplateInput(
                        platform=platform,
                        category=category,  # type: ignore[arg-type]
                        name=f"{ {'good':'好评','medium':'中评','bad':'差评'}[category] }样板 {counter}",
                        content=content,
                        priority=150 + counter,
                        source="manual",
                    )
                )
                added += 1
        print(f"[curated] {platform}: added={added}")

    for platform in PLATFORMS:
        templates = await repository.list_templates(platform=platform)
        by_cat: dict[str, int] = {}
        for tpl in templates:
            by_cat[tpl["category"]] = by_cat.get(tpl["category"], 0) + 1
        print(f"[summary] {platform}: total={len(templates)} by_category={by_cat}")


if __name__ == "__main__":
    asyncio.run(main())
