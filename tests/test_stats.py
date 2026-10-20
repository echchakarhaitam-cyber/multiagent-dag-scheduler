"""Tests for the statistics helpers (expected values computed by hand)."""

import math
import statistics

import pytest

from dagsched.stats import (
    bootstrap_ci,
    holm_bonferroni,
    mean_std,
    paired_differences,
    sign_test,
    win_rate,
)


def test_mean_std_known_values():
    # Mean 5; squared deviations 9, 1, 1, 9 sum to 20; sample variance 20 / 3.
    mean, std = mean_std([2, 4, 6, 8])
    assert mean == 5
    assert std == pytest.approx(math.sqrt(20 / 3))


def test_mean_std_single_value():
    assert mean_std([3.5]) == (3.5, 0.0)


def test_mean_std_rejects_empty():
    with pytest.raises(ValueError):
        mean_std([])


def test_bootstrap_ci_deterministic_and_contains_mean():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    low, high = bootstrap_ci(values, seed=3)
    assert (low, high) == bootstrap_ci(values, seed=3)
    assert low <= statistics.fmean(values) <= high
    assert min(values) <= low < high <= max(values)


def test_bootstrap_ci_narrower_at_lower_level():
    values = [1.0, 5.0, 2.0, 8.0, 3.0, 9.0, 4.0]
    wide = bootstrap_ci(values, level=0.95)
    narrow = bootstrap_ci(values, level=0.5)
    assert wide[0] <= narrow[0] <= narrow[1] <= wide[1]


def test_bootstrap_ci_seed_matters_and_single_value_collapses():
    values = [0.1, 0.4, 0.2, 0.9, 0.5]
    assert bootstrap_ci(values, seed=0) != bootstrap_ci(values, seed=1)
    assert bootstrap_ci([2.0]) == (2.0, 2.0)


@pytest.mark.parametrize(
    "kwargs", [{"level": 0.0}, {"level": 1.0}, {"n_resamples": 0}, {"n_resamples": 2.5}]
)
def test_bootstrap_ci_validation(kwargs):
    with pytest.raises(ValueError):
        bootstrap_ci([1.0, 2.0], **kwargs)


def test_bootstrap_ci_custom_statistic_resamples_items_as_units():
    pairs = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (4.0, 40.0)]

    def ratio_of_means(sample):
        return statistics.fmean(b for _, b in sample) / statistics.fmean(a for a, _ in sample)

    # Each resample keeps pairs together, so the ratio is exactly 10 in every resample.
    assert bootstrap_ci(pairs, statistic=ratio_of_means) == pytest.approx((10.0, 10.0))
    values = [1.0, 2.0, 5.0]
    assert bootstrap_ci(values) == bootstrap_ci(values, statistic=statistics.fmean)


def test_paired_differences():
    assert paired_differences([3, 5, 1], [1, 5, 2]) == [2, 0, -1]
    with pytest.raises(ValueError, match="equal length"):
        paired_differences([1, 2], [1])


def test_sign_test_all_wins():
    assert sign_test([-1.0] * 10) == 2 * 0.5**10 == 0.001953125


def test_sign_test_even_split_is_one():
    assert sign_test([-1] * 5 + [1] * 5) == 1.0


def test_sign_test_drops_zeros():
    # Zeros are ignored: same p-value as the 10 wins alone.
    assert sign_test([-1.0] * 10 + [0.0] * 7) == 0.001953125
    assert sign_test([0.0, 0.0]) == 1.0
    assert sign_test([]) == 1.0


def test_sign_test_asymmetric_known_value():
    # 8 negative, 2 positive of 10: 2 * (C(10,0)+C(10,1)+C(10,2)) / 1024 = 2 * 56 / 1024.
    assert sign_test([-1] * 8 + [2] * 2) == 112 / 1024


def test_holm_bonferroni_hand_computed():
    # Sorted: 0.005 (x4 = 0.02), 0.01 (x3 = 0.03), 0.03 (x2 = 0.06), 0.04 (x1 = 0.04 -> running
    # max 0.06). Back in input order: [0.03, 0.06, 0.06, 0.02].
    adjusted = holm_bonferroni([0.01, 0.04, 0.03, 0.005])
    assert adjusted == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_bonferroni_caps_at_one_and_is_monotone():
    assert holm_bonferroni([0.5, 0.6]) == [1.0, 1.0]
    raw = [0.2, 0.001, 0.04, 0.04, 0.9]
    adjusted = holm_bonferroni(raw)
    assert all(a >= r for a, r in zip(adjusted, raw))
    by_raw = [a for _, a in sorted(zip(raw, adjusted))]
    assert by_raw == sorted(by_raw)


def test_holm_bonferroni_trivial_cases():
    assert holm_bonferroni([]) == []
    assert holm_bonferroni([0.03]) == [0.03]


@pytest.mark.parametrize("bad", [[-0.1], [1.5], [True], ["0.1"]])
def test_holm_bonferroni_rejects_invalid(bad):
    with pytest.raises(ValueError):
        holm_bonferroni(bad)


def test_win_rate():
    assert win_rate([-1, -0.5, 0, 2]) == 0.5
    with pytest.raises(ValueError):
        win_rate([])
