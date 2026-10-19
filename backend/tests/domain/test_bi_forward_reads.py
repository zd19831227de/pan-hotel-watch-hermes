# -*- coding: utf-8 -*-
"""bi_daily 前瞻现读解析器测试：夹具取自 2026-10-10 远端 CDP 实抓页面数据。"""

import unittest
from datetime import date
from pathlib import Path

from app.domain.bi_daily.forward_reads import (
    parse_basic_room_status_html,
    parse_calendar_dates,
    parse_calendar_rate_plans,
    parse_calendar_room_groups,
    parse_competitor_occupied_trend,
    parse_hot_calendar_events,
    parse_market_heats,
    parse_price_change_trend,
    parse_rc_product_list,
    parse_room_inventory_info,
    parse_travel_heats,
    _clean_lines,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "bi_daily_calendar.txt"
TODAY = date(2026, 10, 10)


def _fixture_lines() -> list[str]:
    return _clean_lines(FIXTURE.read_text(encoding="utf-8"))


class CalendarTextParseTest(unittest.TestCase):
    def setUp(self):
        self.lines = _fixture_lines()
        self.dates = parse_calendar_dates(self.lines, today=TODAY)

    def test_dates_parsed_fourteen_days_from_today(self):
        self.assertEqual(len(self.dates), 14)
        self.assertEqual(self.dates[0], date(2026, 10, 10))
        self.assertEqual(self.dates[-1], date(2026, 10, 23))

    def test_dates_cross_year_boundary(self):
        lines = _clean_lines("星期五\n12-31\n星期六\n01-01\n星期日\n01-02")
        dates = parse_calendar_dates(lines, today=date(2026, 12, 30))
        self.assertEqual(dates, [date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2)])

    def test_room_groups_with_total_rooms(self):
        groups = parse_calendar_room_groups(self.lines)
        self.assertEqual(groups[0]["room_group_name"], "豪华套房(全屋智能+会客厅+全套厨卫家电)")
        self.assertEqual(groups[0]["total_rooms"], 14)

    def test_rate_plan_full_cells_parsed(self):
        plans = parse_calendar_rate_plans(self.lines, self.dates)
        self.assertEqual(len(plans), 1)  # 第二个计划只有4格，整计划弃收
        plan = plans[0]
        self.assertEqual(plan["rate_plan_id"], 1741110287)
        self.assertEqual(len(plan["cells"]), 14)
        first = plan["cells"][0]
        self.assertEqual(first["stay_date"], "2026-10-10")
        self.assertEqual(first["room_status"], "已开房")
        self.assertEqual(first["limit_kind"], "限量")
        self.assertEqual(first["remaining"], 9)
        self.assertEqual(first["sale_price"], 406.0)
        self.assertEqual(first["floor_price"], 365.4)
        self.assertEqual(first["breakfast"], 0)
        # 周五/周六提价单元格
        self.assertEqual(plan["cells"][6]["sale_price"], 443.0)
        self.assertEqual(plan["cells"][13]["sale_price"], 480.0)

    def test_lirui_style_plan_id_on_own_line_and_fs_cells(self):
        """立瑞版式：ID 独立成行（名字不带尾巴）+ FS(FreeSale) 房量。"""
        lines = _clean_lines(
            "\n".join(
                [
                    "芮享舒适大床房（戴森吹风机+MINI冰箱+落地窗）<早餐><芮享舒适大床房>",
                    "预付 礼盒",
                    "1631534698",
                    "房态",
                    "房量",
                    "卖价(元)",
                    "底价(元)",
                    "餐食",
                ]
            )
            + "\n"
            + "\n".join(
                ["已开房", "FS", "480", "432", "2"] * 3
            )
        )
        plans = parse_calendar_rate_plans(lines, self.dates[:3])
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan["rate_plan_id"], 1631534698)
        self.assertTrue(plan["rate_plan_name"].startswith("芮享舒适大床房"))
        cell = plan["cells"][0]
        self.assertEqual(cell["limit_kind"], "FS")
        self.assertIsNone(cell["remaining"])
        self.assertEqual(cell["sale_price"], 480.0)
        self.assertEqual(cell["floor_price"], 432.0)
        self.assertEqual(cell["breakfast"], 2)

    def test_partial_plan_dropped_not_misaligned(self):
        plans = parse_calendar_rate_plans(self.lines, self.dates)
        # 完整计划的末尾单元格日期必须对位第 14 天，不允许错位输出。
        self.assertEqual(plans[0]["cells"][-1]["stay_date"], "2026-10-23")

    def test_garbage_text_returns_empty(self):
        self.assertEqual(parse_calendar_rate_plans(["无法访问", "页面不存在"], []), [])


BASIC_ROOM_HTML = {
    "code": 200,
    "data": {
        "467494979": {
            "2026-10-10": '<div class="room-td"><a>已售16</a></div>',
            "2026-10-11": '<div class="room-td"></div>',
        },
        "467494990": {"2026-10-10": '<a>已售4</a>'},
    },
}


class ApiJsonParseTest(unittest.TestCase):
    def test_basic_room_status_sold_extracted(self):
        sold = parse_basic_room_status_html(BASIC_ROOM_HTML)
        self.assertEqual(sold["467494979"]["2026-10-10"], 16)
        self.assertNotIn("2026-10-11", sold["467494979"])  # 无已售不猜 0

    def test_room_inventory_rows(self):
        payload = {
            "data": {
                "roomStatusResult": [
                    {
                        "roomTypeID": 1741110287,
                        "effectDate": "2026-10-10",
                        "roomStatus": "G",
                        "limitSale": "T",
                        "totalQuantity": 25,
                        "canUsedQuantity": 9,
                    }
                ]
            }
        }
        rows = parse_room_inventory_info(payload)
        self.assertEqual(rows[0]["rate_plan_id"], 1741110287)
        self.assertEqual(rows[0]["total_quantity"], 25)
        self.assertEqual(rows[0]["can_used_quantity"], 9)

    def test_rc_product_registry(self):
        payload = {
            "data": [
                {
                    "basicRoomTypeID": 467494979,
                    "roomNameDesc": "豪华套房(全屋智能)",
                    "roomInfos": [
                        {
                            "roomTypeID": 1741110287,
                            "roomNameDesc": "豪华套房<乐享旅程><无早><高级大床房>(1741110287)",
                        }
                    ],
                }
            ]
        }
        registry = parse_rc_product_list(payload)
        entry = registry["1741110287"]
        self.assertEqual(entry["basic_room_type_id"], 467494979)
        self.assertEqual(entry["breakfast_hint"], "none")


MARKET_HEATS_PAYLOAD = {
    "marketHeatsBo": {
        "cityMarketHeats": [
            {"effectDate": "/Date(1791561600000+0800)/", "heat": 3.28, "heatLabel": "Normal", "dateDes": "2026-10-10T00:00.000+0800"},
            {"effectDate": "/Date(1791648000000+0800)/", "heat": 2.37, "heatLabel": "Low", "dateDes": "2026-10-11T00:00.000+0800"},
        ],
        "cityMarketHeatsLastYear": [
            {"effectDate": "/Date(1760025600000+0800)/", "heat": 4.5, "heatLabel": "Normal", "dateDes": "2025-10-10T00:00.000+0800"},
        ],
        "zoneMarketHeats": [
            {"effectDate": "2026-10-10", "heat": 3.5, "heatLabel": "Normal"},
        ],
    }
}


class MarketDemandParseTest(unittest.TestCase):
    def test_market_heats_epoch_and_iso_dates(self):
        parsed = parse_market_heats(MARKET_HEATS_PAYLOAD)
        self.assertEqual(parsed["city"][0]["stay_date"], "2026-10-10")
        self.assertEqual(parsed["city"][0]["heat"], 3.28)
        self.assertEqual(parsed["city_last_year"][0]["stay_date"], "2025-10-10")
        self.assertEqual(parsed["zone"][0]["stay_date"], "2026-10-10")

    def test_travel_heats_with_last_year(self):
        rows = parse_travel_heats(
            {"inlandMarketHeatBo": [{"effectDate": "2026-10-10", "heat": 2.03, "heatLastYear": 2.34}]}
        )
        self.assertEqual(rows[0]["heat_last_year"], 2.34)

    def test_competitor_occupied_trend_rates_to_fraction(self):
        payload = {
            "dateList": ["2026-10-10", "2026-10-11"],
            "nocturnalAmountList": ["58", "22"],
            "rentalRateList": ["33.14", "12.57"],
            "competitorNocturnalAmountList": ["35", "23"],
            "competitorRentalRateList": ["22.56", "15.08"],
        }
        trend = parse_competitor_occupied_trend(payload)
        first = trend["rows"][0]
        self.assertAlmostEqual(first["my_occupancy_rate"], 0.3314)
        self.assertAlmostEqual(first["competitor_occupancy_rate"], 0.2256)
        self.assertEqual(first["my_booked_rooms"], 58.0)

    def test_price_change_trend_merges_last_year(self):
        payload = {
            "competitorPriceChangeTrendBo": {
                "priceChangeTrend": [{"effectDate": "2026-10-10", "trend": -0.09}],
                "priceChangeTrendLastYear": [{"effectDate": "2026-10-10", "trend": 0}],
            }
        }
        rows = parse_price_change_trend(payload)
        self.assertEqual(rows[0]["trend"], -0.09)
        self.assertEqual(rows[0]["trend_last_year"], 0)

    def test_hot_calendar_events(self):
        payload = {
            "otherDataList": [
                {"hotSpotName": "安又琪演唱会", "startDate": "2026-10-17", "endDate": "2026-10-17"},
                {"hotSpotName": "", "startDate": None, "endDate": None},
            ]
        }
        events = parse_hot_calendar_events(payload)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "安又琪演唱会")

    def test_non_dict_payloads_return_empty(self):
        self.assertEqual(parse_market_heats(None), {})
        self.assertEqual(parse_travel_heats([]), [])
        self.assertEqual(parse_competitor_occupied_trend("x"), {})
        self.assertEqual(parse_price_change_trend(123), [])
        self.assertEqual(parse_hot_calendar_events(0.5), [])
        self.assertEqual(parse_basic_room_status_html([]), {})
        self.assertEqual(parse_room_inventory_info("y"), [])
        self.assertEqual(parse_rc_product_list(1), {})


if __name__ == "__main__":
    unittest.main()
