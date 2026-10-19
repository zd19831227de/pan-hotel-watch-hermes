"""实测竞品趋势卡片：拉取数据、生成折线图、推送到绑定飞书群。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from client.feishu_cards import attach_feishu_card
    from client.watch_api import WatchApiClient

    watch_group_key = sys.argv[1] if len(sys.argv) > 1 else "default_competitor_watch"
    days = int(sys.argv[2] if len(sys.argv) > 2 else 3)

    client = WatchApiClient()
    payload = await client.get_competitor_price_trend(
        watch_group_key,
        days=days,
        granularity="run",
    )
    enriched = attach_feishu_card(payload, "competitor_price_trend")
    card = enriched["feishu_card"]
    uploads = card.get("_feishu_image_uploads") or []
    fallback = card.get("_feishu_fallback_elements") or []

    print(f"watch_group_key={watch_group_key}")
    print(f"status={payload.get('status')}")
    print(f"point_count={payload.get('point_count')}")
    print(f"series_count={len(payload.get('series') or [])}")
    print(f"card_title={card.get('header', {}).get('title', {}).get('content')}")
    print(f"chart_mode={'image' if uploads else 'text_fallback'}")
    if uploads:
        print(f"chart_base64_len={len(uploads[0].get('data_base64') or '')}")
    print(f"fallback_block_count={len(fallback)}")

    # 从后端读取绑定 chat_id（不打印完整 ID）
    from app.domain.competitor_prices.repository import get_competitor_watch_group_detail

    detail = await get_competitor_watch_group_detail(
        watch_group_key=watch_group_key,
        include_secrets=True,
    )
    chat_id = str((detail or {}).get("feishu_chat_id") or "").strip()
    if not chat_id:
        print("feishu_send=skipped_no_chat_id")
        return

    reply = await client.send_operator_feishu_card(
        chat_id=chat_id,
        card=card,
        fallback_text=str(enriched.get("operator_summary", {}).get("intro") or "竞品价格趋势"),
    )
    print(f"feishu_send_status={reply.get('status')}")
    print(f"feishu_delivery={reply.get('delivery')}")
    if reply.get("status") != "success":
        print(f"feishu_summary={reply.get('summary')}")
        data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
        if data.get("feishu_code"):
            print(f"feishu_code={data.get('feishu_code')}")


if __name__ == "__main__":
    asyncio.run(main())
