from __future__ import annotations

import shutil
from pathlib import Path

MEMORY_FILENAMES = {
    "USER.md": "hermes.USER.template.md",
    "MEMORY.md": "hermes.MEMORY.template.md",
}


def seed_hermes_memory_templates(
    *,
    hermes_root: Path,
    template_dir: Path,
) -> dict[str, str]:
    memories_dir = hermes_root / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for target_name, template_name in MEMORY_FILENAMES.items():
        destination = memories_dir / target_name
        if destination.exists():
            results[target_name] = "skipped_existing"
            continue
        source = template_dir / template_name
        if not source.is_file():
            results[target_name] = "missing_template"
            continue
        shutil.copy2(source, destination)
        results[target_name] = "copied"
    return results
