"""Tests for the independent schedule validator, plus randomized bound checks on the baselines."""

import dataclasses
import random

import pytest

from dagsched import (
    FIFOScheduler,
    RandomScheduler,
    ScheduleViolation,
    SimulationResult,
    Task,
    TaskGraph,
    simulate,
    validate_schedule,
)

# ----- hand-made broken results --------------------------------------------------------------


@pytest.fixture
def fifo_two(worked, make_workers):
    """Valid FIFO schedule on 2 workers:
    A:W0[0,2] B:W0[2,5] C:W1[2,3] E:W1[3,5] D:W0[5,9] F:W0[9,10]."""
    workers = make_workers(2)
    return worked, workers, simulate(worked, workers, FIFOScheduler())


def with_record(result: SimulationResult, target: str, **changes) -> SimulationResult:
    """Copy of `result` with the record of task `target` changed by `changes`."""
    records = tuple(
        dataclasses.replace(r, **changes) if r.task_id == target else r for r in result.records
    )
    return dataclasses.replace(result, records=records)


def test_valid_schedule_passes(fifo_two):
    validate_schedule(*fifo_two)


def test_violation_is_an_assertion_error():
    assert issubclass(ScheduleViolation, AssertionError)


@pytest.mark.parametrize(
    ("break_result", "message"),
    [
        # F needs D (ends 9); moved to idle W1 at [8, 9] it starts too early.
        (lambda r: with_record(r, "F", worker_id="W1", start=8, end=9), "precedence"),
        # E moved onto W0 at [3, 5] collides with B on W0 [2, 5].
        (lambda r: with_record(r, "E", worker_id="W0"), "overlap"),
        (lambda r: with_record(r, "D", end=8.5), "duration"),
        (lambda r: with_record(r, "A", worker_id="W7"), "unknown worker"),
        (lambda r: with_record(r, "A", start=-2, end=0), "negative"),
        (
            lambda r: dataclasses.replace(r, records=r.records[:-1]),
            r"missing from the schedule: \['F'\]",
        ),
        (
            lambda r: dataclasses.replace(r, records=r.records + r.records[:1]),
            r"more than once: \['A'\]",
        ),
        (
            lambda r: with_record(r, "A", task_id="Z"),
            "unknown tasks",
        ),
        (lambda r: dataclasses.replace(r, makespan=11), "makespan"),
        (lambda r: dataclasses.replace(r, worker_busy={"W0": 0.0, "W1": 3.0}), "worker_busy"),
        (lambda r: dataclasses.replace(r, worker_busy={"W0": 9.0}), "worker_busy keys"),
        (lambda r: dataclasses.replace(r, utilization=0.5), "utilization"),
    ],
    ids=[
        "precedence", "overlap", "wrong-duration", "unknown-worker", "negative-start",
        "missing-task", "duplicated-task", "unknown-task", "makespan", "busy", "busy-keys",
        "utilization",
    ],
)
def test_broken_result_detected(fifo_two, break_result, message):
    graph, workers, result = fifo_two
    with pytest.raises(ScheduleViolation, match=message):
        validate_schedule(graph, workers, break_result(result))


def test_speed_is_taken_into_account(worked, make_workers):
    fast = make_workers(1, speed=2.0)
    result = simulate(worked, fast, FIFOScheduler())
    validate_schedule(worked, fast, result)
    with pytest.raises(ScheduleViolation, match="duration"):
        validate_schedule(worked, make_workers(1), result)  # same ids, speed 1


# ----- randomized checks ---------------------------------------------------------------------


def random_dag(rng: random.Random) -> TaskGraph:
    """20-40 tasks, edges only from lower to higher index (hence acyclic), durations in [1, 10]."""
    n = rng.randint(20, 40)
    density = rng.uniform(0.05, 0.3)
    ids = [f"t{i:02d}" for i in range(n)]
    tasks = [
        Task(ids[i], rng.uniform(1, 10), tuple(ids[j] for j in range(i) if rng.random() < density))
        for i in range(n)
    ]
    return TaskGraph(tasks)


RANDOM_GRAPHS = [random_dag(random.Random(seed)) for seed in range(50)]


@pytest.mark.parametrize("make_scheduler", [FIFOScheduler, RandomScheduler])
@pytest.mark.parametrize("k", [1, 2, 4, 8])
def test_random_dags_valid_and_within_bounds(make_workers, make_scheduler, k):
    workers = make_workers(k)
    for index, graph in enumerate(RANDOM_GRAPHS):
        result = simulate(graph, workers, make_scheduler(), seed=index)
        validate_schedule(graph, workers, result)
        critical_length, _ = graph.critical_path()
        assert result.makespan >= graph.makespan_lower_bound(k) - 1e-9
        graham = graph.total_work() / k + (1 - 1 / k) * critical_length
        assert result.makespan <= graham + 1e-9, f"graph {index}: Graham bound violated"
