"""页面健康/selector 配置加载（JSON 文件化 + mtime 热更新）。"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "page_health_config.json"
DEFAULT_CONSECUTIVE_FAILURES_TO_ALERT = 3
DEFAULT_ALERT_REPEAT_SECONDS = 3600


@dataclass
class PageHealthTargetConfig:
    consecutive_failures_to_alert: int = DEFAULT_CONSECUTIVE_FAILURES_TO_ALERT
    alert_repeat_seconds: int = DEFAULT_ALERT_REPEAT_SECONDS
    # selector 配置文件化（V7 Step 1）：页面结构 expected_terms 的文件覆盖值；
    # 空元组表示未配置，调用方回落代码内置 selector。
    expected_terms: tuple[str, ...] = ()
    # 提交按钮词表文件化（B6）：完整文案白名单优先，热更新同 expected_terms。
    submit_button_labels: tuple[str, ...] = ()
    non_submit_button_labels: tuple[str, ...] = ()
    preferred_submit_label: str = ""
    preview_button_label: str = ""


@dataclass
class PageHealthConfig:
    defaults: PageHealthTargetConfig = field(default_factory=PageHealthTargetConfig)
    targets: dict[str, PageHealthTargetConfig] = field(default_factory=dict)

    def for_target(self, platform: str, target: str) -> PageHealthTargetConfig:
        return self.targets.get(f"{platform}:{target}", self.defaults)


_config_cache: dict[tuple[str, float, int], PageHealthConfig] = {}
_config_lock = threading.Lock()


def load_page_health_config(path: str | Path | None = None) -> PageHealthConfig:
    """加载页面健康配置；按 (path, mtime, size) 缓存实现热更新。"""
    config_path = _resolve_config_path(path)
    try:
        stat = config_path.stat()
        cache_key = (str(config_path), stat.st_mtime, stat.st_size)
    except OSError:
        cache_key = (str(config_path), 0.0, 0)

    with _config_lock:
        cached = _config_cache.get(cache_key)
    if cached is not None:
        return cached

    config = _parse_config(config_path)
    # 简单防膨胀：只保留最近 8 个不同的缓存键
    with _config_lock:
        if len(_config_cache) > 8:
            _config_cache.clear()
        _config_cache[cache_key] = config
    return config


def _resolve_config_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    override = os.environ.get("PAGE_HEALTH_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    return DEFAULT_CONFIG_PATH


def _parse_config(config_path: Path) -> PageHealthConfig:
    defaults = PageHealthTargetConfig()
    targets: dict[str, PageHealthTargetConfig] = {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PageHealthConfig(defaults=defaults, targets=targets)
    if not isinstance(raw, dict):
        return PageHealthConfig(defaults=defaults, targets=targets)

    defaults = _target_config_from(raw.get("defaults"), defaults)
    raw_targets = raw.get("targets")
    if isinstance(raw_targets, dict):
        for key, value in raw_targets.items():
            if not isinstance(key, str) or not key.strip():
                continue
            targets[key.strip()] = _target_config_from(value, defaults)
    return PageHealthConfig(defaults=defaults, targets=targets)


def _target_config_from(data: Any, base: PageHealthTargetConfig) -> PageHealthTargetConfig:
    if not isinstance(data, dict):
        return base
    return PageHealthTargetConfig(
        consecutive_failures_to_alert=_positive_int(
            data.get("consecutive_failures_to_alert"),
            base.consecutive_failures_to_alert,
        ),
        alert_repeat_seconds=_positive_int(
            data.get("alert_repeat_seconds"),
            base.alert_repeat_seconds,
        ),
        expected_terms=_nonempty_str_tuple(data.get("expected_terms")),
        submit_button_labels=_nonempty_str_tuple(data.get("submit_button_labels")),
        non_submit_button_labels=_nonempty_str_tuple(
            data.get("non_submit_button_labels")
        ),
        preferred_submit_label=_single_str(data.get("preferred_submit_label")),
        preview_button_label=_single_str(data.get("preview_button_label")),
    )


def _single_str(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _nonempty_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    )


def resolve_expected_terms(
    platform: str,
    target: str,
    fallback: tuple[str, ...] | list[str],
    *,
    config_path: str | Path | None = None,
) -> tuple[str, ...]:
    """按 target 读取 JSON 配置覆盖的页面结构 expected_terms（selector 文件化）。

    配置了非空 ``expected_terms`` 时返回文件值，否则回落调用方内置 selector；
    文件改动由 ``load_page_health_config`` 的 mtime 缓存自动热更新。
    """
    config = load_page_health_config(config_path).for_target(platform, target)
    if config.expected_terms:
        return config.expected_terms
    return tuple(str(term) for term in fallback)
