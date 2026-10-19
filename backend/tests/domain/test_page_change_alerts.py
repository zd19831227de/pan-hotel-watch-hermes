from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ALERT_WEBHOOK = "https://open.feishu.cn/open-apis/bot/hook/test-page-health"


class PageChangeAlertsTests(unittest.TestCase):
    """V7 Step 1 — selector 健康检查 / 结构指纹对比 / 分级告警（DB 隔离）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "page-health-test.db"
        self._saved_env: dict[str, str | None] = {}
        for key, value in {
            "DATABASE_URL": f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            "DATABASE_AUTO_CREATE_TABLES": "true",
        }.items():
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        # engine 在 app.db.session import 时已按 .env 构建；这里必须重指到临时库，
        # 否则第一个 DB 测试会写进真实开发库（与 RAG 测试的 _client 同一套路）。
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        db_session._database_ready_engine = None

    def tearDown(self) -> None:
        for key, previous in self._saved_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        settings = importlib.import_module("app.core.settings")
        settings.get_settings.cache_clear()
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()

    def _push_ok(self):
        return mock.patch(
            "app.domain.page_change_alerts.service.push_interactive_card_to_feishu_webhook"
        )

    # ------------------------------------------------------------------
    # selector 连续失败 → hard_fail
    # ------------------------------------------------------------------

    def test_three_consecutive_failures_trigger_hard_fail_alert(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            alerts = []
            for _ in range(3):
                alert = await service.observe_read_result(
                    platform="ctrip",
                    target="dataset:business_outline",
                    status="page_structure_changed",
                    parser_version="ctrip_ebooking_dataset_visible_text_v1",
                    summary="页面结构未识别",
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts.append(alert)
            return alerts

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts = asyncio.run(_scenario())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "hard_fail")
        self.assertEqual(alerts[0]["platform"], "ctrip")
        push.assert_called_once()
        card = push.call_args.kwargs["card"]
        self.assertEqual(card["header"]["template"], "red")

    def test_success_resets_consecutive_failure_counter(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            sequence = [
                ("needs_review", None),
                ("success", ["门店", "数据中心"]),
                ("needs_review", None),
                ("needs_review", None),
            ]
            alerts = []
            for status, terms in sequence:
                alert = await service.observe_read_result(
                    platform="meituan",
                    target="data_center_home",
                    status=status,
                    matched_terms=terms,
                    summary=status,
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts.append(alert)
            return alerts

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts = asyncio.run(_scenario())
        self.assertEqual(alerts, [])
        push.assert_not_called()

    def test_human_required_never_counts_as_selector_failure(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            alerts = []
            for _ in range(3):
                alert = await service.observe_read_result(
                    platform="ctrip",
                    target="dataset:flow_data",
                    status="human_required",
                    summary="需要登录",
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts.append(alert)
            return alerts

        alerts = asyncio.run(_scenario())
        self.assertEqual(alerts, [])

    def test_alert_deduped_within_one_hour(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            alerts = []
            # 两轮各 3 次失败；第一轮告警后计数器重置，第二轮再触发但被 1 小时去重拦下
            for _ in range(6):
                alert = await service.observe_read_result(
                    platform="ctrip",
                    target="dataset:ad_pyramid",
                    status="failed",
                    summary="读取失败",
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts.append(alert)
            return alerts

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts = asyncio.run(_scenario())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(push.call_count, 1)

    # ------------------------------------------------------------------
    # partial → partial_fail
    # ------------------------------------------------------------------

    def test_partial_status_alerts_partial_fail(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            return await service.observe_read_result(
                platform="ctrip",
                target="dataset:loss_analysis",
                status="partial",
                matched_terms=["流失分析"],
                summary="部分字段缺失",
                webhook_url=ALERT_WEBHOOK,
            )

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alert = asyncio.run(_scenario())
        self.assertIsNotNone(alert)
        self.assertEqual(alert["level"], "partial_fail")
        card = push.call_args.kwargs["card"]
        self.assertEqual(card["header"]["template"], "orange")

    # ------------------------------------------------------------------
    # 结构指纹 → layout_changed
    # ------------------------------------------------------------------

    def test_structure_fingerprint_change_alerts_layout_changed(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            first = await service.observe_read_result(
                platform="meituan",
                target="data_center_trade",
                status="success",
                matched_terms=["门店", "曝光量", "访客数"],
                webhook_url=ALERT_WEBHOOK,
            )
            second = await service.observe_read_result(
                platform="meituan",
                target="data_center_trade",
                status="success",
                matched_terms=["门店", "新标签A", "新指标B"],
                webhook_url=ALERT_WEBHOOK,
            )
            return [first, second]

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            first, second = asyncio.run(_scenario())
        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(second["level"], "layout_changed")
        card = push.call_args.kwargs["card"]
        self.assertEqual(card["header"]["template"], "blue")

    def test_fingerprint_value_drift_does_not_alert(self) -> None:
        """数字漂移（如同行不同日期）不应触发 layout_changed。"""
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            first = await service.observe_read_result(
                platform="meituan",
                target="data_center_trade",
                status="success",
                matched_terms=["门店经营2026-08-18", "曝光量1234"],
                webhook_url=ALERT_WEBHOOK,
            )
            second = await service.observe_read_result(
                platform="meituan",
                target="data_center_trade",
                status="success",
                matched_terms=["门店经营2026-08-19", "曝光量9999"],
                webhook_url=ALERT_WEBHOOK,
            )
            return [first, second]

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts = asyncio.run(_scenario())
        self.assertEqual(alerts, [None, None])
        push.assert_not_called()

    def test_fingerprint_alert_not_repeated_for_same_layout(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            results = []
            for terms in (["门店曝光", "访客转化"], ["新版模块", "全新指标"], ["新版模块", "全新指标"]):
                alert = await service.observe_read_result(
                    platform="meituan",
                    target="data_center_traffic",
                    status="success",
                    matched_terms=terms,
                    webhook_url=ALERT_WEBHOOK,
                )
                results.append(alert)
            return results

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            results = asyncio.run(_scenario())
        # 第二次指纹变化触发一次 layout_changed；第三次与已存指纹一致 → 不再告警
        self.assertEqual([alert["level"] if alert else None for alert in results][1], "layout_changed")
        self.assertIsNone(results[2])
        self.assertEqual(push.call_count, 1)

    # ------------------------------------------------------------------
    # 推送失败不影响采集链路
    # ------------------------------------------------------------------

    def test_push_failure_never_raises(self) -> None:
        from app.domain.page_change_alerts.service import PageChangeAlertService

        async def _scenario():
            service = PageChangeAlertService()
            return await service.observe_read_result(
                platform="ctrip",
                target="dataset:promotion_effect",
                status="partial",
                summary="partial read",
                webhook_url=ALERT_WEBHOOK,
            )

        with self._push_ok() as push:
            push.side_effect = RuntimeError("webhook down")
            alert = asyncio.run(_scenario())
        self.assertIsNotNone(alert)
        push.assert_called_once()

    # ------------------------------------------------------------------
    # 配置热更新
    # ------------------------------------------------------------------

    def test_selector_config_hot_reload_changes_threshold(self) -> None:
        from app.domain.page_change_alerts.config import load_page_health_config
        from app.domain.page_change_alerts.service import PageChangeAlertService

        config_path = Path(self._tmp.name) / "page-health.json"

        async def _scenario():
            config_path.write_text(
                json.dumps({"defaults": {"consecutive_failures_to_alert": 2}}),
                encoding="utf-8",
            )
            service = PageChangeAlertService(config_path=config_path)
            alerts_strict = []
            for _ in range(2):
                alert = await service.observe_read_result(
                    platform="ctrip",
                    target="dataset:business_outline",
                    status="failed",
                    summary="读取失败",
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts_strict.append(alert)

            # 热更新：改文件后阈值变为 5，新 service 无需改代码
            config_path.write_text(
                json.dumps({"defaults": {"consecutive_failures_to_alert": 5}}),
                encoding="utf-8",
            )
            os.utime(config_path, (0, 2000000000))  # 强制 mtime 变化触发重载
            service2 = PageChangeAlertService(config_path=config_path)
            alerts_loose = []
            for _ in range(3):
                alert = await service2.observe_read_result(
                    platform="ctrip",
                    target="dataset:flow_data",
                    status="failed",
                    summary="读取失败",
                    webhook_url=ALERT_WEBHOOK,
                )
                if alert:
                    alerts_loose.append(alert)
            return alerts_strict, alerts_loose

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts_strict, alerts_loose = asyncio.run(_scenario())
        self.assertEqual(len(alerts_strict), 1)
        self.assertEqual(alerts_loose, [])

        config = load_page_health_config(config_path)
        self.assertEqual(config.defaults.consecutive_failures_to_alert, 5)

    def test_default_config_threshold_is_three(self) -> None:
        from app.domain.page_change_alerts.config import load_page_health_config

        config = load_page_health_config(Path(self._tmp.name) / "not-exist.json")
        self.assertEqual(config.defaults.consecutive_failures_to_alert, 3)
        self.assertEqual(config.defaults.alert_repeat_seconds, 3600)

    # ------------------------------------------------------------------
    # selector/expected_terms 配置文件化（验收项：selector 配置可通过修改文件热更新）
    # ------------------------------------------------------------------

    def test_expected_terms_config_file_override_and_fallback(self) -> None:
        from app.domain.page_change_alerts.config import resolve_expected_terms

        config_path = Path(self._tmp.name) / "page-health-terms.json"
        config_path.write_text(
            json.dumps(
                {
                    "targets": {
                        "ctrip:dataset:business_outline": {
                            "expected_terms": ["实时预订订单", "改版后新指标"]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        # 配置了 expected_terms 的 target：文件值生效
        self.assertEqual(
            resolve_expected_terms(
                "ctrip",
                "dataset:business_outline",
                ("内置旧词",),
                config_path=config_path,
            ),
            ("实时预订订单", "改版后新指标"),
        )
        # 未配置的 target：回落内置 selector
        self.assertEqual(
            resolve_expected_terms(
                "ctrip",
                "dataset:flow_data",
                ("访客量", "转化率"),
                config_path=config_path,
            ),
            ("访客量", "转化率"),
        )

    def test_expected_terms_config_hot_reload(self) -> None:
        from app.domain.page_change_alerts.config import resolve_expected_terms

        config_path = Path(self._tmp.name) / "page-health-reload.json"
        config_path.write_text(
            json.dumps(
                {
                    "targets": {
                        "ctrip:dataset:business_outline": {"expected_terms": ["第一批词"]}
                    }
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            resolve_expected_terms(
                "ctrip",
                "dataset:business_outline",
                ("内置旧词",),
                config_path=config_path,
            ),
            ("第一批词",),
        )

        # 热更新：只改文件（mtime 变化），不重启进程即可生效
        config_path.write_text(
            json.dumps(
                {
                    "targets": {
                        "ctrip:dataset:business_outline": {"expected_terms": ["第二批词"]}
                    }
                }
            ),
            encoding="utf-8",
        )
        os.utime(config_path, (0, 2000000000))
        self.assertEqual(
            resolve_expected_terms(
                "ctrip",
                "dataset:business_outline",
                ("内置旧词",),
                config_path=config_path,
            ),
            ("第二批词",),
        )

    def test_dataset_parser_uses_file_expected_terms(self) -> None:
        parsers = importlib.import_module("app.domain.browser.ctrip_ebooking_parsers")
        dataset_parsers = importlib.import_module(
            "app.domain.browser.ctrip_ebooking_dataset_parsers"
        )
        config_path = Path(self._tmp.name) / "page-health-parser.json"
        config_path.write_text(
            json.dumps(
                {
                    "targets": {
                        "ctrip:dataset:business_outline": {
                            "expected_terms": ["文件化专属指标"]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        body_text = "文件化专属指标 128\n不相关的行"

        with mock.patch.dict(os.environ, {"PAGE_HEALTH_CONFIG_PATH": str(config_path)}):
            dataset = parsers._CTRIP_DATASETS["business_outline"]
            # helper 层：文件值覆盖内置 expected_terms
            self.assertEqual(
                parsers.dataset_expected_terms(dataset), ("文件化专属指标",)
            )
            # 解析链路层：matched_expected_terms 来自文件 selector
            parsed = dataset_parsers._extract_dataset_snapshot(
                dataset=dataset, body_text=body_text, filters=None
            )
        self.assertIn("文件化专属指标", parsed["matched_expected_terms"])
        for builtin_term in dataset.expected_terms:
            self.assertNotIn(builtin_term, parsed["matched_expected_terms"])

    # ------------------------------------------------------------------
    # 观测入口适配器（ctrip watch 结果 / meituan 读取 payload）
    # ------------------------------------------------------------------

    def test_observe_ctrip_dataset_result_extracts_fields(self) -> None:
        from app.domain.page_change_alerts.service import observe_ctrip_dataset_result

        result = {
            "dataset_id": "business_outline",
            "status": "success",
            "summary": "ok",
            "evidence": {
                "parser_version": "ctrip_ebooking_dataset_visible_text_v1",
                "matched_expected_terms": ["营业概况", "经营概况"],
            },
        }

        async def _scenario():
            return await observe_ctrip_dataset_result(result, webhook_url=ALERT_WEBHOOK)

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alert = asyncio.run(_scenario())
        self.assertIsNone(alert)
        push.assert_not_called()

    def test_observe_ctrip_dataset_result_uses_dataset_id_as_target(self) -> None:
        from app.domain.page_change_alerts.service import observe_ctrip_dataset_result

        result = {
            "dataset_id": "flow_data",
            "status": "failed",
            "summary": "read failed",
            "evidence": {"parser_version": "v1", "matched_expected_terms": []},
        }

        async def _scenario():
            alerts = []
            for _ in range(3):
                alert = await observe_ctrip_dataset_result(result, webhook_url=ALERT_WEBHOOK)
                if alert:
                    alerts.append(alert)
            return alerts

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alerts = asyncio.run(_scenario())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["target"], "dataset:flow_data")

    def test_observe_meituan_read_payload_extracts_fields(self) -> None:
        from app.domain.page_change_alerts.service import observe_meituan_read_payload

        payload = {
            "status": "success",
            "summary": "读取成功",
            "data": {"门店": "x", "曝光量": 1},
            "evidence": {"parser_version": "meituan_data_center_dom_v1"},
        }

        async def _scenario():
            return await observe_meituan_read_payload(
                "data_center_home", payload, webhook_url=ALERT_WEBHOOK
            )

        with self._push_ok() as push:
            push.return_value = type("R", (), {"status": "success", "summary": "ok"})()
            alert = asyncio.run(_scenario())
        self.assertIsNone(alert)

    # ------------------------------------------------------------------
    # 告警卡片
    # ------------------------------------------------------------------

    def test_alert_card_levels_map_to_header_colors(self) -> None:
        from app.domain.page_change_alerts.cards import build_page_health_alert_card

        for level, color in [
            ("hard_fail", "red"),
            ("partial_fail", "orange"),
            ("layout_changed", "blue"),
        ]:
            card = build_page_health_alert_card(
                level=level, platform="ctrip", target="dataset:flow_data", summary="测试"
            )
            self.assertEqual(card["config"], {"wide_screen_mode": True})
            self.assertEqual(card["header"]["template"], color)
            self.assertTrue(card["elements"])
            payload = json.dumps(card, ensure_ascii=False)
            self.assertNotIn("webhook", payload.lower())
            self.assertNotIn("http://127", payload)


if __name__ == "__main__":
    unittest.main()
