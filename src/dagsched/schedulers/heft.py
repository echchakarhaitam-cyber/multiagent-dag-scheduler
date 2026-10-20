"""Online HEFT-style list scheduler (inspired by Topcuoglu, Hariri & Wu, 2002).

The original HEFT ("Heterogeneous Earliest Finish Time") is a STATIC, offline algorithm: it
ranks all tasks once, then places each task on the processor that gives it the earliest finish
time, building a complete schedule before execution, and it accounts for communication costs.
This module is an ONLINE adaptation for the dagsched simulator, without communication costs:

- Upward rank, computed once in reset():
      rank(v) = mean_cost(v) + max(rank(s) for successors s)   (0 if v is a sink)
      mean_cost(v) = duration(v) * mean(1 / speed_w over all workers)
  i.e. the bottom level with each task's cost averaged over the heterogeneous workers.
- At every decision point the scheduler re-plans from scratch: ready tasks in (-rank, id) order
  each pick the worker with the earliest finish time (EFT), given when each worker becomes
  available. A task starts now only if its best worker is idle and not yet taken in this
  decision; otherwise it waits and tentatively reserves that worker, so later tasks in the same
  decision see the reservation. Reservations are discarded after the decision.

This means HEFT may deliberately leave a slow worker idle when waiting for a fast one finishes
earlier: that is the point of the policy (see docs/theory/03-schedulers.md, case b).
"""

from __future__ import annotations

import random
import statistics

from dagsched.graph import TaskGraph
from dagsched.simulator import Assignment, SchedulerView, Worker


class HEFTScheduler:
    """Online HEFT: rank by upward rank, place each ready task on its earliest-finish worker."""

    name = "heft"

    def __init__(self) -> None:
        self._graph: TaskGraph | None = None
        self._speeds: dict[str, float] = {}
        self._ranks: dict[str, float] = {}

    def reset(self, graph: TaskGraph, workers: tuple[Worker, ...], rng: random.Random) -> None:
        self._graph = graph
        self._speeds = {worker.id: worker.speed for worker in workers}
        mean_inverse_speed = statistics.fmean(1.0 / worker.speed for worker in workers)
        ranks: dict[str, float] = {}
        # Reverse topological order: every successor's rank is known first (no recursion).
        for task_id in reversed(graph.topological_order()):
            tail = max((ranks[s] for s in graph.successors(task_id)), default=0.0)
            ranks[task_id] = graph.tasks[task_id].duration * mean_inverse_speed + tail
        self._ranks = ranks

    def upward_ranks(self) -> dict[str, float]:
        """Copy of the upward ranks computed by the last reset()."""
        return dict(self._ranks)

    def select(self, view: SchedulerView) -> list[Assignment]:
        if self._graph is None:
            raise RuntimeError("HEFTScheduler.reset() must be called before select()")
        now = view.time
        # When each worker can take new work: now if idle, else when its current task ends.
        avail = {worker_id: now for worker_id in view.idle_workers}
        for worker_id, _, expected_end in view.running:
            avail[worker_id] = expected_end
        free = set(view.idle_workers)  # idle and not yet given a task in this decision
        ranks = self._ranks
        order = sorted((task_id for task_id, _ in view.ready), key=lambda t: (-ranks[t], t))

        # Deadlock freedom: if nothing is running, every worker is idle, so avail[w] == now
        # for all w and all are free. The first task's EFT-minimizing worker is then idle and
        # free, so at least one task starts. Hence HEFT never leaves the simulation with
        # nothing running while tasks are ready.
        assignments: list[Assignment] = []
        for task_id in order:
            if not free:
                break  # later tasks could only wait; reservations are discarded anyway
            duration = self._graph.tasks[task_id].duration
            best = min(
                avail, key=lambda w: (max(now, avail[w]) + duration / self._speeds[w], w)
            )
            finish = max(now, avail[best]) + duration / self._speeds[best]
            if best in free:
                assignments.append(Assignment(task_id, best))
                free.remove(best)
            avail[best] = finish  # real start or tentative reservation for this decision
        return assignments
