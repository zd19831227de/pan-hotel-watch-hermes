from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


_PROFILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]+$")
_CHAT_ID_RE = re.compile(r"^oc_[A-Za-z0-9_-]+$")
_SUPPORTED_POLICIES = frozenset({"open", "allowlist", "disabled"})


class FeishuProfilesManifestError(ValueError):
    pass


@dataclass(frozen=True)
class FeishuGroupRule:
    chat_id: str
    policy: str
    allowed_users: tuple[str, ...]
    require_mention: bool


@dataclass(frozen=True)
class FeishuProfile:
    profile_key: str
    app_id: str
    app_secret: str
    display_name: str
    require_mention: bool
    soul: str
    group_rules: tuple[FeishuGroupRule, ...]


@dataclass(frozen=True)
class FeishuProfilesManifest:
    profiles: Mapping[str, FeishuProfile]


def load_feishu_profiles_manifest(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
) -> FeishuProfilesManifest:
    manifest_path = Path(path).resolve()
    root = Path(hermes_root).resolve() if hermes_root is not None else manifest_path.parent.parent
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeishuProfilesManifestError(f"Feishu profile manifest not found: {manifest_path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise FeishuProfilesManifestError(f"Feishu profile manifest is not readable: {manifest_path}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        raise FeishuProfilesManifestError("Feishu profile manifest must contain a profiles mapping.")
    raw_profiles = raw["profiles"]
    if not raw_profiles:
        raise FeishuProfilesManifestError("Feishu profile manifest must contain at least one profile.")

    profiles: dict[str, FeishuProfile] = {}
    app_ids: set[str] = set()
    chat_ids: set[str] = set()
    for raw_profile_key, raw_profile in raw_profiles.items():
        profile_key = _required_text(raw_profile_key, "profile key").lower()
        if not _PROFILE_KEY_RE.fullmatch(profile_key):
            raise FeishuProfilesManifestError(f"Invalid profile key: {profile_key!r}.")
        if not isinstance(raw_profile, dict):
            raise FeishuProfilesManifestError(f"Profile {profile_key!r} must be a mapping.")

        app_id = _required_text(raw_profile.get("app_id"), f"{profile_key}.app_id")
        app_secret = _required_text(raw_profile.get("app_secret"), f"{profile_key}.app_secret")
        _reject_line_breaks(app_id, f"{profile_key}.app_id")
        _reject_line_breaks(app_secret, f"{profile_key}.app_secret")
        if app_id in app_ids:
            raise FeishuProfilesManifestError(f"Duplicate Feishu App ID for profile {profile_key!r}.")
        app_ids.add(app_id)

        require_mention = raw_profile.get("require_mention", True)
        if not isinstance(require_mention, bool):
            raise FeishuProfilesManifestError(f"{profile_key}.require_mention must be a boolean.")
        soul = _required_text(raw_profile.get("soul"), f"{profile_key}.soul")
        _validate_soul_path(root, soul, profile_key)

        raw_rules = raw_profile.get("group_rules")
        # 允许空 group_rules：set-credentials 新建的 profile 还没有任何准入群，
        # 强制非空会让 write-backend-env / apply 在配置凭据后立刻失败。
        if not isinstance(raw_rules, list):
            raise FeishuProfilesManifestError(f"{profile_key}.group_rules must be a list.")
        rules: list[FeishuGroupRule] = []
        for index, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                raise FeishuProfilesManifestError(
                    f"{profile_key}.group_rules[{index}] must be a mapping."
                )
            chat_id = _required_text(
                raw_rule.get("chat_id"),
                f"{profile_key}.group_rules[{index}].chat_id",
            )
            _reject_line_breaks(chat_id, f"{profile_key}.group_rules[{index}].chat_id")
            if chat_id in chat_ids:
                raise FeishuProfilesManifestError(f"Duplicate Feishu chat_id: {chat_id!r}.")
            chat_ids.add(chat_id)
            policy = _required_text(
                raw_rule.get("policy"),
                f"{profile_key}.group_rules[{index}].policy",
            ).lower()
            if policy not in _SUPPORTED_POLICIES:
                raise FeishuProfilesManifestError(
                    f"Unsupported policy {policy!r} for {profile_key} chat {chat_id}."
                )
            allowed_users = _string_list(
                raw_rule.get("allowed_users", []),
                f"{profile_key}.group_rules[{index}].allowed_users",
            )
            if policy == "allowlist" and not allowed_users:
                raise FeishuProfilesManifestError(
                    f"{profile_key} chat {chat_id} requires non-empty allowed_users."
                )
            rule_require_mention = raw_rule.get("require_mention", require_mention)
            if not isinstance(rule_require_mention, bool):
                raise FeishuProfilesManifestError(
                    f"{profile_key} chat {chat_id} require_mention must be a boolean."
                )
            rules.append(
                FeishuGroupRule(
                    chat_id=chat_id,
                    policy=policy,
                    allowed_users=allowed_users,
                    require_mention=rule_require_mention,
                )
            )

        profiles[profile_key] = FeishuProfile(
            profile_key=profile_key,
            app_id=app_id,
            app_secret=app_secret,
            display_name=str(raw_profile.get("display_name") or profile_key).strip() or profile_key,
            require_mention=require_mention,
            soul=soul,
            group_rules=tuple(rules),
        )
    return FeishuProfilesManifest(profiles=profiles)


def build_backend_environment(manifest: FeishuProfilesManifest) -> tuple[str, str]:
    credentials = {
        key: {"app_id": profile.app_id, "app_secret": profile.app_secret}
        for key, profile in manifest.profiles.items()
    }
    chat_map = {
        rule.chat_id: key
        for key, profile in manifest.profiles.items()
        for rule in profile.group_rules
    }
    return (
        json.dumps(credentials, ensure_ascii=False, sort_keys=True),
        json.dumps(chat_map, ensure_ascii=False, sort_keys=True),
    )


def build_status_payload(
    manifest: FeishuProfilesManifest,
    *,
    context_loader: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for key, profile in manifest.profiles.items():
        bound_stores: set[str] = set()
        context_statuses: list[str] = []
        if context_loader is not None:
            for rule in profile.group_rules:
                try:
                    context = context_loader(rule.chat_id)
                except Exception:
                    context_statuses.append("unavailable")
                    continue
                context_statuses.append(str(context.get("status") or "unknown"))
                for item in context.get("store_catalog") or []:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("store_label") or "").strip()
                    if label:
                        bound_stores.add(label)
        context_status = "not_queried"
        if context_statuses:
            context_status = (
                context_statuses[0]
                if len(set(context_statuses)) == 1
                else "partial"
            )
        payload.append(
            {
            "profile": key,
            "display_name": profile.display_name,
            "credentials_configured": bool(profile.app_id and profile.app_secret),
            "allowed_group_count": len(profile.group_rules),
            "allowed_user_count": len(
                {user for rule in profile.group_rules for user in rule.allowed_users}
            ),
            "policies": sorted({rule.policy for rule in profile.group_rules}),
            "require_mention": profile.require_mention,
            "soul_configured": bool(profile.soul),
                "bound_stores": sorted(bound_stores),
                "context_status": context_status,
            }
        )
    return payload


def read_profile_admission(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
) -> dict[str, Any]:
    manifest = load_feishu_profiles_manifest(path, hermes_root=hermes_root)
    normalized_profile_key = _normalize_profile_key(profile_key)
    profile = manifest.profiles.get(normalized_profile_key)
    if profile is None:
        raise FeishuProfilesManifestError(
            f"Feishu profile not found: {normalized_profile_key!r}."
        )
    return {
        "profile_key": profile.profile_key,
        "display_name": profile.display_name,
        "groups": [
            {
                "group_key": _admission_group_key(rule.chat_id),
                "chat_id_masked": _mask_identifier(rule.chat_id),
                "policy": rule.policy,
                "allowed_users": list(rule.allowed_users),
                "require_mention": rule.require_mention,
            }
            for rule in profile.group_rules
        ],
    }


def set_profile_credentials(
    path: str | Path,
    *,
    profile_key: str,
    app_id: str,
    app_secret: str,
    display_name: str | None = None,
    hermes_root: str | Path | None = None,
) -> str:
    """Write/update a profile's app credentials in the YAML manifest.

    Creates the profile entry if it doesn't exist (with defaults for
    require_mention/soul/group_rules). Never prints or returns secrets.
    Returns the normalized profile_key on success.
    """

    manifest_path = Path(path).resolve()
    raw = _read_manifest_mapping(manifest_path)
    normalized_key = _normalize_profile_key(profile_key)
    _reject_line_breaks(app_id, f"{normalized_key}.app_id")
    _reject_line_breaks(app_secret, f"{normalized_key}.app_secret")

    profiles = raw["profiles"]
    if not isinstance(profiles.get(normalized_key), dict):
        profiles[normalized_key] = {
            "app_id": "",
            "app_secret": "",
            "display_name": display_name or normalized_key,
            "require_mention": True,
            "soul": f"config/profiles/{normalized_key}/SOUL.md",
            "group_rules": [],
        }
    profile_entry = profiles[normalized_key]
    profile_entry["app_id"] = _required_text(app_id, "app_id")
    profile_entry["app_secret"] = _required_text(app_secret, "app_secret")
    if display_name:
        profile_entry["display_name"] = display_name.strip()
    _atomic_write_text(
        manifest_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )
    _ensure_profile_soul_file(
        manifest_path,
        normalized_key,
        hermes_root=hermes_root,
    )
    return normalized_key


_DEFAULT_SOUL_TEMPLATE = """# {display_name}

你是 {display_name}，服务对应酒店的运营群；也可回答行业/话术等非本店读数问题。

## 证据边界（硬）

1. **不编造**：店名绑定、价格、出租率、订单、点评分数、竞品价等数字与事实，只能来自本轮 pan-hotel-watch MCP；没有就说暂无。
2. **正确调用工具**：经营/竞品/绑定问题按 Skill 工具能力目录选用；参数只取所问；有 `chat_id` 时透传。
3. **真实数据来源**：按当前飞书 `chat_id` 现查后端；禁止从 MEMORY 猜测经营事实。
4. **不答非所问**：只回用户当前问的模块。

## 身份与安全

- 账号能力以当前 `chat_id` 解析到的后端当前绑定及 `connection_mode` 为准；`direct/unknown` 可读数、解释、整理建议，但必须按 `write_not_allowed` 处理，不能自动写价或承诺自动调价。

（本文件为创建 profile 时自动生成的缺省 SOUL，可在 pan-hotel-hermes/config/profiles/{profile_key}/SOUL.md 中按店铺口径定制。）
"""


def _ensure_profile_soul_file(
    manifest_path: Path,
    profile_key: str,
    *,
    hermes_root: str | Path | None = None,
) -> None:
    """Create the default SOUL.md when a freshly created profile has none.

    新 profile 的 manifest 条目引用 config/profiles/<key>/SOUL.md；文件缺失
    会让整个 manifest 校验失败（包括其他 profile 的读写），因此配置凭据时
    兜底生成缺省模板，绝不覆盖已有内容。
    """
    root = Path(hermes_root).resolve() if hermes_root is not None else manifest_path.parent.parent
    soul_path = root / "config" / "profiles" / profile_key / "SOUL.md"
    if soul_path.is_file():
        return
    display_name = profile_key
    raw = _read_manifest_mapping(manifest_path)
    raw_profile = raw["profiles"].get(profile_key)
    if isinstance(raw_profile, dict):
        display_name = str(raw_profile.get("display_name") or profile_key)
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(
        _DEFAULT_SOUL_TEMPLATE.format(
            display_name=display_name,
            profile_key=profile_key,
        ),
        encoding="utf-8",
    )


def update_profile_admission(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
    group_key: str,
    policy: str | None = None,
    allowed_users: Sequence[str] | None = None,
    require_mention: bool | None = None,
) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    root = Path(hermes_root).resolve() if hermes_root is not None else manifest_path.parent.parent
    raw = _read_manifest_mapping(manifest_path)
    normalized_profile_key, raw_rule = _raw_admission_rule(
        raw,
        profile_key=profile_key,
        group_key=group_key,
    )

    if policy is not None:
        normalized_policy = str(policy).strip().lower()
        if normalized_policy not in _SUPPORTED_POLICIES:
            raise FeishuProfilesManifestError(
                f"Unsupported admission policy: {normalized_policy!r}."
            )
        raw_rule["policy"] = normalized_policy
    if allowed_users is not None:
        raw_rule["allowed_users"] = list(
            _normalize_open_ids(allowed_users, field="allowed_users")
        )
    if require_mention is not None:
        if not isinstance(require_mention, bool):
            raise FeishuProfilesManifestError("require_mention must be a boolean.")
        raw_rule["require_mention"] = require_mention

    next_policy = str(raw_rule.get("policy") or "").strip().lower()
    next_allowed_users = _normalize_open_ids(
        raw_rule.get("allowed_users", []),
        field="allowed_users",
    )
    if next_policy == "allowlist" and not next_allowed_users:
        raise FeishuProfilesManifestError(
            f"{normalized_profile_key} admission allowed_users cannot be empty for allowlist policy."
        )
    raw_rule["allowed_users"] = list(next_allowed_users)
    _atomic_write_text(
        manifest_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )
    return read_profile_admission(
        manifest_path,
        hermes_root=root,
        profile_key=normalized_profile_key,
    )


def add_profile_admission_user(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
    group_key: str,
    open_id: str,
) -> dict[str, Any]:
    current = read_profile_admission(
        path,
        hermes_root=hermes_root,
        profile_key=profile_key,
    )
    group = _public_admission_group(current, group_key)
    normalized_open_id = _normalize_open_id(open_id)
    allowed_users = list(group["allowed_users"])
    if normalized_open_id not in allowed_users:
        allowed_users.append(normalized_open_id)
    return update_profile_admission(
        path,
        hermes_root=hermes_root,
        profile_key=profile_key,
        group_key=group_key,
        allowed_users=allowed_users,
    )


def remove_profile_admission_user(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
    group_key: str,
    open_id: str,
) -> dict[str, Any]:
    current = read_profile_admission(
        path,
        hermes_root=hermes_root,
        profile_key=profile_key,
    )
    group = _public_admission_group(current, group_key)
    normalized_open_id = _normalize_open_id(open_id)
    allowed_users = [
        item for item in group["allowed_users"] if item != normalized_open_id
    ]
    if len(allowed_users) == len(group["allowed_users"]):
        raise FeishuProfilesManifestError(
            f"Feishu admission user not found: {normalized_open_id!r}."
        )
    return update_profile_admission(
        path,
        hermes_root=hermes_root,
        profile_key=profile_key,
        group_key=group_key,
        allowed_users=allowed_users,
    )


def add_profile_admission_group(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
    chat_id: str,
    policy: str = "open",
    require_mention: bool = True,
) -> dict[str, Any]:
    """Append a new group admission rule for a chat_id (idempotent).

    Used by the backend when a chat→profile binding is created so the new
    group actually reaches the gateway's admission list. Adding a chat that
    is already admitted under another profile fails: chat IDs are globally
    unique in the manifest.
    """

    manifest_path = Path(path).resolve()
    root = Path(hermes_root).resolve() if hermes_root is not None else manifest_path.parent.parent
    raw = _read_manifest_mapping(manifest_path)
    normalized_profile_key = _normalize_profile_key(profile_key)
    normalized_chat_id = _required_text(chat_id, "chat_id")
    _reject_line_breaks(normalized_chat_id, "chat_id")
    if not _CHAT_ID_RE.fullmatch(normalized_chat_id):
        raise FeishuProfilesManifestError(
            "chat_id must be a valid Feishu oc_ identifier."
        )
    normalized_policy = str(policy or "open").strip().lower()
    if normalized_policy not in _SUPPORTED_POLICIES:
        raise FeishuProfilesManifestError(
            f"Unsupported admission policy: {normalized_policy!r}."
        )
    if not isinstance(require_mention, bool):
        raise FeishuProfilesManifestError("require_mention must be a boolean.")

    for other_key, other_profile in raw["profiles"].items():
        if not isinstance(other_profile, dict):
            continue
        other_rules = other_profile.get("group_rules")
        if not isinstance(other_rules, list):
            continue
        for raw_rule in other_rules:
            if not isinstance(raw_rule, dict):
                continue
            existing_chat_id = str(raw_rule.get("chat_id") or "").strip()
            if existing_chat_id != normalized_chat_id:
                continue
            if str(other_key).strip().lower() == normalized_profile_key:
                return read_profile_admission(
                    manifest_path,
                    hermes_root=root,
                    profile_key=normalized_profile_key,
                )
            raise FeishuProfilesManifestError(
                f"Feishu chat_id already admitted for profile {str(other_key)!r}."
            )

    raw_profile = raw["profiles"].get(normalized_profile_key)
    if not isinstance(raw_profile, dict):
        raise FeishuProfilesManifestError(
            f"Feishu profile not found: {normalized_profile_key!r}."
        )
    raw_rules = raw_profile.get("group_rules")
    if not isinstance(raw_rules, list):
        raise FeishuProfilesManifestError(
            f"{normalized_profile_key}.group_rules must be a list."
        )
    raw_rules.append(
        {
            "chat_id": normalized_chat_id,
            "policy": normalized_policy,
            "allowed_users": [],
            "require_mention": require_mention,
        }
    )
    _atomic_write_text(
        manifest_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )
    return read_profile_admission(
        manifest_path,
        hermes_root=root,
        profile_key=normalized_profile_key,
    )


def remove_profile_admission_group(
    path: str | Path,
    *,
    hermes_root: str | Path | None = None,
    profile_key: str,
    chat_id: str,
) -> dict[str, Any]:
    """Remove a chat's admission rule from a profile (idempotent no-op if absent).

    Removing the last group leaves the profile with zero admitted chats (the
    gateway then rejects every message for it) which is a valid state: empty
    group_rules is what set-credentials creates for a brand-new profile.
    """

    manifest_path = Path(path).resolve()
    root = Path(hermes_root).resolve() if hermes_root is not None else manifest_path.parent.parent
    raw = _read_manifest_mapping(manifest_path)
    normalized_profile_key = _normalize_profile_key(profile_key)
    normalized_chat_id = _required_text(chat_id, "chat_id")

    raw_profile = raw["profiles"].get(normalized_profile_key)
    if not isinstance(raw_profile, dict):
        raise FeishuProfilesManifestError(
            f"Feishu profile not found: {normalized_profile_key!r}."
        )
    raw_rules = raw_profile.get("group_rules")
    if not isinstance(raw_rules, list):
        raise FeishuProfilesManifestError(
            f"{normalized_profile_key}.group_rules must be a list."
        )
    remaining_rules = [
        raw_rule
        for raw_rule in raw_rules
        if not (
            isinstance(raw_rule, dict)
            and str(raw_rule.get("chat_id") or "").strip() == normalized_chat_id
        )
    ]
    if len(remaining_rules) == len(raw_rules):
        return read_profile_admission(
            manifest_path,
            hermes_root=root,
            profile_key=normalized_profile_key,
        )
    raw_profile["group_rules"] = remaining_rules
    _atomic_write_text(
        manifest_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )
    return read_profile_admission(
        manifest_path,
        hermes_root=root,
        profile_key=normalized_profile_key,
    )


def apply_profile_configuration(
    profile: FeishuProfile,
    profile_root: str | Path,
    *,
    service_token: str | None = None,
    supervisor_key: str | None = None,
    hermes_root: str | Path | None = None,
    python_exe: str | Path | None = None,
    watch_api_base_url: str | None = None,
) -> None:
    """Deploy one profile's full Hermes gateway runtime.

    传统上这部分由 register-hermes.ps1 手工执行（MCP 块、插件/技能 junction、
    SOUL 组合、记忆模板），前端链路只写准入 stub，导致新 profile 缺 MCP/插件、
    时区/卡片格式与首个 profile 漂移。现在 apply 是唯一的部署收口：带
    ``hermes_root`` 时幂等地把 profile 运行环境规整到与仓库一致的形态，
    已有部署重复执行只补缺、不破坏本地手工安装。
    """

    root = Path(profile_root)
    root.mkdir(parents=True, exist_ok=True)
    repo = Path(hermes_root).resolve() if hermes_root is not None else None
    chat_ids = [rule.chat_id for rule in profile.group_rules]
    allowed_users = sorted({user for rule in profile.group_rules for user in rule.allowed_users})
    # 多群 profile 无法从清单推断 home channel；空推断值不得覆盖运维手工设定的
    # FEISHU_HOME_CHANNEL（否则网关 send_message 兜底投递报 "No home channel is
    # set for Feishu"，2026-09-18 迁移实测回归）。
    home_channel = chat_ids[0] if len(chat_ids) == 1 else ""
    if not home_channel:
        existing_env = root / ".env"
        if existing_env.exists():
            for line in existing_env.read_text(encoding="utf-8-sig").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "FEISHU_HOME_CHANNEL" and value.strip():
                    home_channel = value.strip()
                    break
    env_values = {
        "FEISHU_APP_ID": profile.app_id,
        "FEISHU_APP_SECRET": profile.app_secret,
        "FEISHU_DOMAIN": "feishu",
        "FEISHU_CONNECTION_MODE": "websocket",
        "FEISHU_GROUP_POLICY": "disabled",
        "FEISHU_ALLOWED_CHATS": ",".join(chat_ids),
        "FEISHU_ALLOWED_USERS": ",".join(allowed_users),
        "FEISHU_REQUIRE_MENTION": str(profile.require_mention).lower(),
        "FEISHU_HOME_CHANNEL": home_channel,
    }
    if str(service_token or "").strip():
        env_values["WATCH_API_SHARED_SECRET"] = str(service_token).strip()
    # supervisor key（来自后端 PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY）让 MCP 的
    # 跨店分析 / RAG 工具能通过后端 supervisor 鉴权；缺失时这些工具会优雅降级。
    if str(supervisor_key or "").strip():
        env_values["WATCH_API_SUPERVISOR_KEY"] = str(supervisor_key).strip()
    _merge_dotenv(root / ".env", env_values)

    config_path = root / "config.yaml"
    config = _read_yaml_mapping(config_path)
    platforms = _mapping_child(config, "platforms")
    feishu = _mapping_child(platforms, "feishu")
    extra = _mapping_child(feishu, "extra")
    extra["default_group_policy"] = "disabled"
    extra["group_rules"] = {
        rule.chat_id: {
            "policy": rule.policy,
            **({"allowlist": list(rule.allowed_users)} if rule.allowed_users else {}),
            "require_mention": rule.require_mention,
        }
        for rule in profile.group_rules
    }
    if repo is not None:
        _reconcile_profile_runtime_config(
            config,
            repo,
            profile_root=root,
            python_exe=python_exe,
            watch_api_base_url=watch_api_base_url,
        )
    _atomic_write_text(
        config_path,
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    )
    if repo is not None:
        _compose_profile_soul_file(repo, profile, root)
        _link_repo_runtime_dirs(repo, root)
        _seed_profile_memory_files(repo, root)


_MCP_SERVER_NAME = "pan-hotel-watch"
_FEISHU_SESSION_PLUGIN = "pan-hotel-feishu-session"
_PROFILE_TIMEZONE = "Asia/Shanghai"
_MCP_TIMEOUT_SECONDS = 300
_DEFAULT_WATCH_API_PORT = 8100


def _posix_path(path: Path) -> str:
    # Hermes config.yaml 使用正斜杠路径，避免 Windows 反斜杠转义歧义。
    return str(path).replace("\\", "/")


def _resolve_mcp_python_exe(
    profile_root: Path,
    python_exe: str | Path | None,
) -> Path:
    if python_exe is not None:
        return Path(python_exe)
    # profile_root 形如 <hermes_base>/profiles/<key>；MCP 子进程约定用
    # hermes-agent venv（pan_hotel_mcp 已 editable 安装在里面）。
    candidates: list[Path] = []
    if profile_root.parent.name == "profiles":
        agent_venv = profile_root.parent.parent / "hermes-agent" / "venv"
        candidates.append(agent_venv / "Scripts" / "python.exe")
        candidates.append(agent_venv / "bin" / "python")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FeishuProfilesManifestError(
        "未找到 Hermes agent venv 的 python，无法写入 pan-hotel-watch MCP 配置；"
        "请通过 --python-exe 显式指定。"
    )


def _reconcile_profile_runtime_config(
    config: dict[str, Any],
    repo: Path,
    *,
    profile_root: Path,
    python_exe: str | Path | None,
    watch_api_base_url: str | None,
) -> None:
    """Idempotently pin the managed runtime keys of a profile config.yaml.

    只管 MCP/插件/语言/时区/压缩等运行键；model/providers 归后端
    sync_hermes_model_configs 管，这里绝不触碰。
    """

    mcp_python = _resolve_mcp_python_exe(profile_root, python_exe)
    base_url = str(watch_api_base_url or "").strip() or (
        f"http://127.0.0.1:{_DEFAULT_WATCH_API_PORT}"
    )
    servers = _mapping_child(config, "mcp_servers")
    server = servers.get(_MCP_SERVER_NAME)
    if server is None:
        server = {}
        servers[_MCP_SERVER_NAME] = server
    if not isinstance(server, dict):
        raise FeishuProfilesManifestError(
            f"Hermes config 字段 mcp_servers.{_MCP_SERVER_NAME} 必须是映射。"
        )
    canonical_env = {
        "WATCH_API_BASE_URL": base_url,
        "WATCH_API_BROWSER_TIMEOUT": str(_MCP_TIMEOUT_SECONDS),
        # ${VAR} 占位符由 profile .env 插值解析；MCP 子进程环境不透传网关变量。
        "WATCH_API_SHARED_SECRET": "${WATCH_API_SHARED_SECRET}",
        "WATCH_API_SUPERVISOR_KEY": "${WATCH_API_SUPERVISOR_KEY}",
        "PYTHONPATH": _posix_path(repo),
        "WATCH_DEFAULT_EBK_GROUP": "ctrip_ebooking_data_center",
        "WATCH_DEFAULT_COMPETITOR_GROUP": "default_competitor_watch",
    }
    server["command"] = _posix_path(mcp_python)
    server["args"] = ["-m", "pan_hotel_mcp"]
    server["cwd"] = _posix_path(repo)
    server["timeout"] = _MCP_TIMEOUT_SECONDS
    server_env = server.get("env")
    server_env = dict(server_env) if isinstance(server_env, dict) else {}
    server_env.update(canonical_env)
    server["env"] = server_env

    plugins = _mapping_child(config, "plugins")
    enabled = plugins.get("enabled")
    enabled_list = list(enabled) if isinstance(enabled, list) else []
    if _FEISHU_SESSION_PLUGIN not in enabled_list:
        enabled_list.append(_FEISHU_SESSION_PLUGIN)
    plugins["enabled"] = enabled_list

    display = _mapping_child(config, "display")
    display.setdefault("language", "zh")
    display.setdefault("tool_progress", False)

    config.setdefault("group_sessions_per_user", True)
    compression = _mapping_child(config, "compression")
    compression.setdefault("enabled", True)
    compression.setdefault("threshold", 0.45)
    compression.setdefault("protect_last_n", 24)
    # 时区显式固定为北京时间：留空会回落到服务器本地时区，卡片/定时时间漂移。
    if not str(config.get("timezone") or "").strip():
        config["timezone"] = _PROFILE_TIMEZONE


def _compose_profile_soul_file(repo: Path, profile: FeishuProfile, profile_root: Path) -> None:
    """Rewrite the profile-home SOUL.md as base SOUL + profile-specific SOUL.

    与 register-hermes.ps1 的组合规则一致：仓库是唯一事实源，profile_home 的
    SOUL 每次部署重写（本地直接改 profile SOUL 会被下次部署覆盖，定制请改
    仓库 config/profiles/<key>/SOUL.md）。
    """

    base_path = repo / "config" / "hermes.SOUL.md"
    profile_soul_path = repo / profile.soul
    parts: list[str] = []
    if base_path.is_file():
        parts.append(_strip_bom(base_path.read_text(encoding="utf-8-sig")).strip())
    profile_soul = _strip_bom(profile_soul_path.read_text(encoding="utf-8-sig")).strip()
    if parts:
        composed = (
            parts[0]
            + "\n\n---\n\n## profile-specific SOUL\n\n"
            + profile_soul
            + "\n"
        )
    else:
        composed = profile_soul + "\n"
    soul_path = profile_root / "SOUL.md"
    soul_path.write_text(composed, encoding="utf-8", newline="\n")


def _strip_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def _link_repo_runtime_dirs(repo: Path, profile_root: Path) -> None:
    """Junction the repo plugin and skills into the profile home.

    与 register-hermes.ps1 的单源部署等价：junction 不需要管理员权限且
    os.walk/rglob 都会跟随；真实目录（手工安装）保留不动；链接失败退化为拷贝。
    """

    _link_repo_dir(
        repo / "hermes_plugin" / _FEISHU_SESSION_PLUGIN,
        profile_root / "plugins" / _FEISHU_SESSION_PLUGIN,
    )
    skills_root = repo / "skills"
    if not skills_root.is_dir():
        return
    for skill_dir in sorted(skills_root.iterdir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        destination = profile_root / "skills" / skill_dir.name
        _link_repo_dir(skill_dir, destination)


def _link_repo_dir(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    if destination.exists() or destination.is_symlink():
        # 旧单源迁移留下的 .pre-single-source 备份会让 Hermes 看到
        # 重名技能（SKILL.md frontmatter 相同），链接就位后即清理。
        if destination.resolve() == source.resolve():
            stale_backup = destination.parent / f"{destination.name}.pre-single-source"
            if stale_backup.exists():
                shutil.rmtree(stale_backup, ignore_errors=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    linked = False
    if os.name == "nt":
        try:
            import _winapi

            _winapi.CreateJunction(str(source), str(destination))
            linked = True
        except (OSError, ImportError):
            linked = False
    if not linked:
        try:
            os.symlink(str(source), str(destination), target_is_directory=True)
            linked = True
        except (OSError, NotImplementedError):
            linked = False
    if not linked:
        shutil.copytree(source, destination)
        for cache in destination.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def _seed_profile_memory_files(repo: Path, profile_root: Path) -> None:
    memories_dir = profile_root / "memories"
    templates = {
        "MEMORY.md": repo / "config" / "hermes.MEMORY.template.md",
        "USER.md": repo / "config" / "hermes.USER.template.md",
    }
    for target_name, template in templates.items():
        destination = memories_dir / target_name
        if destination.exists() or not template.is_file():
            continue
        memories_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, destination)


def remove_profile_entry(
    path: str | Path,
    *,
    profile_key: str,
) -> str:
    """Drop one profile from the YAML manifest (idempotent, no file deletes).

    删除 AI 助手时回收 manifest 条目，让下一次 write-backend-env 不再把
    已删助手的凭据写回后端 .env。绝不删除 SOUL 文件或 profile 运行目录
    （会话/记忆数据留给管理员处置）。最后一个 profile 拒绝删除：空 manifest
    会让所有后续 validate/apply 失败。
    """

    manifest_path = Path(path).resolve()
    normalized_profile_key = _normalize_profile_key(profile_key)
    raw = _read_manifest_mapping(manifest_path)
    profiles = raw["profiles"]
    if normalized_profile_key not in profiles:
        return normalized_profile_key
    if len(profiles) <= 1:
        raise FeishuProfilesManifestError(
            "不能删除 manifest 中最后一个 Feishu profile；如确需清空请人工处理 "
            "feishu-profiles.yaml 与后端 .env。"
        )
    profiles.pop(normalized_profile_key)
    _atomic_write_text(
        manifest_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )
    return normalized_profile_key


def write_backend_environment(
    manifest: FeishuProfilesManifest,
    dotenv_path: str | Path,
) -> str:
    credentials_json, chat_map_json = build_backend_environment(manifest)
    path = Path(dotenv_path)
    existing = _read_dotenv(path) if path.is_file() else {}
    service_token = str(existing.get("WATCH_API_SHARED_SECRET") or "").strip()
    if not service_token:
        service_token = secrets.token_urlsafe(32)
    # PAN_HOTEL_ENFORCE_API_AUTH 是运维的显式选择（源机曾长期为 false；MCP 侧
    # 曾依赖该值免鉴权调用后端）。迁移/重跑时静默翻转为 true 会让所有未携带
    # 服务令牌的调用方 401。保留已有值，仅在缺失时按安全默认开启。
    enforce_api_auth = str(existing.get("PAN_HOTEL_ENFORCE_API_AUTH") or "").strip()
    if not enforce_api_auth:
        enforce_api_auth = "true"
    _merge_dotenv(
        path,
        {
            "FEISHU_APP_CREDENTIALS_JSON": credentials_json,
            "FEISHU_CHAT_APP_MAP_JSON": chat_map_json,
            "PAN_HOTEL_ENFORCE_API_AUTH": enforce_api_auth,
            "WATCH_API_SHARED_SECRET": service_token,
        },
    )
    return service_token


def migrate_existing_profiles(
    *,
    template_path: str | Path,
    destination_path: str | Path,
    hermes_base: str | Path,
    hermes_root: str | Path,
    overwrite: bool = False,
) -> FeishuProfilesManifest:
    destination = Path(destination_path)
    if destination.exists() and not overwrite:
        raise FeishuProfilesManifestError(
            f"Feishu profile manifest already exists: {destination.resolve()}"
        )
    template = load_feishu_profiles_manifest(template_path, hermes_root=hermes_root)
    base = Path(hermes_base)
    raw_profiles: dict[str, Any] = {}
    for profile_key, template_profile in template.profiles.items():
        env_path = base / "profiles" / profile_key / ".env"
        values = _read_dotenv(env_path)
        app_id = _required_text(values.get("FEISHU_APP_ID"), f"{profile_key}.FEISHU_APP_ID")
        app_secret = _required_text(
            values.get("FEISHU_APP_SECRET"),
            f"{profile_key}.FEISHU_APP_SECRET",
        )
        policy = str(values.get("FEISHU_GROUP_POLICY") or "allowlist").strip().lower()
        if policy not in _SUPPORTED_POLICIES:
            raise FeishuProfilesManifestError(
                f"Unsupported FEISHU_GROUP_POLICY for profile {profile_key!r}."
            )
        chat_ids = _split_env_list(values.get("FEISHU_ALLOWED_CHATS", ""))
        if not chat_ids:
            raise FeishuProfilesManifestError(
                f"{profile_key}.FEISHU_ALLOWED_CHATS must contain at least one chat."
            )
        allowed_users = _split_env_list(values.get("FEISHU_ALLOWED_USERS", ""))
        require_mention = _parse_env_bool(
            values.get("FEISHU_REQUIRE_MENTION"),
            default=template_profile.require_mention,
            field=f"{profile_key}.FEISHU_REQUIRE_MENTION",
        )
        raw_profiles[profile_key] = {
            "app_id": app_id,
            "app_secret": app_secret,
            "group_rules": [
                {
                    "chat_id": chat_id,
                    "policy": policy,
                    "allowed_users": list(allowed_users),
                }
                for chat_id in chat_ids
            ],
            "require_mention": require_mention,
            "display_name": template_profile.display_name,
            "soul": template_profile.soul,
        }
    _atomic_write_text(
        destination,
        yaml.safe_dump(
            {"profiles": raw_profiles},
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    return load_feishu_profiles_manifest(destination, hermes_root=hermes_root)


def _normalize_profile_key(value: str) -> str:
    profile_key = _required_text(value, "profile key").lower()
    if not _PROFILE_KEY_RE.fullmatch(profile_key):
        raise FeishuProfilesManifestError(f"Invalid profile key: {profile_key!r}.")
    return profile_key


def _admission_group_key(chat_id: str) -> str:
    return hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:16]


def _mask_identifier(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) <= 8:
        return "***"
    return f"{normalized[:4]}...{normalized[-4:]}"


def _read_manifest_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeishuProfilesManifestError(f"Feishu profile manifest not found: {path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise FeishuProfilesManifestError(f"Feishu profile manifest is not readable: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        raise FeishuProfilesManifestError("Feishu profile manifest must contain a profiles mapping.")
    return raw


def _raw_admission_rule(
    raw: dict[str, Any],
    *,
    profile_key: str,
    group_key: str,
) -> tuple[str, dict[str, Any]]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    raw_profile = raw["profiles"].get(normalized_profile_key)
    if not isinstance(raw_profile, dict):
        raise FeishuProfilesManifestError(
            f"Feishu profile not found: {normalized_profile_key!r}."
        )
    raw_rules = raw_profile.get("group_rules")
    if not isinstance(raw_rules, list):
        raise FeishuProfilesManifestError(
            f"{normalized_profile_key}.group_rules must be a list."
        )
    normalized_group_key = _required_text(group_key, "group key").lower()
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue
        chat_id = str(raw_rule.get("chat_id") or "").strip()
        if chat_id and _admission_group_key(chat_id) == normalized_group_key:
            return normalized_profile_key, raw_rule
    raise FeishuProfilesManifestError(
        f"Feishu admission group not found for profile {normalized_profile_key!r}."
    )


def _normalize_open_id(value: str) -> str:
    normalized = _required_text(value, "open_id")
    _reject_line_breaks(normalized, "open_id")
    if not _OPEN_ID_RE.fullmatch(normalized):
        raise FeishuProfilesManifestError("open_id must be a valid Feishu ou_ identifier.")
    return normalized


def _normalize_open_ids(values: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise FeishuProfilesManifestError(f"{field} must be a list.")
    result: list[str] = []
    for value in values:
        normalized = _normalize_open_id(str(value))
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _public_admission_group(payload: dict[str, Any], group_key: str) -> dict[str, Any]:
    normalized_group_key = _required_text(group_key, "group key").lower()
    for group in payload.get("groups", []):
        if isinstance(group, dict) and group.get("group_key") == normalized_group_key:
            return group
    raise FeishuProfilesManifestError("Feishu admission group not found.")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FeishuProfilesManifestError(f"{field} is required.")
    return text


def _reject_line_breaks(value: str, field: str) -> None:
    if "\n" in value or "\r" in value:
        raise FeishuProfilesManifestError(f"{field} must not contain line breaks.")


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FeishuProfilesManifestError(f"{field} must be a list.")
    result: list[str] = []
    for item in value:
        text = _required_text(item, field)
        _reject_line_breaks(text, field)
        if text not in result:
            result.append(text)
    return tuple(result)


def _validate_soul_path(root: Path, soul: str, profile_key: str) -> None:
    candidate = (root / soul).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FeishuProfilesManifestError(f"{profile_key}.soul must stay inside the Hermes root.") from exc
    if not candidate.is_file():
        raise FeishuProfilesManifestError(f"SOUL file not found for profile {profile_key!r}.")


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise FeishuProfilesManifestError(f"Hermes config is not readable: {path}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FeishuProfilesManifestError(f"Hermes config must contain a YAML mapping: {path}")
    return value


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FeishuProfilesManifestError(f"Hermes profile env not found: {path}")
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _split_env_list(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in re.split(r"[,;\s]+", str(value or "")):
        normalized = item.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _parse_env_bool(value: str | None, *, default: bool, field: str) -> bool:
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise FeishuProfilesManifestError(f"{field} must be true or false.")


def _mapping_child(parent: dict[str, Any], key: str) -> dict[str, Any]:
    current = parent.get(key)
    if current is None:
        current = {}
        parent[key] = current
    if not isinstance(current, dict):
        raise FeishuProfilesManifestError(f"Hermes config field {key!r} must be a mapping.")
    return current


def _merge_dotenv(path: Path, values: Mapping[str, str]) -> None:
    existing = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    remaining = dict(values)
    managed_keys = set(remaining)
    result: list[str] = []
    for line in existing:
        key = line.partition("=")[0].strip() if "=" in line else ""
        if key in remaining:
            result.append(f"{key}={remaining.pop(key)}")
        elif key not in managed_keys:
            result.append(line)
    result.extend(f"{key}={value}" for key, value in remaining.items())
    _atomic_write_text(path, "\n".join(result).rstrip() + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        _secure_managed_file(temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _secure_managed_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return
    username = str(os.environ.get("USERNAME") or getpass.getuser()).strip()
    domain = str(os.environ.get("USERDOMAIN") or "").strip()
    identity = f"{domain}\\{username}" if domain else username
    # icacls 在中文 Windows 输出 GBK；UTF-8 模式下解码会崩 reader 线程。
    subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:(F)",
            "*S-1-5-18:(F)",
            "*S-1-5-32-544:(F)",
        ],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
        shell=False,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and apply Feishu profile manifests.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--hermes-root", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    describe_parser = subparsers.add_parser("describe")
    describe_parser.add_argument("--context-api-base")
    describe_parser.add_argument("--context-timeout", type=float, default=3.0)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--profile", required=True)
    apply_parser.add_argument("--profile-root", required=True, type=Path)
    apply_parser.add_argument("--backend-dotenv", type=Path)
    apply_parser.add_argument("--python-exe", type=Path)
    apply_parser.add_argument("--watch-api-base-url")
    backend_parser = subparsers.add_parser("write-backend-env")
    backend_parser.add_argument("--dotenv", required=True, type=Path)
    admission_get_parser = subparsers.add_parser("admission-get")
    admission_get_parser.add_argument("--profile", required=True)
    admission_patch_parser = subparsers.add_parser("admission-patch")
    admission_patch_parser.add_argument("--profile", required=True)
    admission_patch_parser.add_argument("--group-key", required=True)
    admission_patch_parser.add_argument("--policy", choices=sorted(_SUPPORTED_POLICIES))
    admission_patch_parser.add_argument(
        "--require-mention",
        choices=("true", "false"),
    )
    admission_patch_parser.add_argument("--allowed-user", action="append")
    admission_patch_parser.add_argument("--clear-allowed-users", action="store_true")
    admission_add_parser = subparsers.add_parser("admission-add-user")
    admission_add_parser.add_argument("--profile", required=True)
    admission_add_parser.add_argument("--group-key", required=True)
    admission_add_parser.add_argument("--open-id", required=True)
    admission_remove_parser = subparsers.add_parser("admission-remove-user")
    admission_remove_parser.add_argument("--profile", required=True)
    admission_remove_parser.add_argument("--group-key", required=True)
    admission_remove_parser.add_argument("--open-id", required=True)
    admission_add_group_parser = subparsers.add_parser("admission-add-group")
    admission_add_group_parser.add_argument("--profile", required=True)
    admission_add_group_parser.add_argument("--chat-id", required=True)
    admission_add_group_parser.add_argument(
        "--policy",
        choices=sorted(_SUPPORTED_POLICIES),
        default="open",
    )
    admission_add_group_parser.add_argument(
        "--require-mention",
        choices=("true", "false"),
        default="true",
    )
    admission_remove_group_parser = subparsers.add_parser("admission-remove-group")
    admission_remove_group_parser.add_argument("--profile", required=True)
    admission_remove_group_parser.add_argument("--chat-id", required=True)
    set_creds_parser = subparsers.add_parser("set-credentials")
    set_creds_parser.add_argument("--profile", required=True)
    set_creds_parser.add_argument("--app-id", required=True)
    set_creds_parser.add_argument("--app-secret", required=True)
    set_creds_parser.add_argument("--display-name")
    remove_profile_parser = subparsers.add_parser("remove-profile")
    remove_profile_parser.add_argument("--profile", required=True)
    migrate_parser = subparsers.add_parser("migrate-existing")
    migrate_parser.add_argument("--template", required=True, type=Path)
    migrate_parser.add_argument("--hermes-base", required=True, type=Path)
    migrate_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "migrate-existing":
            manifest = migrate_existing_profiles(
                template_path=args.template,
                destination_path=args.manifest,
                hermes_base=args.hermes_base,
                hermes_root=args.hermes_root,
                overwrite=args.force,
            )
            print(f"Migrated {len(manifest.profiles)} Feishu profiles without printing credentials.")
            return 0
        manifest = load_feishu_profiles_manifest(args.manifest, hermes_root=args.hermes_root)
        if args.command == "list":
            for profile_key in manifest.profiles:
                print(profile_key)
        elif args.command == "describe":
            context_loader = None
            if args.context_api_base:
                context_loader = _build_context_loader(
                    args.context_api_base,
                    timeout=args.context_timeout,
                )
            print(
                json.dumps(
                    build_status_payload(manifest, context_loader=context_loader),
                    ensure_ascii=True,
                )
            )
        elif args.command == "apply":
            profile_key = str(args.profile).strip().lower()
            profile = manifest.profiles.get(profile_key)
            if profile is None:
                raise FeishuProfilesManifestError(f"Unknown Feishu profile: {profile_key!r}.")
            service_token = None
            supervisor_key = None
            backend_port: str | None = None
            if args.backend_dotenv is not None and args.backend_dotenv.is_file():
                backend_env = _read_dotenv(args.backend_dotenv)
                service_token = backend_env.get("WATCH_API_SHARED_SECRET")
                supervisor_key = backend_env.get("PAN_HOTEL_BOOTSTRAP_SUPERVISOR_API_KEY")
                backend_port = backend_env.get("PAN_HOTEL_WATCH_BACKEND_PORT")
            watch_api_base_url = args.watch_api_base_url
            if not watch_api_base_url and backend_port:
                watch_api_base_url = f"http://127.0.0.1:{backend_port.strip()}"
            apply_profile_configuration(
                profile,
                args.profile_root,
                service_token=service_token,
                supervisor_key=supervisor_key,
                hermes_root=args.hermes_root,
                python_exe=args.python_exe,
                watch_api_base_url=watch_api_base_url,
            )
            print(f"Applied Feishu profile configuration: {profile_key}")
        elif args.command == "write-backend-env":
            write_backend_environment(manifest, args.dotenv)
            print("Updated backend Feishu credential mappings without printing credentials.")
        elif args.command == "admission-get":
            print(
                json.dumps(
                    read_profile_admission(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "admission-patch":
            if args.allowed_user is not None and args.clear_allowed_users:
                raise FeishuProfilesManifestError(
                    "Use either --allowed-user or --clear-allowed-users, not both."
                )
            allowed_users = (
                []
                if args.clear_allowed_users
                else args.allowed_user
            )
            print(
                json.dumps(
                    update_profile_admission(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                        group_key=args.group_key,
                        policy=args.policy,
                        allowed_users=allowed_users,
                        require_mention=(
                            args.require_mention == "true"
                            if args.require_mention is not None
                            else None
                        ),
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "admission-add-user":
            print(
                json.dumps(
                    add_profile_admission_user(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                        group_key=args.group_key,
                        open_id=args.open_id,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "set-credentials":
            profile_key = set_profile_credentials(
                args.manifest,
                profile_key=args.profile,
                app_id=args.app_id,
                app_secret=args.app_secret,
                display_name=args.display_name,
                hermes_root=args.hermes_root,
            )
            print(f"Updated Feishu profile credentials: {profile_key}")
        elif args.command == "remove-profile":
            profile_key = remove_profile_entry(args.manifest, profile_key=args.profile)
            print(f"Removed Feishu profile manifest entry: {profile_key}")
        elif args.command == "admission-remove-user":
            print(
                json.dumps(
                    remove_profile_admission_user(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                        group_key=args.group_key,
                        open_id=args.open_id,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "admission-add-group":
            print(
                json.dumps(
                    add_profile_admission_group(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                        chat_id=args.chat_id,
                        policy=args.policy,
                        require_mention=args.require_mention == "true",
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "admission-remove-group":
            print(
                json.dumps(
                    remove_profile_admission_group(
                        args.manifest,
                        hermes_root=args.hermes_root,
                        profile_key=args.profile,
                        chat_id=args.chat_id,
                    ),
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Validated {len(manifest.profiles)} Feishu profiles.")
    except FeishuProfilesManifestError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


def _build_context_loader(
    base_url: str,
    *,
    timeout: float,
) -> Callable[[str], Mapping[str, Any]]:
    normalized = str(base_url or "").strip().rstrip("?&")
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise FeishuProfilesManifestError("Context API must use a loopback HTTP address.")
    request_timeout = min(max(float(timeout), 0.1), 10.0)

    def load(chat_id: str) -> Mapping[str, Any]:
        separator = "&" if parsed.query else "?"
        url = normalized + separator + urllib.parse.urlencode({"chat_id": chat_id})
        with urllib.request.urlopen(url, timeout=request_timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    return load


if __name__ == "__main__":
    raise SystemExit(main())
