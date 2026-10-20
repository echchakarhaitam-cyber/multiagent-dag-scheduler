"""Tests for failure models, retry policy and failure handling in the simulator."""

import math
import random

import pytest

from dagsched import BernoulliFailures, NoFailures, ScriptedFailures
from dagsched.failures import check_fail_fraction

# ----- failure models ------------------------------------------------------------------------


def test_no_failures_never_fails():
    model = NoFailures()
    assert all(model.fail_fraction("A", "W0", n, u) is None for n in (1, 2) for u in (0.0, 0.99))


@pytest.mark.parametrize(
    ("u", "expected"),
    [(0.0, 1.0), (0.1, 0.5), (0.15, 0.25), (0.2, None), (0.7, None)],
)
def test_bernoulli_fails_below_p_with_fraction_from_u(u, expected):
    result = BernoulliFailures(0.2).fail_fraction("A", "W0", 1, u)
    assert result == (None if expected is None else pytest.approx(expected))


def test_bernoulli_fraction_always_in_unit_interval():
    model = BernoulliFailures(0.9)
    rng = random.Random(0)
    for _ in range(10_000):
        fraction = model.fail_fraction("A", "W0", 1, rng.random())
        assert fraction is None or 0 < fraction <= 1
    # Values just below p must still give a strictly positive fraction.
    assert model.fail_fraction("A", "W0", 1, math.nextafter(0.9, 0.0)) > 0


def test_bernoulli_extremes():
    assert BernoulliFailures(0.0).fail_fraction("A", "W0", 1, 0.0) is None
    always = BernoulliFailures(1.0)
    assert all(always.fail_fraction("A", "W0", 1, u) is not None for u in (0.0, 0.5, 0.999))


def test_bernoulli_per_worker_rates_override_default():
    model = BernoulliFailures(0.0, per_worker={"bad": 1.0})
    assert model.fail_fraction("A", "bad", 1, 0.5) == pytest.approx(0.5)
    assert model.fail_fraction("A", "good", 1, 0.5) is None


def test_bernoulli_copies_per_worker_mapping():
    rates = {"bad": 1.0}
    model = BernoulliFailures(0.0, per_worker=rates)
    rates["bad"] = 0.0
    assert model.fail_fraction("A", "bad", 1, 0.5) is not None


@pytest.mark.parametrize("p", [-0.1, 1.1, math.nan])
def test_bernoulli_rejects_bad_probability(p):
    with pytest.raises(ValueError):
        BernoulliFailures(p)


@pytest.mark.parametrize("p", [True, "0.5", None])
def test_bernoulli_rejects_non_numeric_probability(p):
    with pytest.raises(TypeError):
        BernoulliFailures(p)


def test_bernoulli_rejects_bad_per_worker_rate():
    with pytest.raises(ValueError, match="per_worker"):
        BernoulliFailures(0.1, per_worker={"W0": 2.0})


def test_scripted_failures_only_listed_attempts():
    model = ScriptedFailures({("A", 1): 0.5, ("B", 2): 1})
    assert model.fail_fraction("A", "W0", 1, 0.99) == 0.5
    assert model.fail_fraction("A", "W0", 2, 0.0) is None
    assert model.fail_fraction("B", "W1", 2, 0.5) == 1.0
    assert model.fail_fraction("C", "W0", 1, 0.0) is None


@pytest.mark.parametrize("fraction", [0, -0.5, 1.5, math.nan])
def test_scripted_failures_rejects_bad_fraction(fraction):
    with pytest.raises(ValueError):
        ScriptedFailures({("A", 1): fraction})


@pytest.mark.parametrize("key", [("A",), ("A", "1"), (1, 1), "A1", ("A", True)])
def test_scripted_failures_rejects_bad_key(key):
    with pytest.raises(TypeError):
        ScriptedFailures({key: 0.5})


def test_scripted_failures_rejects_attempt_zero():
    with pytest.raises(ValueError, match="start at 1"):
        ScriptedFailures({("A", 0): 0.5})


@pytest.mark.parametrize("value", [0.25, 1, None])
def test_check_fail_fraction_accepts_valid(value):
    assert check_fail_fraction(value) == value


@pytest.mark.parametrize("value", [0.0, 1.01, -1, math.inf, math.nan])
def test_check_fail_fraction_rejects_invalid(value):
    with pytest.raises(ValueError):
        check_fail_fraction(value)
