from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.db.session import ensure_db_ready, session_scope

from .models import Shop


def _new_shop_key() -> str:
    return f"shop_{uuid.uuid4().hex}"


async def list_shops() -> list[Shop]:
    await ensure_db_ready()
    async with session_scope() as session:
        result = await session.execute(
            select(Shop).order_by(Shop.sort_order, Shop.id)
        )
        return list(result.scalars().all())


async def get_shop_by_key(shop_key: str) -> Shop | None:
    await ensure_db_ready()
    async with session_scope() as session:
        result = await session.execute(select(Shop).where(Shop.shop_key == shop_key))
        return result.scalars().first()


async def create_shop(
    *, name: str, short_name: str | None = None, sort_order: int = 0
) -> Shop:
    await ensure_db_ready()
    async with session_scope() as session:
        shop = Shop(
            shop_key=_new_shop_key(),
            name=name,
            short_name=short_name,
            sort_order=sort_order,
        )
        session.add(shop)
        await session.flush()
        await session.refresh(shop)
        return shop


async def update_shop_by_key(
    shop_key: str,
    *,
    name: str | None = None,
    short_name: str | None = None,
    sort_order: int | None = None,
) -> Shop | None:
    await ensure_db_ready()
    async with session_scope() as session:
        result = await session.execute(select(Shop).where(Shop.shop_key == shop_key))
        shop = result.scalars().first()
        if shop is None:
            return None
        if name is not None:
            shop.name = name
        if short_name is not None:
            shop.short_name = short_name
        if sort_order is not None:
            shop.sort_order = sort_order
        await session.flush()
        await session.refresh(shop)
        return shop


async def delete_shop_by_key(shop_key: str) -> bool:
    await ensure_db_ready()
    async with session_scope() as session:
        result = await session.execute(delete(Shop).where(Shop.shop_key == shop_key))
        return bool(result.rowcount)
