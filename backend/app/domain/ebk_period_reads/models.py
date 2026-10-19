from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, BigInteger, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


def local_today_start() -> datetime:
    now = local_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class EbkPeriodReadSnapshot(Base):
    """周/月维度读取快照：append-only，同日多次抓取各留一条，查询取最新。

    仅供飞书问答（Hermes 工具）按需读取；captured_at 不是当天即视为过期。
    """

    __tablename__ = "ebk_period_read_snapshots"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    data_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    # 店铺维度（2026-09-16）：多店铺同维度快照并存，问答/日终快照按账号过滤。
    # 存量行为 NULL，由迁移按「首个有效绑定组」回填（历史写入只走过第一个组）。
    platform_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    caliber: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    # 业务日期（day 维度=页面数据日期，从 data_period_label 提取；week/month
    # 与历史行无此列）。此前 day 业务日期按 captured-1 换算，9 点前美团按日
    # 页面仍是前前日数据却被标成昨日口径——现按页面真实数据日期落库。
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)


class EbkPeriodDaySnapshotRun(Base):
    """昨日快照日终轮次运行记录：每轮一条（append-only），供运行日志展示。

    与 ebk_period_read_snapshots（每维度一条、仅 ok 落库）互补：本表记录轮次
    整体结果——全部失败/error 的轮次也要在日志里可见。
    """

    __tablename__ = "ebk_period_day_snapshot_runs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    platform_results_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ok_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_available_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=local_now)
