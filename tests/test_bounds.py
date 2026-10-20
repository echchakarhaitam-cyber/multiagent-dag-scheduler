"""Tests for the heterogeneous makespan lower bound (expected values computed by hand)."""

import pytest

from dagsched import Task, TaskGraph, Worker, lower_bound


@pytest.mark.parametrize("k", [1, 2, 3, 4, 8, 100])
def test_equals_homogeneous_bound_for_unit_speeds(worked, make_workers, k):
    assert lower_bound(worked, make_workers(k)) == worked.makespan_lower_bound(k)


def test_work_term_dominates_with_fast_and_slow_worker():
    # Two independent 4s tasks; speeds 2 and 0.5: max(4/2, 8/2.5) = 3.2.
    graph = TaskGraph([Task("t1", 4), Task("t2", 4)])
    assert lower_bound(graph, [Worker("fast", 2.0), Worker("slow", 0.5)]) == 3.2


def test_critical_term_dominates():
    # One 6s task; speeds 1 and 3: max(6/3, 6/4) = 2.
    graph = TaskGraph([Task("only", 6)])
    assert lower_bound(graph, [Worker("a", 1.0), Worker("b", 3.0)]) == 2


def test_doubling_all_speeds_halves_the_bound(worked, make_workers):
    assert lower_bound(worked, make_workers(2, speed=2.0)) == worked.makespan_lower_bound(2) / 2


def test_accepts_any_iterable_of_workers(worked, make_workers):
    assert lower_bound(worked, iter(make_workers(2))) == 10


def test_requires_a_worker(worked):
    with pytest.raises(ValueError, match="at least one worker"):
        lower_bound(worked, [])
