"""Independent schedule checker.

`validate_schedule` re-derives every property of a valid schedule from the graph, the workers
and the result's records alone. It deliberately does not import or call any simulator code (the
simulator types appear only as type hints), so a bug in the simulator cannot also hide itself
in the check that is supposed to catch it.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from dagsched.graph import TaskGraph

if TYPE_CHECKING:
    from dagsched.simulator import SimulationResult, TaskRecord, Worker

TOLERANCE = 1e-9


class ScheduleViolation(AssertionError):
    """A simulation result is not a valid schedule for the given graph and workers."""


def validate_schedule(
    graph: TaskGraph, workers: Iterable[Worker], result: SimulationResult
) -> None:
    """Raise ScheduleViolation unless `result` is a valid, internally consistent schedule.

    Checks: every task exactly once; known worker ids; end - start == duration / speed;
    every task starts no earlier than all its dependencies end; no overlap on any worker;
    makespan == max end; worker_busy and utilization consistent with the records.
    Time comparisons use an absolute tolerance of TOLERANCE.
    """
    speeds = {worker.id: worker.speed for worker in workers}
    records = tuple(result.records)
    by_task = _check_coverage(graph, records)
    for record in records:
        _check_record(graph, speeds, by_task, record)
    _check_no_overlap(records)
    _check_aggregates(speeds, records, result)


def _check_coverage(graph: TaskGraph, records: tuple[TaskRecord, ...]) -> dict[str, TaskRecord]:
    counts = Counter(record.task_id for record in records)
    unknown = sorted(task_id for task_id in counts if task_id not in graph)
    if unknown:
        raise ScheduleViolation(f"records contain unknown tasks {unknown}")
    repeated = sorted(task_id for task_id, n in counts.items() if n > 1)
    if repeated:
        raise ScheduleViolation(f"tasks scheduled more than once: {repeated}")
    missing = sorted(task_id for task_id in graph if task_id not in counts)
    if missing:
        raise ScheduleViolation(f"tasks missing from the schedule: {missing}")
    return {record.task_id: record for record in records}


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
        if record.start < by_task[dep].end - TOLERANCE:
            raise ScheduleViolation(
                f"precedence violated: task {record.task_id!r} starts at {record.start} "
                f"before its dependency {dep!r} ends at {by_task[dep].end}"
            )


def _check_no_overlap(records: tuple[TaskRecord, ...]) -> None:
    per_worker: dict[str, list[TaskRecord]] = defaultdict(list)
    for record in records:
        per_worker[record.worker_id].append(record)
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
    speeds: Mapping[str, float], records: tuple[TaskRecord, ...], result: SimulationResult
) -> None:
    max_end = max(record.end for record in records)
    if not _close(result.makespan, max_end):
        raise ScheduleViolation(f"makespan {result.makespan} != latest end time {max_end}")
    if set(result.worker_busy) != set(speeds):
        raise ScheduleViolation(
            f"worker_busy keys {sorted(result.worker_busy)} != workers {sorted(speeds)}"
        )
    spans: dict[str, list[float]] = {worker_id: [] for worker_id in speeds}
    for record in records:
        spans[record.worker_id].append(record.end - record.start)
    for worker_id, worker_spans in spans.items():
        busy = math.fsum(worker_spans)
        if not _close(result.worker_busy[worker_id], busy):
            raise ScheduleViolation(
                f"worker_busy[{worker_id!r}] = {result.worker_busy[worker_id]}, "
                f"records give {busy}"
            )
    expected = math.fsum(result.worker_busy.values()) / (len(speeds) * result.makespan)
    if not _close(result.utilization, expected):
        raise ScheduleViolation(f"utilization {result.utilization} != expected {expected}")
