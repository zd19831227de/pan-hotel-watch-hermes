from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


class Shop(Base):
    """店铺实体：树状结构的根节点（重构计划 §3.2）。

    shop_key 由服务端生成（`shop_` + uuid hex），创建后不可改，不从中文名或
    现有账号推导；展示名允许重名，不能成为解析外键。
    """

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    shop_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=local_now, onupdate=local_now
    )
