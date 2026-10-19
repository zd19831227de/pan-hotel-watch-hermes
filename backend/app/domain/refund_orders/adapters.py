"""退单抓取平台适配器（缝隙）。

TODO（待三平台浏览器 CDP 在线后逆向订单管理页的退单/已取消订单区）：
- meituan: 订单管理 → 已取消（当日），按房型统计
- ctrip / fliggy: 订单管理对应退单口径

逆向前默认 NotReady：返回 not_ready，不产生假数据。
接入时实现 :class:`RefundOrderAdapter` 并 :func:`register_refund_adapter`。
"""

from __future__ import annotations

from typing import Any, Protocol


class RefundAdapterError(RuntimeError):
    """退单适配器异常，message 面向运营可读。"""


class RefundAdapterNotReadyError(RefundAdapterError):
    """平台退单抓取尚未逆向接入。"""


class RefundOrderAdapter(Protocol):
    platform: str

    async def fetch_refund_orders(self) -> dict[str, Any]:
        """返回 {refund_date: YYYY-MM-DD, total_count: int, by_room_type: {房型: 数量}}。"""
        ...


class NotReadyRefundAdapter:
    def __init__(self, platform: str) -> None:
        self.platform = platform

    async def fetch_refund_orders(self) -> dict[str, Any]:
        raise RefundAdapterNotReadyError(
            f"{self.platform} 退单抓取尚未接入（等待订单管理页逆向），未产生数据。"
        )


_ADAPTERS: dict[str, RefundOrderAdapter] = {}


def register_refund_adapter(adapter: RefundOrderAdapter) -> None:
    _ADAPTERS[adapter.platform] = adapter


def _register_default_adapters() -> None:
    """按 settings 开关注册已逆向的真实退单适配器（默认关，测试/未配置环境 NotReady）。

    美团（订单管理-已取消，当日下单口径）、携程（今日预订-已取消，当日取消口径）
    已接通（2026-09-02）；飞猪待逆向。
    """
    try:
        from app.core.settings import get_settings

        settings = get_settings()
        if getattr(settings, "refund_meituan_real_adapter_enabled", False):
            from .meituan_refund_adapter import MeituanRefundAdapter

            register_refund_adapter(MeituanRefundAdapter())
        if getattr(settings, "refund_ctrip_real_adapter_enabled", False):
            from .ctrip_refund_adapter import CtripRefundAdapter

            register_refund_adapter(CtripRefundAdapter())
        if getattr(settings, "refund_fliggy_real_adapter_enabled", False):
            from .fliggy_refund_adapter import FliggyRefundAdapter

            register_refund_adapter(FliggyRefundAdapter())
    except Exception:
        pass


_register_default_adapters()


def get_refund_adapter(platform: str) -> RefundOrderAdapter:
    return _ADAPTERS.get(platform, NotReadyRefundAdapter(platform))
