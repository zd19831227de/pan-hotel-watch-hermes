from __future__ import annotations

import unittest

from pan_hotel_mcp.server import _safe_tool_error


class SafeToolErrorTests(unittest.TestCase):
    def test_playwright_path_is_not_returned_to_operator(self) -> None:
        exc = RuntimeError(
            "Executable doesn't exist at C:\\Users\\ADMINI~1\\AppData\\Local\\Temp\\cursor-sandbox-cache\\playwright\\chromium-1148\\chrome-win\\chrome.exe"
        )
        payload = _safe_tool_error("get_competitor_latest_prices", exc)
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("cursor-sandbox-cache", str(payload))
        self.assertNotIn("chrome.exe", str(payload))
        self.assertNotIn("error", payload)

    def test_generic_errors_keep_short_message(self) -> None:
        exc = ValueError("缺少 watch_group_key")
        payload = _safe_tool_error("pause_watch", exc)
        self.assertEqual(payload["error"], "缺少 watch_group_key")


if __name__ == "__main__":
    unittest.main()
