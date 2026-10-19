from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import ensure_db_ready, session_scope
from app.domain.config.models import (
    FeishuAgentProfileRecord,
    FeishuChatAgentBindingRecord,
    FeishuRoleAssignmentRecord,
    RuntimeIntegrationConfigRecord,
    RuntimeLlmModelRecord,
)

DEFAULT_RUNTIME_CONFIG_KEY = "default"


class PersistedIntegrationConfig(BaseModel):
    llm_provider: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str


async def get_persisted_integration_config(
    *,
    config_key: str = DEFAULT_RUNTIME_CONFIG_KEY,
) -> PersistedIntegrationConfig | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(RuntimeIntegrationConfigRecord).where(
                RuntimeIntegrationConfigRecord.config_key == config_key,
            ),
        )
    if record is None:
        return None
    return PersistedIntegrationConfig(
        llm_provider=record.llm_provider or "deepseek",
        llm_base_url=record.llm_base_url or "https://api.deepseek.com",
        llm_model=record.llm_model or "deepseek-v4-flash",
        llm_api_key=record.llm_api_key or "",
    )


async def upsert_persisted_integration_config(
    *,
    llm_provider: str,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str,
    config_key: str = DEFAULT_RUNTIME_CONFIG_KEY,
) -> PersistedIntegrationConfig:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.scalar(
            select(RuntimeIntegrationConfigRecord).where(
                RuntimeIntegrationConfigRecord.config_key == config_key,
            ),
        )
        if record is None:
            record = RuntimeIntegrationConfigRecord(
                config_key=config_key,
                llm_provider=llm_provider,
                llm_base_url=llm_base_url,
                llm_model=llm_model,
                llm_api_key=llm_api_key or None,
            )
            session.add(record)
        else:
            record.llm_provider = llm_provider
            record.llm_base_url = llm_base_url
            record.llm_model = llm_model
            if llm_api_key:
                record.llm_api_key = llm_api_key
        await session.flush()
        return PersistedIntegrationConfig(
            llm_provider=record.llm_provider,
            llm_base_url=record.llm_base_url,
            llm_model=record.llm_model,
            llm_api_key=record.llm_api_key or "",
        )


async def list_feishu_agent_profiles() -> list[FeishuAgentProfileRecord]:
    await ensure_db_ready()
    async with session_scope() as session:
        return list(
            (
                await session.scalars(
                    select(FeishuAgentProfileRecord).order_by(
                        FeishuAgentProfileRecord.profile_key.asc()
                    )
                )
            ).all()
        )


async def get_feishu_agent_profile(
    profile_key: str,
) -> FeishuAgentProfileRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        return await session.get(FeishuAgentProfileRecord, profile_key)


async def create_feishu_agent_profile(
    *,
    profile_key: str,
    display_name: str,
    enabled: bool,
    credential_configured: bool,
    app_id_fingerprint: str | None,
) -> FeishuAgentProfileRecord | None:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            if await session.get(FeishuAgentProfileRecord, profile_key) is not None:
                return None
            record = FeishuAgentProfileRecord(
                profile_key=profile_key,
                display_name=display_name,
                enabled=enabled,
                credential_configured=credential_configured,
                app_id_fingerprint=app_id_fingerprint,
            )
            session.add(record)
            await session.flush()
            return record
    except IntegrityError:
        return None


async def update_feishu_agent_profile(
    *,
    profile_key: str,
    display_name: str | None,
    enabled: bool | None,
    credential_configured: bool,
    app_id_fingerprint: str | None,
) -> FeishuAgentProfileRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuAgentProfileRecord, profile_key)
        if record is None:
            return None
        if display_name is not None:
            record.display_name = display_name
        if enabled is not None:
            record.enabled = enabled
        record.credential_configured = credential_configured
        record.app_id_fingerprint = app_id_fingerprint
        await session.flush()
        return record


async def delete_feishu_agent_profile(profile_key: str) -> FeishuAgentProfileRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuAgentProfileRecord, profile_key)
        if record is None:
            return None
        await session.delete(record)
        await session.flush()
        return record


async def count_feishu_chat_bindings_for_profile(profile_key: str) -> int:
    await ensure_db_ready()
    async with session_scope() as session:
        return int(
            await session.scalar(
                select(func.count(FeishuChatAgentBindingRecord.id)).where(
                    FeishuChatAgentBindingRecord.profile_key == profile_key
                )
            )
            or 0
        )


async def list_feishu_chat_agent_bindings() -> list[FeishuChatAgentBindingRecord]:
    await ensure_db_ready()
    async with session_scope() as session:
        return list(
            (
                await session.scalars(
                    select(FeishuChatAgentBindingRecord).order_by(
                        FeishuChatAgentBindingRecord.id.asc()
                    )
                )
            ).all()
        )


async def get_feishu_chat_agent_binding_by_id(
    binding_id: int,
) -> FeishuChatAgentBindingRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        return await session.get(FeishuChatAgentBindingRecord, binding_id)


async def get_feishu_chat_agent_binding_by_chat_id(
    chat_id: str,
) -> FeishuChatAgentBindingRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        return await session.scalar(
            select(FeishuChatAgentBindingRecord).where(
                FeishuChatAgentBindingRecord.chat_id == chat_id
            )
        )


async def create_feishu_chat_agent_binding(
    *,
    chat_id: str,
    profile_key: str,
    display_name: str,
    enabled: bool,
) -> FeishuChatAgentBindingRecord | None:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            existing = await session.scalar(
                select(FeishuChatAgentBindingRecord.id).where(
                    FeishuChatAgentBindingRecord.chat_id == chat_id
                )
            )
            if existing is not None:
                return None
            record = FeishuChatAgentBindingRecord(
                chat_id=chat_id,
                profile_key=profile_key,
                display_name=display_name,
                enabled=enabled,
            )
            session.add(record)
            await session.flush()
            return record
    except IntegrityError:
        return None


async def update_feishu_chat_agent_binding(
    *,
    binding_id: int,
    profile_key: str | None,
    display_name: str | None,
    enabled: bool | None,
) -> FeishuChatAgentBindingRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuChatAgentBindingRecord, binding_id)
        if record is None:
            return None
        if profile_key is not None:
            record.profile_key = profile_key
        if display_name is not None:
            record.display_name = display_name
        if enabled is not None:
            record.enabled = enabled
        await session.flush()
        return record


async def delete_feishu_chat_agent_binding(
    binding_id: int,
) -> FeishuChatAgentBindingRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuChatAgentBindingRecord, binding_id)
        if record is None:
            return None
        await session.execute(
            delete(FeishuChatAgentBindingRecord).where(
                FeishuChatAgentBindingRecord.id == binding_id
            )
        )
        await session.flush()
        return record


async def list_feishu_role_assignments() -> list[FeishuRoleAssignmentRecord]:
    await ensure_db_ready()
    async with session_scope() as session:
        return list(
            (
                await session.scalars(
                    select(FeishuRoleAssignmentRecord).order_by(
                        FeishuRoleAssignmentRecord.id.asc()
                    )
                )
            ).all()
        )


async def get_feishu_role_assignment_by_id(
    assignment_id: int,
) -> FeishuRoleAssignmentRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        return await session.get(FeishuRoleAssignmentRecord, assignment_id)


async def get_feishu_role_assignment_by_subject(
    *,
    subject_type: str,
    subject_ref: str,
) -> FeishuRoleAssignmentRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        return await session.scalar(
            select(FeishuRoleAssignmentRecord).where(
                FeishuRoleAssignmentRecord.subject_type == subject_type,
                FeishuRoleAssignmentRecord.subject_ref == subject_ref,
            )
        )


async def create_feishu_role_assignment(
    *,
    subject_type: str,
    subject_ref: str,
    role: str,
    granted_by: str,
) -> FeishuRoleAssignmentRecord | None:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            existing = await session.scalar(
                select(FeishuRoleAssignmentRecord.id).where(
                    FeishuRoleAssignmentRecord.subject_type == subject_type,
                    FeishuRoleAssignmentRecord.subject_ref == subject_ref,
                )
            )
            if existing is not None:
                return None
            record = FeishuRoleAssignmentRecord(
                subject_type=subject_type,
                subject_ref=subject_ref,
                role=role,
                granted_by=granted_by,
            )
            session.add(record)
            await session.flush()
            return record
    except IntegrityError:
        return None


async def update_feishu_role_assignment(
    *,
    assignment_id: int,
    role: str | None,
    granted_by: str | None,
) -> FeishuRoleAssignmentRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuRoleAssignmentRecord, assignment_id)
        if record is None:
            return None
        if role is not None:
            record.role = role
        if granted_by is not None:
            record.granted_by = granted_by
        await session.flush()
        return record


async def delete_feishu_role_assignment(
    assignment_id: int,
) -> FeishuRoleAssignmentRecord | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(FeishuRoleAssignmentRecord, assignment_id)
        if record is None:
            return None
        await session.delete(record)
        await session.flush()
        return record


# --- V5 Step 1: multi-model LLM fallback chain persistence ---


class PersistedLlmModelConfig(BaseModel):
    id: int
    config_key: str
    provider: str
    model_name: str
    base_url: str
    api_key: str
    priority: int
    enabled: bool
    last_health_status: str | None
    last_health_summary: str | None
    last_health_checked_at: object | None


def _llm_model_config_from_record(record: RuntimeLlmModelRecord) -> PersistedLlmModelConfig:
    return PersistedLlmModelConfig(
        id=record.id,
        config_key=record.config_key,
        provider=record.provider,
        model_name=record.model_name,
        base_url=record.base_url,
        api_key=record.api_key or "",
        priority=record.priority,
        enabled=bool(record.enabled),
        last_health_status=record.last_health_status,
        last_health_summary=record.last_health_summary,
        last_health_checked_at=record.last_health_checked_at,
    )


async def list_persisted_llm_model_configs(
    *, config_key: str = DEFAULT_RUNTIME_CONFIG_KEY
) -> list[PersistedLlmModelConfig]:
    await ensure_db_ready()
    async with session_scope() as session:
        records = (
            await session.scalars(
                select(RuntimeLlmModelRecord)
                .where(RuntimeLlmModelRecord.config_key == config_key)
                .order_by(
                    RuntimeLlmModelRecord.priority.asc(),
                    RuntimeLlmModelRecord.id.asc(),
                )
            )
        ).all()
    return [_llm_model_config_from_record(record) for record in records]


async def get_persisted_llm_model_config(
    model_id: int,
) -> PersistedLlmModelConfig | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RuntimeLlmModelRecord, model_id)
    return _llm_model_config_from_record(record) if record is not None else None


async def create_persisted_llm_model_config(
    *,
    config_key: str = DEFAULT_RUNTIME_CONFIG_KEY,
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str | None,
    priority: int,
    enabled: bool,
) -> PersistedLlmModelConfig | None:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            record = RuntimeLlmModelRecord(
                config_key=config_key,
                provider=provider,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key or None,
                priority=priority,
                enabled=enabled,
            )
            session.add(record)
            await session.flush()
            return _llm_model_config_from_record(record)
    except IntegrityError:
        return None


async def update_persisted_llm_model_config(
    model_id: int,
    *,
    provider: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
) -> PersistedLlmModelConfig | None:
    await ensure_db_ready()
    try:
        async with session_scope() as session:
            record = await session.get(RuntimeLlmModelRecord, model_id)
            if record is None:
                return None
            if provider is not None:
                record.provider = provider
            if model_name is not None:
                record.model_name = model_name
            if base_url is not None:
                record.base_url = base_url
            if api_key is not None and api_key.strip():
                record.api_key = api_key.strip()
            if priority is not None:
                record.priority = priority
            if enabled is not None:
                record.enabled = enabled
            await session.flush()
            return _llm_model_config_from_record(record)
    except IntegrityError:
        return None


async def delete_persisted_llm_model_config(
    model_id: int,
) -> PersistedLlmModelConfig | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RuntimeLlmModelRecord, model_id)
        if record is None:
            return None
        await session.delete(record)
        await session.flush()
        return _llm_model_config_from_record(record)


async def reorder_persisted_llm_model_configs(
    ordered_ids: list[int],
    *,
    config_key: str = DEFAULT_RUNTIME_CONFIG_KEY,
) -> list[PersistedLlmModelConfig] | None:
    """Rewrite priorities so the chain order matches ``ordered_ids``.

    Returns ``None`` if ``ordered_ids`` does not exactly cover every model in
    the config (caller treats that as a 409/422), otherwise the refreshed list.
    """

    await ensure_db_ready()
    async with session_scope() as session:
        records = list(
            (
                await session.scalars(
                    select(RuntimeLlmModelRecord).where(
                        RuntimeLlmModelRecord.config_key == config_key
                    )
                )
            ).all()
        )
        existing_ids = {record.id for record in records}
        if set(ordered_ids) != existing_ids or len(ordered_ids) != len(existing_ids):
            return None
        by_id = {record.id: record for record in records}
        for priority, model_id in enumerate(ordered_ids):
            by_id[model_id].priority = priority
        await session.flush()
        refreshed = (
            await session.scalars(
                select(RuntimeLlmModelRecord)
                .where(RuntimeLlmModelRecord.config_key == config_key)
                .order_by(
                    RuntimeLlmModelRecord.priority.asc(),
                    RuntimeLlmModelRecord.id.asc(),
                )
            )
        ).all()
    return [_llm_model_config_from_record(record) for record in refreshed]


async def update_persisted_llm_model_health(
    model_id: int,
    *,
    status: str,
    summary: str | None,
    checked_at,
) -> PersistedLlmModelConfig | None:
    await ensure_db_ready()
    async with session_scope() as session:
        record = await session.get(RuntimeLlmModelRecord, model_id)
        if record is None:
            return None
        record.last_health_status = status
        record.last_health_summary = summary
        record.last_health_checked_at = checked_at
        await session.flush()
        return _llm_model_config_from_record(record)
