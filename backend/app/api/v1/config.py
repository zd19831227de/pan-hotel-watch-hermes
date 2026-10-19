from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status

from app.api.security import require_supervisor_api_key
from app.domain.config.service import (
    FeishuAgentConfigConflictError,
    FeishuAgentConfigNotFoundError,
    FeishuAgentProfileCreate,
    FeishuAgentProfilePatch,
    FeishuAdmissionPatch,
    FeishuAdmissionUserMutation,
    FeishuChatAgentBindingCreate,
    FeishuChatAgentBindingPatch,
    FeishuRoleAssignmentCreate,
    FeishuRoleAssignmentPatch,
    FeishuProfileCredentialsSet,
    FeishuProfileDeploymentError,
    IntegrationConfigUpdate,
    LlmModelsRequest,
    LlmModelConfigCreate,
    LlmModelConfigPatch,
    LlmModelReorderRequest,
    add_feishu_profile_admission_user_payload,
    create_feishu_agent_profile_payload,
    create_feishu_chat_agent_binding_payload,
    create_feishu_role_assignment_payload,
    delete_feishu_agent_profile_payload,
    set_feishu_profile_credentials_payload,
    delete_feishu_chat_agent_binding_payload,
    delete_feishu_role_assignment_payload,
    get_runtime_config,
    get_active_open_ids_payload,
    get_feishu_profile_admission_payload,
    get_supervisor_overview_payload,
    list_feishu_agent_profile_payloads,
    list_feishu_chat_agent_binding_payloads,
    list_feishu_role_assignment_payloads,
    resolve_api_key_role,
    remove_feishu_profile_admission_user_payload,
    update_feishu_agent_profile_payload,
    update_feishu_profile_admission_payload,
    update_feishu_chat_agent_binding_payload,
    update_feishu_role_assignment_payload,
)

router = APIRouter(prefix="/config", tags=["config"])


def _agent_config_http_exception(exc: ValueError) -> HTTPException:
    if isinstance(exc, FeishuAgentConfigConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FeishuAgentConfigNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def _require_supervisor_api_key(
    api_key: Annotated[
        str | None,
        Header(alias="X-Pan-Hotel-API-Key"),
    ] = None,
) -> None:
    await require_supervisor_api_key(api_key)


def _admission_http_exception(exc: ValueError) -> HTTPException:
    if isinstance(exc, FeishuProfileDeploymentError):
        return HTTPException(status_code=503, detail=str(exc))
    return _agent_config_http_exception(exc)


@router.get(
    "/feishu-agent-profiles",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def list_feishu_agent_profiles() -> dict[str, Any]:
    return {"profiles": await list_feishu_agent_profile_payloads()}


@router.post(
    "/feishu-agent-profiles",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def create_feishu_agent_profile(
    request: FeishuAgentProfileCreate,
) -> dict[str, Any]:
    try:
        profile = await create_feishu_agent_profile_payload(request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"profile": profile}


@router.patch(
    "/feishu-agent-profiles/{profile_key}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def patch_feishu_agent_profile(
    request: FeishuAgentProfilePatch,
    profile_key: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    try:
        profile = await update_feishu_agent_profile_payload(profile_key, request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"profile": profile}


@router.post(
    "/feishu-agent-profiles/{profile_key}/credentials",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def set_feishu_profile_credentials(
    request: FeishuProfileCredentialsSet,
    profile_key: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    """前端配置飞书应用凭据：写 YAML → 更新 .env → 部署 + 重启 Gateway。

    app_secret 不进日志、不进响应（响应只含凭据状态与指纹）。
    """
    try:
        profile = await set_feishu_profile_credentials_payload(profile_key, request)
    except (ValueError, FeishuProfileDeploymentError) as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"profile": profile}


@router.delete(
    "/feishu-agent-profiles/{profile_key}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def delete_feishu_agent_profile(
    profile_key: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    try:
        profile = await delete_feishu_agent_profile_payload(profile_key)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"status": "deleted", "profile": profile}


@router.get(
    "/feishu-chat-agent-bindings",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def list_feishu_chat_agent_bindings() -> dict[str, Any]:
    return {"bindings": await list_feishu_chat_agent_binding_payloads()}


@router.post(
    "/feishu-chat-agent-bindings",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def create_feishu_chat_agent_binding(
    request: FeishuChatAgentBindingCreate,
) -> dict[str, Any]:
    try:
        binding = await create_feishu_chat_agent_binding_payload(request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"binding": binding}


@router.patch(
    "/feishu-chat-agent-bindings/{binding_id}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def patch_feishu_chat_agent_binding(
    request: FeishuChatAgentBindingPatch,
    binding_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        binding = await update_feishu_chat_agent_binding_payload(binding_id, request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"binding": binding}


@router.delete(
    "/feishu-chat-agent-bindings/{binding_id}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def delete_feishu_chat_agent_binding(
    binding_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        binding = await delete_feishu_chat_agent_binding_payload(binding_id)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"status": "deleted", "binding": binding}


@router.get(
    "/feishu-role-assignments",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def list_feishu_role_assignments() -> dict[str, Any]:
    return {"assignments": await list_feishu_role_assignment_payloads()}


@router.post(
    "/feishu-role-assignments",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def create_feishu_role_assignment(
    request: FeishuRoleAssignmentCreate,
) -> dict[str, Any]:
    try:
        assignment = await create_feishu_role_assignment_payload(request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"assignment": assignment}


@router.patch(
    "/feishu-role-assignments/{assignment_id}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def patch_feishu_role_assignment(
    request: FeishuRoleAssignmentPatch,
    assignment_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        assignment = await update_feishu_role_assignment_payload(
            assignment_id,
            request,
        )
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"assignment": assignment}


@router.delete(
    "/feishu-role-assignments/{assignment_id}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def delete_feishu_role_assignment(
    assignment_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        assignment = await delete_feishu_role_assignment_payload(assignment_id)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {"status": "deleted", "assignment": assignment}


@router.get(
    "/feishu/profiles/{profile_key}/admission",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def get_feishu_profile_admission(
    profile_key: str = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
) -> dict[str, Any]:
    try:
        return await get_feishu_profile_admission_payload(profile_key)
    except ValueError as exc:
        raise _admission_http_exception(exc) from exc


@router.patch(
    "/feishu/profiles/{profile_key}/admission",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def patch_feishu_profile_admission(
    request: FeishuAdmissionPatch,
    profile_key: str = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
) -> dict[str, Any]:
    try:
        return await update_feishu_profile_admission_payload(profile_key, request)
    except ValueError as exc:
        raise _admission_http_exception(exc) from exc


@router.post(
    "/feishu/profiles/{profile_key}/admission/users",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def add_feishu_profile_admission_user(
    request: FeishuAdmissionUserMutation,
    profile_key: str = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
) -> dict[str, Any]:
    try:
        return await add_feishu_profile_admission_user_payload(profile_key, request)
    except ValueError as exc:
        raise _admission_http_exception(exc) from exc


@router.delete(
    "/feishu/profiles/{profile_key}/admission/users/{open_id}",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def remove_feishu_profile_admission_user(
    profile_key: str = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
    open_id: str = Path(pattern=r"^ou_[A-Za-z0-9_-]+$", max_length=128),
    group_key: str = Query(pattern=r"^[0-9a-f]{16}$"),
) -> dict[str, Any]:
    try:
        return await remove_feishu_profile_admission_user_payload(
            profile_key,
            group_key=group_key,
            open_id=open_id,
        )
    except ValueError as exc:
        raise _admission_http_exception(exc) from exc


@router.get(
    "/feishu/profiles/{profile_key}/admission/active-open-ids",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def get_feishu_profile_active_open_ids(
    profile_key: str = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
) -> dict[str, Any]:
    return await get_active_open_ids_payload(profile_key)


@router.get(
    "/supervisor-overview",
    dependencies=[Depends(_require_supervisor_api_key)],
)
async def get_supervisor_overview() -> dict[str, Any]:
    return await get_supervisor_overview_payload()


@router.get("/integrations")
async def get_integration_config() -> dict[str, Any]:
    return (await get_runtime_config().snapshot()).model_dump(mode="json")


@router.post("/integrations", dependencies=[Depends(require_supervisor_api_key)])
async def update_integration_config(update: IntegrationConfigUpdate) -> dict[str, Any]:
    return (await get_runtime_config().update(update)).model_dump(mode="json")


@router.post(
    "/integrations/llm/test",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def test_llm_connection(update: LlmModelsRequest | None = None) -> dict[str, Any]:
    try:
        result = await get_runtime_config().test_llm_connection(update)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return result.model_dump(mode="json")


@router.post(
    "/integrations/llm/models",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def list_llm_models(update: LlmModelsRequest | None = None) -> dict[str, Any]:
    try:
        result = await get_runtime_config().list_llm_models(update)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return result.model_dump(mode="json")


# --- V5 Step 1: multi-model fallback chain management ---

@router.get(
    "/integrations/llm/model-configs",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def list_llm_model_configs() -> dict[str, Any]:
    runtime = get_runtime_config()
    models = await runtime.list_model_configs()
    return {"models": [model.model_dump(mode="json") for model in models]}


@router.post(
    "/integrations/llm/model-configs",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supervisor_api_key)],
)
async def create_llm_model_config(request: LlmModelConfigCreate) -> dict[str, Any]:
    try:
        model, hermes_sync = await get_runtime_config().create_model_config(request)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {
        "model": model.model_dump(mode="json"),
        "hermes_sync": hermes_sync.model_dump(mode="json"),
    }


@router.put(
    "/integrations/llm/model-configs/{model_id}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def update_llm_model_config(
    request: LlmModelConfigPatch,
    model_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        model, hermes_sync = await get_runtime_config().update_model_config(
            model_id, request
        )
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {
        "model": model.model_dump(mode="json"),
        "hermes_sync": hermes_sync.model_dump(mode="json"),
    }


@router.delete(
    "/integrations/llm/model-configs/{model_id}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def delete_llm_model_config(
    model_id: int = Path(ge=1),
) -> dict[str, Any]:
    try:
        return await get_runtime_config().delete_model_config(model_id)
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc


@router.post(
    "/integrations/llm/model-configs/reorder",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def reorder_llm_model_configs(
    request: LlmModelReorderRequest,
) -> dict[str, Any]:
    try:
        models, hermes_sync = await get_runtime_config().reorder_model_configs(
            request
        )
    except ValueError as exc:
        raise _agent_config_http_exception(exc) from exc
    return {
        "models": [model.model_dump(mode="json") for model in models],
        "hermes_sync": hermes_sync.model_dump(mode="json"),
    }


@router.post(
    "/integrations/llm/model-configs/sync-hermes",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def sync_llm_model_configs_to_hermes() -> dict[str, Any]:
    result = await get_runtime_config().sync_hermes_model_configs()
    return {"hermes_sync": result.model_dump(mode="json")}


@router.post(
    "/integrations/llm/model-configs/health",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def health_check_llm_model_configs() -> dict[str, Any]:
    runtime = get_runtime_config()
    models = await runtime.health_check_model_configs()
    return {"models": [model.model_dump(mode="json") for model in models]}
