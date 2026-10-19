from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import yaml
from dotenv import set_key, unset_key
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.core.settings import get_settings
from app.domain.config import repository as config_repository
from app.domain.config.repository import (
    get_persisted_integration_config,
    upsert_persisted_integration_config,
)

_LOGGER = logging.getLogger(__name__)


class IntegrationConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_llm_base_url(value)


class IntegrationConfigSnapshot(BaseModel):
    llm_provider: str
    llm_base_url: str
    llm_model: str
    llm_api_key_configured: bool
    llm_api_key_masked: str | None = None


class LlmRuntimeConfig(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key: str


class LlmConnectionTestResult(BaseModel):
    status: str
    summary: str
    provider: str
    base_url: str
    model: str
    api_key_configured: bool
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


class LlmModelsResult(BaseModel):
    status: str
    summary: str
    provider: str
    base_url: str
    api_key_configured: bool
    models: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None


class LlmModelsRequest(IntegrationConfigUpdate):
    model_config_id: int | None = Field(default=None, ge=1)


class LlmModelConfigCreate(BaseModel):
    """V5 Step 1 — one entry in the multi-model fallback chain."""

    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    priority: int = 0
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url_field(cls, value: str) -> str:
        return _validate_llm_base_url(value)


class LlmModelConfigPatch(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = None
    api_key: str | None = None
    priority: int | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_llm_base_url(value)


class LlmModelConfigSnapshot(BaseModel):
    id: int
    provider: str
    model_name: str
    base_url: str
    api_key_configured: bool
    api_key_masked: str | None = None
    priority: int
    enabled: bool
    last_health_status: str | None = None
    last_health_summary: str | None = None
    last_health_checked_at: datetime | None = None


class LlmModelReorderRequest(BaseModel):
    ordered_ids: list[int] = Field(min_length=1)


class HermesLlmSyncResult(BaseModel):
    status: Literal["synced", "not_configured", "failed"]
    summary: str
    profile_keys: list[str] = Field(default_factory=list)
    primary_model: str | None = None
    fallback_count: int = 0


PROFILE_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
FeishuRole = Literal["viewer", "operator", "supervisor"]
FeishuRoleSubjectType = Literal["profile", "api_key"]
FeishuAdmissionPolicy = Literal["open", "allowlist", "disabled"]
ADMISSION_GROUP_KEY_PATTERN = r"^[0-9a-f]{16}$"
FEISHU_OPEN_ID_PATTERN = r"^ou_[A-Za-z0-9_-]+$"
FEISHU_CHAT_ID_PATTERN = r"^oc_[A-Za-z0-9_-]+$"


class FeishuAgentProfileCreate(BaseModel):
    profile_key: str = Field(pattern=PROFILE_KEY_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    enabled: bool = False


class FeishuProfileCredentialsSet(BaseModel):
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)


class FeishuAgentProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None


class FeishuChatAgentBindingCreate(BaseModel):
    chat_id: str = Field(pattern=FEISHU_CHAT_ID_PATTERN, max_length=128)
    profile_key: str = Field(pattern=PROFILE_KEY_PATTERN)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool = True


class FeishuChatAgentBindingPatch(BaseModel):
    profile_key: str | None = Field(default=None, pattern=PROFILE_KEY_PATTERN)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None


class FeishuRoleAssignmentCreate(BaseModel):
    subject_type: FeishuRoleSubjectType
    subject_ref: str | None = Field(default=None, max_length=128)
    api_key: SecretStr | None = None
    role: FeishuRole
    granted_by: str = Field(min_length=1, max_length=128)


class FeishuRoleAssignmentPatch(BaseModel):
    role: FeishuRole | None = None
    granted_by: str | None = Field(default=None, min_length=1, max_length=128)


class FeishuAdmissionPatch(BaseModel):
    group_key: str = Field(pattern=ADMISSION_GROUP_KEY_PATTERN)
    policy: FeishuAdmissionPolicy | None = None
    allowed_users: list[str] | None = None
    require_mention: bool | None = None

    @model_validator(mode="after")
    def require_update_field(self) -> "FeishuAdmissionPatch":
        if (
            self.policy is None
            and self.allowed_users is None
            and self.require_mention is None
        ):
            raise ValueError("准入策略、白名单或 mention 要求至少需要修改一项。")
        if self.allowed_users is not None:
            for open_id in self.allowed_users:
                _validate_feishu_open_id(open_id)
        return self


class FeishuAdmissionUserMutation(BaseModel):
    group_key: str = Field(pattern=ADMISSION_GROUP_KEY_PATTERN)
    open_id: str = Field(pattern=FEISHU_OPEN_ID_PATTERN, max_length=128)


class FeishuAgentConfigConflictError(ValueError):
    pass


class FeishuAgentConfigNotFoundError(ValueError):
    pass


class FeishuProfileDeploymentError(ValueError):
    pass


@dataclass(frozen=True)
class _FeishuProfileDeploymentPaths:
    project_root: Path
    hermes_root: Path
    manifest: Path
    tool: Path
    start_script: Path
    hermes_base: Path
    profile_root: Path
    python_exe: Path
    hermes_exe: Path
    backend_dotenv: Path


_FEISHU_ADMISSION_DEPLOYMENT_LOCK = asyncio.Lock()


class RuntimeIntegrationConfig:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        settings = get_settings()
        self.llm_provider = settings.llm_provider
        self.llm_base_url = _validate_llm_base_url(settings.llm_base_url)
        self.llm_model = _normalize_default_llm_model(
            provider=settings.llm_provider,
            model=settings.llm_model,
        )
        self.llm_api_key = settings.llm_api_key

    async def snapshot(self) -> IntegrationConfigSnapshot:
        async with self._lock:
            await self._refresh_from_store()
            return self._snapshot()

    async def llm_runtime(self) -> LlmRuntimeConfig:
        async with self._lock:
            await self._refresh_from_store()
            return LlmRuntimeConfig(
                provider=self.llm_provider,
                base_url=_validate_llm_base_url(
                    self.llm_base_url,
                    resolve_hostname=True,
                ),
                model=self.llm_model,
                api_key=self.llm_api_key,
            )

    async def update(self, update: IntegrationConfigUpdate) -> IntegrationConfigSnapshot:
        async with self._lock:
            await self._refresh_from_store()
            if update.llm_provider is not None:
                self.llm_provider = update.llm_provider.strip() or self.llm_provider
            if update.llm_base_url is not None:
                next_base_url = update.llm_base_url.strip()
                self.llm_base_url = _validate_llm_base_url(next_base_url or self.llm_base_url)
            if update.llm_model is not None:
                self.llm_model = _normalize_default_llm_model(
                    provider=self.llm_provider,
                    model=update.llm_model,
                )
            if update.llm_api_key is not None and update.llm_api_key.strip():
                self.llm_api_key = update.llm_api_key.strip()
            await upsert_persisted_integration_config(
                llm_provider=self.llm_provider,
                llm_base_url=self.llm_base_url,
                llm_model=self.llm_model,
                llm_api_key=self.llm_api_key,
            )
            return self._snapshot()

    async def test_llm_connection(
        self,
        update: LlmModelsRequest | None = None,
    ) -> LlmConnectionTestResult:
        runtime = await self._runtime_for_model_request(update)
        if not runtime.api_key:
            return LlmConnectionTestResult(
                status="config_required",
                summary="大模型 API key 未配置。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                model=runtime.model,
                api_key_configured=False,
                evidence={"api_key_returned": False, "request_sent": False},
                next_action="填写 API key 并保存后，再测试模型连接。",
            )
        try:
            _validate_llm_base_url(runtime.base_url, resolve_hostname=True)
        except ValueError:
            return LlmConnectionTestResult(
                status="failed",
                summary="大模型地址未通过公网安全校验。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                model=runtime.model,
                api_key_configured=True,
                evidence={
                    "api_key_returned": False,
                    "request_sent": False,
                    "url_safety_valid": False,
                },
                next_action="检查 LLM_ALLOWED_HOSTS 和该域名的 DNS 解析结果。",
            )

        endpoint = _chat_completions_endpoint(runtime.base_url)
        payload = {
            "model": runtime.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a concise connection test for Pan Hotel Watch. "
                        "Reply with a short JSON object and no secrets."
                    ),
                },
                {
                    "role": "user",
                    "content": "Return JSON with status ok and product Pan Hotel Watch.",
                },
            ],
            "temperature": 0,
            "max_tokens": 80,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return LlmConnectionTestResult(
                status="failed",
                summary="大模型连接请求失败。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                model=runtime.model,
                api_key_configured=True,
                evidence={
                    "api_key_returned": False,
                    "request_url": endpoint,
                    "error_type": type(exc).__name__,
                },
                next_action="检查网络、base_url、模型名称和服务商可用性。",
            )

        response_payload = _safe_json(response)
        reply_preview = _redact_value(
            _extract_reply_preview(response_payload),
            runtime.api_key,
        )
        is_success = response.is_success and bool(reply_preview)
        return LlmConnectionTestResult(
            status="success" if is_success else "failed",
            summary="大模型连接测试成功。" if is_success else "大模型返回错误或空回复。",
            provider=runtime.provider,
            base_url=runtime.base_url,
            model=runtime.model,
            api_key_configured=True,
            data={
                "http_status": response.status_code,
                "reply_preview": reply_preview,
                "usage": response_payload.get("usage") if isinstance(response_payload, dict) else None,
            },
            evidence={
                "api_key_returned": False,
                "request_url": endpoint,
            },
            next_action=None if is_success else "检查模型名称、API key 和服务商响应。",
        )

    async def list_llm_models(
        self,
        update: LlmModelsRequest | None = None,
    ) -> LlmModelsResult:
        runtime = await self._runtime_for_model_request(update)

        if not runtime.api_key:
            return LlmModelsResult(
                status="config_required",
                summary="大模型 API key 未配置。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                api_key_configured=False,
                evidence={"api_key_returned": False, "request_sent": False},
                next_action="填写 API key 后拉取模型列表。",
            )
        try:
            _validate_llm_base_url(runtime.base_url, resolve_hostname=True)
        except ValueError:
            return LlmModelsResult(
                status="failed",
                summary="大模型地址未通过公网安全校验。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                api_key_configured=True,
                evidence={
                    "api_key_returned": False,
                    "request_sent": False,
                    "url_safety_valid": False,
                },
                next_action="检查 LLM_ALLOWED_HOSTS 和该域名的 DNS 解析结果。",
            )

        endpoint = _models_endpoint(runtime.base_url)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {runtime.api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            return LlmModelsResult(
                status="failed",
                summary="模型列表请求失败。",
                provider=runtime.provider,
                base_url=runtime.base_url,
                api_key_configured=True,
                evidence={
                    "api_key_returned": False,
                    "request_url": endpoint,
                    "error_type": type(exc).__name__,
                },
                next_action="检查网络、接口地址、API key 和服务商可用性。",
            )

        payload = _safe_json(response)
        models = _extract_model_ids(payload)
        is_success = response.is_success and bool(models)
        return LlmModelsResult(
            status="success" if is_success else "failed",
            summary="模型列表拉取成功。" if is_success else "服务商未返回可用模型列表。",
            provider=runtime.provider,
            base_url=runtime.base_url,
            api_key_configured=True,
            models=models,
            evidence={
                "api_key_returned": False,
                "request_url": endpoint,
                "http_status": response.status_code,
                "model_count": len(models),
            },
            next_action=None if is_success else "检查服务商是否支持 OpenAI-compatible /models 接口。",
        )

    async def _runtime_for_model_request(
        self,
        update: LlmModelsRequest | None,
    ) -> LlmRuntimeConfig:
        if update is not None and update.model_config_id is not None:
            record = await config_repository.get_persisted_llm_model_config(
                update.model_config_id
            )
            if record is None:
                raise FeishuAgentConfigNotFoundError("未找到大模型配置。")
            provider = (update.llm_provider or record.provider).strip()
            base_url = _validate_llm_base_url(
                (update.llm_base_url or record.base_url).strip()
            )
            same_saved_endpoint = (
                provider == record.provider
                and base_url.rstrip("/") == record.base_url.rstrip("/")
            )
            api_key = (update.llm_api_key or "").strip()
            if not api_key and same_saved_endpoint:
                api_key = record.api_key.strip()
            return LlmRuntimeConfig(
                provider=provider,
                base_url=base_url,
                model=(update.llm_model or record.model_name).strip(),
                api_key=api_key,
            )
        return await self._runtime_for_request(update)

    def _snapshot(self) -> IntegrationConfigSnapshot:
        return IntegrationConfigSnapshot(
            llm_provider=self.llm_provider,
            llm_base_url=self.llm_base_url,
            llm_model=self.llm_model,
            llm_api_key_configured=bool(self.llm_api_key),
            llm_api_key_masked=_mask_secret(self.llm_api_key),
        )

    # --- V5 Step 1: multi-model fallback chain management ---

    async def list_model_configs(self) -> list[LlmModelConfigSnapshot]:
        records = await config_repository.list_persisted_llm_model_configs()
        return [_llm_model_config_public(record) for record in records]

    async def create_model_config(
        self, request: LlmModelConfigCreate
    ) -> tuple[LlmModelConfigSnapshot, HermesLlmSyncResult]:
        record = await config_repository.create_persisted_llm_model_config(
            provider=request.provider.strip(),
            model_name=request.model_name.strip(),
            base_url=request.base_url,
            api_key=request.api_key,
            priority=request.priority,
            enabled=request.enabled,
        )
        if record is None:
            raise FeishuAgentConfigConflictError("该大模型配置已存在（供应商/模型/地址重复）。")
        return _llm_model_config_public(record), await self.sync_hermes_model_configs()

    async def update_model_config(
        self, model_id: int, request: LlmModelConfigPatch
    ) -> tuple[LlmModelConfigSnapshot, HermesLlmSyncResult]:
        existing = await config_repository.get_persisted_llm_model_config(model_id)
        if existing is None:
            raise FeishuAgentConfigNotFoundError("未找到大模型配置。")
        next_provider = (
            request.provider.strip()
            if request.provider is not None
            else existing.provider
        )
        next_base_url = request.base_url or existing.base_url
        endpoint_changed = (
            next_provider != existing.provider
            or next_base_url.rstrip("/") != existing.base_url.rstrip("/")
        )
        if endpoint_changed and not (request.api_key or "").strip():
            raise ValueError("更换大模型供应商或接口地址时必须输入新的 API key。")
        record = await config_repository.update_persisted_llm_model_config(
            model_id,
            provider=request.provider.strip() if request.provider is not None else None,
            model_name=request.model_name.strip()
            if request.model_name is not None
            else None,
            base_url=request.base_url,
            api_key=request.api_key,
            priority=request.priority,
            enabled=request.enabled,
        )
        if record is None:
            raise FeishuAgentConfigNotFoundError("未找到大模型配置。")
        return _llm_model_config_public(record), await self.sync_hermes_model_configs()

    async def delete_model_config(self, model_id: int) -> dict[str, Any]:
        record = await config_repository.delete_persisted_llm_model_config(model_id)
        if record is None:
            raise FeishuAgentConfigNotFoundError("未找到大模型配置。")
        return {
            "status": "deleted",
            "hermes_sync": (await self.sync_hermes_model_configs()).model_dump(
                mode="json"
            ),
        }

    async def reorder_model_configs(
        self, request: LlmModelReorderRequest
    ) -> tuple[list[LlmModelConfigSnapshot], HermesLlmSyncResult]:
        records = await config_repository.reorder_persisted_llm_model_configs(
            list(request.ordered_ids)
        )
        if records is None:
            raise ValueError("排序模型列表必须覆盖全部已配置模型，不能多也不能少。")
        return (
            [_llm_model_config_public(record) for record in records],
            await self.sync_hermes_model_configs(records=records),
        )

    async def sync_hermes_model_configs(
        self,
        *,
        records: list[Any] | None = None,
    ) -> HermesLlmSyncResult:
        model_records = (
            records
            if records is not None
            else await config_repository.list_persisted_llm_model_configs()
        )
        enabled_records = _usable_llm_model_records(model_records)
        profiles = [
            record
            for record in await config_repository.list_feishu_agent_profiles()
            if record.enabled
        ]
        hermes_base = _hermes_base_path()
        profile_roots = [
            (str(profile.profile_key), hermes_base / "profiles" / str(profile.profile_key))
            for profile in profiles
            if (hermes_base / "profiles" / str(profile.profile_key) / "config.yaml").is_file()
        ]
        if not profile_roots:
            return HermesLlmSyncResult(
                status="not_configured",
                summary="未发现可同步的已启用 Hermes profile。",
                primary_model=(
                    str(enabled_records[0].model_name) if enabled_records else None
                ),
                fallback_count=max(0, len(enabled_records) - 1),
            )

        synced_profiles: list[str] = []
        file_snapshots: dict[Path, bytes | None] = {}
        try:
            for _, profile_root in profile_roots:
                for filename in ("config.yaml", ".env"):
                    path = profile_root / filename
                    file_snapshots[path] = path.read_bytes() if path.exists() else None
            for profile_key, profile_root in profile_roots:
                _sync_hermes_profile_llm_config(profile_root, enabled_records)
                synced_profiles.append(profile_key)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            rollback_failed = False
            for path, content in file_snapshots.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(content)
                except OSError:
                    rollback_failed = True
            return HermesLlmSyncResult(
                status="failed",
                summary=(
                    "Hermes 模型配置同步失败，回滚未完全完成，请人工检查："
                    if rollback_failed
                    else "Hermes 模型配置同步失败，已回滚："
                )
                + type(exc).__name__,
                profile_keys=[],
                primary_model=(
                    str(enabled_records[0].model_name) if enabled_records else None
                ),
                fallback_count=max(0, len(enabled_records) - 1),
            )
        return HermesLlmSyncResult(
            status="synced",
            summary=(
                f"已同步 {len(synced_profiles)} 个 Hermes profile，"
                "下一条 Hermes 消息生效。"
            ),
            profile_keys=synced_profiles,
            primary_model=(
                str(enabled_records[0].model_name) if enabled_records else None
            ),
            fallback_count=max(0, len(enabled_records) - 1),
        )

    async def health_check_model_configs(self) -> list[LlmModelConfigSnapshot]:
        """Ping every enabled model with an api_key; persist ok/fail status.

        The probe payload is intentionally tiny and the api_key is never written
        into ``last_health_summary``. Models without an api_key are marked
        ``config_required``. The probe never raises: each model is independent.
        """

        records = await config_repository.list_persisted_llm_model_configs()
        snapshots: list[LlmModelConfigSnapshot] = []
        for record in records:
            if not record.enabled:
                snapshots.append(_llm_model_config_public(record))
                continue
            if not record.api_key:
                updated = await config_repository.update_persisted_llm_model_health(
                    record.id,
                    status="config_required",
                    summary="未配置 api_key，无法执行健康检查。",
                    checked_at=datetime.utcnow(),
                )
                snapshots.append(
                    _llm_model_config_public(updated or record)
                )
                continue
            status, summary = await _probe_llm_model_health(record)
            updated = await config_repository.update_persisted_llm_model_health(
                record.id,
                status=status,
                summary=summary,
                checked_at=datetime.utcnow(),
            )
            snapshots.append(_llm_model_config_public(updated or record))
        return snapshots

    async def llm_runtime_chain(self) -> list[LlmRuntimeConfig]:
        """Return enabled, ordered, usable runtimes for the fallback scheduler.

        Usable = enabled AND has api_key AND base_url passes the runtime SSRF
        resolve check. Models whose base_url fails the resolve check are
        skipped (with a logged warning) rather than crashing the chain. An
        empty list means no model is configured for runtime use.
        """

        records = _usable_llm_model_records(
            await config_repository.list_persisted_llm_model_configs()
        )
        chain: list[LlmRuntimeConfig] = []
        for record in records:
            chain.append(
                LlmRuntimeConfig(
                    provider=record.provider,
                    base_url=record.base_url,
                    model=_normalize_default_llm_model(
                        provider=record.provider, model=record.model_name
                    ),
                    api_key=record.api_key,
                )
            )
        return chain

    async def _refresh_from_store(self) -> None:
        persisted = await get_persisted_integration_config()
        if persisted is None:
            return
        self.llm_provider = persisted.llm_provider
        self.llm_base_url = _validate_llm_base_url(persisted.llm_base_url)
        self.llm_model = _normalize_default_llm_model(
            provider=persisted.llm_provider,
            model=persisted.llm_model,
        )
        self.llm_api_key = persisted.llm_api_key

    async def _runtime_for_request(
        self,
        update: IntegrationConfigUpdate | None,
    ) -> LlmRuntimeConfig:
        async with self._lock:
            await self._refresh_from_store()
            if update is None:
                return LlmRuntimeConfig(
                    provider=self.llm_provider,
                    base_url=self.llm_base_url,
                    model=self.llm_model,
                    api_key=self.llm_api_key,
                )

            provider = update.llm_provider.strip() if update.llm_provider is not None else ""
            provider = provider or self.llm_provider
            base_url = update.llm_base_url.strip() if update.llm_base_url is not None else ""
            base_url = _validate_llm_base_url(base_url or self.llm_base_url)
            model = update.llm_model.strip() if update.llm_model is not None else ""
            model = _normalize_default_llm_model(provider=provider, model=model or self.llm_model)
            api_key = update.llm_api_key.strip() if update.llm_api_key is not None else ""
            same_saved_endpoint = (
                provider == self.llm_provider
                and base_url.rstrip("/") == self.llm_base_url.rstrip("/")
            )
            if not api_key and same_saved_endpoint:
                api_key = self.llm_api_key
            return LlmRuntimeConfig(
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
            )


def _configured_feishu_profile_metadata(profile_key: str) -> tuple[bool, str | None]:
    settings = get_settings()
    try:
        parsed = json.loads(str(settings.feishu_app_credentials_json or "").strip() or "{}")
    except (TypeError, ValueError):
        parsed = {}
    mapping = parsed if isinstance(parsed, dict) else {}
    raw = mapping.get(profile_key)
    source = raw if isinstance(raw, dict) else {}
    app_id = str(source.get("app_id") or source.get("FEISHU_APP_ID") or "").strip()
    app_secret = str(
        source.get("app_secret") or source.get("FEISHU_APP_SECRET") or ""
    ).strip()
    if profile_key == "default" and not source:
        app_id = str(settings.feishu_app_id or "").strip()
        app_secret = str(settings.feishu_app_secret or "").strip()
    return bool(app_id and app_secret), _fingerprint(app_id) if app_id else None


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _api_key_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _masked_chat_id(chat_id: str) -> str:
    normalized = str(chat_id or "").strip()
    if len(normalized) <= 8:
        return "***"
    return f"{normalized[:4]}...{normalized[-4:]}"


def _profile_public_payload(record: Any) -> dict[str, Any]:
    credential_configured, app_id_fingerprint = _configured_feishu_profile_metadata(
        record.profile_key
    )
    return {
        "profile_key": record.profile_key,
        "display_name": record.display_name,
        "enabled": bool(record.enabled),
        "credential_configured": credential_configured,
        "app_id_fingerprint": app_id_fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _binding_public_payload(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "profile_key": record.profile_key,
        "display_name": record.display_name,
        "enabled": bool(record.enabled),
        "chat_id_masked": _masked_chat_id(record.chat_id),
        "chat_id_fingerprint": _fingerprint(record.chat_id),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _role_assignment_public_payload(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "subject_type": record.subject_type,
        "subject_ref": record.subject_ref,
        "role": record.role,
        "granted_by": record.granted_by,
        "created_at": record.created_at,
    }


def _require_profile_can_run(record: Any) -> None:
    credential_configured, _ = _configured_feishu_profile_metadata(record.profile_key)
    if not record.enabled:
        raise ValueError("飞书 Agent profile 已停用，不能绑定启用中的业务群。")
    if not credential_configured:
        raise ValueError("飞书 Agent profile 未配置受控 App 凭据，不能启用或绑定业务群。")


def _validate_feishu_open_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not re.fullmatch(
        FEISHU_OPEN_ID_PATTERN,
        normalized,
    ):
        raise ValueError("open_id 必须是有效的飞书 ou_ 标识。")
    return normalized


def _feishu_profile_deployment_paths(
    profile_key: str,
) -> _FeishuProfileDeploymentPaths:
    normalized_profile_key = str(profile_key or "").strip().lower()
    if not re.fullmatch(PROFILE_KEY_PATTERN, normalized_profile_key):
        raise ValueError("飞书 profile 标识无效。")
    project_root = Path(__file__).resolve().parents[4]
    hermes_root = project_root / "pan-hotel-hermes"
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA")
        or Path.home() / "AppData" / "Local"
    )
    hermes_base = Path(
        os.environ.get("PAN_HOTEL_HERMES_BASE_PATH")
        or local_app_data / "hermes"
    ).expanduser()
    manifest = Path(
        os.environ.get("PAN_HOTEL_FEISHU_PROFILES_MANIFEST")
        or hermes_root / "config" / "feishu-profiles.yaml"
    ).expanduser()
    python_exe = Path(
        os.environ.get("PAN_HOTEL_HERMES_PYTHON_EXE")
        or hermes_base / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    ).expanduser()
    hermes_exe = Path(
        os.environ.get("PAN_HOTEL_HERMES_EXE")
        or hermes_base / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    ).expanduser()
    return _FeishuProfileDeploymentPaths(
        project_root=project_root,
        hermes_root=hermes_root,
        manifest=manifest.resolve(),
        tool=(hermes_root / "scripts" / "feishu_profiles.py").resolve(),
        start_script=(
            hermes_root / "scripts" / "start-hermes-gateways.ps1"
        ).resolve(),
        hermes_base=hermes_base.resolve(),
        profile_root=(hermes_base / "profiles" / normalized_profile_key).resolve(),
        python_exe=python_exe.resolve(),
        hermes_exe=hermes_exe.resolve(),
        backend_dotenv=(project_root / ".env").resolve(),
    )


def _validate_feishu_profile_deployment_paths(
    paths: _FeishuProfileDeploymentPaths,
    *,
    require_runtime: bool,
) -> None:
    required_files = [paths.manifest, paths.tool, paths.python_exe]
    if require_runtime:
        required_files.extend((paths.start_script, paths.hermes_exe))
    if any(not path.is_file() for path in required_files):
        raise FeishuProfileDeploymentError("飞书准入部署文件未就绪，请检查 Hermes 部署路径。")
    if require_runtime and not paths.profile_root.is_dir():
        raise FeishuProfileDeploymentError("目标 Hermes profile 尚未创建，无法热重载。")


def _run_feishu_profile_process(
    command: list[str],
    *,
    project_root: Path,
    failure_message: str,
    configuration_error: bool = False,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FeishuProfileDeploymentError(failure_message) from exc
    if result.returncode != 0:
        if configuration_error:
            raise ValueError("飞书准入配置未通过校验，请检查 profile、群策略和 open_id。")
        raise FeishuProfileDeploymentError(failure_message)
    return str(result.stdout or "")


def _feishu_profile_tool_command(
    paths: _FeishuProfileDeploymentPaths,
    action: str,
    arguments: list[str],
) -> list[str]:
    return [
        str(paths.python_exe),
        str(paths.tool),
        "--manifest",
        str(paths.manifest),
        "--hermes-root",
        str(paths.hermes_root),
        action,
        *arguments,
    ]


def _run_feishu_admission_command(
    paths: _FeishuProfileDeploymentPaths,
    action: str,
    arguments: list[str],
) -> dict[str, Any]:
    output = _run_feishu_profile_process(
        _feishu_profile_tool_command(paths, action, arguments),
        project_root=paths.project_root,
        failure_message="飞书准入清单工具执行失败。",
        configuration_error=True,
    )
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise FeishuProfileDeploymentError("飞书准入清单工具返回了无效结果。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise FeishuProfileDeploymentError("飞书准入清单工具返回了无效结果。")
    forbidden_keys = {"app_id", "app_secret", "chat_id"}
    if forbidden_keys.intersection(payload) or any(
        isinstance(group, dict) and forbidden_keys.intersection(group)
        for group in payload["groups"]
    ):
        raise FeishuProfileDeploymentError("飞书准入清单工具返回了受保护字段。")
    return payload


def _apply_feishu_profile(
    paths: _FeishuProfileDeploymentPaths,
    profile_key: str,
) -> None:
    """Run the manifest tool's apply: full runtime bootstrap + admission deploy."""

    _run_feishu_profile_process(
        _feishu_profile_tool_command(
            paths,
            "apply",
            [
                "--profile",
                profile_key,
                "--profile-root",
                str(paths.profile_root),
                "--backend-dotenv",
                str(paths.backend_dotenv),
                # MCP 子进程 python 显式传给工具，避免其回退推导；
                # 后端路径已按 PAN_HOTEL_HERMES_PYTHON_EXE 校验存在。
                "--python-exe",
                str(paths.python_exe),
            ],
        ),
        project_root=paths.project_root,
        failure_message="飞书准入配置已保存，但 Hermes profile 应用失败。",
    )


def _restart_feishu_profile(
    paths: _FeishuProfileDeploymentPaths,
    profile_key: str,
) -> None:
    powershell_exe = str(
        os.environ.get("PAN_HOTEL_POWERSHELL_EXE") or "powershell.exe"
    ).strip()
    _run_feishu_profile_process(
        [
            powershell_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(paths.start_script),
            "-Profiles",
            profile_key,
            "-ManifestPath",
            str(paths.manifest),
            "-HermesBasePath",
            str(paths.hermes_base),
            "-HermesExePath",
            str(paths.hermes_exe),
            "-PythonExePath",
            str(paths.python_exe),
            "-Replace",
        ],
        project_root=paths.project_root,
        failure_message="飞书准入配置已保存，但 Hermes Gateway 热重载失败。",
    )


def _apply_and_restart_feishu_profile(
    paths: _FeishuProfileDeploymentPaths,
    profile_key: str,
) -> None:
    _apply_feishu_profile(paths, profile_key)
    _restart_feishu_profile(paths, profile_key)


async def _sync_chat_binding_admission(
    chat_id: str,
    *,
    add_to_profile: str | None = None,
    remove_from_profile: str | None = None,
) -> dict[str, Any]:
    """Keep the Hermes admission manifest in step with chat binding changes.

    新增/启用绑定时把该群追加进 profile 的准入 group_rules 并热重载对应
    Gateway；删除/停用/换 profile 时反向移除。目标 profile 未部署凭据、或
    本机缺少可运行的 Hermes 部署文件时静默跳过（绑定管理不依赖部署环境）；
    真正的部署失败以 warning 返回而不是抛错——绑定变更已提交，抛错会让
    运营误以为整步失败且无从重试。
    """
    admission_synced = False
    admission_warning: str | None = None
    for profile_key, action in (
        (remove_from_profile, "admission-remove-group"),
        (add_to_profile, "admission-add-group"),
    ):
        if not profile_key:
            continue
        credential_configured, _ = _configured_feishu_profile_metadata(profile_key)
        if not credential_configured:
            continue
        paths = _feishu_profile_deployment_paths(profile_key)
        try:
            _validate_feishu_profile_deployment_paths(paths, require_runtime=True)
        except FeishuProfileDeploymentError:
            continue
        try:
            async with _FEISHU_ADMISSION_DEPLOYMENT_LOCK:
                await asyncio.to_thread(
                    _run_feishu_admission_command,
                    paths,
                    action,
                    ["--profile", profile_key, "--chat-id", chat_id],
                )
                await asyncio.to_thread(
                    _apply_and_restart_feishu_profile,
                    paths,
                    profile_key,
                )
        except FeishuProfileDeploymentError as exc:
            admission_warning = (
                f"群绑定已保存，但 profile {profile_key} 的飞书准入部署失败"
                f"（{exc}）。机器人暂不会按新准入名单应答；请排查部署后，"
                "停用再启用该绑定重试同步。"
            )
            continue
        admission_synced = True
    return {
        "admission_synced": admission_synced,
        "admission_warning": admission_warning,
    }


async def get_feishu_profile_admission_payload(
    profile_key: str,
) -> dict[str, Any]:
    paths = _feishu_profile_deployment_paths(profile_key)
    _validate_feishu_profile_deployment_paths(paths, require_runtime=False)
    return await asyncio.to_thread(
        _run_feishu_admission_command,
        paths,
        "admission-get",
        ["--profile", profile_key.strip().lower()],
    )


async def update_feishu_profile_admission_payload(
    profile_key: str,
    request: FeishuAdmissionPatch,
) -> dict[str, Any]:
    arguments = [
        "--profile",
        profile_key.strip().lower(),
        "--group-key",
        request.group_key,
    ]
    if request.policy is not None:
        arguments.extend(("--policy", request.policy))
    if request.require_mention is not None:
        arguments.extend(
            ("--require-mention", str(request.require_mention).lower())
        )
    if request.allowed_users is not None:
        if request.allowed_users:
            for open_id in request.allowed_users:
                arguments.extend(("--allowed-user", open_id))
        else:
            arguments.append("--clear-allowed-users")
    return await _mutate_and_reload_feishu_admission(
        profile_key,
        "admission-patch",
        arguments,
    )


async def add_feishu_profile_admission_user_payload(
    profile_key: str,
    request: FeishuAdmissionUserMutation,
) -> dict[str, Any]:
    return await _mutate_and_reload_feishu_admission(
        profile_key,
        "admission-add-user",
        [
            "--profile",
            profile_key.strip().lower(),
            "--group-key",
            request.group_key,
            "--open-id",
            request.open_id,
        ],
    )


async def remove_feishu_profile_admission_user_payload(
    profile_key: str,
    *,
    group_key: str,
    open_id: str,
) -> dict[str, Any]:
    normalized_open_id = _validate_feishu_open_id(open_id)
    if not re.fullmatch(ADMISSION_GROUP_KEY_PATTERN, group_key):
        raise ValueError("飞书准入群标识无效。")
    return await _mutate_and_reload_feishu_admission(
        profile_key,
        "admission-remove-user",
        [
            "--profile",
            profile_key.strip().lower(),
            "--group-key",
            group_key,
            "--open-id",
            normalized_open_id,
        ],
    )


def _session_bridge_dir() -> Path:
    """Resolve the Hermes session-bridge cache directory.

    Mirrors ``pan-hotel-hermes/client/feishu_session_bridge.py::bridge_dir`` so
    the backend reads the exact directory Hermes writes: HERMES_HOME first, then
    $LOCALAPPDATA/hermes, then ~/AppData/Local/hermes, suffixed with
    ``feishu-session-bridge``.
    """
    root = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "hermes",
    )
    return Path(root) / "feishu-session-bridge"


def _mask_chat_id(chat_id: str) -> str:
    """Mask a raw Feishu chat_id for display (keep prefix + tail)."""
    if not chat_id:
        return ""
    if len(chat_id) <= 10:
        return chat_id[:4] + "..."
    return f"{chat_id[:6]}...{chat_id[-4:]}"


async def get_active_open_ids_payload(profile_key: str) -> dict[str, Any]:
    """Return recently-active open_ids seen by the session bridge.

    Scans the Hermes session-bridge cache (one JSON per inbound session, written
    only after admission passes) and returns a de-duplicated list of open_ids
    with a masked chat_id and the cache file's mtime as ``last_seen``. The bridge
    never records rejected senders, so this list only contains senders who
    already passed admission at least once.
    """
    bridge_dir = _session_bridge_dir()
    latest: dict[str, dict[str, Any]] = {}
    if bridge_dir.is_dir():
        for cache_file in bridge_dir.glob("*.json"):
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            open_id = str(payload.get("open_id") or "").strip()
            if not open_id:
                continue
            chat_id = str(payload.get("chat_id") or "").strip()
            seen_at = cache_file.stat().st_mtime
            existing = latest.get(open_id)
            if existing is None or seen_at > existing["_seen_at"]:
                latest[open_id] = {
                    "open_id": open_id,
                    "chat_id_masked": _mask_chat_id(chat_id),
                    "last_seen": datetime.fromtimestamp(seen_at).isoformat(),
                    "_seen_at": seen_at,
                }
    rows = [
        {k: v for k, v in row.items() if k != "_seen_at"}
        for row in sorted(latest.values(), key=lambda r: r["_seen_at"], reverse=True)
    ]
    return {"open_ids": rows}


async def _mutate_and_reload_feishu_admission(
    profile_key: str,
    action: str,
    arguments: list[str],
) -> dict[str, Any]:
    paths = _feishu_profile_deployment_paths(profile_key)
    _validate_feishu_profile_deployment_paths(paths, require_runtime=True)
    async with _FEISHU_ADMISSION_DEPLOYMENT_LOCK:
        payload = await asyncio.to_thread(
            _run_feishu_admission_command,
            paths,
            action,
            arguments,
        )
        await asyncio.to_thread(
            _apply_and_restart_feishu_profile,
            paths,
            profile_key.strip().lower(),
        )
    return payload


async def list_feishu_agent_profile_payloads() -> list[dict[str, Any]]:
    records = await config_repository.list_feishu_agent_profiles()
    return [_profile_public_payload(record) for record in records]


async def create_feishu_agent_profile_payload(
    request: FeishuAgentProfileCreate,
) -> dict[str, Any]:
    profile_key = request.profile_key.strip().lower()
    credential_configured, app_id_fingerprint = _configured_feishu_profile_metadata(
        profile_key
    )
    if request.enabled and not credential_configured:
        raise ValueError("该飞书 Agent profile 未配置受控 App 凭据，不能启用。")
    record = await config_repository.create_feishu_agent_profile(
        profile_key=profile_key,
        display_name=request.display_name.strip(),
        enabled=request.enabled,
        credential_configured=credential_configured,
        app_id_fingerprint=app_id_fingerprint,
    )
    if record is None:
        raise FeishuAgentConfigConflictError("飞书 Agent profile 已存在。")
    return _profile_public_payload(record)


async def set_feishu_profile_credentials_payload(
    profile_key: str,
    request: FeishuProfileCredentialsSet,
) -> dict[str, Any]:
    """前端配置飞书应用凭据：写 YAML → 更新 .env → 部署 + 重启 Gateway → 刷新 DB。

    全程不把 app_secret 写进日志或 API 响应；响应只返回凭据状态和指纹。
    """

    normalized_profile_key = profile_key.strip().lower()
    paths = _feishu_profile_deployment_paths(normalized_profile_key)
    _validate_feishu_profile_deployment_paths(paths, require_runtime=False)

    async with _FEISHU_ADMISSION_DEPLOYMENT_LOCK:
        # 1. 写入 YAML manifest（凭据 + 可选备注名）
        await asyncio.to_thread(
            _run_feishu_profile_process,
            _feishu_profile_tool_command(
                paths,
                "set-credentials",
                [
                    "--profile",
                    normalized_profile_key,
                    "--app-id",
                    request.app_id,
                    "--app-secret",
                    request.app_secret,
                ]
                + (["--display-name", request.display_name] if request.display_name else []),
            ),
            project_root=paths.project_root,
            failure_message="飞书应用凭据写入 YAML manifest 失败。",
        )
        # 2. 更新后端 .env（FEISHU_APP_CREDENTIALS_JSON）
        await asyncio.to_thread(
            _run_feishu_profile_process,
            _feishu_profile_tool_command(
                paths,
                "write-backend-env",
                ["--dotenv", str(paths.backend_dotenv)],
            ),
            project_root=paths.project_root,
            failure_message="后端 .env 凭据映射更新失败。",
        )
        # 3. 部署 profile 全量运行环境（准入 + MCP/插件/技能/SOUL/时区引导），
        #    并在重启 Gateway 前把已配置的大模型供应商同步进新 profile 的
        #    config.yaml —— 否则新助手要等下一次 LLM 配置变更才有可用模型。
        await asyncio.to_thread(
            _apply_feishu_profile,
            paths,
            normalized_profile_key,
        )
        try:
            await get_runtime_config().sync_hermes_model_configs()
        except Exception as exc:  # 同步失败不阻塞部署；下次 LLM 配置变更会重试
            _LOGGER.warning("Hermes 模型配置同步失败（继续重启 Gateway）：%s", exc)
        await asyncio.to_thread(
            _restart_feishu_profile,
            paths,
            normalized_profile_key,
        )

    # 4. 清缓存让后端立即读到新 .env
    from app.core.settings import get_settings

    get_settings.cache_clear()

    # 5. 更新 DB 记录的凭据状态
    credential_configured, app_id_fingerprint = _configured_feishu_profile_metadata(
        normalized_profile_key
    )
    record = await config_repository.get_feishu_agent_profile(normalized_profile_key)
    if record is not None:
        record = await config_repository.update_feishu_agent_profile(
            profile_key=normalized_profile_key,
            display_name=request.display_name.strip() if request.display_name else None,
            enabled=None,
            credential_configured=credential_configured,
            app_id_fingerprint=app_id_fingerprint,
        )
    return _profile_public_payload(record) if record is not None else {
        "profile_key": normalized_profile_key,
        "credential_configured": credential_configured,
        "app_id_fingerprint": app_id_fingerprint,
    }


async def update_feishu_agent_profile_payload(
    profile_key: str,
    request: FeishuAgentProfilePatch,
) -> dict[str, Any]:
    normalized_profile_key = profile_key.strip().lower()
    existing = await config_repository.get_feishu_agent_profile(normalized_profile_key)
    if existing is None:
        raise FeishuAgentConfigNotFoundError("未找到飞书 Agent profile。")
    credential_configured, app_id_fingerprint = _configured_feishu_profile_metadata(
        normalized_profile_key
    )
    next_enabled = request.enabled if request.enabled is not None else bool(existing.enabled)
    if next_enabled and not credential_configured:
        raise ValueError("该飞书 Agent profile 未配置受控 App 凭据，不能启用。")
    record = await config_repository.update_feishu_agent_profile(
        profile_key=normalized_profile_key,
        display_name=request.display_name.strip() if request.display_name is not None else None,
        enabled=request.enabled,
        credential_configured=credential_configured,
        app_id_fingerprint=app_id_fingerprint,
    )
    assert record is not None
    return _profile_public_payload(record)


async def delete_feishu_agent_profile_payload(profile_key: str) -> dict[str, Any]:
    normalized_profile_key = profile_key.strip().lower()
    existing = await config_repository.get_feishu_agent_profile(normalized_profile_key)
    if existing is None:
        raise FeishuAgentConfigNotFoundError("未找到飞书 Agent profile。")
    binding_count = await config_repository.count_feishu_chat_bindings_for_profile(
        normalized_profile_key
    )
    if binding_count:
        raise FeishuAgentConfigConflictError(
            "该飞书 Agent profile 仍被业务群绑定，不能删除。"
        )
    # 删除前记录凭据状态：已部署凭据的 profile 删除时要回收 manifest 条目、
    # 后端 .env 凭据映射，并停掉仍在运行的 Gateway。
    credential_configured, _ = _configured_feishu_profile_metadata(
        normalized_profile_key
    )
    record = await config_repository.delete_feishu_agent_profile(normalized_profile_key)
    assert record is not None
    payload = _profile_public_payload(record)
    payload["manifest_cleaned"] = False
    payload["cleanup_warning"] = None
    if credential_configured:
        payload.update(await _cleanup_deleted_feishu_profile(normalized_profile_key))
    return payload


async def _cleanup_deleted_feishu_profile(profile_key: str) -> dict[str, Any]:
    """Recycle manifest entry + backend .env credentials + running gateway.

    DB 行已删除，本步失败只降级为 warning（删除不应因清理失败整体报错）；
    warning 提示操作员按指引人工收尾。
    """

    try:
        paths = _feishu_profile_deployment_paths(profile_key)
        _validate_feishu_profile_deployment_paths(paths, require_runtime=False)
        async with _FEISHU_ADMISSION_DEPLOYMENT_LOCK:
            await asyncio.to_thread(
                _run_feishu_profile_process,
                _feishu_profile_tool_command(
                    paths,
                    "remove-profile",
                    ["--profile", profile_key],
                ),
                project_root=paths.project_root,
                failure_message="AI 助手已删除，但凭据清单回收失败。",
            )
            await asyncio.to_thread(
                _run_feishu_profile_process,
                _feishu_profile_tool_command(
                    paths,
                    "write-backend-env",
                    ["--dotenv", str(paths.backend_dotenv)],
                ),
                project_root=paths.project_root,
                failure_message="AI 助手已删除，但后端 .env 凭据映射更新失败。",
            )
            if paths.hermes_exe.is_file():
                # gateway stop 在网关本就未运行时常以非零退出，纯 best-effort，
                # 失败只记日志，不算清理失败。
                try:
                    await asyncio.to_thread(
                        _run_feishu_profile_process,
                        [
                            str(paths.hermes_exe),
                            "-p",
                            profile_key,
                            "gateway",
                            "stop",
                        ],
                        project_root=paths.project_root,
                        failure_message="gateway stop",
                    )
                except FeishuProfileDeploymentError as exc:
                    _LOGGER.info(
                        "Feishu profile %s gateway stop 返回非零（忽略）：%s",
                        profile_key,
                        exc,
                    )
    except (FeishuProfileDeploymentError, ValueError) as exc:
        return {
            "manifest_cleaned": False,
            "cleanup_warning": (
                f"AI 助手已删除，但部署侧清理未完成（{exc}）。请人工从 "
                "feishu-profiles.yaml 移除该 profile、重写后端 .env 凭据映射，"
                "并停止其 Hermes Gateway。"
            ),
        }
    from app.core.settings import get_settings

    get_settings.cache_clear()
    return {"manifest_cleaned": True, "cleanup_warning": None}


async def list_feishu_chat_agent_binding_payloads() -> list[dict[str, Any]]:
    records = await config_repository.list_feishu_chat_agent_bindings()
    return [_binding_public_payload(record) for record in records]


async def create_feishu_chat_agent_binding_payload(
    request: FeishuChatAgentBindingCreate,
) -> dict[str, Any]:
    chat_id = request.chat_id.strip()
    profile_key = request.profile_key.strip().lower()
    profile = await config_repository.get_feishu_agent_profile(profile_key)
    if profile is None:
        raise FeishuAgentConfigNotFoundError("未找到待绑定的飞书 Agent profile。")
    if request.enabled:
        _require_profile_can_run(profile)
    display_name = request.display_name.strip() if request.display_name else ""
    record = await config_repository.create_feishu_chat_agent_binding(
        chat_id=chat_id,
        profile_key=profile_key,
        display_name=display_name or f"运营群 {_masked_chat_id(chat_id)}",
        enabled=request.enabled,
    )
    if record is None:
        raise FeishuAgentConfigConflictError("该业务群已绑定其他飞书 Agent profile。")
    payload = _binding_public_payload(record)
    if request.enabled:
        # 新群绑定即同步准入并部署，机器人直接可在该群应答；未部署凭据的
        # profile 只落库，等配置凭据启用后再同步。
        payload.update(
            await _sync_chat_binding_admission(chat_id, add_to_profile=profile_key)
        )
    else:
        payload.update({"admission_synced": False, "admission_warning": None})
    return payload


async def update_feishu_chat_agent_binding_payload(
    binding_id: int,
    request: FeishuChatAgentBindingPatch,
) -> dict[str, Any]:
    existing = await config_repository.get_feishu_chat_agent_binding_by_id(binding_id)
    if existing is None:
        raise FeishuAgentConfigNotFoundError("未找到业务群 Agent 绑定。")
    profile_key = (
        request.profile_key.strip().lower()
        if request.profile_key is not None
        else existing.profile_key
    )
    profile = await config_repository.get_feishu_agent_profile(profile_key)
    if profile is None:
        raise FeishuAgentConfigNotFoundError("未找到待绑定的飞书 Agent profile。")
    next_enabled = request.enabled if request.enabled is not None else bool(existing.enabled)
    if next_enabled:
        _require_profile_can_run(profile)
    record = await config_repository.update_feishu_chat_agent_binding(
        binding_id=binding_id,
        profile_key=request.profile_key.strip().lower()
        if request.profile_key is not None
        else None,
        display_name=request.display_name.strip() if request.display_name is not None else None,
        enabled=request.enabled,
    )
    assert record is not None
    sync_result = {"admission_synced": False, "admission_warning": None}
    profile_changed = (
        request.profile_key is not None
        and request.profile_key.strip().lower() != existing.profile_key
    )
    enabled_changed = request.enabled is not None and bool(request.enabled) != bool(
        existing.enabled
    )
    if profile_changed:
        # 换 profile：先从旧 profile 移除（保持 chat 全局唯一）再加入新 profile。
        sync_result = await _sync_chat_binding_admission(
            record.chat_id,
            remove_from_profile=existing.profile_key if existing.enabled else None,
            add_to_profile=record.profile_key if record.enabled else None,
        )
    elif enabled_changed:
        if record.enabled:
            sync_result = await _sync_chat_binding_admission(
                record.chat_id,
                add_to_profile=record.profile_key,
            )
        else:
            sync_result = await _sync_chat_binding_admission(
                record.chat_id,
                remove_from_profile=record.profile_key,
            )
    payload = _binding_public_payload(record)
    payload.update(sync_result)
    return payload


async def delete_feishu_chat_agent_binding_payload(binding_id: int) -> dict[str, Any]:
    record = await config_repository.delete_feishu_chat_agent_binding(binding_id)
    if record is None:
        raise FeishuAgentConfigNotFoundError("未找到业务群 Agent 绑定。")
    payload = _binding_public_payload(record)
    # 只有启用中的绑定才可能已进准入名单；停用中的删除无需打扰 Gateway。
    if record.enabled:
        payload.update(
            await _sync_chat_binding_admission(
                record.chat_id,
                remove_from_profile=record.profile_key,
            )
        )
    else:
        payload.update({"admission_synced": False, "admission_warning": None})
    return payload


async def list_feishu_role_assignment_payloads() -> list[dict[str, Any]]:
    records = await config_repository.list_feishu_role_assignments()
    return [_role_assignment_public_payload(record) for record in records]


async def create_feishu_role_assignment_payload(
    request: FeishuRoleAssignmentCreate,
) -> dict[str, Any]:
    subject_ref = await _validated_role_subject_ref(request)
    record = await config_repository.create_feishu_role_assignment(
        subject_type=request.subject_type,
        subject_ref=subject_ref,
        role=request.role,
        granted_by=request.granted_by.strip(),
    )
    if record is None:
        raise FeishuAgentConfigConflictError("该权限主体已存在角色分配。")
    return _role_assignment_public_payload(record)


async def update_feishu_role_assignment_payload(
    assignment_id: int,
    request: FeishuRoleAssignmentPatch,
) -> dict[str, Any]:
    if request.role is None and request.granted_by is None:
        raise ValueError("角色或授权人至少需要修改一项。")
    record = await config_repository.update_feishu_role_assignment(
        assignment_id=assignment_id,
        role=request.role,
        granted_by=request.granted_by.strip()
        if request.granted_by is not None
        else None,
    )
    if record is None:
        raise FeishuAgentConfigNotFoundError("未找到飞书角色分配。")
    return _role_assignment_public_payload(record)


async def delete_feishu_role_assignment_payload(
    assignment_id: int,
) -> dict[str, Any]:
    record = await config_repository.delete_feishu_role_assignment(assignment_id)
    if record is None:
        raise FeishuAgentConfigNotFoundError("未找到飞书角色分配。")
    return _role_assignment_public_payload(record)


async def _validated_role_subject_ref(
    request: FeishuRoleAssignmentCreate,
) -> str:
    if request.subject_type == "profile":
        if request.api_key is not None:
            raise ValueError("profile 角色主体不能同时提交 API key。")
        profile_key = str(request.subject_ref or "").strip().lower()
        if not profile_key:
            raise ValueError("profile 角色主体必须提供 subject_ref。")
        if await config_repository.get_feishu_agent_profile(profile_key) is None:
            raise FeishuAgentConfigNotFoundError("未找到角色分配对应的飞书 Agent profile。")
        return profile_key

    if str(request.subject_ref or "").strip():
        raise ValueError("api_key 角色主体只接受原始 API key，由服务端生成指纹。")
    raw_api_key = (
        request.api_key.get_secret_value().strip()
        if request.api_key is not None
        else ""
    )
    if len(raw_api_key) < 16:
        raise ValueError("api_key 角色主体必须提供至少 16 个字符的 API key。")
    return _api_key_fingerprint(raw_api_key)


async def resolve_feishu_role_for_chat(chat_id: str | None) -> FeishuRole:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return "operator"
    binding = await config_repository.get_feishu_chat_agent_binding_by_chat_id(
        normalized_chat_id
    )
    if binding is None or not binding.enabled:
        return "operator"
    profile = await config_repository.get_feishu_agent_profile(binding.profile_key)
    if profile is None or not profile.enabled:
        return "operator"
    assignment = await config_repository.get_feishu_role_assignment_by_subject(
        subject_type="profile",
        subject_ref=str(profile.profile_key),
    )
    role = str(assignment.role) if assignment is not None else "operator"
    return role if role in {"viewer", "operator", "supervisor"} else "operator"


async def resolve_api_key_role(api_key: str | None) -> FeishuRole | None:
    raw_api_key = str(api_key or "").strip()
    if not raw_api_key:
        return None
    fingerprint = _api_key_fingerprint(raw_api_key)
    bootstrap_key = str(
        get_settings().pan_hotel_bootstrap_supervisor_api_key or ""
    ).strip()
    if bootstrap_key and hmac.compare_digest(
        fingerprint,
        _api_key_fingerprint(bootstrap_key),
    ):
        return "supervisor"
    assignment = await config_repository.get_feishu_role_assignment_by_subject(
        subject_type="api_key",
        subject_ref=fingerprint,
    )
    if assignment is None:
        return None
    role = str(assignment.role)
    return role if role in {"viewer", "operator", "supervisor"} else None


async def get_supervisor_overview_payload() -> dict[str, Any]:
    from app.domain.competitor_prices.repository import (
        list_competitor_watch_group_summaries,
    )
    from app.domain.ctrip_ebooking_watch.repository import (
        list_data_center_watch_group_summaries,
    )
    from app.domain.operator_payload import sanitize_supervisor_payload

    # Fetch watch groups WITH raw secrets (feishu_chat_id, feishu_webhook_url, ...)
    # before sanitizing. The supervisor overview is API-key-gated, so
    # authenticated admins need the raw webhook URLs to verify/edit them.
    # sanitize_supervisor_payload masks any key containing "webhook" or any
    # value matching the Feishu webhook pattern, so we capture the raw values
    # keyed by group_key and restore them AFTER sanitization. The generic
    # (non-supervisor) endpoints never go through this path and stay masked.
    raw_ebk_groups = await list_data_center_watch_group_summaries(include_secrets=True)
    raw_competitor_groups = await list_competitor_watch_group_summaries(
        include_secrets=True
    )
    platform_scheduler_heartbeats = _platform_ebk_scheduler_heartbeats()

    return {
        "profiles": await list_feishu_agent_profile_payloads(),
        "bindings": [
            {
                **_binding_public_payload(record),
                "chat_id": record.chat_id,
            }
            for record in await config_repository.list_feishu_chat_agent_bindings()
        ],
        "role_assignments": await list_feishu_role_assignment_payloads(),
        "watch_groups": {
            "ebk": _restore_raw_webhook_urls(
                sanitize_supervisor_payload(raw_ebk_groups),
                raw_ebk_groups,
            ),
            "competitor": _restore_raw_webhook_urls(
                sanitize_supervisor_payload(raw_competitor_groups),
                raw_competitor_groups,
            ),
        },
        "platform_ebk_schedulers": platform_scheduler_heartbeats,
    }


def _platform_ebk_scheduler_heartbeats() -> dict[str, Any]:
    """美团/飞猪 EBK 调度心跳摘要（设计 §5 P2：纳入 supervisor 总览）。"""

    from app.domain.ops.worker_heartbeat import get_worker_heartbeat_status

    heartbeats: dict[str, Any] = {}
    for worker_name in ("meituan_ebooking_scheduler", "fliggy_ebooking_scheduler"):
        heartbeat = get_worker_heartbeat_status(worker_name)
        heartbeats[worker_name] = {
            "status": heartbeat.get("status") or "missing",
            "process_confirmed": bool(heartbeat.get("process_confirmed")),
            "last_heartbeat_at": heartbeat.get("last_heartbeat_at"),
            "heartbeat_age_seconds": heartbeat.get("heartbeat_age_seconds"),
        }
    return heartbeats


# Fields whose raw value sanitize_supervisor_payload masks (any key containing
# "webhook") that the supervisor overview must restore for authenticated admins.
_SUPERVISOR_WEBHOOK_FIELDS = (
    "feishu_webhook_url",
    "feishu_mobile_webhook_url",
    "feishu_desktop_webhook_url",
)


def _restore_raw_webhook_urls(
    sanitized_groups: list[dict[str, Any]],
    raw_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Restore raw webhook URLs that sanitize_supervisor_payload masked.

    Matches sanitized entries to their raw counterparts by group_key, then
    writes back each raw webhook field (only when the raw value is truthy).
    Entries without a group_key are returned unchanged.
    """
    raw_by_key = {
        group.get("group_key"): group
        for group in raw_groups
        if group.get("group_key")
    }
    for group in sanitized_groups:
        group_key = group.get("group_key")
        raw_group = raw_by_key.get(group_key) if group_key else None
        if not raw_group:
            continue
        for field in _SUPERVISOR_WEBHOOK_FIELDS:
            raw_value = raw_group.get(field)
            if raw_value:
                group[field] = raw_value
    return sanitized_groups


async def resolve_active_feishu_chat_agent_profile(chat_id: str) -> str:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        raise ValueError("飞书业务群 chat_id 不能为空。")
    binding = await config_repository.get_feishu_chat_agent_binding_by_chat_id(
        normalized_chat_id
    )
    if binding is None:
        raise ValueError("该飞书业务群尚未绑定 Agent，请先绑定飞书 Agent profile。")
    if not binding.enabled:
        raise ValueError("该飞书业务群的 Agent 绑定已停用，请先启用绑定。")
    profile = await config_repository.get_feishu_agent_profile(binding.profile_key)
    if profile is None:
        raise ValueError("该飞书业务群绑定的 Agent profile 不存在，请重新绑定。")
    _require_profile_can_run(profile)
    return str(profile.profile_key)


async def require_active_feishu_chat_agent_binding(chat_id: str) -> None:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return
    await resolve_active_feishu_chat_agent_profile(normalized_chat_id)


def get_runtime_config() -> RuntimeIntegrationConfig:
    return _RUNTIME_CONFIG


def reset_runtime_config() -> None:
    global _RUNTIME_CONFIG
    _RUNTIME_CONFIG = RuntimeIntegrationConfig()


_HERMES_MANAGED_PROVIDER_PREFIX = "pan-hotel-llm-"
_HERMES_MANAGED_KEY_PREFIX = "PAN_HOTEL_LLM_MODEL_"


def _hermes_base_path() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    )
    return Path(
        os.environ.get("PAN_HOTEL_HERMES_BASE_PATH")
        or local_app_data / "hermes"
    ).expanduser().resolve()


def _hermes_provider_key(record: Any) -> str:
    return f"{_HERMES_MANAGED_PROVIDER_PREFIX}{int(record.id)}"


def _hermes_api_key_env(record: Any) -> str:
    return f"{_HERMES_MANAGED_KEY_PREFIX}{int(record.id)}_API_KEY"


def _usable_llm_model_records(records: list[Any]) -> list[Any]:
    import logging as _logging

    logger = _logging.getLogger("app.domain.config.service")
    usable: list[Any] = []
    for record in records:
        if not record.enabled or not record.api_key:
            continue
        try:
            _validate_llm_base_url(
                record.base_url,
                resolve_hostname=True,
            )
        except ValueError as exc:
            logger.warning(
                "LLM 模型 %s/%s 的 base_url 未通过公网校验，已从回退链和 Hermes 同步中跳过：%s",
                record.provider,
                record.model_name,
                exc,
            )
            continue
        usable.append(record)
    return usable


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(
                payload,
                stream,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _sync_hermes_profile_llm_config(
    profile_root: Path,
    records: list[Any],
) -> None:
    hermes_base = _hermes_base_path()
    resolved_root = profile_root.resolve()
    try:
        resolved_root.relative_to(hermes_base)
    except ValueError as exc:
        raise ValueError("Hermes profile 路径超出允许范围。") from exc

    config_path = resolved_root / "config.yaml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(raw_config, dict):
        raise ValueError("Hermes config.yaml 顶层必须是对象。")
    config: dict[str, Any] = dict(raw_config)
    model = config.get("model")
    model_config = dict(model) if isinstance(model, dict) else {}

    providers = config.get("providers")
    provider_configs = dict(providers) if isinstance(providers, dict) else {}
    for key in list(provider_configs):
        if str(key).startswith(_HERMES_MANAGED_PROVIDER_PREFIX):
            provider_configs.pop(key, None)

    for record in records:
        provider_key = _hermes_provider_key(record)
        provider_configs[provider_key] = {
            "name": f"Pan Hotel {record.provider} #{record.id}",
            "api": str(record.base_url).rstrip("/"),
            "key_env": _hermes_api_key_env(record),
            "default_model": str(record.model_name),
            "models": {str(record.model_name): {}},
            "transport": "chat_completions",
        }

    if records:
        primary = records[0]
        model_config.update(
            {
                "default": str(primary.model_name),
                "provider": f"custom:{_hermes_provider_key(primary)}",
                "base_url": str(primary.base_url).rstrip("/"),
            }
        )
        config["fallback_providers"] = [
            {
                "provider": f"custom:{_hermes_provider_key(record)}",
                "model": str(record.model_name),
            }
            for record in records[1:]
        ]
    else:
        model_config.update({"default": "", "provider": "auto"})
        model_config.pop("base_url", None)
        config["fallback_providers"] = []
    env_path = resolved_root / ".env"
    if not env_path.exists():
        env_path.touch()
    current_env = env_path.read_text(encoding="utf-8-sig")
    configured_keys = {_hermes_api_key_env(record) for record in records}
    stale_keys = {
        match.group(1)
        for match in re.finditer(
            rf"(?m)^({_HERMES_MANAGED_KEY_PREFIX}[A-Z0-9_]+)=",
            current_env,
        )
        if match.group(1) not in configured_keys
    }
    for record in records:
        api_key = str(record.api_key or "")
        if api_key:
            set_key(
                str(env_path),
                _hermes_api_key_env(record),
                api_key,
                quote_mode="always",
                encoding="utf-8",
            )
    config["model"] = model_config
    config["providers"] = provider_configs
    _atomic_write_yaml(config_path, config)
    for key in sorted(stale_keys):
        unset_key(str(env_path), key, quote_mode="auto", encoding="utf-8")


def _mask_secret(secret: str) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def _redact_value(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def _llm_model_config_public(record: Any) -> LlmModelConfigSnapshot:
    """Build a secret-safe snapshot of a multi-model config row.

    The api_key is reduced to a masked preview; the raw value never appears in
    API responses or health-check summaries.
    """

    api_key = getattr(record, "api_key", "") or ""
    return LlmModelConfigSnapshot(
        id=record.id,
        provider=record.provider,
        model_name=record.model_name,
        base_url=record.base_url,
        api_key_configured=bool(api_key),
        api_key_masked=_mask_secret(api_key),
        priority=record.priority,
        enabled=bool(record.enabled),
        last_health_status=record.last_health_status,
        last_health_summary=record.last_health_summary,
        last_health_checked_at=record.last_health_checked_at,
    )


async def _probe_llm_model_health(record: Any) -> tuple[str, str]:
    """Send a tiny chat-completions probe to one model; never raise.

    Returns ``(status, summary)`` where status is ``ok``/``fail`` and summary
    is a short, secret-free note (http status or error type — never api_key).
    """

    api_key = str(getattr(record, "api_key", "") or "")
    base_url = str(getattr(record, "base_url", "") or "")
    model_name = _normalize_default_llm_model(
        provider=str(getattr(record, "provider", "")),
        model=str(getattr(record, "model_name", "")),
    )
    endpoint = _chat_completions_endpoint(base_url)
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "ping",
            }
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        return "fail", f"健康检查请求失败：{type(exc).__name__}"
    if response.is_success:
        return "ok", f"健康检查通过（HTTP {response.status_code}）。"
    return "fail", f"健康检查失败（HTTP {response.status_code}）。"


def _normalize_default_llm_model(*, provider: str, model: str) -> str:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip()
    # DeepSeek retired deepseek-chat / deepseek-reasoner after 2026-07-24 UTC.
    if normalized_provider == "deepseek":
        aliases = {
            "": "deepseek-v4-flash",
            "deepseek-chat": "deepseek-v4-flash",
            "deepseek-reasoner": "deepseek-v4-flash",
        }
        if normalized_model in aliases:
            return aliases[normalized_model]
    return normalized_model or "deepseek-v4-flash"


def _chat_completions_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


_BLOCKED_LLM_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.internal",
    }
)
_DEFAULT_LLM_ALLOWED_HOSTS = frozenset(
    {
        "api.deepseek.com",
        "api.openai.com",
        "api.siliconflow.cn",
    }
)


def _is_disallowed_llm_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _validate_llm_hostname_addresses(hostname: str, port: int) -> None:
    try:
        address_info = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("LLM base_url 主机无法解析为可验证的公网地址。") from exc
    resolved_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for item in address_info:
        socket_address = item[4] if len(item) > 4 else None
        raw_address = socket_address[0] if socket_address else ""
        try:
            resolved_addresses.add(ipaddress.ip_address(raw_address))
        except ValueError as exc:
            raise ValueError("LLM base_url 主机解析结果无效。") from exc
    if not resolved_addresses:
        raise ValueError("LLM base_url 主机无法解析为可验证的公网地址。")
    if any(_is_disallowed_llm_address(address) for address in resolved_addresses):
        raise ValueError("LLM base_url 禁止解析到内网、元数据或保留地址。")


def _validate_llm_base_url(
    value: str,
    *,
    resolve_hostname: bool = False,
) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("LLM base_url 不能为空。")
    try:
        parsed = urlsplit(normalized)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("LLM base_url 格式无效。") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("LLM base_url 仅允许使用 HTTPS。")
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LLM base_url 必须是无凭据、无 query、无 fragment 的 HTTPS 地址。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("LLM base_url 端口无效。")
    if hostname in _BLOCKED_LLM_HOSTS or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("LLM base_url 禁止使用本机或内部域名。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and _is_disallowed_llm_address(address):
        raise ValueError("LLM base_url 禁止使用内网、元数据或保留地址。")
    configured_allowed_hosts = {
        item.strip().lower().rstrip(".")
        for item in str(get_settings().llm_allowed_hosts or "").split(",")
        if item.strip()
    }
    allowed_hosts = configured_allowed_hosts or _DEFAULT_LLM_ALLOWED_HOSTS
    if hostname not in allowed_hosts:
        raise ValueError("LLM base_url 主机不在允许列表中。")
    if address is None and resolve_hostname:
        _validate_llm_hostname_addresses(hostname, port or 443)
    return normalized


def _models_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/models"):
        return normalized
    return f"{normalized}/models"


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_reply_preview(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return str(content or "").strip()[:300]


def _extract_model_ids(payload: dict[str, Any]) -> list[str]:
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        return []
    model_ids: set[str] = set()
    for item in raw_models:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id:
            model_ids.add(model_id)
    return sorted(model_ids)


_RUNTIME_CONFIG = RuntimeIntegrationConfig()
