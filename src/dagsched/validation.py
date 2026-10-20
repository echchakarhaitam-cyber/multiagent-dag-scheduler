"""Independent schedule checker.

`validate_schedule` re-derives every property of a valid schedule from the graph, the workers
and the result's records and attempts alone. It deliberately does not import or call any
simulator code (the simulator types appear only as type hints), so a bug in the simulator
cannot also hide itself in the check that is supposed to catch it.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from dagsched.graph import TaskGraph

if TYPE_CHECKING:
    from dagsched.simulator import AttemptRecord, SimulationResult, TaskRecord, Worker

TOLERANCE = 1e-9
_SUCCESS, _FAILED = "success", "failed"


class ScheduleViolation(AssertionError):
    """A simulation result is not a valid schedule for the given graph and workers."""


class _Interval(Protocol):
    """Anything that occupied a worker: a successful record or a failed attempt."""

    task_id: str
    worker_id: str
    start: float
    end: float


def validate_schedule(
    graph: TaskGraph,
    workers: Iterable[Worker],
    result: SimulationResult,
    max_attempts: int | None = None,
) -> None:
    """Raise ScheduleViolation unless `result` is a valid, internally consistent schedule.

    Outcomes: completed (tasks with a record), failed_tasks and cancelled_tasks are sorted,
    disjoint and cover all tasks; the cancelled tasks are exactly the descendants of failed
    tasks; status is "completed" iff nothing failed or was cancelled.
    Successful records: every dependency completed and ended before the start;
    end - start == duration / speed.
    Attempts: known task/worker/outcome; a failed attempt lasts at most its nominal time;
    attempt numbers per task are 1..n (n <= max_attempts if given), each starting after the
    previous one ended; completed tasks end with their single success, failed tasks have only
    failures (exactly max_attempts if given), cancelled tasks have none; successful attempts
    match the records exactly.
    Workers: no overlap among all attempts; makespan, worker_busy and utilization consistent.
    Time comparisons use an absolute tolerance of TOLERANCE.
    """
    speeds = {worker.id: worker.speed for worker in workers}
    records = tuple(result.records)
    attempts = tuple(result.attempts)
    by_task = _check_outcomes(graph, records, result)
    _check_cancellations(graph, result)
    for record in records:
        _check_record(graph, speeds, by_task, record)
    for attempt in attempts:
        _check_attempt(graph, speeds, attempt)
    _check_attempt_sequences(graph, result, by_task, attempts, max_attempts)
    busy: tuple[_Interval, ...] = records + tuple(a for a in attempts if a.outcome == _FAILED)
    _check_no_overlap(busy)
    _check_aggregates(speeds, busy, result)
    _check_attempts_match_records(records, attempts)


# ----- outcomes ------------------------------------------------------------------------------


def _check_outcomes(
    graph: TaskGraph, records: tuple[TaskRecord, ...], result: SimulationResult
) -> dict[str, TaskRecord]:
    counts = Counter(record.task_id for record in records)
    unknown = sorted(task_id for task_id in counts if task_id not in graph)
    if unknown:
        raise ScheduleViolation(f"records contain unknown tasks {unknown}")
    repeated = sorted(task_id for task_id, n in counts.items() if n > 1)
    if repeated:
        raise ScheduleViolation(f"tasks scheduled more than once: {repeated}")
    for name, group in (("failed_tasks", result.failed_tasks),
                        ("cancelled_tasks", result.cancelled_tasks)):
        if tuple(group) != tuple(sorted(set(group))):
            raise ScheduleViolation(f"{name} must be sorted and without repeats, got {group}")
        stray = sorted(task_id for task_id in group if task_id not in graph)
        if stray:
            raise ScheduleViolation(f"{name} contains unknown tasks {stray}")
    completed, failed = set(counts), set(result.failed_tasks)
    cancelled = set(result.cancelled_tasks)
    shared = (completed & failed) | (completed & cancelled) | (failed & cancelled)
    if shared:
        raise ScheduleViolation(
            f"completed, failed and cancelled tasks must be disjoint; in more than one: "
            f"{sorted(shared)}"
        )
    resolved = completed | failed | cancelled
    missing = sorted(task_id for task_id in graph if task_id not in resolved)
    if missing:
        raise ScheduleViolation(f"tasks missing from the schedule: {missing}")
    return {record.task_id: record for record in records}


def _check_cancellations(graph: TaskGraph, result: SimulationResult) -> None:
    doomed: set[str] = set()
    stack = [succ for task_id in result.failed_tasks for succ in graph.successors(task_id)]
    while stack:  # iterative: graphs may be deep
        node = stack.pop()
        if node not in doomed:
            doomed.add(node)
            stack.extend(graph.successors(node))
    cancelled = set(result.cancelled_tasks)
    orphans = sorted(cancelled - doomed)
    if orphans:
        raise ScheduleViolation(f"cancelled tasks without a failed ancestor: {orphans}")
    spared = sorted(doomed - cancelled)
    if spared:
        raise ScheduleViolation(f"descendants of failed tasks not cancelled: {spared}")
    expected = "completed" if not result.failed_tasks and not result.cancelled_tasks else "partial"
    if result.status != expected:
        raise ScheduleViolation(
            f"status {result.status!r} inconsistent with outcomes, expected {expected!r}"
        )


# ----- records and attempts ------------------------------------------------------------------


def _check_record(
    graph: TaskGraph,
    speeds: Mapping[str, float],
    by_task: Mapping[str, TaskRecord],
    record: TaskRecord,
) -> None:
    if record.worker_id not in speeds:
        raise ScheduleViolation(f"task {record.task_id!r} ran on unknown worker {record.worker_id!r}")
    if record.start < -TOLERANCE:
        raise ScheduleViolation(f"task {record.task_id!r} starts at negative time {record.start}")
    expected = graph.tasks[record.task_id].duration / speeds[record.worker_id]
    if abs((record.end - record.start) - expected) > TOLERANCE:
        raise ScheduleViolation(
            f"task {record.task_id!r} on {record.worker_id!r} has duration "
            f"{record.end - record.start}, expected {expected}"
        )
    for dep in graph.predecessors(record.task_id):
        # Defensive: _check_cancellations already rules this out for consistent outcomes.
        if dep not in by_task:
            raise ScheduleViolation(
                f"completed task {record.task_id!r} depends on {dep!r}, which did not complete"
            )
        if record.start < by_task[dep].end - TOLERANCE:
            raise ScheduleViolation(
                f"precedence violated: task {record.task_id!r} starts at {record.start} "
                f"before its dependency {dep!r} ends at {by_task[dep].end}"
            )


def _check_attempt(graph: TaskGraph, speeds: Mapping[str, float], attempt: AttemptRecord) -> None:
    label = f"attempt {attempt.attempt} of task {attempt.task_id!r}"
    if attempt.task_id not in graph:
        raise ScheduleViolation(f"{label}: unknown task")
    if attempt.worker_id not in speeds:
        raise ScheduleViolation(f"{label} ran on unknown worker {attempt.worker_id!r}")
    if attempt.outcome not in (_SUCCESS, _FAILED):
        raise ScheduleViolation(f"{label} has unknown outcome {attempt.outcome!r}")
    if attempt.start < -TOLERANCE or attempt.end < attempt.start:
        raise ScheduleViolation(f"{label} has invalid interval [{attempt.start}, {attempt.end}]")
    nominal = graph.tasks[attempt.task_id].duration / speeds[attempt.worker_id]
    if attempt.outcome == _FAILED and attempt.end - attempt.start > nominal + TOLERANCE:
        raise ScheduleViolation(
            f"failed {label} ran {attempt.end - attempt.start}, longer than its nominal {nominal}"
        )


def _check_attempt_sequences(
    graph: TaskGraph,
    result: SimulationResult,
    by_task: Mapping[str, TaskRecord],
    attempts: tuple[AttemptRecord, ...],
    max_attempts: int | None,
) -> None:
    per_task: dict[str, list[AttemptRecord]] = defaultdict(list)
    for attempt in attempts:
        per_task[attempt.task_id].append(attempt)
    failed = set(result.failed_tasks)
    for task_id in graph:
        runs = sorted(per_task.get(task_id, ()), key=lambda a: a.attempt)
        _check_task_attempts(task_id, runs, task_id in by_task, task_id in failed, max_attempts)


def _check_task_attempts(
    task_id: str,
    runs: Sequence[AttemptRecord],
    completed: bool,
    failed: bool,
    max_attempts: int | None,
) -> None:
    numbers = [a.attempt for a in runs]
    if numbers != list(range(1, len(runs) + 1)):
        raise ScheduleViolation(
            f"attempt numbers of task {task_id!r} are {numbers}, expected 1..{len(runs)}"
        )
    if max_attempts is not None and len(runs) > max_attempts:
        raise ScheduleViolation(
            f"task {task_id!r} has {len(runs)} attempts, more than max_attempts={max_attempts}"
        )
    for before, after in itertools.pairwise(runs):
        if after.start < before.end - TOLERANCE:
            raise ScheduleViolation(
                f"attempt {after.attempt} of task {task_id!r} starts at {after.start} before "
                f"attempt {before.attempt} ended at {before.end}"
            )
    outcomes = [a.outcome for a in runs]
    if completed:
        if not runs or outcomes[-1] != _SUCCESS or _SUCCESS in outcomes[:-1]:
            raise ScheduleViolation(
                f"completed task {task_id!r} must have failed attempts followed by one "
                f"success, got {outcomes}"
            )
    elif failed:
        if not runs or _SUCCESS in outcomes:
            raise ScheduleViolation(
                f"failed task {task_id!r} must have only failed attempts, got {outcomes}"
            )
        if max_attempts is not None and len(runs) != max_attempts:
            raise ScheduleViolation(
                f"task {task_id!r} failed permanently after {len(runs)} attempts, "
                f"expected {max_attempts}"
            )
    elif runs:
        raise ScheduleViolation(f"cancelled task {task_id!r} has attempts {outcomes}")


def _check_attempts_match_records(
    records: tuple[TaskRecord, ...], attempts: tuple[AttemptRecord, ...]
) -> None:
    successes = Counter(
        (a.task_id, a.worker_id, a.start, a.end) for a in attempts if a.outcome == _SUCCESS
    )
    recorded = Counter((r.task_id, r.worker_id, r.start, r.end) for r in records)
    if successes != recorded:
        raise ScheduleViolation(
            f"successful attempts do not match records: only in attempts "
            f"{sorted(successes - recorded)}, only in records {sorted(recorded - successes)}"
        )


# ----- workers -------------------------------------------------------------------------------


def _check_no_overlap(intervals: Iterable[_Interval]) -> None:
    per_worker: dict[str, list[_Interval]] = defaultdict(list)
    for interval in intervals:
        per_worker[interval.worker_id].append(interval)
    for worker_id, runs in per_worker.items():
        runs.sort(key=lambda r: (r.start, r.end))
        for before, after in itertools.pairwise(runs):
            if after.start < before.end - TOLERANCE:
                raise ScheduleViolation(
                    f"overlap on worker {worker_id!r}: {before.task_id!r} "
                    f"[{before.start}, {before.end}] and {after.task_id!r} "
                    f"[{after.start}, {after.end}]"
                )


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE)


def _check_aggregates(
    speeds: Mapping[str, float], intervals: Sequence[_Interval], result: SimulationResult
) -> None:
    max_end = max((interval.end for interval in intervals), default=0.0)
    if not _close(result.makespan, max_end):
        raise ScheduleViolation(f"makespan {result.makespan} != latest end time {max_end}")
    if set(result.worker_busy) != set(speeds):
        raise ScheduleViolation(
            f"worker_busy keys {sorted(result.worker_busy)} != workers {sorted(speeds)}"
        )
    spans: dict[str, list[float]] = {worker_id: [] for worker_id in speeds}
    for interval in intervals:
        spans[interval.worker_id].append(interval.end - interval.start)
    for worker_id, worker_spans in spans.items():
        busy = math.fsum(worker_spans)
        if not _close(result.worker_busy[worker_id], busy):
            raise ScheduleViolation(
                f"worker_busy[{worker_id!r}] = {result.worker_busy[worker_id]}, "
                f"records give {busy}"
            )
    total = math.fsum(result.worker_busy.values())
    expected = total / (len(speeds) * result.makespan) if result.makespan > 0 else 0.0
    if not _close(result.utilization, expected):
        raise ScheduleViolation(f"utilization {result.utilization} != expected {expected}")
