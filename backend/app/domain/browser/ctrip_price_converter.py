from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DEFAULT_COMMISSION_RATE: float = 0.12


def sell_to_base(sell_price_yuan: float, commission_rate: float = DEFAULT_COMMISSION_RATE) -> float:
    if commission_rate < 0:
        raise ValueError("commission_rate must not be negative")
    if sell_price_yuan < 0:
        raise ValueError("sell_price_yuan must not be negative")
    sell = Decimal(str(sell_price_yuan))
    rate = Decimal(str(commission_rate))
    return float((sell / (Decimal("1") + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def base_to_sell(base_price_yuan: float, commission_rate: float = DEFAULT_COMMISSION_RATE) -> float:
    if commission_rate < 0:
        raise ValueError("commission_rate must not be negative")
    if base_price_yuan < 0:
        raise ValueError("base_price_yuan must not be negative")
    base = Decimal(str(base_price_yuan))
    rate = Decimal(str(commission_rate))
    return float((base * (Decimal("1") + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
