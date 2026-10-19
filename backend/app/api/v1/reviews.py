from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.api.security import require_operator_api_key
from app.domain.review_management.adapters import ReviewAdapterError
from app.domain.review_management import repository
from app.domain.review_management import service
from app.domain.review_management.types import (
    AUTO_REPLY_MODES,
    REVIEW_CATEGORIES,
    REVIEW_PLATFORMS,
    REVIEW_REPLY_STATUSES,
    ReplyTemplateInput,
    ReviewAutoConfigPatch,
)
from app.domain.review_management.adapters import list_ready_platforms

router = APIRouter(prefix="/reviews", tags=["reviews"])

_PLATFORM_QUERY = Query(pattern="^(ctrip|meituan|fliggy)$")
_PLATFORM_QUERY_OR_NONE = Query(default=None, pattern="^(ctrip|meituan|fliggy)$")
_SESSION_QUERY = Query(default=None, max_length=128)


class ReviewFetchRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    browser_session_id: str | None = Field(default=None, max_length=128)
    # 飞书「刷新读取评价」：按群绑定的监控组解析店铺现读（Hermes 传 chat_id）。
    chat_id: str | None = Field(default=None, max_length=64)


class ReviewImportHistoryRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    browser_session_id: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=200, ge=1, le=1000)


class ReviewAutoUpdateTemplatesRequest(BaseModel):
    """「自动更新评价」：覆盖式重建店铺模板，前端必须先弹确认框。"""

    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    browser_session_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=200, ge=1, le=1000)


class ReviewProcessPendingRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    browser_session_id: str | None = Field(default=None, max_length=128)


class ReviewAutoConfigRequest(BaseModel):
    platform: str = Field(pattern="^(ctrip|meituan|fliggy)$")
    browser_session_id: str | None = Field(default=None, max_length=128)
    # 按监控组+平台保存配置：浏览器账号与飞书群从监控组继承（不手填飞书群）。
    ctrip_watch_group_key: str | None = Field(default=None, max_length=64)
    auto_reply_mode: str | None = Field(default=None, pattern="^(off|semi_auto|full_auto)$")
    auto_fetch_enabled: bool | None = None
    fetch_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    # 每日定时触发时间（HH:MM，参照 EBK 定时推送的时间设置）。
    auto_process_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    approval_chat_id: str | None = Field(default=None, max_length=128)


def _platform_catalog() -> dict[str, Any]:
    return {
        "platforms": list(REVIEW_PLATFORMS),
        "categories": list(REVIEW_CATEGORIES),
        "auto_reply_modes": list(AUTO_REPLY_MODES),
        "reply_statuses": list(REVIEW_REPLY_STATUSES),
        "reply_max_length": service.REPLY_MAX_LENGTH,
        "adapter_ready_platforms": list_ready_platforms(),
    }


@router.get("")
async def list_reviews_route(
    platform: str = _PLATFORM_QUERY,
    reply_status: str | None = Query(default=None, pattern="^(pending|replied|ignored)$"),
    category: str | None = Query(default=None, pattern="^(good|medium|bad)$"),
    browser_session_id: str | None = _SESSION_QUERY,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    reviews = await repository.list_reviews(
        platform=platform,
        reply_status=reply_status,
        category=category,
        browser_session_id=browser_session_id,
        limit=limit,
    )
    return {"reviews": reviews, "catalog": _platform_catalog()}


@router.get("/stores")
async def list_review_stores_route(
    platform: str = _PLATFORM_QUERY,
) -> dict[str, Any]:
    stores = await service.list_review_stores(platform=platform)
    return {"stores": stores}


@router.get("/groups")
async def list_review_groups_route() -> dict[str, Any]:
    """评价店铺下拉 = 全部携程 EBK 监控组（含未绑浏览器的组），带三平台挂载状态。

    同 EBK 挂载模型：一个监控组=一个店铺；美团/飞猪通过 anchor 挂在监控组下。
    未挂载/未绑浏览器的子平台 mounted=false，前端禁用该 tab。
    """
    groups = await service.list_review_groups()
    return {"groups": groups}


@router.get("/pending-count")
async def get_pending_count_route(
    platform: str = _PLATFORM_QUERY,
    ctrip_watch_group_key: str = Query(default=None, max_length=64),
    browser_session_id: str | None = _SESSION_QUERY,
    chat_id: str = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """查询某监控组某子平台的未回复数量（飞书只读问答用，不返回评价正文）。

    解析顺序：browser_session_id > ctrip_watch_group_key > chat_id（按飞书群
    绑定的监控组解析，飞书问答主路径）。抓取记录挂在店铺绑定上，绝不能在
    缺参时回落平台级空配置——那会让问答永远「读取不到数据」。
    """
    session_id = browser_session_id
    group_keys: list[str] = []
    chat_had_bound_groups = False
    if ctrip_watch_group_key and not session_id:
        resolved, _ = await service.resolve_store_browser_session(
            ctrip_watch_group_key, platform
        )
        session_id = resolved
        if resolved:
            group_keys = [ctrip_watch_group_key]
        else:
            # 组存在但该平台未挂载：明确提示挂载问题，不落入下方
            # 「未绑定监控组」误导文案（组是绑了的）。
            return {
                "platform": platform,
                "ctrip_watch_group_key": ctrip_watch_group_key,
                "pending_count": None,
                "last_fetched_at": None,
                "stores": [],
                "summary": (
                    f"监控组 {ctrip_watch_group_key} 的 {platform} 子平台"
                    "未挂载浏览器账号，请先在 EBK 监控里挂载该平台后再查。"
                ),
            }
    if not session_id and not group_keys and chat_id:
        from app.domain.ctrip_ebooking_watch.repository import (
            list_data_center_watch_groups_by_feishu_chat_id,
        )

        bound_groups = await list_data_center_watch_groups_by_feishu_chat_id(chat_id)
        chat_had_bound_groups = bool(bound_groups)
        for group in bound_groups:
            resolved, _ = await service.resolve_store_browser_session(
                str(group.get("group_key") or ""), platform
            )
            if resolved:
                group_keys.append(str(group.get("group_key") or ""))
                if session_id is None:
                    session_id = resolved
        if len(group_keys) > 1:
            # 一群多店：不能把数量算到某一家，返回逐店明细由问答层复述。
            session_id = None
    if session_id and not group_keys:
        config = await repository.get_effective_review_config(
            platform=platform, browser_session_id=session_id
        )
        return {
            "platform": platform,
            "ctrip_watch_group_key": ctrip_watch_group_key or None,
            "pending_count": config.get("last_pending_count"),
            "last_fetched_at": config.get("last_fetched_at"),
        }
    if group_keys:
        stores = []
        for group_key in group_keys:
            resolved, _ = await service.resolve_store_browser_session(
                group_key, platform
            )
            if not resolved:
                continue
            config = await repository.get_effective_review_config(
                platform=platform, browser_session_id=resolved
            )
            stores.append(
                {
                    "ctrip_watch_group_key": group_key,
                    "pending_count": config.get("last_pending_count"),
                    "last_fetched_at": config.get("last_fetched_at"),
                }
            )
        single = stores[0] if len(stores) == 1 else {}
        summary = _pending_count_summary(stores, chat_id=chat_id)
        return {
            "platform": platform,
            "ctrip_watch_group_key": ctrip_watch_group_key or None,
            "pending_count": single.get("pending_count"),
            "last_fetched_at": single.get("last_fetched_at"),
            "stores": stores,
            "summary": summary,
        }
    if chat_id and chat_had_bound_groups:
        # 群已绑监控组但该平台子平台未挂载：明确提示挂载问题，绝不回落
        # 平台级旧数量（否则旧数+误导文案一起报给飞书问答）。
        return {
            "platform": platform,
            "ctrip_watch_group_key": None,
            "pending_count": None,
            "last_fetched_at": None,
            "stores": [],
            "summary": (
                f"飞书群已绑定监控组，但组内 {platform} 子平台未挂载浏览器账号，"
                "请先在 EBK 监控里挂载该平台后再查。"
            ),
        }
    config = await repository.get_effective_review_config(
        platform=platform, browser_session_id=None
    )
    return {
        "platform": platform,
        "ctrip_watch_group_key": None,
        "pending_count": config.get("last_pending_count"),
        "last_fetched_at": config.get("last_fetched_at"),
        "stores": [],
        "summary": (
            f"当前飞书群未绑定携程 EBK 监控组，无法解析 {platform} 店铺。"
            if chat_id
            else None
        ),
    }


def _pending_count_summary(
    stores: list[dict[str, Any]], *, chat_id: str | None
) -> str | None:
    if not stores:
        return "当前飞书群未绑定携程 EBK 监控组，无法解析店铺。"
    if all(store.get("last_fetched_at") is None for store in stores):
        return "监控组已解析，但该店铺还没有抓取过评价，可先执行一次评价抓取。"
    if len(stores) > 1:
        return "当前飞书群绑定了多个监控组，请按店铺查看 stores 明细。"
    return None


@router.post("/fetch", dependencies=[Depends(require_operator_api_key)])
async def fetch_reviews_route(request: ReviewFetchRequest) -> dict[str, Any]:
    """只抓取并刷新未回复计数；不触发半自动/全自动处理（2026-09-16 起定时触发）。"""
    session_ids: list[str | None] = []
    if request.browser_session_id:
        session_ids = [request.browser_session_id]
    elif request.chat_id:
        # 飞书链路：按群绑定的监控组解析店铺；多个组绑同一群时逐店铺抓取。
        session_ids = await _chat_bound_store_sessions(request.chat_id, request.platform)
        if not session_ids:
            # 传了 chat_id 但解析为空（未绑监控组/该平台未挂载）时绝不能回落
            # 平台默认店铺抓取——那会拉错浏览器、跑错店铺并把默认店铺的
            # pending 计数刷成“已刷新”，与 pending-count 的未绑定口径矛盾。
            return {
                "fetch": {
                    "status": "skipped",
                    "summary": (
                        "当前飞书群绑定的监控组未挂载该平台评价账号"
                        "（或未绑定监控组），未执行抓取。"
                    ),
                    "browser_session_id": None,
                },
                "process": {
                    "status": "skipped",
                    "summary": "未执行抓取，无后续评价处理。",
                },
            }
    if not session_ids:
        # 兼容旧路径：两者都没传的旧调用方按平台默认端口账号抓取。
        session_ids = [None]
    results = [
        await service.fetch_and_store_reviews(
            platform=request.platform,
            browser_session_id=session_id,
        )
        for session_id in session_ids
    ]
    first = results[0]
    payload: dict[str, Any] = {
        "fetch": {**first, "browser_session_id": session_ids[0]},
        "process": {
            "status": "skipped",
            "summary": (
                "抓取不触发自动处理：自动处理按店铺「定时处理时间」到点执行，"
                "或点「处理待回复」手动触发。"
            ),
        },
    }
    if len(results) > 1:
        payload["stores"] = [
            {
                "browser_session_id": session_id,
                "fetch": result,
            }
            for session_id, result in zip(session_ids, results)
        ]
    return payload


async def _chat_bound_store_sessions(chat_id: str, platform: str) -> list[str]:
    """飞书群绑定的监控组 -> 该平台已挂载的浏览器账号列表。"""
    from app.domain.ctrip_ebooking_watch.repository import (
        list_data_center_watch_groups_by_feishu_chat_id,
    )

    bound_groups = await list_data_center_watch_groups_by_feishu_chat_id(chat_id)
    sessions: list[str] = []
    for group in bound_groups:
        resolved, _ = await service.resolve_store_browser_session(
            str(group.get("group_key") or ""), platform
        )
        if resolved and resolved not in sessions:
            sessions.append(resolved)
    return sessions


@router.get("/templates")
async def list_templates_route(
    platform: str = _PLATFORM_QUERY,
    category: str | None = Query(default=None, pattern="^(good|medium|bad)$"),
    only_enabled: bool = Query(default=False),
    browser_session_id: str | None = _SESSION_QUERY,
) -> dict[str, Any]:
    templates = await repository.list_templates(
        platform=platform,
        category=category,
        only_enabled=only_enabled,
        browser_session_id=browser_session_id,
    )
    return {"templates": templates}


@router.post("/templates", dependencies=[Depends(require_operator_api_key)])
async def save_template_route(request: ReplyTemplateInput) -> dict[str, Any]:
    try:
        template = await repository.save_template(
            template=request, browser_session_id=request.browser_session_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"template": template}


@router.delete("/templates/{template_id}", dependencies=[Depends(require_operator_api_key)])
async def delete_template_route(template_id: int = Path(ge=1)) -> dict[str, Any]:
    deleted = await repository.delete_template(template_id=template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="模板不存在。")
    return {"deleted": True}


@router.post("/import-history", dependencies=[Depends(require_operator_api_key)])
async def import_history_route(request: ReviewImportHistoryRequest) -> dict[str, Any]:
    result = await service.import_history_as_templates(
        platform=request.platform,
        browser_session_id=request.browser_session_id,
        limit=request.limit,
    )
    return {"result": result}


@router.post(
    "/auto-update-templates", dependencies=[Depends(require_operator_api_key)]
)
async def auto_update_templates_route(
    request: ReviewAutoUpdateTemplatesRequest,
) -> dict[str, Any]:
    result = await service.auto_update_store_templates(
        platform=request.platform,
        browser_session_id=request.browser_session_id,
        limit=request.limit,
    )
    return {"result": result}


@router.post("/process-pending", dependencies=[Depends(require_operator_api_key)])
async def process_pending_route(request: ReviewProcessPendingRequest) -> dict[str, Any]:
    result = await service.process_pending_reviews(
        platform=request.platform,
        browser_session_id=request.browser_session_id,
        source="api",
    )
    return {"process": result}


@router.get("/tasks")
async def list_tasks_route(
    platform: str | None = _PLATFORM_QUERY_OR_NONE,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    tasks = await repository.list_review_tasks(platform=platform, limit=limit)
    return {"tasks": tasks}


@router.get("/auto-config")
async def get_auto_config_route(
    platform: str = _PLATFORM_QUERY,
    browser_session_id: str | None = _SESSION_QUERY,
) -> dict[str, Any]:
    config = await repository.get_effective_review_config(
        platform=platform, browser_session_id=browser_session_id
    )
    # 店铺未单独配飞书群时，展示监控组继承的飞书推送群（只读回显，不落库）。
    if not config.get("approval_chat_id") and config.get("browser_session_id"):
        inherited = await service.resolve_store_feishu_chat_id(
            platform, config["browser_session_id"]
        )
        if inherited:
            config["approval_chat_id"] = inherited
            config["approval_chat_id_inherited"] = True
    return {"config": config}


@router.put("/auto-config", dependencies=[Depends(require_operator_api_key)])
async def update_auto_config_route(request: ReviewAutoConfigRequest) -> dict[str, Any]:
    payload = request.model_dump(exclude_unset=True)
    payload.pop("platform", None)
    group_key = payload.pop("ctrip_watch_group_key", None)
    session_id = payload.pop("browser_session_id", None)
    patch = ReviewAutoConfigPatch(**payload)
    try:
        if group_key:
            # 按监控组+平台保存：浏览器账号与飞书群从监控组继承（不手填飞书群）。
            session_id, feishu_chat_id = await service.resolve_store_browser_session(
                group_key, request.platform
            )
            if not session_id:
                raise ValueError(
                    f"该监控组下 {request.platform} 子平台未挂载或未绑定浏览器，"
                    "请先在对应 EBK 监控里挂载到本监控组。"
                )
            # 飞书群以监控组为准：表单带回的空值不得清掉刚继承的群 ID。
            patch_data = patch.model_dump(exclude_unset=True)
            patch_data.pop("approval_chat_id", None)
            patch = ReviewAutoConfigPatch(**patch_data)
            await repository.upsert_store_binding(
                platform=request.platform,
                browser_session_id=session_id,
                platform_account_id="",
                display_name="",
                feishu_chat_id=feishu_chat_id,
            )
            config = await repository.update_store_config(
                platform=request.platform,
                browser_session_id=session_id,
                patch=patch,
            )
        elif session_id:
            # 兼容：直接按浏览器账号绑定（飞书群仅在显式提交时更新）。
            store_info = _resolve_store_info(request.platform, session_id)
            await repository.upsert_store_binding(
                platform=request.platform,
                browser_session_id=session_id,
                platform_account_id=store_info["platform_account_id"],
                display_name=store_info["display_name"],
                feishu_chat_id=patch.approval_chat_id
                if "approval_chat_id" in payload
                else None,
            )
            config = await repository.update_store_config(
                platform=request.platform,
                browser_session_id=session_id,
                patch=patch,
            )
        else:
            config = await repository.update_review_auto_config(
                platform=request.platform, patch=patch
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": config}


def _resolve_store_info(platform: str, browser_session_id: str) -> dict[str, str]:
    """店铺绑定的账号信息以浏览器会话为准；账号不存在时 422，避免写错店铺。"""
    from app.domain.browser.sessions import get_browser_session_store
    from app.domain.review_management.adapters import ReviewAdapterError, resolve_review_session_port

    try:
        resolve_review_session_port(platform, browser_session_id)
    except ReviewAdapterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_browser_session_store().get(browser_session_id)
    return {
        "platform_account_id": session.platform_account_id,
        "display_name": session.display_name or session.platform_account_id,
    }
