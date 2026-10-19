from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-v5-step2-bootstrap-supervisor-key"


class HotelAnalyticsTests(unittest.TestCase):
    """V5 Step 2 — cross-profile analytics with role-scoped, fail-closed access."""

    def setUp(self) -> None:
        self._credentials = {
            "jingli-ai": {"app_id": "cli_jingli", "app_secret": "jingli_secret"},
            "manxin-ai": {"app_id": "cli_manxin", "app_secret": "manxin_secret"},
            "quanji-ai": {"app_id": "cli_quanji", "app_secret": "quanji_secret"},
        }

    def test_analytics_endpoint_requires_supervisor_key(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            anonymous = TestClient(app)

            denied = anonymous.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_any_group", "scope": "all"},
            )
            self.assertEqual(denied.status_code, 401)

    def test_supervisor_all_scope_maps_groups_to_profiles_without_leaking_chat_id(
        self,
    ) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            self._seed_two_hotels(client)
            self._make_supervisor(client, "jingli-ai")

            response = client.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_jingli_room", "scope": "all"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["scope"], "all")
            hotels = {h["profile_key"]: h for h in payload["hotels"]}
            self.assertEqual(set(hotels), {"jingli-ai", "manxin-ai", "quanji-ai"})

            jingli = hotels["jingli-ai"]
            self.assertEqual(len(jingli["competitor_groups"]), 1)
            self.assertEqual(jingli["competitor_groups"][0]["group_key"], "jingli_comp")
            self.assertEqual(len(jingli["ebk_groups"]), 1)
            self.assertEqual(jingli["ebk_groups"][0]["group_key"], "jingli_ebk")
            # quanji has no watch groups → explicit "no data" note, never omitted.
            self.assertEqual(hotels["quanji-ai"]["competitor_groups"], [])
            self.assertEqual(hotels["quanji-ai"]["ebk_groups"], [])
            self.assertIsNotNone(hotels["quanji-ai"]["note"])

            # Raw chat_ids / webhooks / account ids must never appear.
            rendered = response.text
            self.assertNotIn("oc_jingli_room", rendered)
            self.assertNotIn("oc_manxin_room", rendered)
            self.assertNotIn("feishu_chat_id", rendered)
            self.assertNotIn("webhook", rendered)

    def test_operator_is_confined_to_own_profile_and_cannot_query_all(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            self._seed_two_hotels(client)
            # jingli-ai binding has the default operator role (no supervisor assignment).
            denied_all = client.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_jingli_room", "scope": "all"},
            )
            self.assertEqual(denied_all.status_code, 403)
            self.assertIn("supervisor", denied_all.text)

            denied_other = client.get(
                "/api/v1/analytics/hotel-overview",
                params={
                    "chat_id": "oc_jingli_room",
                    "scope": "selected",
                    "profile_keys": ["manxin-ai"],
                },
            )
            self.assertEqual(denied_other.status_code, 403)

            own = client.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_jingli_room", "scope": "own"},
            )
            self.assertEqual(own.status_code, 200)
            payload = own.json()
            self.assertEqual(payload["caller_role"], "operator")
            self.assertEqual(payload["scope"], "own")
            self.assertEqual([h["profile_key"] for h in payload["hotels"]], ["jingli-ai"])

    def test_supervisor_selected_scope_validates_unknown_profile_keys(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            self._seed_two_hotels(client)
            # Make jingli-ai a supervisor so the caller can request selected scope.
            client.post(
                "/api/v1/config/feishu-role-assignments",
                json={
                    "subject_type": "profile",
                    "subject_ref": "jingli-ai",
                    "role": "supervisor",
                    "granted_by": "bootstrap",
                },
            )

            unknown = client.get(
                "/api/v1/analytics/hotel-overview",
                params={
                    "chat_id": "oc_jingli_room",
                    "scope": "selected",
                    "profile_keys": ["jingli-ai", "ghost-ai"],
                },
            )
            self.assertEqual(unknown.status_code, 422)
            self.assertIn("ghost-ai", unknown.text)

            valid = client.get(
                "/api/v1/analytics/hotel-overview",
                params={
                    "chat_id": "oc_jingli_room",
                    "scope": "selected",
                    "profile_keys": ["jingli-ai", "manxin-ai"],
                },
            )
            self.assertEqual(valid.status_code, 200)
            self.assertEqual(valid.json()["scope"], "selected")
            self.assertEqual(
                [h["profile_key"] for h in valid.json()["hotels"]],
                ["jingli-ai", "manxin-ai"],
            )

    def test_unbound_chat_returns_clear_error_instead_of_empty(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            self._seed_two_hotels(client)

            response = client.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_never_bound_room", "scope": "own"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn("未绑定", response.text)

    def test_latest_trusted_price_sample_surfaces_under_own_hotel(self) -> None:
        with _isolated_sqlite_database() as db_url:
            _reload_db_session(db_url)
            app = _reload_app()
            client = _supervisor_client(app)
            self._seed_two_hotels(client)
            self._make_supervisor(client, "jingli-ai")
            self._seed_competitor_run(client)

            response = client.get(
                "/api/v1/analytics/hotel-overview",
                params={"chat_id": "oc_jingli_room", "scope": "all"},
            )
            self.assertEqual(response.status_code, 200)
            jingli = next(
                h for h in response.json()["hotels"] if h["profile_key"] == "jingli-ai"
            )
            trusted = jingli["competitor_groups"][0]["latest_trusted"]
            self.assertIsNotNone(trusted)
            names = [hotel["name"] for hotel in trusted["hotels"]]
            self.assertIn("静里本店", names)

    # --- seed helpers ---

    def _make_supervisor(self, client: TestClient, profile_key: str) -> None:
        client.post(
            "/api/v1/config/feishu-role-assignments",
            json={
                "subject_type": "profile",
                "subject_ref": profile_key,
                "role": "supervisor",
                "granted_by": "bootstrap",
            },
        )

    def _seed_two_hotels(self, client: TestClient) -> None:
        from tests._feishu_deployment_isolation import isolated_feishu_deployment

        with mock.patch.dict(
            os.environ,
            {"FEISHU_APP_CREDENTIALS_JSON": json.dumps(self._credentials)},
            clear=False,
        ):
            # 群绑定创建会同步 Hermes 准入清单；隔离部署路径避免写真实 manifest。
            with isolated_feishu_deployment(
                profiles=("jingli-ai", "manxin-ai", "quanji-ai")
            ):
                for profile_key, chat_id in (
                    ("jingli-ai", "oc_jingli_room"),
                    ("manxin-ai", "oc_manxin_room"),
                    ("quanji-ai", "oc_quanji_room"),
                ):
                    enabled = profile_key != "quanji-ai"
                    client.post(
                        "/api/v1/config/feishu-agent-profiles",
                        json={
                            "profile_key": profile_key,
                            "display_name": f"{profile_key} 管家",
                            "enabled": enabled,
                        },
                    )
                    client.post(
                        "/api/v1/config/feishu-chat-agent-bindings",
                        json={
                            "chat_id": chat_id,
                            "profile_key": profile_key,
                            "display_name": f"{profile_key} 群",
                            "enabled": enabled,
                        },
                    )
                # competitor + EBK watch groups for the two active hotels.
                client.post(
                    "/api/v1/ctrip-ebooking/competitor-watch-groups",
                    json={
                        "group_key": "jingli_comp",
                        "name": "静里竞品",
                        "feishu_chat_id": "oc_jingli_room",
                    },
                )
                client.post(
                    "/api/v1/ctrip-ebooking/competitor-watch-groups",
                    json={
                        "group_key": "manxin_comp",
                        "name": "曼馨竞品",
                        "feishu_chat_id": "oc_manxin_room",
                    },
                )
                client.post(
                    "/api/v1/ctrip-ebooking/data-center-watch-groups",
                    json={
                        "group_key": "jingli_ebk",
                        "name": "静里 EBK",
                        "feishu_chat_id": "oc_jingli_room",
                    },
                )
                client.post(
                    "/api/v1/ctrip-ebooking/data-center-watch-groups",
                    json={
                        "group_key": "manxin_ebk",
                        "name": "曼馨 EBK",
                        "feishu_chat_id": "oc_manxin_room",
                    },
                )

    def _seed_competitor_run(self, client: TestClient) -> None:
        types = importlib.import_module("app.domain.competitor_prices.types")
        repository = importlib.import_module("app.domain.competitor_prices.repository")

        async def scenario() -> None:
            await repository.upsert_competitor_hotel_config(
                platform_account_id="ctrip_public_account",
                watch_group_key="jingli_comp",
                hotel=types.CompetitorHotelConfigIn(
                    role="own",
                    alias="静里本店",
                    hotel_name="静里本店",
                    detail_url="https://hotels.ctrip.com/hotels/detail/?hotelId=200001",
                    sort_order=0,
                ),
            )
            await repository.save_ctrip_competitor_price_result(
                result_status="success",
                summary="ok",
                platform_account_id="ctrip_public_account",
                browser_session_id="browser_public",
                actor_id="test",
                evidence={},
                watch_group_key="jingli_comp",
                data={
                    "captured_at": "2026-08-12T08:00:00",
                    "targets": [
                        {
                            "role": "own",
                            "label": "本店",
                            "url": "https://hotels.ctrip.com/hotels/detail/?hotelId=200001",
                        }
                    ],
                    "snapshots": [
                        {
                            "role": "own",
                            "hotel_name": "静里本店",
                            "status": "success",
                            "requested_url": "https://hotels.ctrip.com/hotels/detail/?hotelId=200001",
                            "date_context": {
                                "check_in": "2026-08-13",
                                "check_out": "2026-08-14",
                            },
                            "room_rates": [
                                {
                                    "source": "room_rate_dom_display_price",
                                    "room_type": "大床房",
                                    "breakfast": "无早",
                                    "availability": "有房",
                                    "price": {"amount": 420, "currency": "CNY"},
                                }
                            ],
                        }
                    ],
                },
            )

        asyncio.run(scenario())


class _isolated_sqlite_database:
    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watch-analytics-test.db"
        self._previous_database_url = os.environ.get("DATABASE_URL")
        self._previous_auto_create = os.environ.get("DATABASE_AUTO_CREATE_TABLES")
        self._previous_llm_key = os.environ.get("LLM_API_KEY")
        self._previous_bootstrap_key = os.environ.get(
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        )
        self._previous_startup_health = os.environ.get(
            "LLM_STARTUP_HEALTH_CHECK_ENABLED"
        )
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        os.environ["DATABASE_AUTO_CREATE_TABLES"] = "true"
        os.environ[
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
        ] = BOOTSTRAP_SUPERVISOR_API_KEY
        os.environ["LLM_STARTUP_HEALTH_CHECK_ENABLED"] = "false"
        os.environ.pop("LLM_API_KEY", None)
        return os.environ["DATABASE_URL"]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._previous_database_url
        if self._previous_auto_create is None:
            os.environ.pop("DATABASE_AUTO_CREATE_TABLES", None)
        else:
            os.environ["DATABASE_AUTO_CREATE_TABLES"] = self._previous_auto_create
        if self._previous_llm_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self._previous_llm_key
        if self._previous_bootstrap_key is None:
            os.environ.pop("PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY", None)
        else:
            os.environ[
                "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY"
            ] = self._previous_bootstrap_key
        if self._previous_startup_health is None:
            os.environ.pop("LLM_STARTUP_HEALTH_CHECK_ENABLED", None)
        else:
            os.environ[
                "LLM_STARTUP_HEALTH_CHECK_ENABLED"
            ] = self._previous_startup_health
        from tests._db_isolation import reset_global_db_session

        reset_global_db_session()
        self._tmp.cleanup()


def _reload_db_session(db_url: str):
    settings = importlib.import_module("app.core.settings")
    settings.get_settings.cache_clear()
    db_session = importlib.import_module("app.db.session")
    db_session.settings = settings.get_settings()
    db_session.engine = db_session.create_async_engine(db_url, pool_pre_ping=True)
    db_session.SessionLocal = db_session.async_sessionmaker(
        db_session.engine,
        expire_on_commit=False,
    )
    config_service = importlib.import_module("app.domain.config.service")
    config_service.reset_runtime_config()
    return db_session


def _reload_app():
    main = importlib.import_module("app.main")
    return importlib.reload(main).create_app()


def _supervisor_client(app) -> TestClient:
    client = TestClient(app)
    client.headers["X-Pan-Hotel-API-Key"] = BOOTSTRAP_SUPERVISOR_API_KEY
    return client


if __name__ == "__main__":
    unittest.main()
