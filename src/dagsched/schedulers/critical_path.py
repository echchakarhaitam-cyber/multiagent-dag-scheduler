"""Critical-path list scheduling, a.k.a. Highest Level First (Hu, 1961).

The task whose remaining chain to the end of the graph is longest (largest bottom level) is the
one most likely to determine the makespan, so it should start first, and on the fastest worker.
Bottom levels use nominal durations: they rank chains of work, independent of which worker will
eventually run each task.
"""

from __future__ import annotations

import random

from dagsched.graph import TaskGraph
from dagsched.simulator import Assignment, SchedulerView, Worker


class CriticalPathScheduler:
    """Ready tasks by (-bottom_level, id) are paired with idle workers by (-speed, id).

    Work-conserving: it starts min(#ready, #idle) tasks at every decision.
    """

    name = "cp"

    def __init__(self) -> None:
        self._levels: dict[str, float] | None = None
        self._speeds: dict[str, float] = {}

    def reset(self, graph: TaskGraph, workers: tuple[Worker, ...], rng: random.Random) -> None:
        self._levels = graph.bottom_levels()
        self._speeds = {worker.id: worker.speed for worker in workers}

    def select(self, view: SchedulerView) -> list[Assignment]:
        if self._levels is None:
            raise RuntimeError("CriticalPathScheduler.reset() must be called before select()")
        levels, speeds = self._levels, self._speeds
        tasks = sorted((task_id for task_id, _ in view.ready), key=lambda t: (-levels[t], t))
        workers = sorted(view.idle_workers, key=lambda w: (-speeds[w], w))
        return [Assignment(task_id, worker_id) for task_id, worker_id in zip(tasks, workers)]
