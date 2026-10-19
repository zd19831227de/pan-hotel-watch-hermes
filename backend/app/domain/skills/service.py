"""V5 Step 3 — Skill management over the repository skill source.

Hermes loads skills from ``pan-hotel-hermes/skills/<name>/SKILL.md`` (the install
directories under AppData are symbolic links to this source — V4 made it the
single source of truth). This module exposes a secret-free, supervisor-gated
CRUD over that source so an operator can edit skills without touching the
filesystem by hand.

Design:
  - A *skill* is a directory ``<source_dir>/<skill_name>/`` containing a
    ``SKILL.md`` (YAML frontmatter ``name``/``description`` + markdown body) and
    optional ``references/`` and ``templates/`` markdown files.
  - Write operations land ONLY on the repository source; the profile install
    symlinks pick up the changes, so we never write into AppData.
  - Source attribution: a sidecar manifest ``<source_dir>/.skill-sources.json``
    records which skills were created via this API (``manual``). Skills without
    a manifest entry are treated as ``curated`` (the shipped pan-hotel-watch).
    The manifest lives OUTSIDE skill dirs so Hermes only ever sees SKILL.md /
    references / templates.
  - The curated ``pan-hotel-watch`` skill cannot be deleted (it would break
    every profile). Editing it is allowed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_SKILLS_SOURCE_DIR = "pan-hotel-hermes/skills"
CURATED_SKILLS = frozenset({"pan-hotel-watch"})
MANIFEST_FILENAME = ".skill-sources.json"
SKILL_FILENAME = "SKILL.md"
SKILL_SUBDIRS = ("references", "templates")


class SkillError(ValueError):
    """Base error for skill management problems (→ HTTP 422)."""


class SkillNotFoundError(SkillError):
    """Skill directory does not exist (→ HTTP 404)."""


class SkillConflictError(SkillError):
    """Skill already exists / is curated and immutable (→ HTTP 409)."""


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=512)
    content: str = Field(default="")


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=512)
    content: str | None = None


class SkillSummary(BaseModel):
    name: str
    description: str
    source: str
    references: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    size_bytes: int
    updated_at: str | None = None


class SkillDetail(BaseModel):
    name: str
    description: str
    source: str
    content: str
    references: dict[str, str] = Field(default_factory=dict)
    templates: dict[str, str] = Field(default_factory=dict)
    updated_at: str | None = None


def skills_source_dir() -> Path:
    """Resolve the repository skill source directory (env-overridable)."""

    project_root = Path(__file__).resolve().parents[4]
    configured = os.environ.get("PAN_HOTEL_SKILLS_SOURCE_DIR", "").strip()
    base = Path(configured).expanduser() if configured else project_root / DEFAULT_SKILLS_SOURCE_DIR
    return base.resolve()


# --- frontmatter (minimal, dependency-free) ---

_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<frontmatter>.*?)\n---\s*\n?(?P<body>.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a minimal YAML frontmatter (``name``/``description`` only)."""

    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    frontmatter: dict[str, str] = {}
    for raw_line in match.group("frontmatter").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = _unquote_yaml_value(value.strip())
    return frontmatter, match.group("body")


def _unquote_yaml_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _serialize_frontmatter(name: str, description: str) -> str:
    return "---\nname: {}\ndescription: {}\n---\n".format(
        _yaml_scalar(name), _yaml_scalar(description)
    )


def _yaml_scalar(value: str) -> str:
    text = str(value or "")
    needs_quoting = not text or any(ch in text for ch in {":", "#", "'", '"', "\n", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`"})
    if not needs_quoting:
        return text
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def _normalize_skill_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if not SKILL_NAME_PATTERN.fullmatch(normalized):
        raise SkillError("Skill 名称只能包含小写字母、数字、下划线或短横线。")
    return normalized


def _skill_dir(name: str) -> Path:
    return skills_source_dir() / name


def _read_manifest() -> dict[str, dict[str, Any]]:
    manifest_path = skills_source_dir() / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    source_dir = skills_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _source_for(name: str, manifest: dict[str, dict[str, Any]]) -> str:
    entry = manifest.get(name)
    if isinstance(entry, dict):
        return str(entry.get("source") or "curated")
    return "curated" if name in CURATED_SKILLS else "unknown"


def _list_markdown_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        path.name for path in directory.glob("*.md") if path.is_file()
    )


def _read_markdown_files(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.md")):
        if path.is_file():
            result[path.name] = path.read_text(encoding="utf-8")
    return result


def _directory_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _directory_mtime(directory: Path) -> str | None:
    latest: float | None = None
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if latest is None or mtime > latest:
                latest = mtime
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()


async def list_skills() -> list[SkillSummary]:
    source_dir = skills_source_dir()
    manifest = _read_manifest()
    if not source_dir.is_dir():
        return []
    summaries: list[SkillSummary] = []
    for entry in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if not entry.is_dir() or not (entry / SKILL_FILENAME).is_file():
            continue
        frontmatter, _ = _parse_frontmatter((entry / SKILL_FILENAME).read_text(encoding="utf-8"))
        summaries.append(
            SkillSummary(
                name=entry.name,
                description=frontmatter.get("description", ""),
                source=_source_for(entry.name, manifest),
                references=_list_markdown_files(entry / "references"),
                templates=_list_markdown_files(entry / "templates"),
                size_bytes=_directory_size(entry),
                updated_at=_directory_mtime(entry),
            )
        )
    return summaries


async def get_skill(name: str) -> SkillDetail:
    normalized = _normalize_skill_name(name)
    directory = _skill_dir(normalized)
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        raise SkillNotFoundError("未找到该 Skill。")
    frontmatter, body = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    manifest = _read_manifest()
    return SkillDetail(
        name=normalized,
        description=frontmatter.get("description", ""),
        source=_source_for(normalized, manifest),
        content=body,
        references=_read_markdown_files(directory / "references"),
        templates=_read_markdown_files(directory / "templates"),
        updated_at=_directory_mtime(directory),
    )


async def create_skill(request: SkillCreate) -> SkillDetail:
    normalized = _normalize_skill_name(request.name)
    directory = _skill_dir(normalized)
    if directory.exists():
        raise SkillConflictError("该 Skill 已存在。")
    source_dir = skills_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    body = request.content.strip()
    (directory / SKILL_FILENAME).write_text(
        _serialize_frontmatter(normalized, request.description.strip()) + body
        + ("" if body.endswith("\n") else "\n"),
        encoding="utf-8",
    )
    manifest = _read_manifest()
    manifest[normalized] = {
        "source": "manual",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _write_manifest(manifest)
    return await get_skill(normalized)


async def update_skill(name: str, request: SkillUpdate) -> SkillDetail:
    normalized = _normalize_skill_name(name)
    directory = _skill_dir(normalized)
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        raise SkillNotFoundError("未找到该 Skill。")
    frontmatter, body = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    description = request.description if request.description is not None else frontmatter.get("description", "")
    new_body = request.content if request.content is not None else body
    skill_file.write_text(
        _serialize_frontmatter(normalized, description.strip()) + new_body
        + ("" if new_body.endswith("\n") else "\n"),
        encoding="utf-8",
    )
    manifest = _read_manifest()
    if normalized not in manifest:
        # First edit of a curated skill: leave source as curated (do not rebrand).
        pass
    return await get_skill(normalized)


async def delete_skill(name: str) -> dict[str, Any]:
    normalized = _normalize_skill_name(name)
    if normalized in CURATED_SKILLS:
        raise SkillConflictError("核心 Skill 不可删除（会破坏所有 Hermes profile）。")
    directory = _skill_dir(normalized)
    if not directory.is_dir():
        raise SkillNotFoundError("未找到该 Skill。")
    shutil.rmtree(directory)
    manifest = _read_manifest()
    manifest.pop(normalized, None)
    if manifest:
        _write_manifest(manifest)
    else:
        manifest_path = skills_source_dir() / MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest_path.unlink()
    return {"status": "deleted", "name": normalized}
