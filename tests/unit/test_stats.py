from __future__ import annotations

import math

import pytest

from predmarkbot.research.stats import (
    BUCKET_WIDTH,
    NUM_BUCKETS,
    bias_bps,
    binomial_p_value,
    bucket_for,
    wilson_ci,
)


def test_bucket_constants() -> None:
    assert BUCKET_WIDTH == 5
    assert NUM_BUCKETS == 20


@pytest.mark.parametrize(
    "price,expected_lo",
    [
        (0, 0), (1, 0), (4, 0),
        (5, 5), (9, 5),
        (50, 50),
        (94, 90),
        (95, 95), (99, 95),
    ],
)
def test_bucket_for_boundaries(price: int, expected_lo: int) -> None:
    assert bucket_for(price) == expected_lo


def test_bucket_for_out_of_range() -> None:
    with pytest.raises(ValueError):
        bucket_for(-1)
    with pytest.raises(ValueError):
        bucket_for(100)


def test_bias_bps_zero_when_realized_equals_expected() -> None:
    assert bias_bps(realized=0.5, expected=0.5) == 0


def test_bias_bps_positive_when_realized_above_expected() -> None:
    assert bias_bps(realized=0.10, expected=0.05) == 500


def test_bias_bps_negative_when_realized_below_expected() -> None:
    assert bias_bps(realized=0.30, expected=0.50) == -2000


def test_wilson_ci_known_value() -> None:
    # Wilson CI for 7/10 at 95% should be roughly (0.397, 0.892)
    lo, hi = wilson_ci(n_success=7, n_total=10, confidence=0.95)
    assert 0.38 <= lo <= 0.42
    assert 0.87 <= hi <= 0.92


def test_wilson_ci_zero_total_returns_full_range() -> None:
    lo, hi = wilson_ci(n_success=0, n_total=0, confidence=0.95)
    assert lo == 0.0
    assert hi == 1.0


def test_binomial_two_tailed_at_null() -> None:
    p = binomial_p_value(n_success=50, n_total=100, expected=0.5)
    assert math.isclose(p, 1.0, abs_tol=0.01)


def test_binomial_low_p_when_far_from_null() -> None:
    p = binomial_p_value(n_success=80, n_total=100, expected=0.5)
    assert p < 1e-8
