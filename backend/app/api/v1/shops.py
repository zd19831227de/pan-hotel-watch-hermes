from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.security import require_operator_api_key, require_supervisor_api_key
from app.domain.shops import service
from app.domain.shops.service import ShopNotFound, ShopReferenceBlocked

router = APIRouter(prefix="/shops", tags=["shops"])


class ShopCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    short_name: str | None = Field(default=None, max_length=32)
    sort_order: int = 0


class ShopUpdateRequest(BaseModel):
    # PATCH 只改展示元数据（§3.2.1）；shop_key 不可改。
    name: str | None = Field(default=None, min_length=1, max_length=128)
    short_name: str | None = Field(default=None, max_length=32)
    sort_order: int | None = None


@router.get("")
async def list_shops_route() -> dict:
    return {"shops": await service.list_shops()}


@router.get("/tree")
async def get_shop_tree_route() -> dict:
    return await service.list_shop_tree()


@router.post("", dependencies=[Depends(require_operator_api_key)])
async def create_shop_route(request: ShopCreateRequest) -> dict:
    try:
        shop = await service.create_shop(request.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {"shop": shop}


@router.get("/{shop_key}")
async def get_shop_route(shop_key: Annotated[str, Field(min_length=1, max_length=64)]) -> dict:
    try:
        return {"shop": await service.get_shop(shop_key)}
    except ShopNotFound:
        raise HTTPException(status_code=404, detail="店铺不存在。") from None


@router.patch(
    "/{shop_key}",
    dependencies=[Depends(require_operator_api_key)],
)
async def patch_shop_route(
    shop_key: Annotated[str, Field(min_length=1, max_length=64)],
    request: ShopUpdateRequest,
) -> dict:
    try:
        shop = await service.update_shop(shop_key, request.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except ShopNotFound:
        raise HTTPException(status_code=404, detail="店铺不存在。") from None
    return {"shop": shop}


@router.delete(
    "/{shop_key}",
    dependencies=[Depends(require_supervisor_api_key)],
)
async def delete_shop_route(
    shop_key: Annotated[str, Field(min_length=1, max_length=64)],
) -> dict:
    try:
        return await service.delete_shop(shop_key)
    except ShopNotFound:
        raise HTTPException(status_code=404, detail="店铺不存在。") from None
    except ShopReferenceBlocked as error:
        raise HTTPException(
            status_code=409,
            detail="店铺仍有引用绑定，禁止删除：" + "；".join(error.references),
        ) from None
