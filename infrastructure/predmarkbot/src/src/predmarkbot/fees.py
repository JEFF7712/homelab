"""Fee math. Conservative over-estimate by design.

Kalshi's published trading fee (verify against current docs):
    fee_per_side = ceil(0.07 * size * price_in_dollars * (1 - price_in_dollars))

Where price_in_dollars = price_cents / 100.

We compute in integer cents and round UP, so the strategy will sometimes
decline trades that are technically profitable but will never enter losing
trades due to fee underestimation.
"""
from __future__ import annotations

import math

_FEE_COEFFICIENT_BPS = 700  # 0.07 in basis points (1/100 of a percent)


def _validate(price_cents: int, size: int) -> None:
    if not (1 <= price_cents <= 99):
        raise ValueError(f"price_cents must be 1..99, got {price_cents}")
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")


def estimate_fee_cents(price_cents: int, size: int) -> int:
    """Conservative upper-bound on Kalshi's per-side fee, in integer cents."""
    _validate(price_cents, size)
    # 0.07 * size * (price/100) * (1 - price/100) in cents
    #   = 0.07 * size * price * (100 - price) / 100
    #   in cents (price is already cents, size is contracts)
    # Multiply numerator first to keep ints, then ceil-divide.
    numerator = _FEE_COEFFICIENT_BPS * size * price_cents * (100 - price_cents)
    # denominator: 10000 (basis points scale) * 100 (price-as-percent)
    denominator = 10000 * 100
    return max(1, math.ceil(numerator / denominator))


def round_trip_fee_cents(yes_price: int, no_price: int, size: int) -> int:
    """Total fees to enter both legs of a YES+NO arbitrage at the given size."""
    return estimate_fee_cents(yes_price, size) + estimate_fee_cents(no_price, size)
