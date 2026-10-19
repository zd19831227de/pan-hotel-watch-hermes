from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client.memory_seed import seed_hermes_memory_templates


class MemorySeedTests(unittest.TestCase):
    def test_copies_templates_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template_dir = root / "config"
            template_dir.mkdir()
            (template_dir / "hermes.USER.template.md").write_text("user seed", encoding="utf-8")
            (template_dir / "hermes.MEMORY.template.md").write_text("memory seed", encoding="utf-8")
            results = seed_hermes_memory_templates(hermes_root=root, template_dir=template_dir)
            self.assertEqual(results["USER.md"], "copied")
            self.assertEqual(results["MEMORY.md"], "copied")
            self.assertEqual((root / "memories" / "USER.md").read_text(encoding="utf-8"), "user seed")

    def test_skips_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template_dir = root / "config"
            template_dir.mkdir()
            memories = root / "memories"
            memories.mkdir()
            (memories / "USER.md").write_text("existing", encoding="utf-8")
            (template_dir / "hermes.USER.template.md").write_text("new", encoding="utf-8")
            (template_dir / "hermes.MEMORY.template.md").write_text("memory seed", encoding="utf-8")
            results = seed_hermes_memory_templates(hermes_root=root, template_dir=template_dir)
            self.assertEqual(results["USER.md"], "skipped_existing")
            self.assertEqual(results["MEMORY.md"], "copied")
            self.assertEqual((memories / "USER.md").read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
