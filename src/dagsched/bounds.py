"""Makespan lower bounds that also hold for heterogeneous worker speeds."""

from __future__ import annotations

import math
from collections.abc import Iterable

from dagsched.graph import TaskGraph
from dagsched.simulator import Worker


def lower_bound(graph: TaskGraph, workers: Iterable[Worker]) -> float:
    """max(critical_length / max_speed, total_work / sum_of_speeds); no schedule can beat it.

    - critical_length / max_speed: the tasks on a critical path must run one after another,
      and each one runs at best on the fastest worker, so that chain alone takes at least this
      long no matter how many workers exist.
    - total_work / sum_of_speeds: in one unit of time all workers together process at most
      sum(speeds) units of nominal work, and all of total_work must be processed.

    For k workers of speed 1 this is exactly graph.makespan_lower_bound(k).
    """
    speeds = [worker.speed for worker in workers]
    if not speeds:
        raise ValueError("at least one worker is required")
    critical_length, _ = graph.critical_path()
    return max(critical_length / max(speeds), graph.total_work() / math.fsum(speeds))
