from __future__ import annotations

import unittest

from app.domain.calendar_price_watch.browser_binding import (
    resolve_calendar_browser_binding_for_config,
)


class CalendarPriceBrowserBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_calendar_browser_session_returns_config_required(self) -> None:
        result = await resolve_calendar_browser_binding_for_config(
            config={"browser_session_id": None},
            watch_group_key="ops_watch",
        )
        self.assertEqual(result.status, "config_required")
        self.assertIn("独立", result.summary)

    async def test_invalid_calendar_browser_session_returns_config_required(self) -> None:
        result = await resolve_calendar_browser_binding_for_config(
            config={"browser_session_id": "missing-browser"},
            watch_group_key="ops_watch",
        )
        self.assertEqual(result.status, "config_required")


if __name__ == "__main__":
    unittest.main()
