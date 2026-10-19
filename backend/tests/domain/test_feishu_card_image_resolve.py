from __future__ import annotations

import base64
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.notifications.feishu_client import resolve_feishu_card_images


class ResolveFeishuCardImagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_injects_img_and_strips_internal_keys(self) -> None:
        card = {
            "elements": [{"tag": "markdown", "content": "intro"}],
            "_feishu_image_uploads": [
                {
                    "alt": "趋势图",
                    "data_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                    "position": 1,
                }
            ],
            "_feishu_fallback_elements": [{"tag": "markdown", "content": "fallback"}],
        }
        with patch(
            "app.domain.notifications.feishu_client.FeishuBotClient.upload_image",
            new=AsyncMock(return_value={"status": "success", "img_key": "img_test_key"}),
        ):
            resolved = await resolve_feishu_card_images(card)
        self.assertNotIn("_feishu_image_uploads", resolved)
        tags = [item.get("tag") for item in resolved["elements"]]
        self.assertEqual(tags[0], "markdown")
        self.assertEqual(tags[1], "img")
        self.assertEqual(resolved["elements"][1]["img_key"], "img_test_key")

    async def test_uses_fallback_when_upload_fails(self) -> None:
        card = {
            "elements": [{"tag": "markdown", "content": "intro"}],
            "_feishu_image_uploads": [
                {
                    "alt": "趋势图",
                    "data_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                    "position": 1,
                }
            ],
            "_feishu_fallback_elements": [{"tag": "markdown", "content": "fallback-block"}],
        }
        with patch(
            "app.domain.notifications.feishu_client.FeishuBotClient.upload_image",
            new=AsyncMock(return_value={"status": "failed"}),
        ):
            resolved = await resolve_feishu_card_images(card)
        contents = [item.get("content", "") for item in resolved["elements"]]
        self.assertIn("fallback-block", contents)


if __name__ == "__main__":
    unittest.main()
