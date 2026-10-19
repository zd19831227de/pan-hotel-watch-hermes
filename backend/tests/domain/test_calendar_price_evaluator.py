from __future__ import annotations

import unittest

from app.domain.calendar_price_watch.evaluator import evaluate_calendar_price_alerts


class CalendarPriceEvaluatorTests(unittest.TestCase):
    def test_own_higher_than_lowest_competitor_by_10_alerts(self) -> None:
        alerts = evaluate_calendar_price_alerts(
            points=[
                {
                    "role": "own",
                    "hotel_alias": "本店",
                    "checkin_date": "2026-07-08",
                    "price_amount_yuan": 420,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对A",
                    "checkin_date": "2026-07-08",
                    "price_amount_yuan": 410,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对B",
                    "checkin_date": "2026-07-08",
                    "price_amount_yuan": 450,
                    "is_trusted": True,
                },
            ],
            high_gap_threshold_yuan=10,
            low_gap_threshold_yuan=20,
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_type"], "own_price_high")
        self.assertEqual(alerts[0]["gap_yuan"], 10)
        self.assertEqual(alerts[0]["reference_hotel_alias"], "竞对A")

    def test_no_alert_when_own_equals_lowest_competitor(self) -> None:
        alerts = evaluate_calendar_price_alerts(
            points=[
                {
                    "role": "own",
                    "hotel_alias": "本店",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 400,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对A",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 400,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对B",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 430,
                    "is_trusted": True,
                },
            ],
            high_gap_threshold_yuan=10,
        )

        self.assertEqual(alerts, [])

    def test_no_alert_when_own_is_cheapest(self) -> None:
        alerts = evaluate_calendar_price_alerts(
            points=[
                {
                    "role": "own",
                    "hotel_alias": "本店",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 380,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对A",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 390,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对B",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 400,
                    "is_trusted": True,
                },
            ],
            high_gap_threshold_yuan=10,
            low_gap_threshold_yuan=20,
        )

        self.assertEqual(alerts, [])

    def test_own_much_lower_than_competitors_alerts_without_pricing_action(self) -> None:
        alerts = evaluate_calendar_price_alerts(
            points=[
                {
                    "role": "own",
                    "hotel_alias": "本店",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 360,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对A",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 390,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对B",
                    "checkin_date": "2026-07-09",
                    "price_amount_yuan": 400,
                    "is_trusted": True,
                },
            ],
            high_gap_threshold_yuan=10,
            low_gap_threshold_yuan=20,
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_type"], "own_price_low")
        self.assertEqual(alerts[0]["gap_yuan"], 30)
        self.assertEqual(alerts[0]["reference_hotel_alias"], "竞对A")
        self.assertNotIn("调价", alerts[0]["summary"])

    def test_no_alert_when_gap_is_below_threshold(self) -> None:
        alerts = evaluate_calendar_price_alerts(
            points=[
                {
                    "role": "own",
                    "hotel_alias": "本店",
                    "checkin_date": "2026-07-10",
                    "price_amount_yuan": 419,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对A",
                    "checkin_date": "2026-07-10",
                    "price_amount_yuan": 410,
                    "is_trusted": True,
                },
                {
                    "role": "competitor",
                    "hotel_alias": "竞对B",
                    "checkin_date": "2026-07-10",
                    "price_amount_yuan": 431,
                    "is_trusted": True,
                },
            ],
            high_gap_threshold_yuan=10,
        )

        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
