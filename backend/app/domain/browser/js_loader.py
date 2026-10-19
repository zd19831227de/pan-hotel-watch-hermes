"""Lightweight JS snippet loader.

Loads Playwright page.evaluate() snippets from individual .js files so they
can be edited, linted, and tested independently of Python strings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_JS_DIR = Path(__file__).parent / "js"


@lru_cache(maxsize=32)
def load_js(name: str) -> str:
    """Return the content of a named .js snippet file."""
    return (_JS_DIR / f"{name}.js").read_text(encoding="utf-8")
