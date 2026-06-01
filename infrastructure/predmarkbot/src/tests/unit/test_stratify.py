from __future__ import annotations

from datetime import UTC, datetime

from predmarkbot.research.stratify import (
    cohort_key,
    compute_implied_median,
    distance_bucket_idx,
    strike_step_for_series,
)

# ----- cohort_key -----

def test_cohort_key_groups_by_utc_date() -> None:
    a = datetime(2026, 5, 30, 23, 59, 0, tzinfo=UTC)
    b = datetime(2026, 5, 31, 0, 1, 0, tzinfo=UTC)
    assert cohort_key(a) != cohort_key(b)
    assert cohort_key(a).isoformat() == "2026-05-30"


# ----- strike_step_for_series -----

def test_strike_step_uniform_spacing() -> None:
    assert strike_step_for_series([70.0, 71.0, 72.0, 73.0]) == 1.0


def test_strike_step_irregular_spacing_returns_median() -> None:
    # diffs: 1, 1, 5, 1, 1 -> median 1.0
    assert strike_step_for_series([70.0, 71.0, 72.0, 77.0, 78.0, 79.0]) == 1.0


def test_strike_step_unsorted_input() -> None:
    assert strike_step_for_series([72.0, 70.0, 71.0]) == 1.0


def test_strike_step_single_strike_returns_none() -> None:
    assert strike_step_for_series([70.0]) is None


def test_strike_step_identical_strikes_returns_none() -> None:
    assert strike_step_for_series([70.0, 70.0, 70.0]) is None


# ----- compute_implied_median -----

def test_implied_median_picks_closest_to_50c() -> None:
    cohort = [(70.0, 5), (72.0, 30), (74.0, 48), (76.0, 75), (78.0, 95)]
    assert compute_implied_median(cohort) == 74.0


def test_implied_median_tiebreaks_to_higher_strike() -> None:
    # two strikes exactly 5¢ from 50¢: 45¢ and 55¢ → pick the higher strike
    cohort = [(70.0, 5), (74.0, 45), (76.0, 55), (80.0, 95)]
    assert compute_implied_median(cohort) == 76.0


def test_implied_median_returns_none_when_all_strikes_extreme() -> None:
    cohort = [(70.0, 1), (72.0, 3), (74.0, 5), (76.0, 95), (78.0, 99)]
    # closest to 50¢ is 5 → |5-50|=45 > 30 → undefined
    assert compute_implied_median(cohort) is None


def test_implied_median_returns_none_for_cohort_below_size_3() -> None:
    assert compute_implied_median([(70.0, 50), (72.0, 50)]) is None


# ----- distance_bucket_idx -----

def test_distance_bucket_idx_zero_at_median() -> None:
    assert distance_bucket_idx(strike=74.0, median=74.0, step=1.0) == 0


def test_distance_bucket_idx_positive_above_median() -> None:
    assert distance_bucket_idx(strike=77.0, median=74.0, step=1.0) == 3


def test_distance_bucket_idx_negative_below_median() -> None:
    assert distance_bucket_idx(strike=71.0, median=74.0, step=1.0) == -3


def test_distance_bucket_idx_non_unit_step() -> None:
    # step=0.5 -> distance of 1.0 is 2 buckets
    assert distance_bucket_idx(strike=75.0, median=74.0, step=0.5) == 2


def test_distance_bucket_idx_fractional_floor() -> None:
    # distance 1.4 / step 1.0 -> floor = 1
    assert distance_bucket_idx(strike=75.4, median=74.0, step=1.0) == 1
    # negative: -1.4 / 1.0 -> floor = -2 (Python's floor division)
    assert distance_bucket_idx(strike=72.6, median=74.0, step=1.0) == -2
