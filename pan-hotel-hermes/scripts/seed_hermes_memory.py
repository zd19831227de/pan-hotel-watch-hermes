from __future__ import annotations

import os
import sys
from pathlib import Path

from client.memory_seed import seed_hermes_memory_templates


def main() -> int:
    hermes_root = Path(os.environ.get("HERMES_SEED_ROOT", "")).expanduser()
    template_dir = Path(os.environ.get("HERMES_SEED_TEMPLATE_DIR", "")).expanduser()
    if not hermes_root or not template_dir.is_dir():
        print("HERMES_SEED_ROOT and HERMES_SEED_TEMPLATE_DIR must be set", file=sys.stderr)
        return 1
    print(seed_hermes_memory_templates(hermes_root=hermes_root, template_dir=template_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
