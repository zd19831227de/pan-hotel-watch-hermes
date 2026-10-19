from __future__ import annotations

"""浏览器操作租约（重构 P8）：同一浏览器会话的完整页面操作临界区互斥。

设计要点（09-05 计划 §5.2/§9.5）：
- 租约存 SQLite（API 与 worker 共见），键 = 浏览器会话；
- 短 TTL + 等待窗口：冲突方短暂排队后明确报 browser_busy，不无限等；
- 过期可接管（持有方崩溃后不永久锁死）；
- 存储不可用时 fail-closed：抛 LeaseUnavailable，调用方必须停止操作，
  绝不无租约继续读（不做降级放行）。
- 只覆盖只读链路；pricing 写链路接入前需单独审查（计划 §7 P8）。
"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.session import ensure_db_ready, session_scope

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

BigInt = BigInteger().with_variant(Integer, "sqlite")

DEFAULT_LEASE_TTL_SECONDS = 180
DEFAULT_WAIT_SECONDS = 6.0
RETRY_INTERVAL_SECONDS = 0.5


class BrowserOperationLease(Base):
    __tablename__ = "browser_operation_leases"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    lease_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    holder: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class LeaseConflict(Exception):
    """租约被他人持有且等待窗口内未释放（调用方应返回 browser_busy）。"""

    def __init__(self, lease_key: str, holder: str, expires_at: datetime) -> None:
        super().__init__(f"browser lease busy: {lease_key} held by {holder}")
        self.lease_key = lease_key
        self.holder = holder
        self.expires_at = expires_at


class LeaseUnavailable(Exception):
    """租约存储不可用——fail-closed：调用方必须停止浏览器操作。"""


class LeaseLost(Exception):
    """租约已被接管/释放，当前持有方不再有效（调用方必须立即停止操作）。"""


@dataclass(frozen=True)
class LeaseHandle:
    lease_key: str
    holder: str
    token: str


def _now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


def lease_holder_id(actor_id: str | None = None) -> str:
    base = f"pid{os.getpid()}"
    actor = str(actor_id or "").strip()
    return f"{base}:{actor}" if actor else base


def browser_session_lease_key(browser_session_id: str) -> str:
    return f"browser_session:{str(browser_session_id or '').strip()}"


async def acquire_operation_lease(
    lease_key: str,
    *,
    holder: str,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    metadata: dict | None = None,
) -> LeaseHandle:
    """获取租约；冲突时在等待窗口内重试，超时抛 LeaseConflict。"""
    key = str(lease_key or "").strip()
    if not key:
        raise ValueError("lease_key 不能为空。")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(wait_seconds))
    while True:
        handle = await _try_acquire(key, holder=holder, ttl_seconds=ttl_seconds, metadata=metadata)
        if handle is not None:
            return handle
        if loop.time() >= deadline:
            conflict = await _peek_conflict(key)
            raise LeaseConflict(key, conflict[0], conflict[1])
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)


async def _try_acquire(
    key: str,
    *,
    holder: str,
    ttl_seconds: float,
    metadata: dict | None,
) -> LeaseHandle | None:
    import json as _json

    token = uuid.uuid4().hex
    now = _now()
    expires = now + timedelta(seconds=max(0.05, float(ttl_seconds)))
    metadata_payload = _json.dumps(metadata or {}, ensure_ascii=False)
    try:
        await ensure_db_ready()
        async with session_scope() as session:
            # 先原子抢占既有行（含过期接管与同 holder 重入）。
            result = await session.execute(
                text(
                    """
                    UPDATE browser_operation_leases
                    SET holder = :holder, token = :token, expires_at = :expires,
                        acquired_at = :now, metadata_json = :metadata_json
                    WHERE lease_key = :key
                      AND (expires_at <= :now OR holder = :holder)
                    """.strip()
                ),
                {
                    "holder": holder,
                    "token": token,
                    "expires": expires,
                    "now": now,
                    "key": key,
                    "metadata_json": metadata_payload,
                },
            )
            if result.rowcount:
                return LeaseHandle(lease_key=key, holder=holder, token=token)
            dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
            insert_ignore = (
                "INSERT OR IGNORE INTO browser_operation_leases"
                if dialect == "sqlite"
                else "INSERT IGNORE INTO browser_operation_leases"
            )
            insert = await session.execute(
                text(
                    f"""
                    {insert_ignore}
                        (lease_key, holder, token, acquired_at, expires_at, metadata_json)
                    VALUES (:key, :holder, :token, :now, :expires, :metadata_json)
                    """.strip()
                ),
                {
                    "key": key,
                    "holder": holder,
                    "token": token,
                    "now": now,
                    "expires": expires,
                    "metadata_json": metadata_payload,
                },
            )
            if insert.rowcount:
                return LeaseHandle(lease_key=key, holder=holder, token=token)
        return None
    except DBAPIError as exc:
        raise LeaseUnavailable(f"browser lease store unavailable: {type(exc).__name__}") from exc


async def _peek_conflict(key: str) -> tuple[str, datetime]:
    try:
        async with session_scope() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT holder, expires_at FROM browser_operation_leases "
                        "WHERE lease_key = :key"
                    ),
                    {"key": key},
                )
            ).first()
    except DBAPIError as exc:
        raise LeaseUnavailable(f"browser lease store unavailable: {type(exc).__name__}") from exc
    if row is None:
        return ("unknown", _now())
    return (str(row[0]), row[1])


async def renew_operation_lease(
    handle: LeaseHandle,
    *,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
) -> None:
    """续期；租约已被接管/释放时抛 LeaseLost（调用方必须停止操作）。"""
    expires = _now() + timedelta(seconds=max(0.05, float(ttl_seconds)))
    try:
        await ensure_db_ready()
        async with session_scope() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE browser_operation_leases
                    SET expires_at = :expires
                    WHERE lease_key = :key AND holder = :holder AND token = :token
                    """.strip()
                ),
                {"expires": expires, "key": handle.lease_key, "holder": handle.holder, "token": handle.token},
            )
            if not result.rowcount:
                raise LeaseLost(handle.lease_key)
    except DBAPIError as exc:
        raise LeaseUnavailable(f"browser lease store unavailable: {type(exc).__name__}") from exc


async def release_operation_lease(handle: LeaseHandle) -> None:
    """释放租约（仅当仍是本持有方；已被接管则不得误删他人租约）。"""
    try:
        await ensure_db_ready()
        async with session_scope() as session:
            await session.execute(
                text(
                    "DELETE FROM browser_operation_leases "
                    "WHERE lease_key = :key AND holder = :holder AND token = :token"
                ),
                {"key": handle.lease_key, "holder": handle.holder, "token": handle.token},
            )
    except DBAPIError as exc:
        raise LeaseUnavailable(f"browser lease store unavailable: {type(exc).__name__}") from exc
