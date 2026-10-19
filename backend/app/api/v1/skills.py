from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.security import require_supervisor_api_key
from app.domain.skills.service import (
    SkillConflictError,
    SkillCreate,
    SkillNotFoundError,
    SkillUpdate,
    create_skill,
    delete_skill,
    get_skill,
    list_skills,
    update_skill,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _skill_http_exception(exc: ValueError) -> HTTPException:
    if isinstance(exc, SkillNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, SkillConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", dependencies=[Depends(require_supervisor_api_key)])
async def list_skills_route() -> dict[str, Any]:
    skills = await list_skills()
    return {"skills": [skill.model_dump(mode="json") for skill in skills]}


@router.get("/{name}", dependencies=[Depends(require_supervisor_api_key)])
async def get_skill_route(
    name: Annotated[str, Path(min_length=1, max_length=64)],
) -> dict[str, Any]:
    try:
        skill = await get_skill(name)
    except ValueError as exc:
        raise _skill_http_exception(exc) from exc
    return {"skill": skill.model_dump(mode="json")}


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_supervisor_api_key)])
async def create_skill_route(request: SkillCreate) -> dict[str, Any]:
    try:
        skill = await create_skill(request)
    except ValueError as exc:
        raise _skill_http_exception(exc) from exc
    return {"skill": skill.model_dump(mode="json")}


@router.put("/{name}", dependencies=[Depends(require_supervisor_api_key)])
async def update_skill_route(
    request: SkillUpdate,
    name: Annotated[str, Path(min_length=1, max_length=64)],
) -> dict[str, Any]:
    try:
        skill = await update_skill(name, request)
    except ValueError as exc:
        raise _skill_http_exception(exc) from exc
    return {"skill": skill.model_dump(mode="json")}


@router.delete("/{name}", dependencies=[Depends(require_supervisor_api_key)])
async def delete_skill_route(
    name: Annotated[str, Path(min_length=1, max_length=64)],
) -> dict[str, Any]:
    try:
        return await delete_skill(name)
    except ValueError as exc:
        raise _skill_http_exception(exc) from exc
