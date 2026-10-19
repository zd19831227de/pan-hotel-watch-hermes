from __future__ import annotations

import argparse
import json
import sys

from app.domain.browser.profile_migration import (
    apply_browser_profile_migration,
    plan_browser_profile_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate misplaced OTA browser profiles into canonical BROWSER_STATE_DIR."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy pending profiles. Default is dry-run only.",
    )
    args = parser.parse_args(argv)
    result = (
        apply_browser_profile_migration()
        if args.apply
        else plan_browser_profile_migration(dry_run=True)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if int(result.get("aborted") or 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
