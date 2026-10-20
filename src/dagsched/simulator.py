"""Discrete-event simulator: executes a TaskGraph on k workers under a pluggable Scheduler.

Model
-----
- k workers; worker w has a speed s_w > 0 (default 1.0). Executing task v on worker w takes
  duration(v) / s_w time units.
- Non-preemptive: a started task runs to completion on the worker it was assigned to.
- A worker runs at most one task at a time.
- No communication costs: a task may start on any worker as soon as its dependencies have
  completed, wherever they ran. This is a documented limitation of the model.
- A task is READY when all its dependencies have completed; its ready_time is that moment.
- Decision points are time 0 and every time at which at least one task completes. At a decision
  point ALL events with exactly that timestamp are applied first and only then is the scheduler
  called, once, so it sees every worker and task freed at that instant (see
  docs/theory/02-simulation.md). Timestamps are compared exactly; there is no epsilon merging.

Idling is allowed (a scheduler may hold a ready task back, e.g. to wait for a faster worker),
except when nothing is running: then no future event could ever trigger another decision, so
the simulator raises a deadlock SchedulingError instead of looping or silently stopping.

Extensibility: every state change caused by the passage of time goes through
`_Simulation._apply`, which dispatches on the event kind. Failure and recovery events
(Milestone 5) are meant to become additional kinds there, without changing the main loop.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from dagsched.graph import TaskGraph

_TASK_COMPLETED = "task_completed"

Event = tuple[float, int, str, tuple[str, ...]]
"""(time, sequence_number, kind, payload). The unique sequence number makes heap order total
and deterministic, so kind/payload are never compared."""


@dataclass(frozen=True)
class Worker:
    """An execution resource. `speed` scales every task duration run on it (time = d / speed)."""

    id: str
    speed: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            raise TypeError(f"worker id must be a string, got {self.id!r}")
        if not self.id:
            raise ValueError("worker id must be non-empty")
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)):
            raise TypeError(f"worker {self.id!r}: speed must be a number, got {self.speed!r}")
        if not math.isfinite(self.speed) or self.speed <= 0:
            raise ValueError(f"worker {self.id!r}: speed must be finite and > 0, got {self.speed!r}")
        object.__setattr__(self, "speed", float(self.speed))


@dataclass(frozen=True)
class Assignment:
    """A scheduler decision: start `task_id` on `worker_id` now."""

    task_id: str
    worker_id: str


@dataclass(frozen=True)
class SchedulerView:
    """Read-only snapshot of the simulation handed to the scheduler at a decision point.

    Every collection is pre-sorted so that a scheduler iterating over it in order is
    deterministic without having to sort again.
    """

    time: float
    graph: TaskGraph
    workers: tuple[Worker, ...]
    ready: tuple[tuple[str, float], ...]
    """(task_id, ready_time) of ready, unstarted tasks, sorted by (ready_time, task_id)."""
    idle_workers: tuple[str, ...]
    """Ids of workers not running anything, sorted."""
    running: tuple[tuple[str, str, float], ...]
    """(worker_id, task_id, expected_end), sorted by worker_id."""
    completed: frozenset[str]


class Scheduler(Protocol):
    """A scheduling policy.

    `reset` is called once per simulation, before any decision, with the rng the policy must use
    for any randomness (never the global `random` module), so runs are reproducible from the seed.
    `select` is called once per decision point and returns the tasks to start now.
    """

    name: str

    def reset(self, graph: TaskGraph, workers: tuple[Worker, ...], rng: random.Random) -> None:
        ...

    def select(self, view: SchedulerView) -> list[Assignment]:
        ...


class SchedulingError(RuntimeError):
    """The scheduler returned an invalid decision, or left the simulation deadlocked."""


@dataclass(frozen=True)
class TaskRecord:
    """Where and when one task actually ran."""

    task_id: str
    worker_id: str
    start: float
    end: float


@dataclass(frozen=True)
class SimulationResult:
    """Outcome of one simulation run.

    `records` is sorted by (start, task_id); `worker_busy` holds every worker (0.0 if unused);
    `utilization` = sum(worker_busy) / (k * makespan); `num_decisions` counts scheduler calls.
    """

    scheduler_name: str
    seed: int
    makespan: float
    records: tuple[TaskRecord, ...]
    worker_busy: dict[str, float]
    utilization: float
    num_decisions: int


def simulate(
    graph: TaskGraph, workers: Iterable[Worker], scheduler: Scheduler, seed: int = 0
) -> SimulationResult:
    """Run `scheduler` on `graph` with `workers` until every task has completed.

    Raises ValueError (TypeError for non-Worker items) for an invalid worker set and
    SchedulingError for invalid decisions or a deadlock. All randomness flows from `random.Random(seed)` passed to `scheduler.reset`.
    """
    worker_tuple = _check_workers(workers)
    rng = random.Random(seed)
    scheduler.reset(graph, worker_tuple, rng)
    return _Simulation(graph, worker_tuple, scheduler).run(seed)


def _check_workers(workers: Iterable[Worker]) -> tuple[Worker, ...]:
    worker_tuple = tuple(workers)
    if not worker_tuple:
        raise ValueError("at least one worker is required")
    for worker in worker_tuple:
        if not isinstance(worker, Worker):
            raise TypeError(f"expected Worker instances, got {worker!r}")
    duplicates = sorted(wid for wid, n in Counter(w.id for w in worker_tuple).items() if n > 1)
    if duplicates:
        raise ValueError(f"duplicate worker ids {duplicates}")
    return worker_tuple


class _Simulation:
    """Mutable state of one run. Private: callers only ever see SimulationResult."""

    def __init__(self, graph: TaskGraph, workers: tuple[Worker, ...], scheduler: Scheduler):
        self.graph = graph
        self.workers = workers
        self.scheduler = scheduler
        self.worker_by_id = {worker.id: worker for worker in workers}
        self.time = 0.0
        self.events: list[Event] = []
        self.sequence = 0
        self.unmet = {task_id: len(graph.predecessors(task_id)) for task_id in graph}
        self.ready: dict[str, float] = {t: 0.0 for t, count in self.unmet.items() if count == 0}
        self.idle: set[str] = set(self.worker_by_id)
        self.running: dict[str, tuple[str, float, float]] = {}  # worker -> (task, start, end)
        self.started: set[str] = set()
        self.completed: set[str] = set()
        self.records: list[TaskRecord] = []
        self.num_decisions = 0

    def run(self, seed: int) -> SimulationResult:
        self._decide()
        while len(self.completed) < len(self.graph):
            if not self.events:  # unreachable: _decide raises on deadlock; kept as a safeguard
                raise SchedulingError(f"no pending events at time {self.time} but tasks remain")
            self.time = self.events[0][0]
            while self.events and self.events[0][0] == self.time:
                _, _, kind, payload = heapq.heappop(self.events)
                self._apply(kind, payload)
            if len(self.completed) < len(self.graph):
                self._decide()
        return self._result(seed)

    def _push(self, time: float, kind: str, payload: tuple[str, ...]) -> None:
        heapq.heappush(self.events, (time, self.sequence, kind, payload))
        self.sequence += 1

    def _apply(self, kind: str, payload: tuple[str, ...]) -> None:
        """Apply one event to the state. New event kinds (e.g. failures) are added here."""
        if kind == _TASK_COMPLETED:
            worker_id, task_id = payload
            _, start, end = self.running.pop(worker_id)
            self.idle.add(worker_id)
            self.completed.add(task_id)
            self.records.append(TaskRecord(task_id, worker_id, start, end))
            for succ in self.graph.successors(task_id):
                self.unmet[succ] -= 1
                if self.unmet[succ] == 0:
                    self.ready[succ] = self.time
        else:
            raise AssertionError(f"unknown event kind {kind!r}")

    def _decide(self) -> None:
        raw = self.scheduler.select(self._view())
        self.num_decisions += 1
        for assignment in self._check_assignments(raw):
            self._start(assignment)
        if not self.running and self.ready and self.idle:
            raise SchedulingError(
                f"deadlock: at time {self.time} nothing is running, tasks {sorted(self.ready)} "
                f"are ready and workers {sorted(self.idle)} are idle, but nothing was assigned"
            )

    def _view(self) -> SchedulerView:
        return SchedulerView(
            time=self.time,
            graph=self.graph,
            workers=self.workers,
            ready=tuple(sorted(self.ready.items(), key=lambda item: (item[1], item[0]))),
            idle_workers=tuple(sorted(self.idle)),
            running=tuple(sorted((w, task, end) for w, (task, _, end) in self.running.items())),
            completed=frozenset(self.completed),
        )

    def _check_assignments(self, raw: Iterable[Assignment]) -> list[Assignment]:
        """Validate a whole decision before applying any of it."""
        try:
            assignments = list(raw)
        except TypeError:
            raise SchedulingError(f"select() must return a list of Assignment, got {raw!r}") from None
        seen_tasks: set[str] = set()
        seen_workers: set[str] = set()
        for a in assignments:
            if not isinstance(a, Assignment):
                raise SchedulingError(f"expected Assignment, got {a!r}")
            if a.task_id not in self.graph:
                raise SchedulingError(f"unknown task id {a.task_id!r}")
            if a.worker_id not in self.worker_by_id:
                raise SchedulingError(f"unknown worker id {a.worker_id!r}")
            if a.task_id in seen_tasks:
                raise SchedulingError(f"task {a.task_id!r} assigned twice in one decision")
            if a.worker_id in seen_workers:
                raise SchedulingError(f"worker {a.worker_id!r} assigned twice in one decision")
            seen_tasks.add(a.task_id)
            seen_workers.add(a.worker_id)
            if a.task_id in self.started:
                raise SchedulingError(f"task {a.task_id!r} was already started")
            if a.task_id not in self.ready:
                raise SchedulingError(f"task {a.task_id!r} is not ready at time {self.time}")
            if a.worker_id not in self.idle:
                raise SchedulingError(f"worker {a.worker_id!r} is not idle at time {self.time}")
        return assignments

    def _start(self, assignment: Assignment) -> None:
        task_id, worker_id = assignment.task_id, assignment.worker_id
        end = self.time + self.graph.tasks[task_id].duration / self.worker_by_id[worker_id].speed
        del self.ready[task_id]
        self.idle.remove(worker_id)
        self.started.add(task_id)
        self.running[worker_id] = (task_id, self.time, end)
        self._push(end, _TASK_COMPLETED, (worker_id, task_id))

    def _result(self, seed: int) -> SimulationResult:
        records = tuple(sorted(self.records, key=lambda r: (r.start, r.task_id)))
        makespan = max(record.end for record in records)
        per_worker: dict[str, list[float]] = {worker.id: [] for worker in self.workers}
        for record in records:
            per_worker[record.worker_id].append(record.end - record.start)
        busy = {worker_id: math.fsum(spans) for worker_id, spans in per_worker.items()}
        return SimulationResult(
            scheduler_name=self.scheduler.name,
            seed=seed,
            makespan=makespan,
            records=records,
            worker_busy=busy,
            utilization=math.fsum(busy.values()) / (len(self.workers) * makespan),
            num_decisions=self.num_decisions,
        )
