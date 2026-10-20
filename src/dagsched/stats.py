"""Statistics helpers for comparing schedulers (standard library only).

The experiments use a paired design: every scheduler runs on the same graph instance, worker set
and failure draws, so comparisons are made on per-instance differences. The helpers here are
deliberately simple, distribution-free methods suited to that design: a percentile bootstrap
for confidence intervals and an exact sign test for "does A beat B more often than chance".
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence


def _non_empty(values: Sequence[float], what: str = "values") -> list[float]:
    data = list(values)
    if not data:
        raise ValueError(f"{what} must not be empty")
    return data


def mean_std(values: Sequence[float]) -> tuple[float, float]:
    """(mean, sample standard deviation); the std of a single value is 0.0."""
    data = _non_empty(values)
    if len(data) == 1:
        return data[0], 0.0
    return statistics.fmean(data), statistics.stdev(data)


def bootstrap_ci(
    values: Sequence[float], level: float = 0.95, n_resamples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """Percentile-bootstrap confidence interval for the mean.

    Resamples the data with replacement `n_resamples` times using random.Random(seed), so the
    interval is deterministic for given inputs. Percentile method: the interval is formed by the
    (1 - level)/2 and (1 + level)/2 empirical quantiles of the resampled means. With very few
    values the interval is necessarily crude (for one value it collapses to that value).
    """
    data = _non_empty(values)
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in (0, 1), got {level}")
    if isinstance(n_resamples, bool) or not isinstance(n_resamples, int) or n_resamples < 1:
        raise ValueError(f"n_resamples must be a positive int, got {n_resamples!r}")
    rng = random.Random(seed)
    n = len(data)
    means = sorted(statistics.fmean(rng.choices(data, k=n)) for _ in range(n_resamples))
    alpha = (1.0 - level) / 2.0
    low_index = min(n_resamples - 1, max(0, math.floor(alpha * n_resamples)))
    high_index = min(n_resamples - 1, max(0, math.ceil((1.0 - alpha) * n_resamples) - 1))
    return means[low_index], means[high_index]


def paired_differences(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """[a_i - b_i]; a and b must be aligned on the same instances, hence equally long."""
    if len(a) != len(b):
        raise ValueError(f"paired samples must have equal length, got {len(a)} and {len(b)}")
    return [x - y for x, y in zip(a, b)]


def sign_test(differences: Sequence[float]) -> float:
    """Two-sided exact sign test p-value for "positive and negative differences are equally
    likely".

    Zero differences (ties) carry no information about which side is better and are dropped,
    the standard treatment; the test then runs on the n non-zero differences with
    p = min(1, 2 * P(X <= min(#pos, #neg))), X ~ Binomial(n, 1/2), computed exactly with
    math.comb. Returns 1.0 when there is no non-zero difference.
    """
    non_zero = [d for d in differences if d != 0]
    n = len(non_zero)
    if n == 0:
        return 1.0
    positives = sum(1 for d in non_zero if d > 0)
    k = min(positives, n - positives)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2 * tail / 2**n)


def win_rate(differences: Sequence[float]) -> float:
    """Fraction of strictly negative differences (a lower makespan is a win)."""
    data = _non_empty(differences, "differences")
    return sum(1 for d in data if d < 0) / len(data)
