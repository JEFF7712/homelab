from __future__ import annotations

import pytest

from predmarkbot.fees import estimate_fee_cents, round_trip_fee_cents


@pytest.mark.parametrize(
    "price_cents,size,expected_min",
    [
        (50, 1, 1),   # tiny order: at least 1¢
        (50, 10, 2),  # 10 contracts at 50¢: at least a couple cents
        (1, 100, 1),  # edge: 1¢ price
        (99, 100, 1), # edge: 99¢ price
    ],
)
def test_estimate_fee_is_nonnegative_and_reasonable(
    price_cents: int, size: int, expected_min: int
) -> None:
    fee = estimate_fee_cents(price_cents, size)
    assert fee >= expected_min
    # Sanity: fee should never exceed 25% of notional (gross over-bound)
    assert fee <= max(1, (size * price_cents) // 4)


def test_estimate_fee_is_monotonic_in_size() -> None:
    assert estimate_fee_cents(50, 5) <= estimate_fee_cents(50, 10)


def test_round_trip_is_sum_of_two_sides() -> None:
    # Round-trip for an arb: pay fee on YES side + fee on NO side
    rt = round_trip_fee_cents(yes_price=52, no_price=51, size=5)
    yes_fee = estimate_fee_cents(52, 5)
    no_fee = estimate_fee_cents(51, 5)
    assert rt == yes_fee + no_fee


def test_invalid_price_raises() -> None:
    with pytest.raises(ValueError):
        estimate_fee_cents(0, 10)
    with pytest.raises(ValueError):
        estimate_fee_cents(100, 10)


def test_invalid_size_raises() -> None:
    with pytest.raises(ValueError):
        estimate_fee_cents(50, 0)
