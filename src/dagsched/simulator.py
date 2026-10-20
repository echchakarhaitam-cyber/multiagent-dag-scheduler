"""Discrete-event simulator: executes a TaskGraph on k workers under a pluggable Scheduler.

Model
-----
- k workers; worker w has a speed s_w > 0 (default 1.0). Executing task v on worker w takes
  duration(v) / s_w time units.
- Non-preemptive: a started attempt runs on its worker until it completes or fails.
- A worker runs at most one task at a time.
- No communication costs: a task may start on any worker as soon as its dependencies have
  completed, wherever they ran. This is a documented limitation of the model.
- A task is READY when all its dependencies have completed (or when a retry becomes due);
  its ready_time is that moment.
- Decision points are time 0 and every time at which an event happens (a completion, a failed
  attempt, a retry becoming due, a duplicate delivery). At a decision point ALL events with
  exactly that timestamp are applied first and only then is the scheduler called, once, so it
  sees every worker and task freed at that instant (see docs/theory/02-simulation.md).
  Timestamps are compared exactly; there is no epsilon merging.

Failures (configured through SimulationConfig; the default has none)
--------------------------------------------------------------------
- Each attempt's fate comes from the failure model and a uniform number drawn from
  random.Random(f"{seed}:{task_id}:{attempt}") (common random numbers, see failures.py).
  A failing attempt frees its worker after fraction f of its execution time; that time is
  wasted work. The scheduler is not told in advance: the view shows the nominal expected end.
- The retry policy makes the task ready again after a backoff, or fails it permanently after
  max_attempts; then the task's descendants are CANCELLED and never started.
- A successful completion is DELIVERED through a ResultCollector; only accepted deliveries
  release successors. With probability duplicate_delivery_prob (drawn from
  random.Random(f"{seed}:dup:{task_id}:{attempt}")) the completion is delivered a second time
  at end + duplicate_delay (at-least-once delivery). The idempotent collector ignores it.
- The run ends when no event is pending. If tasks then remain unresolved that is a bug in the
  simulator and raises SchedulingError.

Idling is allowed (a scheduler may hold a ready task back, e.g. to wait for a faster worker),
except when no event is pending at all (in particular nothing is running): then no future
decision could ever happen, so the simulator raises a deadlock SchedulingError instead of
looping or silently stopping.

Extensibility: every state change caused by the passage of time goes through
`_Simulation._apply`, which dispatches on the event kind.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from dagsched.collector import ResultCollector
from dagsched.failures import FailureModel, NoFailures, check_fail_fraction
from dagsched.graph import TaskGraph
from dagsched.retry import RetryPolicy

_TASK_COMPLETED = "task_completed"
_ATTEMPT_FAILED = "attempt_failed"
_RETRY_READY = "retry_ready"
_DUPLICATE_DELIVERY = "duplicate_delivery"

SUCCESS = "success"
FAILED = "failed"


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
    """(worker_id, task_id, expected_end), sorted by worker_id. expected_end is nominal: the
    scheduler does not know in advance that an attempt will fail."""
    completed: frozenset[str]
    attempts: Mapping[str, int]
    """Number of attempts started so far per task (tasks never started are absent)."""


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
class SimulationConfig:
    """Failure, retry and delivery settings. The default reproduces the failure-free model."""

    failure_model: FailureModel = field(default_factory=NoFailures)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    duplicate_delivery_prob: float = 0.0
    duplicate_delay: float = 0.0
    idempotent_collection: bool = True

    def __post_init__(self) -> None:
        if not callable(getattr(self.failure_model, "fail_fraction", None)):
            raise TypeError(f"failure_model must have fail_fraction(), got {self.failure_model!r}")
        if not isinstance(self.retry, RetryPolicy):
            raise TypeError(f"retry must be a RetryPolicy, got {self.retry!r}")
        for name in ("duplicate_delivery_prob", "duplicate_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number, got {value!r}")
        if not 0.0 <= self.duplicate_delivery_prob <= 1.0:
            raise ValueError(
                f"duplicate_delivery_prob must be in [0, 1], got {self.duplicate_delivery_prob!r}"
            )
        if not math.isfinite(self.duplicate_delay) or self.duplicate_delay < 0:
            raise ValueError(
                f"duplicate_delay must be finite and >= 0, got {self.duplicate_delay!r}"
            )


@dataclass(frozen=True)
class TaskRecord:
    """Where and when the successful attempt of one task ran."""

    task_id: str
    worker_id: str
    start: float
    end: float


@dataclass(frozen=True)
class AttemptRecord:
    """One execution attempt, successful or failed. `end` is the failure time if it failed."""

    task_id: str
    worker_id: str
    attempt: int
    start: float
    end: float
    outcome: str  # SUCCESS or FAILED


@dataclass(frozen=True)
class SimulationResult:
    """Outcome of one simulation run.

    - records: the successful attempt of each completed task, sorted by (start, task_id);
    - attempts: every attempt, sorted by (start, task_id, attempt);
    - makespan: latest end over all attempts (0 if none);
    - worker_busy / utilization: busy time of all attempts, failed ones included, with
      utilization = sum(worker_busy) / (k * makespan);
    - status: "completed" if every task completed, else "partial";
    - failed_tasks / cancelled_tasks: sorted ids of permanently failed / cancelled tasks;
    - wasted_work: total time spent in failed attempts;
    - duplicates_received / duplicates_ignored: duplicate deliveries seen / rejected.
    """

    scheduler_name: str
    seed: int
    makespan: float
    records: tuple[TaskRecord, ...]
    worker_busy: dict[str, float]
    utilization: float
    num_decisions: int
    status: str
    attempts: tuple[AttemptRecord, ...]
    failed_tasks: tuple[str, ...]
    cancelled_tasks: tuple[str, ...]
    wasted_work: float
    duplicates_received: int
    duplicates_ignored: int


def simulate(
    graph: TaskGraph,
    workers: Iterable[Worker],
    scheduler: Scheduler,
    seed: int = 0,
    config: SimulationConfig | None = None,
) -> SimulationResult:
    """Run `scheduler` on `graph` with `workers` until no event is pending.

    Raises ValueError (TypeError for non-Worker items) for an invalid worker set and
    SchedulingError for invalid decisions or a deadlock. Scheduler randomness flows from
    `random.Random(seed)` passed to `scheduler.reset`; failure and duplicate draws use their own
    seeded generators (see the module docstring).
    """
    worker_tuple = _check_workers(workers)
    if config is None:
        config = SimulationConfig()
    elif not isinstance(config, SimulationConfig):
        raise TypeError(f"config must be a SimulationConfig, got {config!r}")
    rng = random.Random(seed)
    scheduler.reset(graph, worker_tuple, rng)
    return _Simulation(graph, worker_tuple, scheduler, config, seed).run()


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


@dataclass(frozen=True)
class _Attempt:
    """An attempt in flight; also the payload of the events it produces."""

    task_id: str
    worker_id: str
    attempt: int
    start: float
    nominal_end: float


Event = tuple[float, int, str, _Attempt]
"""(time, sequence_number, kind, payload). The unique sequence number makes heap order total
and deterministic, so kind/payload are never compared."""


class _Simulation:
    """Mutable state of one run. Private: callers only ever see SimulationResult."""

    def __init__(
        self,
        graph: TaskGraph,
        workers: tuple[Worker, ...],
        scheduler: Scheduler,
        config: SimulationConfig,
        seed: int,
    ):
        self.graph = graph
        self.workers = workers
        self.scheduler = scheduler
        self.config = config
        self.seed = seed
        self.collector = ResultCollector(idempotent=config.idempotent_collection)
        self.worker_by_id = {worker.id: worker for worker in workers}
        self.time = 0.0
        self.events: list[Event] = []
        self.sequence = 0
        self.unmet = {task_id: len(graph.predecessors(task_id)) for task_id in graph}
        self.ready: dict[str, float] = {t: 0.0 for t, count in self.unmet.items() if count == 0}
        self.idle: set[str] = set(self.worker_by_id)
        self.running: dict[str, _Attempt] = {}  # worker id -> attempt in flight
        self.in_progress: set[str] = set()  # task ids currently running
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.cancelled: set[str] = set()
        self.attempt_counts: dict[str, int] = {}
        self.attempt_records: list[AttemptRecord] = []
        self.records: list[TaskRecord] = []
        self.duplicates_received = 0
        self.num_decisions = 0

    # ----- main loop ---------------------------------------------------------------------

    def run(self) -> SimulationResult:
        self._decide()
        while self.events:
            self.time = self.events[0][0]
            while self.events and self.events[0][0] == self.time:
                _, _, kind, payload = heapq.heappop(self.events)
                self._apply(kind, payload)
            if self._unresolved():
                self._decide()
        if self._unresolved():  # unreachable: _decide raises on deadlock; kept as a safeguard
            raise SchedulingError(f"no pending events at time {self.time} but tasks remain")
        return self._result()

    def _unresolved(self) -> bool:
        resolved = len(self.completed) + len(self.failed) + len(self.cancelled)
        return resolved < len(self.graph)

    def _push(self, time: float, kind: str, payload: _Attempt) -> None:
        heapq.heappush(self.events, (time, self.sequence, kind, payload))
        self.sequence += 1

    # ----- events ------------------------------------------------------------------------

    def _apply(self, kind: str, payload: _Attempt) -> None:
        """Apply one event to the state. All event kinds are dispatched here."""
        if kind == _TASK_COMPLETED:
            self._on_success(payload)
        elif kind == _ATTEMPT_FAILED:
            self._on_failure(payload)
        elif kind == _RETRY_READY:
            self.ready[payload.task_id] = self.time
        elif kind == _DUPLICATE_DELIVERY:
            self.duplicates_received += 1
            self._deliver(payload.task_id, payload.attempt)
        else:
            raise AssertionError(f"unknown event kind {kind!r}")

    def _finish_attempt(self, attempt: _Attempt, outcome: str) -> None:
        del self.running[attempt.worker_id]
        self.idle.add(attempt.worker_id)
        self.in_progress.discard(attempt.task_id)
        self.attempt_records.append(
            AttemptRecord(
                attempt.task_id, attempt.worker_id, attempt.attempt, attempt.start, self.time,
                outcome,
            )
        )

    def _on_success(self, attempt: _Attempt) -> None:
        self._finish_attempt(attempt, SUCCESS)
        self.records.append(
            TaskRecord(attempt.task_id, attempt.worker_id, attempt.start, self.time)
        )
        self._deliver(attempt.task_id, attempt.attempt)
        probability = self.config.duplicate_delivery_prob
        if probability > 0:
            u = random.Random(f"{self.seed}:dup:{attempt.task_id}:{attempt.attempt}").random()
            if u < probability:
                self._push(self.time + self.config.duplicate_delay, _DUPLICATE_DELIVERY, attempt)

    def _deliver(self, task_id: str, attempt: int) -> None:
        """Only accepted deliveries count as completion and release successors."""
        if not self.collector.deliver(task_id, attempt):
            return
        self.completed.add(task_id)
        for succ in self.graph.successors(task_id):
            self.unmet[succ] -= 1
            # == 0 (not <= 0): with naive collection the count can go negative, and a task
            # must only ever be released once.
            if self.unmet[succ] == 0 and succ not in self.cancelled:
                self.ready[succ] = self.time

    def _on_failure(self, attempt: _Attempt) -> None:
        self._finish_attempt(attempt, FAILED)
        retry_at = self.config.retry.next_ready_time(self.time, attempt.attempt)
        if retry_at is None:
            self._fail_permanently(attempt.task_id)
        else:
            self._push(retry_at, _RETRY_READY, attempt)

    def _fail_permanently(self, task_id: str) -> None:
        """Mark the task failed and cancel all its descendants (iterative traversal)."""
        self.failed.add(task_id)
        stack = list(self.graph.successors(task_id))
        while stack:
            node = stack.pop()
            if node in self.cancelled:
                continue
            self.cancelled.add(node)
            self.ready.pop(node, None)
            stack.extend(self.graph.successors(node))

    # ----- decisions ---------------------------------------------------------------------

    def _decide(self) -> None:
        raw = self.scheduler.select(self._view())
        self.num_decisions += 1
        for assignment in self._check_assignments(raw):
            self._start(assignment)
        # Without pending events (hence nothing running) no future decision would ever occur.
        if self.ready and not self.events:
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
            running=tuple(
                sorted((w, a.task_id, a.nominal_end) for w, a in self.running.items())
            ),
            completed=frozenset(self.completed),
            attempts=MappingProxyType(dict(self.attempt_counts)),
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
            if a.task_id in self.in_progress or a.task_id in self.completed:
                raise SchedulingError(f"task {a.task_id!r} was already started")
            if a.task_id not in self.ready:
                raise SchedulingError(f"task {a.task_id!r} is not ready at time {self.time}")
            if a.worker_id not in self.idle:
                raise SchedulingError(f"worker {a.worker_id!r} is not idle at time {self.time}")
        return assignments

    def _start(self, assignment: Assignment) -> None:
        task_id, worker_id = assignment.task_id, assignment.worker_id
        number = self.attempt_counts.get(task_id, 0) + 1
        self.attempt_counts[task_id] = number
        exec_time = self.graph.tasks[task_id].duration / self.worker_by_id[worker_id].speed
        attempt = _Attempt(task_id, worker_id, number, self.time, self.time + exec_time)
        del self.ready[task_id]
        self.idle.remove(worker_id)
        self.in_progress.add(task_id)
        self.running[worker_id] = attempt
        fraction = self._fail_fraction(task_id, worker_id, number)
        if fraction is None:
            self._push(attempt.nominal_end, _TASK_COMPLETED, attempt)
        else:
            self._push(self.time + fraction * exec_time, _ATTEMPT_FAILED, attempt)

    def _fail_fraction(self, task_id: str, worker_id: str, attempt: int) -> float | None:
        # Common random numbers: the draw depends only on (seed, task, attempt).
        u = random.Random(f"{self.seed}:{task_id}:{attempt}").random()
        return check_fail_fraction(
            self.config.failure_model.fail_fraction(task_id, worker_id, attempt, u)
        )

    # ----- result ------------------------------------------------------------------------

    def _result(self) -> SimulationResult:
        records = tuple(sorted(self.records, key=lambda r: (r.start, r.task_id)))
        attempts = tuple(
            sorted(self.attempt_records, key=lambda r: (r.start, r.task_id, r.attempt))
        )
        makespan = max((a.end for a in attempts), default=0.0)
        per_worker: dict[str, list[float]] = {worker.id: [] for worker in self.workers}
        for a in attempts:
            per_worker[a.worker_id].append(a.end - a.start)
        busy = {worker_id: math.fsum(spans) for worker_id, spans in per_worker.items()}
        total_busy = math.fsum(busy.values())
        return SimulationResult(
            scheduler_name=self.scheduler.name,
            seed=self.seed,
            makespan=makespan,
            records=records,
            worker_busy=busy,
            utilization=total_busy / (len(self.workers) * makespan) if makespan > 0 else 0.0,
            num_decisions=self.num_decisions,
            status="completed" if len(self.completed) == len(self.graph) else "partial",
            attempts=attempts,
            failed_tasks=tuple(sorted(self.failed)),
            cancelled_tasks=tuple(sorted(self.cancelled)),
            wasted_work=math.fsum(a.end - a.start for a in attempts if a.outcome == FAILED),
            duplicates_received=self.duplicates_received,
            duplicates_ignored=self.collector.duplicates_ignored,
        )
