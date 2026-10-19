from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

BOOTSTRAP_SUPERVISOR_API_KEY = "task-v5-step3-bootstrap-supervisor-key"


class SkillManagementTests(unittest.TestCase):
    """V5 Step 3 — Skill CRUD over the repository source (isolated temp dir)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.source_dir = Path(self._tmp.name) / "skills"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        # Seed a curated skill that mirrors the real pan-hotel-watch layout.
        curated = self.source_dir / "pan-hotel-watch"
        curated.mkdir(parents=True)
        (curated / "SKILL.md").write_text(
            '---\nname: pan-hotel-watch\ndescription: "Curated hotel skill."\n---\n\n# Body\n\n正文。\n',
            encoding="utf-8",
        )
        (curated / "references").mkdir()
        (curated / "references" / "note.md").write_text("ref body", encoding="utf-8")
        # Set env for the WHOLE test (the skill service reads the source dir at
        # request time, so it must stay set while requests execute, not only
        # during app construction). Save prior values to restore in tearDown.
        self._saved_env: dict[str, str | None] = {}
        for key, value in {
            "PAN_HOTEL_SKILLS_SOURCE_DIR": str(self.source_dir),
            "PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY": BOOTSTRAP_SUPERVISOR_API_KEY,
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "DATABASE_AUTO_CREATE_TABLES": "true",
            "LLM_STARTUP_HEALTH_CHECK_ENABLED": "false",
        }.items():
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value

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

    def _client(self) -> TestClient:
        # test_settings_runtime_paths (which runs just before this module in
        # the full suite) reloads ``app.core.settings``, splitting the
        # ``get_settings`` identity: security.py still holds the *old* function
        # with its own lru_cache. Clear the cache on every module that holds a
        # ``get_settings`` binding so they all re-read the current env.
        for modname in (
            "app.core.settings",
            "app.api.security",
            "app.db.session",
            "app.domain.config.service",
        ):
            module = importlib.import_module(modname)
            get_settings = getattr(module, "get_settings", None)
            if get_settings is not None and hasattr(get_settings, "cache_clear"):
                get_settings.cache_clear()
        settings = importlib.import_module("app.core.settings")
        db_session = importlib.import_module("app.db.session")
        db_session.settings = settings.get_settings()
        db_session.engine = db_session.create_async_engine(
            "sqlite+aiosqlite:///:memory:", pool_pre_ping=True
        )
        db_session.SessionLocal = db_session.async_sessionmaker(
            db_session.engine, expire_on_commit=False
        )
        main = importlib.import_module("app.main")
        app = importlib.reload(main).create_app()
        client = TestClient(app)
        client.headers["X-Pan-Hotel-API-Key"] = BOOTSTRAP_SUPERVISOR_API_KEY
        return client

    def test_skill_endpoints_require_supervisor_key(self) -> None:
        client = self._client()
        anonymous = TestClient(client.app)
        denied = anonymous.get("/api/v1/skills")
        self.assertEqual(denied.status_code, 401)

    def test_list_includes_curated_skill_with_source_curated(self) -> None:
        client = self._client()
        response = client.get("/api/v1/skills")
        self.assertEqual(response.status_code, 200)
        skills = response.json()["skills"]
        self.assertEqual([s["name"] for s in skills], ["pan-hotel-watch"])
        self.assertEqual(skills[0]["source"], "curated")
        self.assertEqual(skills[0]["description"], "Curated hotel skill.")
        self.assertEqual(skills[0]["references"], ["note.md"])
        self.assertGreater(skills[0]["size_bytes"], 0)

    def test_create_edit_get_delete_manual_skill_and_persist_source(self) -> None:
        client = self._client()
        created = client.post(
            "/api/v1/skills",
            json={
                "name": "manual-greeting",
                "description": "A manually added skill.",
                "content": "## 问候\n你好。",
            },
        )
        self.assertEqual(created.status_code, 201)
        skill = created.json()["skill"]
        self.assertEqual(skill["name"], "manual-greeting")
        self.assertEqual(skill["source"], "manual")
        self.assertIn("你好", skill["content"])
        # The SKILL.md on disk must carry the frontmatter + body.
        written = (self.source_dir / "manual-greeting" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: manual-greeting", written)
        self.assertIn("A manually added skill.", written)

        updated = client.put(
            "/api/v1/skills/manual-greeting",
            json={"description": "Updated description.", "content": "## 新内容\n更新。"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["skill"]["description"], "Updated description.")
        self.assertIn("更新", updated.json()["skill"]["content"])

        fetched = client.get("/api/v1/skills/manual-greeting")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["skill"]["source"], "manual")

        deleted = client.delete("/api/v1/skills/manual-greeting")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse((self.source_dir / "manual-greeting").exists())

    def test_curated_skill_cannot_be_deleted_but_can_be_edited(self) -> None:
        client = self._client()
        denied = client.delete("/api/v1/skills/pan-hotel-watch")
        self.assertEqual(denied.status_code, 409)
        self.assertIn("不可删除", denied.text)

        edited = client.put(
            "/api/v1/skills/pan-hotel-watch",
            json={"description": "Edited curated description."},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["skill"]["description"], "Edited curated description.")
        self.assertEqual(edited.json()["skill"]["source"], "curated")

    def test_create_rejects_duplicate_and_invalid_name(self) -> None:
        client = self._client()
        duplicate = client.post(
            "/api/v1/skills",
            json={"name": "pan-hotel-watch", "description": "dup", "content": ""},
        )
        self.assertEqual(duplicate.status_code, 409)

        invalid = client.post(
            "/api/v1/skills",
            json={"name": "Bad Name!", "description": "x", "content": ""},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_get_missing_skill_returns_404(self) -> None:
        client = self._client()
        response = client.get("/api/v1/skills/ghost-skill")
        self.assertEqual(response.status_code, 404)

    def test_source_manifest_lives_outside_skill_dirs(self) -> None:
        client = self._client()
        client.post(
            "/api/v1/skills",
            json={"name": "manual-two", "description": "two", "content": "body"},
        )
        manifest = self.source_dir / ".skill-sources.json"
        self.assertTrue(manifest.is_file())
        # Skill directory itself must contain only SKILL.md (+ optional subdirs),
        # never the manifest, so Hermes sees a clean layout.
        skill_files = [
            path.name for path in (self.source_dir / "manual-two").iterdir()
        ]
        self.assertNotIn(".skill-sources.json", skill_files)


if __name__ == "__main__":
    unittest.main()
