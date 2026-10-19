"""今日预定抓取平台适配器（缝隙）。

只供飞书 @机器人 触发（只读）；不挂定时轮询、不做前端页面。逆向前默认 NotReady，
不产生假数据。接入时实现 :class:`ReservationOrderAdapter` 并 :func:`register`。
"""

from __future__ import annotations

from typing import Any, Protocol


class ReservationAdapterError(RuntimeError):
    """今日预定适配器异常，message 面向运营可读。"""


class ReservationAdapterNotReadyError(ReservationAdapterError):
    """平台今日预定抓取尚未逆向接入。"""


class ReservationOrderAdapter(Protocol):
    platform: str

    async def fetch_today_reservations(self) -> dict[str, Any]:
        """返回 {reservation_date: YYYY-MM-DD, total_count, by_room_type, by_hour, time_caliber}。"""
        ...


class NotReadyReservationAdapter:
    def __init__(self, platform: str) -> None:
        self.platform = platform

    async def fetch_today_reservations(self) -> dict[str, Any]:
        raise ReservationAdapterNotReadyError(
            f"{self.platform} 今日预定抓取尚未接入（等待订单管理页逆向），未产生数据。"
        )


_ADAPTERS: dict[str, ReservationOrderAdapter] = {}


def register_reservation_adapter(adapter: ReservationOrderAdapter) -> None:
    _ADAPTERS[adapter.platform] = adapter


def _register_default_adapters() -> None:
    """按 settings 开关注册已逆向的真实今日预定适配器（默认关）。

    携程（订单管理-今日预订，全部今日订单口径）、飞猪（订单列表 listV2，今日全部口径）、
    美团（订单管理-全部订单，购买时间=今天口径）已接通。
    """
    try:
        from app.core.settings import get_settings

        settings = get_settings()
        if getattr(settings, "reservation_ctrip_real_adapter_enabled", False):
            from .ctrip_reservation_adapter import CtripReservationAdapter

            register_reservation_adapter(CtripReservationAdapter())
        if getattr(settings, "reservation_fliggy_real_adapter_enabled", False):
            from .fliggy_reservation_adapter import FliggyReservationAdapter

            register_reservation_adapter(FliggyReservationAdapter())
        if getattr(settings, "reservation_meituan_real_adapter_enabled", False):
            from .meituan_reservation_adapter import MeituanReservationAdapter

            register_reservation_adapter(MeituanReservationAdapter())
    except Exception:
        pass


_register_default_adapters()


def get_reservation_adapter(platform: str) -> ReservationOrderAdapter:
    return _ADAPTERS.get(platform, NotReadyReservationAdapter(platform))
