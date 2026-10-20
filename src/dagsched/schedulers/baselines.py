"""Baseline schedulers: reference points that later, smarter policies must beat.

Both are work-conserving list schedulers: at every decision point they start
min(#ready, #idle) tasks, so no worker idles while a task is ready. That makes Graham's
list-scheduling bound apply to them on homogeneous workers (docs/theory/02-simulation.md).
"""

from __future__ import annotations

import random

from dagsched.graph import TaskGraph
from dagsched.simulator import Assignment, SchedulerView, Worker


class FIFOScheduler:
    """Ready tasks in (ready_time, id) order go to idle workers in id order, one each.

    Ignores worker speed and task structure on purpose: it is the simplest deterministic
    work-conserving policy.
    """

    name = "fifo"

    def reset(self, graph: TaskGraph, workers: tuple[Worker, ...], rng: random.Random) -> None:
        """FIFO is stateless and needs no randomness; reset exists to satisfy the protocol."""

    def select(self, view: SchedulerView) -> list[Assignment]:
        return [
            Assignment(task_id, worker_id)
            for (task_id, _), worker_id in zip(view.ready, view.idle_workers)
        ]


class RandomScheduler:
    """Pairs a random permutation of the ready tasks with a random permutation of idle workers.

    Uses only the rng received in reset(), never the global `random` module, so a run is fully
    determined by the simulation seed.
    """

    name = "random"

    def __init__(self) -> None:
        self._rng: random.Random | None = None

    def reset(self, graph: TaskGraph, workers: tuple[Worker, ...], rng: random.Random) -> None:
        self._rng = rng

    def select(self, view: SchedulerView) -> list[Assignment]:
        if self._rng is None:
            raise RuntimeError("RandomScheduler.reset() must be called before select()")
        tasks = [task_id for task_id, _ in view.ready]
        workers = list(view.idle_workers)
        self._rng.shuffle(tasks)
        self._rng.shuffle(workers)
        return [Assignment(task_id, worker_id) for task_id, worker_id in zip(tasks, workers)]
