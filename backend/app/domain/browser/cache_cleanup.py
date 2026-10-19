"""Browser-state cache audit & cleanup (V4-Step4).

The Ctrip/Meituan/Fliggy EBK browser profiles live under
``.runtime/browser-state/<platform>-ebooking/<profile_id>/`` as Chromium
persistent contexts. They accumulate disk cache (HTTP cache, GPU/shader caches,
Service Worker caches, metrics, crash dumps) that is safe to clear, but they
also hold **login state** (Cookies, Login Data, Web Data, Local State, Local
Storage, ...) and the occasional **non-cache model/learning data** (screen_ai,
segmentation_platform) that must never be deleted.

This module is the single authority on what is cleanable:

* ``classify_browser_profile_entry`` is a whitelist classifier — only *known*
  cache entries are marked ``cleanable_cache``. Login, model/learning and any
  unknown entry are preserved. The cleanup only ever deletes entries the audit
  marked cleanable; it never deletes by hard-coded path on its own.

The audit produces a structured result (``audit_browser_state``) plus a markdown
report (``render_audit_report``). Cleanup removes the *contents* of cleanable
cache directories (the dir shell is kept so Chromium recreates it cleanly) and
cleanable cache files, under the same cross-process lock that serializes
``browser_sessions.json`` writes.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Whitelists. Only entries listed here as cleanable are ever deleted; every
# other entry (including unknowns) is preserved. This is deliberately
# conservative: "if unsure, keep it".
# ---------------------------------------------------------------------------

# Directory names that are pure browser cache / regenerable Chromium data.
PROFILE_CLEANABLE_DIRS: tuple[str, ...] = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    "GPUPersistentCache",
    "Service Worker",
    "Download Service",
    "DownloadService",
    "component_crx_cache",
    "extensions_crx_cache",
    "BrowserMetrics",
    "Crashpad",
    "Safe Browsing",
    "Safe Browsing Network",
    "SafeBrowsingCookies",
    "Feature Engagement Tracker",
    "AutofillStrikeDatabase",
    "BudgetDatabase",
    "blob_storage",
    "optimization_guide_hint_cache_store",
    "commerce_subscription_db",
    "discounts_db",
    "parcel_tracking_db",
    "Shared Dictionary",
    "Site Characteristics Database",
    "Sessions",
)

# Top-level files (under the profile root or under ``Default/``) that are
# regenerable cache/metrics/history, not login state.
PROFILE_CLEANABLE_FILES: tuple[str, ...] = (
    "BrowserMetrics-spare.pma",
    "CrashpadMetrics-active.pma",
    "CrashpadMetrics.pma",
    "History",
    "History-journal",
    "Favicons",
    "Favicons-journal",
    "Top Sites",
    "Top Sites-journal",
    "Visited Links",
    "Network Action Predictor",
    "Network Action Predictor-journal",
    "Shortcuts",
    "Shortcuts-journal",
    "heavy_ad_intervention_opt_out.db",
    "heavy_ad_intervention_opt_out.db-journal",
    "BrowsingTopicsSiteData",
    "BrowsingTopicsSiteData-journal",
    "BrowsingTopicsState",
    "DIPS",
    "DIPS-journal",
)

# Entries that hold login / auth / session state — never deleted.
PROFILE_LOGIN_STATE_ENTRIES: tuple[str, ...] = (
    "Network",  # Default/Network/ holds Cookies, TransportSecurity, Trust Tokens
    "Local State",
    "Login Data",
    "Login Data For Account",
    "Login Data-journal",
    "Login Data For Account-journal",
    "Web Data",
    "Web Data-journal",
    "Preferences",
    "Secure Preferences",
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "WebStorage",
    "SharedStorage",
    "Sync Data",
    "GCM Store",
    "Affiliation Database",
    "Affiliation Database-journal",
    "ClientCertificates",
    "trusted_vault.pb",
    "Cookies",
    "Cookies-journal",
    "TransportSecurity",
    "Trust Tokens",
    "Trust Tokens-journal",
)

# Entries that are model / learning / distillation data — not browser cache.
PROFILE_NON_CACHE_ENTRIES: tuple[str, ...] = (
    "screen_ai",
    "segmentation_platform",
    "Segmentation Platform",
    "OnDeviceHeadSuggestModel",
    "OnDeviceTailSuggestModel",
    "OptGuideOnDeviceModel",
    "OptGuideOnDeviceClassifierModel",
    "OptimizationGuideModelsManifest",
    "optimization_guide_model_store",
    "WasmTtsEngine",
    "first_party_sets.db",
    "first_party_sets.db-journal",
)

CATEGORY_CLEANABLE = "cleanable_cache"
CATEGORY_LOGIN = "login_state"
CATEGORY_NONCACHE = "non_cache_model"
CATEGORY_UNKNOWN = "unknown_preserve"


def classify_browser_profile_entry(name: str) -> str:
    """Classify a Chromium profile entry name.

    Whitelist-first: known cache -> cleanable; known login/model -> preserved;
    anything unknown -> ``unknown_preserve`` (kept).
    """
    if name in PROFILE_CLEANABLE_DIRS or name in PROFILE_CLEANABLE_FILES:
        return CATEGORY_CLEANABLE
    # Substrings that imply login/auth even if not enumerated verbatim.
    if (
        name in PROFILE_LOGIN_STATE_ENTRIES
        or name == "Network"
        or "Login" in name
        or "Cookie" in name
        or name in ("Preferences", "Secure Preferences", "Local State")
    ):
        return CATEGORY_LOGIN
    if name in PROFILE_NON_CACHE_ENTRIES or "model" in name.lower():
        return CATEGORY_NONCACHE
    return CATEGORY_UNKNOWN


# ---------------------------------------------------------------------------
# Sizing helpers
# ---------------------------------------------------------------------------


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                continue
    return total


def _entry_size(path: Path) -> int:
    try:
        if path.is_dir():
            return _dir_size(path)
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    return 0


# ---------------------------------------------------------------------------
# Audit data model
# ---------------------------------------------------------------------------


@dataclass
class ProfileEntry:
    name: str
    category: str
    path: Path
    bytes_size: int

    # Test helpers use ``.name``.
    @property
    def size(self) -> int:  # noqa: D401 - alias for readability
        return self.bytes_size


@dataclass
class ProfileAudit:
    platform: str
    profile_id: str
    profile_dir: Path
    entries: list[ProfileEntry] = field(default_factory=list)

    @property
    def cleanable(self) -> list[ProfileEntry]:
        return [e for e in self.entries if e.category == CATEGORY_CLEANABLE]

    @property
    def cleanable_bytes(self) -> int:
        return sum(e.bytes_size for e in self.cleanable)

    @property
    def login_state_bytes(self) -> int:
        return sum(e.bytes_size for e in self.entries if e.category == CATEGORY_LOGIN)

    @property
    def non_cache_bytes(self) -> int:
        return sum(e.bytes_size for e in self.entries if e.category == CATEGORY_NONCACHE)

    @property
    def unknown_bytes(self) -> int:
        return sum(e.bytes_size for e in self.entries if e.category == CATEGORY_UNKNOWN)

    @property
    def total_bytes(self) -> int:
        return sum(e.bytes_size for e in self.entries)

    @property
    def cleanable_fraction(self) -> float:
        total = self.total_bytes
        return self.cleanable_bytes / total if total else 0.0


@dataclass
class PlatformAudit:
    platform: str
    profiles: list[ProfileAudit] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(p.total_bytes for p in self.profiles)

    @property
    def cleanable_bytes(self) -> int:
        return sum(p.cleanable_bytes for p in self.profiles)


@dataclass
class BrowserStateAudit:
    root: Path
    platforms: dict[str, PlatformAudit] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(p.total_bytes for p in self.platforms.values())

    @property
    def cleanable_bytes(self) -> int:
        return sum(p.cleanable_bytes for p in self.platforms.values())


def _audit_level(parent: Path) -> list[ProfileEntry]:
    entries: list[ProfileEntry] = []
    try:
        children = sorted(parent.iterdir(), key=lambda p: p.name)
    except OSError:
        return entries
    for child in children:
        category = classify_browser_profile_entry(child.name)
        entries.append(
            ProfileEntry(
                name=child.name,
                category=category,
                path=child,
                bytes_size=_entry_size(child),
            )
        )
    return entries


def audit_profile(profile_dir: Path) -> ProfileAudit:
    """Audit a single Chromium profile dir.

    Classifies entries directly under the profile root and under its
    ``Default/`` subdir (where Cache, Network, Login Data live). Login-state
    and non-cache/model entries are reported but never marked cleanable.
    """
    profile_dir = Path(profile_dir)
    platform = profile_dir.parent.name
    entries: list[ProfileEntry] = []
    # Top-level entries (component_crx_cache, screen_ai, Local State, ...).
    # Skip "Default" here: its size is represented by its children below, so we
    # do not double-count it as an opaque unknown dir.
    for e in _audit_level(profile_dir):
        if e.name == "Default":
            continue
        entries.append(e)
    # Default/ subentries (Cache, Code Cache, Service Worker, Network, Login Data, ...)
    default_dir = profile_dir / "Default"
    if default_dir.is_dir():
        for e in _audit_level(default_dir):
            entries.append(e)
    return ProfileAudit(
        platform=platform,
        profile_id=profile_dir.name,
        profile_dir=profile_dir,
        entries=entries,
    )


def audit_browser_state(root: Path) -> BrowserStateAudit:
    """Audit every ``<root>/<platform>-ebooking/<profile>/`` profile."""
    root = Path(root)
    audit = BrowserStateAudit(root=root)
    if not root.is_dir():
        return audit
    for platform_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        platform_audit = PlatformAudit(platform=platform_dir.name)
        for profile_dir in sorted(p for p in platform_dir.iterdir() if p.is_dir()):
            platform_audit.profiles.append(audit_profile(profile_dir))
        audit.platforms[platform_dir.name] = platform_audit
    return audit


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def render_audit_report(audit: BrowserStateAudit) -> str:
    lines: list[str] = []
    lines.append("# Browser Cache Audit Report (browser-cache-audit)")
    lines.append("")
    lines.append(
        "Audited `.runtime/browser-state/` Chromium persistent profiles. Each "
        "entry is classified as **cleanable_cache** / **login_state** / "
        "**non_cache_model** / **unknown_preserve**. Only `cleanable_cache` is "
        "ever deleted by cleanup; everything else (including unknown) is kept."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Root: `{audit.root}`")
    lines.append(f"- Platforms: {len(audit.platforms)}")
    lines.append(f"- Total size: {_human(audit.total_bytes)}")
    lines.append(f"- Cleanable (audit-confirmed): {_human(audit.cleanable_bytes)}")
    frac = audit.cleanable_bytes / audit.total_bytes if audit.total_bytes else 0.0
    lines.append(f"- Cleanable fraction: {frac * 100:.1f}%")
    lines.append("")
    lines.append("## By platform")
    lines.append("")
    lines.append("| platform | profiles | total | cleanable | cleanable % |")
    lines.append("|----------|----------|-------|-----------|-------------|")
    for name, plat in audit.platforms.items():
        pf = (plat.cleanable_bytes / plat.total_bytes * 100) if plat.total_bytes else 0.0
        lines.append(
            f"| {name} | {len(plat.profiles)} | {_human(plat.total_bytes)} | "
            f"{_human(plat.cleanable_bytes)} | {pf:.1f}% |"
        )
    lines.append("")
    lines.append("## By profile")
    lines.append("")
    lines.append(
        "| platform | profile | total | cleanable | login_state | non_cache | unknown |"
    )
    lines.append("|----------|---------|-------|-----------|-------------|-----------|---------|")
    for plat in audit.platforms.values():
        for prof in plat.profiles:
            lines.append(
                f"| {prof.platform} | {prof.profile_id} | {_human(prof.total_bytes)} | "
                f"{_human(prof.cleanable_bytes)} | {_human(prof.login_state_bytes)} | "
                f"{_human(prof.non_cache_bytes)} | {_human(prof.unknown_bytes)} |"
            )
    lines.append("")
    lines.append("## Preserved (never cleaned)")
    lines.append("")
    lines.append(
        "- **Login state**: `Local State`, `Default/Network` (Cookies), "
        "`Login Data*`, `Web Data*`, `Preferences`, `Secure Preferences`, "
        "`Local Storage`, `Session Storage`, `IndexedDB`, `WebStorage`, "
        "`ClientCertificates`, `trusted_vault.pb`, `Affiliation Database`."
    )
    lines.append(
        "- **Non-cache model/learning**: `screen_ai`, `segmentation_platform`, "
        "`OnDeviceHeadSuggestModel`, `first_party_sets.db`."
    )
    lines.append("- **Unknown entries**: anything not on the cleanable whitelist is kept.")
    lines.append("")
    lines.append("## Cleanable categories (whitelist)")
    lines.append("")
    lines.append(f"- Dirs: {', '.join(PROFILE_CLEANABLE_DIRS)}")
    lines.append(f"- Files: {', '.join(PROFILE_CLEANABLE_FILES)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def _try_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _try_unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _browser_state_lock(root: Path) -> Iterator[None]:
    """Serialize cleanup against browser_sessions.json writers.

    Locks the existing ``<root>/browser_sessions.json.lock`` (the same lock file
    the session store uses) so cleanup never races a session-registry write.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "browser_sessions.json.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        _try_lock(handle)
        try:
            yield
        finally:
            _try_unlock(handle)


@dataclass
class CleanupResult:
    profile_id: str
    platform: str
    bytes_freed: int = 0
    items_removed: int = 0
    dry_run: bool = False


def _remove_dir_contents(path: Path, min_age_days: float | None, *, now: float | None) -> tuple[int, int]:
    """Remove the contents of a cleanable cache dir, keeping the dir shell.

    Returns (bytes_freed, items_removed).
    """
    if now is None:
        now = time.time()
    cutoff = now - min_age_days * 86400 if min_age_days is not None else None
    freed = 0
    removed = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0, 0
    for child in children:
        try:
            if cutoff is not None:
                mtime = child.stat().st_mtime
                if mtime > cutoff:
                    continue
            size = _entry_size(child)
            if child.is_dir() or child.is_file():
                # rmtree for dirs, unlink for files
                if child.is_dir():
                    import shutil

                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                freed += size
                removed += 1
        except OSError:
            continue
    return freed, removed


def cleanup_profile(
    profile_dir: Path,
    *,
    dry_run: bool = False,
    min_age_days: float | None = None,
) -> CleanupResult:
    """Remove audit-cleanable cache from one profile.

    Re-audits the profile (whitelist classifier) so the cleanup never deletes by
    a hard-coded path. Login state, non-cache model/learning data and unknown
    entries are untouched. The cache *directory shells* are kept so Chromium
    recreates them cleanly.
    """
    profile_dir = Path(profile_dir)
    audit = audit_profile(profile_dir)
    result = CleanupResult(
        profile_id=audit.profile_id,
        platform=audit.platform,
        dry_run=dry_run,
    )
    now = time.time()
    for entry in audit.cleanable:
        target = entry.path
        if not target.exists():
            continue
        if dry_run:
            result.bytes_freed += entry.bytes_size
            result.items_removed += 1
            continue
        if target.is_dir():
            freed, removed = _remove_dir_contents(target, min_age_days, now=now)
            result.bytes_freed += freed
            result.items_removed += removed
        else:
            try:
                if min_age_days is not None and target.stat().st_mtime > now - min_age_days * 86400:
                    continue
                size = target.stat().st_size
                target.unlink(missing_ok=True)
                result.bytes_freed += size
                result.items_removed += 1
            except OSError:
                continue
    return result


def cleanup_browser_state(
    root: Path,
    *,
    dry_run: bool = False,
    min_age_days: float | None = None,
) -> dict:
    """Clean audit-confirmed cache across all profiles under ``root``.

    Holds the browser-state lock for the whole pass. Root-level registry files
    (``browser_sessions.json``, its lock) are never touched — cleanup only
    operates inside ``<platform>-ebooking/<profile_id>/``.
    """
    root = Path(root)
    with _browser_state_lock(root):
        audit = audit_browser_state(root)
        results: list[CleanupResult] = []
        for plat in audit.platforms.values():
            for prof in plat.profiles:
                results.append(
                    cleanup_profile(prof.profile_dir, dry_run=dry_run, min_age_days=min_age_days)
                )
    total_freed = sum(r.bytes_freed for r in results)
    return {
        "profiles_cleaned": len([r for r in results if r.items_removed]),
        "bytes_freed": total_freed,
        "bytes_freed_human": _human(total_freed),
        "dry_run": dry_run,
        "results": [
            {
                "platform": r.platform,
                "profile_id": r.profile_id,
                "bytes_freed": r.bytes_freed,
                "items_removed": r.items_removed,
                "dry_run": r.dry_run,
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# Playwright OTA-read screenshots (output/playwright/*.png)
# ---------------------------------------------------------------------------
# Each OTA read snaps a browser screenshot for audit/debugging. They accumulate
# fast (thousands of PNGs, multiple GB over weeks). They are NOT business data
# — login state lives in browser-state, business rows live in the DB. Cleanup
# removes PNGs older than ``retention_days`` by mtime; non-PNG files (notes,
# visual-check JSON, etc.) are always preserved.


SCREENSHOT_GLOB = "*.png"


def audit_playwright_screenshots(artifact_dir: Path, retention_days: int) -> dict:
    """Count and size PNG screenshots; report how many are older than retention.

    Descends into subdirs. Non-PNG files are ignored entirely (never counted,
    never deleted).
    """
    artifact_dir = Path(artifact_dir)
    retention_days = int(retention_days)
    now = time.time()
    cutoff = now - retention_days * 86400 if retention_days > 0 else None
    total_files = 0
    total_bytes = 0
    cleanable_files = 0
    cleanable_bytes = 0
    if artifact_dir.is_dir():
        for p in artifact_dir.rglob(SCREENSHOT_GLOB):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
                mtime = p.stat().st_mtime
            except OSError:
                continue
            total_files += 1
            total_bytes += size
            if cutoff is None or mtime <= cutoff:
                cleanable_files += 1
                cleanable_bytes += size
    return {
        "root": str(artifact_dir),
        "retention_days": retention_days,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "cleanable_files": cleanable_files,
        "cleanable_bytes": cleanable_bytes,
    }


def cleanup_playwright_screenshots(
    artifact_dir: Path,
    retention_days: int,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove PNG screenshots older than ``retention_days``.

    ``retention_days <= 0`` removes ALL PNGs. Non-PNG files are never touched.
    Returns a summary dict; safe to JSON-serialize for the API/frontend.
    """
    artifact_dir = Path(artifact_dir)
    retention_days = int(retention_days)
    now = time.time()
    cutoff = now - retention_days * 86400 if retention_days > 0 else None
    files_removed = 0
    bytes_freed = 0
    if artifact_dir.is_dir():
        for p in artifact_dir.rglob(SCREENSHOT_GLOB):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if cutoff is not None and mtime > cutoff:
                continue  # within retention window, keep
            if dry_run:
                files_removed += 1
                bytes_freed += size
                continue
            try:
                p.unlink()
                files_removed += 1
                bytes_freed += size
            except OSError:
                continue
    return {
        "root": str(artifact_dir),
        "retention_days": retention_days,
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "bytes_freed_human": _human(bytes_freed),
        "dry_run": dry_run,
    }
