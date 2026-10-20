"""Tests for the independent schedule validator.

Randomized validity and bound checks over all schedulers live in test_informed_schedulers.py.
"""

import dataclasses

import pytest

from dagsched import (
    AttemptRecord,
    FIFOScheduler,
    RetryPolicy,
    ScheduleViolation,
    ScriptedFailures,
    SimulationConfig,
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


def with_attempt(result: SimulationResult, target: str, number: int, **changes):
    """Copy of `result` with attempt `number` of task `target` changed by `changes`."""
    attempts = tuple(
        dataclasses.replace(a, **changes) if (a.task_id, a.attempt) == (target, number) else a
        for a in result.attempts
    )
    return dataclasses.replace(result, attempts=attempts)


@pytest.fixture
def retried(worked, make_workers):
    """Worked example, 1 worker: A fails at t=1, attempt 2 runs [1, 3], everything completes."""
    workers = make_workers(1)
    config = SimulationConfig(failure_model=ScriptedFailures({("A", 1): 0.5}))
    return worked, workers, simulate(worked, workers, FIFOScheduler(), config=config)


@pytest.fixture
def partial(worked, make_workers):
    """Worked example, 1 worker, one attempt: C fails at 5.5, D, E and F are cancelled."""
    workers = make_workers(1)
    config = SimulationConfig(
        failure_model=ScriptedFailures({("C", 1): 0.5}), retry=RetryPolicy(max_attempts=1)
    )
    return worked, workers, simulate(worked, workers, FIFOScheduler(), config=config)


def test_valid_results_with_failures_pass(retried, partial):
    validate_schedule(*retried)
    validate_schedule(*retried, max_attempts=3)
    validate_schedule(*partial)
    validate_schedule(*partial, max_attempts=1)


@pytest.mark.parametrize(
    ("break_result", "kwargs", "message"),
    [
        (lambda r: with_attempt(r, "A", 2, attempt=3), {}, "attempt numbers"),
        (lambda r: r, {"max_attempts": 1}, "more than max_attempts"),
        (lambda r: with_attempt(r, "A", 2, start=0.5, end=2.5), {}, "before attempt 1 ended"),
        (lambda r: with_attempt(r, "A", 1, end=2.5), {}, "longer than its nominal"),
        (lambda r: with_attempt(r, "A", 1, outcome="crashed"), {}, "unknown outcome"),
        (lambda r: with_attempt(r, "A", 1, outcome="success"), {}, "followed by one success"),
        (lambda r: with_attempt(r, "A", 1, worker_id="W9"), {}, "unknown worker"),
        (lambda r: with_attempt(r, "A", 1, end=-1.0), {}, "invalid interval"),
    ],
    ids=[
        "gap-in-numbers", "too-many-attempts", "retry-before-failure", "failed-too-long",
        "unknown-outcome", "success-then-success", "attempt-unknown-worker", "end-before-start",
    ],
)
def test_broken_attempts_detected(retried, break_result, kwargs, message):
    graph, workers, result = retried
    with pytest.raises(ScheduleViolation, match=message):
        validate_schedule(graph, workers, break_result(result), **kwargs)


@pytest.mark.parametrize(
    ("break_result", "kwargs", "message"),
    [
        (lambda r: dataclasses.replace(r, status="completed"), {}, "status"),
        (
            lambda r: dataclasses.replace(r, failed_tasks=(), cancelled_tasks=("C", "D", "E", "F")),
            {},
            "without a failed ancestor",
        ),
        (
            lambda r: dataclasses.replace(r, failed_tasks=("C", "F"), cancelled_tasks=("D", "E")),
            {},
            r"not cancelled: \['F'\]",
        ),
        (
            lambda r: dataclasses.replace(r, cancelled_tasks=("C", "D", "E", "F")),
            {},
            "disjoint",
        ),
        (lambda r: dataclasses.replace(r, cancelled_tasks=("F", "E", "D")), {}, "sorted"),
        (lambda r: dataclasses.replace(r, failed_tasks=("C", "Z")), {}, "unknown tasks"),
        (lambda r: r, {"max_attempts": 3}, "after 1 attempts, expected 3"),
        (
            lambda r: dataclasses.replace(
                r, attempts=r.attempts + (AttemptRecord("D", "W0", 1, 5.5, 6.0, "failed"),)
            ),
            {},
            "cancelled task 'D' has attempts",
        ),
    ],
    ids=[
        "wrong-status", "orphan-cancellation", "descendant-not-cancelled", "not-disjoint",
        "unsorted", "unknown-failed-task", "too-few-attempts-before-failing",
        "cancelled-with-attempt",
    ],
)
def test_broken_outcomes_detected(partial, break_result, kwargs, message):
    graph, workers, result = partial
    with pytest.raises(ScheduleViolation, match=message):
        validate_schedule(graph, workers, break_result(result), **kwargs)


def test_attempts_must_match_records(fifo_two):
    graph, workers, result = fifo_two
    with pytest.raises(ScheduleViolation, match="do not match records"):
        validate_schedule(graph, workers, with_attempt(result, "F", 1, worker_id="W1"))


def test_completed_task_needs_a_successful_attempt(fifo_two):
    graph, workers, result = fifo_two
    without_f = tuple(a for a in result.attempts if a.task_id != "F")
    with pytest.raises(ScheduleViolation, match="completed task 'F'"):
        validate_schedule(graph, workers, dataclasses.replace(result, attempts=without_f))


def test_failed_attempts_count_towards_overlap(make_workers):
    # Independent X(2), Y(2) on one worker: X fails at t=1, then Y [1, 3], X again [3, 5].
    graph = TaskGraph([Task("X", 2), Task("Y", 2)])
    workers = make_workers(1)
    config = SimulationConfig(failure_model=ScriptedFailures({("X", 1): 0.5}))
    result = simulate(graph, workers, FIFOScheduler(), config=config)
    validate_schedule(graph, workers, result)
    # Y moved to [0.5, 2.5] has no dependency to violate, but collides with X's failed [0, 1].
    moved = dataclasses.replace(
        result,
        records=tuple(
            dataclasses.replace(r, start=0.5, end=2.5) if r.task_id == "Y" else r
            for r in result.records
        ),
    )
    with pytest.raises(ScheduleViolation, match="overlap on worker 'W0': 'X'"):
        validate_schedule(graph, workers, moved)


def test_failed_attempts_count_towards_busy_time(retried):
    graph, workers, result = retried
    assert result.worker_busy == {"W0": 14.0}  # 13 of useful work + 1 lost in A's failure
    with pytest.raises(ScheduleViolation, match="worker_busy"):
        validate_schedule(graph, workers, dataclasses.replace(result, worker_busy={"W0": 13.0}))


def test_speed_is_taken_into_account(worked, make_workers):
    fast = make_workers(1, speed=2.0)
    result = simulate(worked, fast, FIFOScheduler())
    validate_schedule(worked, fast, result)
    with pytest.raises(ScheduleViolation, match="duration"):
        validate_schedule(worked, make_workers(1), result)  # same ids, speed 1
