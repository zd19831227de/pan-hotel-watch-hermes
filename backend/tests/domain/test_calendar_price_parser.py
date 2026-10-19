from __future__ import annotations

import asyncio
import unittest
from datetime import date, timedelta

from app.domain.browser.ctrip_public_hotel_dates import (
    _extract_public_calendar_price_points,
)


class CalendarPriceParserTests(unittest.TestCase):
    def test_extracts_price_from_operator_sample_cell(self) -> None:
        cells = [
            {
                "text": "8",
                "aria_label": (
                    "2026\u5e747\u67088\u65e5(\u661f\u671f\u4e09), "
                    "The price is CNY 420, "
                    "Select the start date you want"
                ),
                "data_d": "2026-07-07T16:00:00.000Z",
                "class_name": "in-range is-allow-hover has-price",
            }
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["checkin_date"], "2026-07-08")
        self.assertEqual(points[0]["price_amount_yuan"], 420)
        self.assertEqual(points[0]["currency"], "CNY")
        self.assertEqual(points[0]["source"], "calendar_dom_cell_price")
        self.assertTrue(points[0]["is_trusted"])
        self.assertLessEqual(len(points[0]["source_text"]), 160)
        self.assertNotIn("\n", points[0]["source_text"])
        self.assertIn("The price is CNY 420", points[0]["source_text"])

    def test_days_zero_clamps_to_one_day(self) -> None:
        cells = [
            {
                "text": "8\n420",
                "data_d": "2026-07-07T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "420",
            },
            {
                "text": "9\n410",
                "data_d": "2026-07-08T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "410",
            },
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=0,
        )

        self.assertEqual([point["checkin_date"] for point in points], ["2026-07-08"])
        self.assertEqual(points[0]["price_amount_yuan"], 420)

    def test_ignores_enabled_cell_without_price(self) -> None:
        cells = [
            {
                "text": "8",
                "aria_label": "2026\u5e747\u67088\u65e5, Select the start date you want",
                "data_d": "2026-07-07T16:00:00.000Z",
                "class_name": "has-price",
            }
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(points, [])

    def test_ignores_class_only_disabled_cell(self) -> None:
        cells = [
            {
                "text": "8\n399",
                "aria_disabled": "false",
                "data_d": "2026-07-07T16:00:00.000Z",
                "class_name": "is-disabled has-price",
                "price_text": "399",
            }
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(points, [])

    def test_uses_aria_label_date_when_data_d_is_absent(self) -> None:
        cells = [
            {
                "text": "10\n450",
                "aria_label": "2026\u5e747\u670810\u65e5, The price is CNY 450",
                "class_name": "has-price",
                "price_text": "450",
            }
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["checkin_date"], "2026-07-10")
        self.assertEqual(points[0]["price_amount_yuan"], 450)

    def test_ignores_day_number_when_it_is_the_only_price_like_value(self) -> None:
        cells = [
            {
                "text": "8",
                "aria_label": "2026\u5e747\u67088\u65e5",
                "data_d": "2026-07-07T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "8",
            }
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(points, [])

    def test_ignores_disabled_and_out_of_range_cells(self) -> None:
        cells = [
            {
                "text": "7\n399",
                "aria_disabled": "true",
                "data_d": "2026-07-06T16:00:00.000Z",
                "class_name": "is-disabled has-price",
                "price_text": "399",
            },
            {
                "text": "9\n410",
                "aria_disabled": "false",
                "data_d": "2026-07-08T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "410",
            },
            {
                "text": "24\n500",
                "aria_disabled": "false",
                "data_d": "2026-07-23T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "500",
            },
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual([point["checkin_date"] for point in points], ["2026-07-09"])
        self.assertEqual(points[0]["price_amount_yuan"], 410)

    def test_uses_lowest_price_when_duplicate_cells_exist_for_same_date(self) -> None:
        cells = [
            {
                "text": "10\n460",
                "aria_label": "2026年7月10日, The price is CNY 460",
                "data_d": "2026-07-09T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "460",
            },
            {
                "text": "10\n450",
                "aria_label": "2026年7月10日, The price is CNY 450",
                "data_d": "2026-07-09T16:00:00.000Z",
                "class_name": "has-price",
                "price_text": "450",
            },
        ]

        points = _extract_public_calendar_price_points(
            cells=cells,
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["checkin_date"], "2026-07-10")
        self.assertEqual(points[0]["price_amount_yuan"], 450)


class CalendarCellCollectionContractTests(unittest.TestCase):
    def test_calendar_cells_include_data_aria_and_price_fields(self) -> None:
        expected_keys = {
            "text",
            "class_name",
            "data_d",
            "aria_label",
            "aria_disabled",
            "aria_selected",
            "price_text",
            "x",
            "y",
            "width",
            "height",
        }

        from app.domain.browser.ctrip_public_page_reader import (
            PUBLIC_CALENDAR_CELL_KEYS,
        )

        self.assertEqual(PUBLIC_CALENDAR_CELL_KEYS, expected_keys)

    def test_calendar_cell_accepts_day_text_with_aria_label_price(self) -> None:
        from app.domain.browser.ctrip_public_page_reader import (
            _public_calendar_cell_has_day_and_price,
        )

        self.assertTrue(
            _public_calendar_cell_has_day_and_price(
                {
                    "text": "8",
                    "aria_label": (
                        "2026\u5e747\u67088\u65e5(\u661f\u671f\u4e09), "
                        "The price is CNY 420, "
                        "Select the start date you want"
                    ),
                    "price_text": "",
                    "data_d": "2026-07-07T16:00:00.000Z",
                }
            )
        )

    def test_calendar_cell_rejects_day_only_without_price_evidence(self) -> None:
        from app.domain.browser.ctrip_public_page_reader import (
            _public_calendar_cell_has_day_and_price,
        )

        self.assertFalse(
            _public_calendar_cell_has_day_and_price(
                {
                    "text": "8",
                    "aria_label": "2026\u5e747\u67088\u65e5, Select the start date you want",
                    "price_text": "",
                    "data_d": "2026-07-07T16:00:00.000Z",
                }
            )
        )

    def test_calendar_cells_do_not_fall_back_to_body_when_calendar_root_is_missing(
        self,
    ) -> None:
        cells = asyncio.run(
            _read_public_calendar_cells_from_html(
                """
                <html>
                  <head>
                    <style>
                      .room-rate { display: block; width: 220px; height: 80px; }
                      .price { display: inline-block; }
                    </style>
                  </head>
                  <body>
                    <div class="room-rate">
                      <div
                        data-d="2026-07-07T16:00:00.000Z"
                        aria-label="2026\u5e747\u67088\u65e5, The price is CNY 420"
                      >
                        <span>8</span><span class="price">420</span>
                      </div>
                    </div>
                  </body>
                </html>
                """
            )
        )

        self.assertEqual(cells, [])

    def test_calendar_cells_ignore_generic_dialog_price_without_calendar_grid(
        self,
    ) -> None:
        cells = asyncio.run(
            _read_public_calendar_cells_from_html(
                """
                <html>
                  <head>
                    <style>
                      [role="dialog"] { display: block; width: 260px; height: 120px; }
                      .room-plan { display: block; width: 220px; height: 80px; }
                      .price { display: inline-block; }
                    </style>
                  </head>
                  <body>
                    <div role="dialog" class="room-popover">
                      <div
                        class="room-plan"
                        data-d="2026-07-07T16:00:00.000Z"
                        aria-label="Room plan price CNY 420"
                      >
                        <span>8</span><span class="price">420</span>
                      </div>
                    </div>
                  </body>
                </html>
                """
            )
        )

        self.assertEqual(cells, [])

    def test_calendar_cells_collect_operator_sample_from_grid_root(self) -> None:
        cells = asyncio.run(
            _read_public_calendar_cells_from_html(
                """
                <html>
                  <head>
                    <style>
                      [role="grid"] { display: table; width: 360px; height: 240px; }
                      table { border-collapse: collapse; }
                      td { width: 48px; height: 40px; }
                      .price, .day { display: inline-block; }
                    </style>
                  </head>
                  <body>
                    <table role="grid">
                      <tbody>
                        <tr>
                          <td
                            aria-selected="false"
                            aria-disabled="false"
                            role="gridcell"
                            data-d="2026-07-07T16:00:00.000Z"
                            class="in-range is-allow-hover has-price"
                          >
                            <div
                              class="tipWrapper"
                              role="checkbox"
                              aria-checked="false"
                              aria-label="2026\u5e747\u67088\u65e5(\u661f\u671f\u4e09), The price is CNY 420, Select the start date you want"
                            >
                              <span class="day">8</span>
                              <span class="price"><span aria-label="420">420</span></span>
                            </div>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </body>
                </html>
                """
            )
        )

        self.assertTrue(
            any(
                cell["data_d"] == "2026-07-07T16:00:00.000Z"
                and cell["price_text"] == "420"
                for cell in cells
            )
        )

    def test_calendar_cells_collect_operator_sample_from_pop_root_with_dated_cell(
        self,
    ) -> None:
        cells = asyncio.run(
            _read_public_calendar_cells_from_html(
                """
                <html>
                  <head>
                    <style>
                      .date-pop-layer { display: block; width: 360px; height: 240px; }
                      td { display: table-cell; width: 48px; height: 40px; }
                      .price, .day { display: inline-block; }
                    </style>
                  </head>
                  <body>
                    <div class="date-pop-layer">
                      <table>
                        <tbody>
                          <tr>
                            <td
                              aria-disabled="false"
                              role="gridcell"
                              data-d="2026-07-07T16:00:00.000Z"
                              class="in-range is-allow-hover has-price"
                            >
                              <div
                                class="tipWrapper"
                                aria-label="2026\u5e747\u67088\u65e5(\u661f\u671f\u4e09), The price is CNY 420, Select the start date you want"
                              >
                                <span class="day">8</span>
                                <span class="price"><span aria-label="420">420</span></span>
                              </div>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </body>
                </html>
                """
            )
        )

        self.assertTrue(
            any(
                cell["data_d"] == "2026-07-07T16:00:00.000Z"
                and cell["price_text"] == "420"
                for cell in cells
            )
        )

    def test_calendar_popup_opener_skips_review_time_and_opens_date_range(
        self,
    ) -> None:
        result = asyncio.run(
            _open_public_calendar_from_html(
                """
                <html>
                  <head>
                    <style>
                      .reviewSwiper_reviewItem-userReviewTime__Y5Mfo {
                        display: block;
                        width: 120px;
                        height: 24px;
                      }
                      .DjfnmDOTlIzAMQQ7xsRp {
                        display: block;
                        width: 360px;
                        height: 48px;
                      }
                      .date-pop-layer {
                        display: none;
                        width: 360px;
                        height: 240px;
                      }
                      .date-pop-layer.is-open {
                        display: block;
                      }
                      table { border-collapse: collapse; }
                      td { display: table-cell; width: 48px; height: 40px; }
                      .price, .day { display: inline-block; }
                    </style>
                  </head>
                  <body>
                    <div
                      class="reviewSwiper_reviewItem-userReviewTime__Y5Mfo"
                      onclick="window.clickedCalendarOpener = 'review-time'"
                    >
                      2026\u5e746\u670815\u65e5
                      \u5176\u4ed6\u53ef\u80fd\u5c31\u662f\u6ce8\u610f\u4e00\u4e0b\u5165\u4f4f\u4eba\u6570
                    </div>
                    <div
                      class="DjfnmDOTlIzAMQQ7xsRp"
                      onclick="document.querySelector('.date-pop-layer').classList.add('is-open'); window.clickedCalendarOpener = 'date-range'"
                    >
                      7\u67086\u65e5(\u5468\u4e00)
                      -
                      7\u67087\u65e5(\u5468\u4e8c)
                      1\u665a
                    </div>
                    <div class="date-pop-layer">
                      <table role="grid">
                        <tbody>
                          <tr>
                            <td
                              aria-disabled="false"
                              role="gridcell"
                              data-d="2026-07-07T16:00:00.000Z"
                              class="in-range is-allow-hover has-price"
                            >
                              <div
                                class="tipWrapper"
                                aria-label="2026\u5e747\u67088\u65e5(\u661f\u671f\u4e09), The price is CNY 420, Select the start date you want"
                              >
                                <span class="day">8</span>
                                <span class="price"><span aria-label="420">420</span></span>
                              </div>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </body>
                </html>
                """
            )
        )

        self.assertTrue(result["opened"])
        self.assertEqual(result["clicked"], "date-range")
        self.assertTrue(
            any(
                cell["data_d"] == "2026-07-07T16:00:00.000Z"
                and cell["price_text"] == "420"
                for cell in result["cells"]
            )
        )


class PublicCalendarPriceReaderContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from app.domain.browser import ctrip_public_price_reader as reader_module

        self.reader_module = reader_module
        self.original_open_calendar = reader_module._open_public_calendar_popup
        self.original_safe_cells = reader_module._safe_public_calendar_cells
        self.original_read_delay_ms = reader_module._public_competitor_read_delay_ms

    def tearDown(self) -> None:
        self.reader_module._open_public_calendar_popup = self.original_open_calendar
        self.reader_module._safe_public_calendar_cells = self.original_safe_cells
        self.reader_module._public_competitor_read_delay_ms = self.original_read_delay_ms

    async def test_reader_returns_trusted_calendar_points_from_collected_cells(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            self.assertIs(opened_page, page)
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            self.assertIs(opened_page, page)
            return [
                {
                    "text": "8\n420",
                    "aria_label": "2026\u5e747\u67088\u65e5, The price is CNY 420",
                    "data_d": "2026-07-07T16:00:00.000Z",
                    "class_name": "has-price",
                    "aria_disabled": "false",
                    "price_text": "420",
                }
            ]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells
        self.reader_module._public_competitor_read_delay_ms = lambda: 2400

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=1,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["checkin_date"], "2026-07-08")
        self.assertEqual(result["points"][0]["price_amount_yuan"], 420)
        self.assertTrue(page.calendar_opened)
        self.assertEqual(page.waits, [2400])
        self.assertEqual(result["screenshot_path"], "screenshot.png")

    async def test_reader_returns_partial_when_some_requested_days_are_missing(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            return [
                {
                    "text": "8\n420",
                    "aria_label": "2026\u5e747\u67088\u65e5, The price is CNY 420",
                    "data_d": "2026-07-07T16:00:00.000Z",
                    "class_name": "has-price",
                    "aria_disabled": "false",
                    "price_text": "420",
                }
            ]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["points"]), 1)
        self.assertIsNone(result["review_reason"])

    async def test_reader_uses_parser_bounded_days_for_success_status(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            return [
                _calendar_cell_for(date(2026, 7, 8) + timedelta(days=offset), 420 + offset)
                for offset in range(30)
            ]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=60,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["points"]), 30)

    async def test_reader_returns_needs_review_when_calendar_has_no_trusted_points(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            return []

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["review_reason"], "calendar_price_not_found")

    async def test_reader_returns_needs_review_when_calendar_popup_not_opened(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )
        open_calls = {"n": 0}

        async def fake_open_calendar(opened_page: object) -> bool:
            open_calls["n"] += 1
            page.calendar_opened = False
            return False

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            raise AssertionError("calendar cells must not be read when popup did not open")

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells
        self.reader_module._public_competitor_read_delay_ms = lambda: 10

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["review_reason"], "calendar_popup_not_opened")
        self.assertEqual(open_calls["n"], 2)
        self.assertEqual(page.waits, [10])

    async def test_reader_retries_once_when_calendar_popup_first_fails(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )
        open_calls = {"n": 0}

        async def fake_open_calendar(opened_page: object) -> bool:
            open_calls["n"] += 1
            if open_calls["n"] == 1:
                page.calendar_opened = False
                return False
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            return [
                {
                    "text": "8\n420",
                    "aria_label": "2026\u5e747\u67088\u65e5, The price is CNY 420",
                    "data_d": "2026-07-07T16:00:00.000Z",
                    "class_name": "has-price",
                    "aria_disabled": "false",
                    "price_text": "420",
                }
            ]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells
        self.reader_module._public_competitor_read_delay_ms = lambda: 15

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "own",
                "label": "Own Hotel",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=1,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(open_calls["n"], 2)
        self.assertEqual(page.waits, [15, 15])

    async def test_reader_returns_human_required_before_calendar_dom_when_blocked(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "login_required",
                "review_reason": "login_required",
            },
        )

        async def fake_open_calendar(opened_page: object) -> None:
            raise AssertionError("blocked pages must not open calendar DOM")

        self.reader_module._open_public_calendar_popup = fake_open_calendar

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(result["status"], "human_required")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["page_state"], "login_required")
        self.assertNotIn("screenshot_path", result)

    async def test_reader_treats_verification_required_as_human_required(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "verification_required",
                "review_reason": "verification_required",
            },
        )

        async def fake_open_calendar(opened_page: object) -> None:
            raise AssertionError("verification pages must not open calendar DOM")

        self.reader_module._open_public_calendar_popup = fake_open_calendar

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=15,
        )

        self.assertEqual(result["status"], "human_required")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["page_state"], "verification_required")

    async def test_reader_does_not_use_room_rate_reader_for_calendar_prices(self) -> None:
        page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            return [_calendar_cell_for(date(2026, 7, 8), 420)]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=1,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(reader.room_rate_reader_calls, 0)

    async def test_reader_reuses_shared_public_page_without_opening_blank_tab(self) -> None:
        shared_page = _FakePublicCalendarPage()
        reader = _PublicCalendarReaderHarness(
            page=shared_page,
            parsed={
                "page_state": "normal",
                "hotel_name": "Sample Hotel",
                "current_url": "https://hotels.ctrip.com/hotels/detail/",
                "requested_url": "https://hotels.ctrip.com/hotels/detail/",
            },
            shared_page=shared_page,
        )

        async def fake_open_calendar(opened_page: object) -> bool:
            self.assertIs(opened_page, shared_page)
            shared_page.calendar_opened = True
            return True

        async def fake_safe_cells(opened_page: object) -> list[dict[str, str]]:
            self.assertIs(opened_page, shared_page)
            return [_calendar_cell_for(date(2026, 7, 8), 420)]

        self.reader_module._open_public_calendar_popup = fake_open_calendar
        self.reader_module._safe_public_calendar_cells = fake_safe_cells

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=1,
        )

        self.assertEqual(result["status"], "success")
        self.assertFalse(shared_page.closed)
        self.assertEqual(reader.new_page_calls, 0)
        self.assertIsNone(reader.new_page_parent)

    async def test_reader_returns_needs_review_when_shared_calendar_page_goes_stale(
        self,
    ) -> None:
        from app.domain.browser.ctrip_public_price_reader import (
            CtripPublicCompetitorPriceReader,
        )

        service = _FakeCalendarReaderService()
        reader = CtripPublicCompetitorPriceReader(service=service)  # type: ignore[arg-type]
        shared_page = _FakePublicCalendarPage(fail_goto_stale=True)

        async def fake_get_page(
            *,
            platform_account_id: str,
            browser_session_id: str,
        ) -> _FakePublicCalendarPage:
            return shared_page

        async def fake_open_calendar(opened_page: object) -> bool:
            raise AssertionError("stale temporary page must not open calendar DOM")

        reader._get_or_create_public_price_page_with_recovery = fake_get_page  # type: ignore[method-assign]
        self.reader_module._open_public_calendar_popup = fake_open_calendar

        result = await reader.read_public_calendar_prices(
            platform_account_id="acct-1",
            browser_session_id="session-1",
            target={
                "role": "competitor",
                "label": "Sample",
                "url": "https://hotels.ctrip.com/hotels/detail/",
            },
            start_date=date(2026, 7, 8),
            days=1,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_reason"], "calendar_page_stale")
        self.assertFalse(shared_page.closed)


class _FakePublicCalendarPage:
    def __init__(
        self,
        *,
        context: object | None = None,
        fail_goto_stale: bool = False,
    ) -> None:
        self.waits: list[int] = []
        self.calendar_opened = False
        self.closed = False
        self.context = context or _FakeCalendarContext(self)
        self.fail_goto_stale = fail_goto_stale
        self.url = "about:blank"

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        if self.fail_goto_stale:
            raise RuntimeError("Target page, context or browser has been closed")
        self.url = url


def _calendar_cell_for(checkin_date: date, price: int) -> dict[str, str]:
    data_date = checkin_date - timedelta(days=1)
    return {
        "text": f"{checkin_date.day}\n{price}",
        "aria_label": (
            f"{checkin_date.year}\u5e74{checkin_date.month}\u6708"
            f"{checkin_date.day}\u65e5, The price is CNY {price}"
        ),
        "data_d": f"{data_date.isoformat()}T16:00:00.000Z",
        "class_name": "has-price",
        "aria_disabled": "false",
        "price_text": str(price),
    }


class _PublicCalendarReaderHarness:
    def __init__(
        self,
        *,
        page: _FakePublicCalendarPage,
        parsed: dict[str, object],
        shared_page: _FakePublicCalendarPage | None = None,
    ) -> None:
        from app.domain.browser.ctrip_public_price_reader import (
            CtripPublicCompetitorPriceReader,
        )

        self._reader = CtripPublicCompetitorPriceReader(service=object())
        self._reader._get_or_create_public_price_page_with_recovery = self._get_page
        self._reader._new_public_price_page = self._new_page
        self._reader._new_public_calendar_page = self._new_page
        self._reader._read_public_price_target = self._read_room_rate_target
        self._reader._parse_public_calendar_target_page = self._read_calendar_target
        self._shared_page = shared_page or page
        self._page = page
        self._parsed = parsed
        self.room_rate_reader_calls = 0
        self.new_page_calls = 0
        self.new_page_parent: _FakePublicCalendarPage | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self._reader, name)

    async def _get_page(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> _FakePublicCalendarPage:
        return self._shared_page

    async def _new_page(
        self,
        *,
        page: _FakePublicCalendarPage,
        platform_account_id: str,
        browser_session_id: str,
    ) -> _FakePublicCalendarPage:
        self.new_page_calls += 1
        self.new_page_parent = page
        return self._page

    async def _read_room_rate_target(
        self,
        *,
        page: _FakePublicCalendarPage,
        platform_account_id: str,
        browser_session_id: str,
        target: dict[str, str],
    ) -> tuple[_FakePublicCalendarPage, dict[str, object], str]:
        self.room_rate_reader_calls += 1
        raise AssertionError("calendar reader must not use room-rate price reader")

    async def _read_calendar_target(
        self,
        *,
        page: _FakePublicCalendarPage,
        platform_account_id: str,
        target: dict[str, str],
    ) -> tuple[dict[str, object], str]:
        self._parsed.setdefault("requested_url", target["url"])
        self._parsed.setdefault("current_url", target["url"])
        return self._parsed, "screenshot.png"


async def _read_public_calendar_cells_from_html(html: str) -> list[dict[str, object]]:
    from playwright.async_api import async_playwright

    from app.domain.browser.ctrip_public_page_reader import _safe_public_calendar_cells

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            await page.set_content(html)
            return await _safe_public_calendar_cells(page)
        finally:
            await browser.close()


async def _open_public_calendar_from_html(html: str) -> dict[str, object]:
    from playwright.async_api import async_playwright

    from app.domain.browser.ctrip_public_page_reader import (
        _open_public_calendar_popup,
        _safe_public_calendar_cells,
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            await page.set_content(html)
            opened = await _open_public_calendar_popup(page)
            await page.wait_for_timeout(50)
            cells = await _safe_public_calendar_cells(page)
            clicked = await page.evaluate("window.clickedCalendarOpener || null")
            return {"opened": opened, "clicked": clicked, "cells": cells}
        finally:
            await browser.close()


class _FakeCalendarContext:
    def __init__(self, page: _FakePublicCalendarPage) -> None:
        self._page = page

    async def new_page(self) -> _FakePublicCalendarPage:
        return self._page


class _StaleCalendarContext:
    async def new_page(self) -> _FakePublicCalendarPage:
        raise RuntimeError("Target page, context or browser has been closed")


class _FakeCalendarReaderService:
    def __init__(self) -> None:
        self._pages: dict[str, object] = {}
        self._contexts: dict[str, object] = {}
        self._cdp_browsers: dict[str, object] = {}
        self.discard_calls: list[tuple[str, str]] = []

    def _lock_for(self, key: str) -> asyncio.Lock:
        return asyncio.Lock()

    async def _capture_screenshot(self, page: object, platform_account_id: str) -> str:
        return "screenshot.png"

    async def _discard_cached_browser_state(
        self,
        *,
        platform_account_id: str,
        browser_session_id: str,
    ) -> None:
        self.discard_calls.append((platform_account_id, browser_session_id))


if __name__ == "__main__":
    unittest.main()
