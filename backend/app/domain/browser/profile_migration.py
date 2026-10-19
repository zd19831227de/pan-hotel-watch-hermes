from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.settings import get_settings

_PLATFORM_SUBDIRS = ("fliggy-ebooking", "meituan-ebooking", "ctrip-ebooking")
_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "lockfile", ".pan_hotel_watch_launch.lock")


def discover_default_fallout_roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    chrome = _resolve_installed_chrome_dir()
    if chrome is not None:
        candidate = chrome / ".runtime" / "browser-state"
        if candidate.is_dir():
            roots.append(("chrome_application", candidate))
    for playwright_root in _discover_playwright_chrome_win_dirs():
        candidate = playwright_root / ".runtime" / "browser-state"
        if candidate.is_dir():
            roots.append(("playwright_chromium", candidate))
    return roots


def plan_browser_profile_migration(
    *,
    canonical_root: Path | None = None,
    fallout_roots: Sequence[tuple[str, Path]] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return _run_migration(
        canonical_root=canonical_root,
        fallout_roots=fallout_roots,
        dry_run=True if dry_run else False,
    )


def apply_browser_profile_migration(
    *,
    canonical_root: Path | None = None,
    fallout_roots: Sequence[tuple[str, Path]] | None = None,
) -> dict[str, Any]:
    return _run_migration(
        canonical_root=canonical_root,
        fallout_roots=fallout_roots,
        dry_run=False,
    )


def _run_migration(
    *,
    canonical_root: Path | None,
    fallout_roots: Sequence[tuple[str, Path]] | None,
    dry_run: bool,
) -> dict[str, Any]:
    canonical = (canonical_root or Path(get_settings().browser_state_dir)).resolve()
    sources = list(fallout_roots) if fallout_roots is not None else discover_default_fallout_roots()
    items: list[dict[str, str]] = []
    pending = 0
    copied = 0
    skipped = 0
    aborted = 0

    for source_label, fallout_root in sources:
        fallout_root = Path(fallout_root).resolve()
        if not fallout_root.is_dir():
            continue
        if fallout_root == canonical:
            continue
        for platform in _PLATFORM_SUBDIRS:
            platform_dir = fallout_root / platform
            if not platform_dir.is_dir():
                continue
            for account_dir in sorted(path for path in platform_dir.iterdir() if path.is_dir()):
                account_id = account_dir.name
                dest = canonical / platform / account_id
                decision = _decide_account_migration(source=account_dir, destination=dest)
                item = {
                    "source_label": source_label,
                    "platform": platform,
                    "account_id": account_id,
                    "action": decision,
                }
                if decision == "copy":
                    pending += 1
                    if dry_run:
                        items.append(item)
                        continue
                    try:
                        _copy_profile(source=account_dir, destination=dest)
                    except OSError:
                        item["action"] = "abort_copy_failed"
                        aborted += 1
                        items.append(item)
                        continue
                    item["action"] = "copied"
                    copied += 1
                    items.append(item)
                elif decision == "abort_locked":
                    aborted += 1
                    items.append(item)
                else:
                    skipped += 1
                    items.append(item)

    return {
        "dry_run": dry_run,
        "pending_copies": pending if dry_run else 0,
        "copied": copied,
        "skipped": skipped,
        "aborted": aborted,
        "items": items,
    }


def _decide_account_migration(*, source: Path, destination: Path) -> str:
    if not _is_real_profile(source):
        return "skip_source_empty"
    if _has_lock(source) or _has_lock(destination):
        return "abort_locked"
    if _is_real_profile(destination):
        return "skip_destination_populated"
    return "copy"


def _is_real_profile(profile_dir: Path) -> bool:
    if not profile_dir.is_dir():
        return False
    if (profile_dir / "Local State").is_file():
        return True
    return (profile_dir / "Default").is_dir()


def _has_lock(profile_dir: Path) -> bool:
    if not profile_dir.is_dir():
        return False
    return any((profile_dir / name).exists() for name in _LOCK_NAMES)


_COPY_IGNORE_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "BrowserMetrics",
    "Crashpad",
    "Safe Browsing",
    "optimization_guide_hint_cache_store",
    "blob_storage",
    "Service Worker",
}


def _copy_profile(*, source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        # Empty placeholder dirs from mkdir are removable.
        if _is_real_profile(destination):
            raise FileExistsError(str(destination))
        shutil.rmtree(destination)

    def _ignore(directory: str, names: list[str]) -> set[str]:
        del directory
        return {name for name in names if name in _COPY_IGNORE_NAMES}

    shutil.copytree(source, destination, ignore=_ignore)


def _resolve_installed_chrome_dir() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application"),
    ]
    for candidate in candidates:
        if (candidate / "chrome.exe").is_file():
            return candidate
    return None


def _discover_playwright_chrome_win_dirs() -> Iterable[Path]:
    local_app_data = Path.home() / "AppData" / "Local" / "ms-playwright"
    if not local_app_data.is_dir():
        return []
    found: list[Path] = []
    for chromium_dir in sorted(local_app_data.glob("chromium-*")):
        chrome_win = chromium_dir / "chrome-win"
        if chrome_win.is_dir():
            found.append(chrome_win)
    return found
