"""Pure statistics for favorite-longshot analysis."""
from __future__ import annotations

import math

BUCKET_WIDTH = 5
NUM_BUCKETS = 100 // BUCKET_WIDTH  # = 20


def bucket_for(price_cents: int) -> int:
    """Map a 0..99¢ price to its 5¢-wide bucket lower bound."""
    if not (0 <= price_cents <= 99):
        raise ValueError(f"price_cents must be 0..99, got {price_cents}")
    return (price_cents // BUCKET_WIDTH) * BUCKET_WIDTH


def bias_bps(*, realized: float, expected: float) -> int:
    """(realized - expected) in basis points, rounded to int.

    Positive = realized > expected (longshots winning more often than priced).
    """
    return round((realized - expected) * 10000)


def wilson_ci(
    *, n_success: int, n_total: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    More accurate than the normal-approximation CI for small n or
    extreme proportions. Returns (lo, hi). For n_total=0 returns (0, 1).
    """
    from scipy import stats  # type: ignore[import-untyped]  # lazy: not available in bot container

    if n_total == 0:
        return (0.0, 1.0)
    alpha = 1.0 - confidence
    z = stats.norm.ppf(1.0 - alpha / 2.0)
    p_hat = n_success / n_total
    denom = 1.0 + (z * z) / n_total
    center = (p_hat + (z * z) / (2.0 * n_total)) / denom
    half = (
        z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4.0 * n_total)) / n_total)
    ) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def binomial_p_value(
    *, n_success: int, n_total: int, expected: float
) -> float:
    """Two-tailed binomial test p-value for H0: p = expected."""
    from scipy import stats  # type: ignore[import-untyped]  # lazy: not available in bot container

    if n_total == 0:
        return 1.0
    result = stats.binomtest(k=n_success, n=n_total, p=expected, alternative="two-sided")
    return float(result.pvalue)
