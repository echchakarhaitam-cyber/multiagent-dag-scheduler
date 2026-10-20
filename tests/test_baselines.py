"""Tests for the FIFO and random baseline schedulers (expected values computed by hand)."""

import random

import pytest

from dagsched import FIFOScheduler, RandomScheduler, TaskRecord, Worker, simulate

SCHEDULERS = [FIFOScheduler, RandomScheduler]


@pytest.mark.parametrize("make_scheduler", SCHEDULERS)
def test_single_worker_makespan_is_total_work(worked, make_workers, make_scheduler):
    result = simulate(worked, make_workers(1), make_scheduler())
    assert result.makespan == 13
    assert result.utilization == 1.0


@pytest.mark.parametrize("make_scheduler", SCHEDULERS)
def test_single_fast_worker(worked, make_scheduler):
    result = simulate(worked, [Worker("W0", 2.0)], make_scheduler())
    assert result.makespan == 6.5


def test_fifo_enough_workers_reaches_critical_path(worked, make_workers):
    result = simulate(worked, make_workers(6), FIFOScheduler())
    assert result.makespan == 10


def test_fifo_two_workers_exact_schedule(worked, make_workers):
    result = simulate(worked, make_workers(2), FIFOScheduler())
    assert result.records == (
        TaskRecord("A", "W0", 0, 2),
        TaskRecord("B", "W0", 2, 5),
        TaskRecord("C", "W1", 2, 3),
        TaskRecord("E", "W1", 3, 5),
        TaskRecord("D", "W0", 5, 9),
        TaskRecord("F", "W0", 9, 10),
    )
    assert result.makespan == 10
    assert result.scheduler_name == "fifo"


def test_random_same_seed_same_result(worked, make_workers):
    first = simulate(worked, make_workers(2), RandomScheduler(), seed=7)
    second = simulate(worked, make_workers(2), RandomScheduler(), seed=7)
    assert first == second
    assert first.scheduler_name == "random"


def test_random_reused_instance_is_reproducible(worked, make_workers):
    scheduler = RandomScheduler()
    first = simulate(worked, make_workers(2), scheduler, seed=3)
    second = simulate(worked, make_workers(2), scheduler, seed=3)
    assert first == second


def test_random_different_seeds_give_different_schedules(worked, make_workers):
    schedules = {
        simulate(worked, make_workers(2), RandomScheduler(), seed=seed).records
        for seed in range(20)
    }
    assert len(schedules) >= 2


def test_random_does_not_touch_global_random_state(worked, make_workers, monkeypatch):
    random.seed(1234)
    state = random.getstate()

    def forbidden(*args, **kwargs):
        raise AssertionError("global random module used")

    for name in ("random", "shuffle", "choice", "randint", "sample"):
        monkeypatch.setattr(random, name, forbidden)
    simulate(worked, make_workers(3), RandomScheduler(), seed=5)
    assert random.getstate() == state


def test_random_select_before_reset_fails():
    with pytest.raises(RuntimeError, match="reset"):
        RandomScheduler().select(None)  # type: ignore[arg-type]


class CountingWrapper:
    """Checks the work-conserving property at every decision of the wrapped scheduler."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name

    def reset(self, graph, workers, rng):
        self.inner.reset(graph, workers, rng)

    def select(self, view):
        assignments = self.inner.select(view)
        assert len(assignments) == min(len(view.ready), len(view.idle_workers))
        return assignments


@pytest.mark.parametrize("make_scheduler", SCHEDULERS)
@pytest.mark.parametrize("k", [1, 2, 3, 8])
def test_baselines_are_work_conserving(worked, make_workers, make_scheduler, k):
    simulate(worked, make_workers(k), CountingWrapper(make_scheduler()), seed=11)
