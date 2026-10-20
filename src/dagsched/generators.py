"""Seeded synthetic DAG generators and graph statistics.

Every generator takes an explicit seed and draws from its own random.Random(seed), never from
the global random state, so a (family, parameters, seed) triple always yields the same graph.
Structure is drawn first, then durations in id order.

Task ids are zero-padded ("t000", "t001", ...) so lexicographic id order equals creation order;
the schedulers break ties by id, and this keeps those ties aligned with the construction order.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dagsched.graph import Task, TaskGraph

MIN_LOGNORMAL_DURATION = 0.1
_FIELDS = {"uniform": ("low", "high"), "lognormal": ("mu", "sigma")}


def _number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def _probability(name: str, value: object) -> float:
    number = _number(name, value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return number


def _count(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class DurationSpec:
    """Distribution of task durations.

    - "uniform": U(low, high) with 0 < low <= high.
    - "lognormal": exp(N(mu, sigma)), clipped below at MIN_LOGNORMAL_DURATION (0.1). The
      lognormal is offered for its heavy right tail (a few very long tasks), but its left tail
      also produces arbitrarily small durations. Those are unrealistic for agent tasks and would
      bring task lengths close to the 1e-9 time tolerance used by the simulator and validator,
      so they are clipped.
    """

    dist: str = "uniform"
    low: float = 1.0
    high: float = 10.0
    mu: float = 0.0
    sigma: float = 1.0

    def __post_init__(self) -> None:
        if self.dist not in _FIELDS:
            raise ValueError(f"dist must be one of {sorted(_FIELDS)}, got {self.dist!r}")
        for name in ("low", "high", "mu", "sigma"):
            object.__setattr__(self, name, _number(name, getattr(self, name)))
        if self.dist == "uniform" and not 0 < self.low <= self.high:
            raise ValueError(f"uniform durations need 0 < low <= high, got {self.low}, {self.high}")
        if self.dist == "lognormal" and self.sigma < 0:
            raise ValueError(f"lognormal sigma must be >= 0, got {self.sigma}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DurationSpec:
        """Strict parser: {"dist": ..., plus exactly that distribution's parameters}."""
        if not isinstance(data, Mapping):
            raise TypeError(f"durations must be an object, got {data!r}")
        if "dist" not in data:
            raise ValueError("durations: missing field 'dist'")
        dist = data["dist"]
        if dist not in _FIELDS:
            raise ValueError(f"durations: dist must be one of {sorted(_FIELDS)}, got {dist!r}")
        allowed = {"dist", *_FIELDS[dist]}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"durations: unknown key(s) {unknown}; allowed: {sorted(allowed)}")
        missing = sorted(allowed - set(data))
        if missing:
            raise ValueError(f"durations: missing field(s) {missing}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {"dist": self.dist, **{name: getattr(self, name) for name in _FIELDS[self.dist]}}

    def sample(self, rng: random.Random) -> float:
        if self.dist == "uniform":
            return rng.uniform(self.low, self.high)
        return max(MIN_LOGNORMAL_DURATION, rng.lognormvariate(self.mu, self.sigma))


def task_ids(n_tasks: int) -> list[str]:
    """["t000", "t001", ...]; at least 3 digits, more if needed so all ids sort numerically."""
    width = max(3, len(str(n_tasks - 1)))
    return [f"t{i:0{width}d}" for i in range(n_tasks)]


def _build(ids: list[str], deps: Mapping[str, list[str]], durations: DurationSpec,
           rng: random.Random) -> TaskGraph:
    return TaskGraph(
        Task(task_id, durations.sample(rng), tuple(sorted(deps[task_id]))) for task_id in ids
    )


def layered_dag(
    n_tasks: int,
    n_layers: int,
    edge_prob: float,
    durations: DurationSpec,
    seed: int,
    skip_prob: float = 0.0,
) -> TaskGraph:
    """Tasks spread as evenly as possible over `n_layers` layers (earlier layers get the extras).

    Each possible edge from layer i to layer i+1 exists with probability edge_prob, and every
    task outside layer 0 gets at least one predecessor in the previous layer (drawn uniformly if
    none was sampled), so the depth is exactly n_layers. Optional "skip" edges from layers
    0..i-2 into layer i exist with probability skip_prob.
    """
    _count("n_tasks", n_tasks, 1)
    _count("n_layers", n_layers, 1)
    if n_layers > n_tasks:
        raise ValueError(f"n_layers ({n_layers}) cannot exceed n_tasks ({n_tasks})")
    edge_prob = _probability("edge_prob", edge_prob)
    skip_prob = _probability("skip_prob", skip_prob)
    rng = random.Random(seed)
    ids = task_ids(n_tasks)
    base, extra = divmod(n_tasks, n_layers)
    layers: list[list[str]] = []
    start = 0
    for index in range(n_layers):
        size = base + (1 if index < extra else 0)
        layers.append(ids[start:start + size])
        start += size
    deps: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for index in range(1, n_layers):
        previous = layers[index - 1]
        for task_id in layers[index]:
            chosen = [u for u in previous if rng.random() < edge_prob]
            deps[task_id] = chosen or [rng.choice(previous)]
            if skip_prob > 0:
                for earlier in layers[: index - 1]:
                    deps[task_id].extend(u for u in earlier if rng.random() < skip_prob)
    return _build(ids, deps, durations, rng)


def erdos_renyi_dag(n_tasks: int, edge_prob: float, durations: DurationSpec, seed: int) -> TaskGraph:
    """Each edge i -> j with i < j exists independently with probability edge_prob."""
    _count("n_tasks", n_tasks, 1)
    edge_prob = _probability("edge_prob", edge_prob)
    rng = random.Random(seed)
    ids = task_ids(n_tasks)
    deps = {ids[j]: [ids[i] for i in range(j) if rng.random() < edge_prob] for j in range(n_tasks)}
    return _build(ids, deps, durations, rng)


def fork_join(width: int, stages: int, durations: DurationSpec, seed: int) -> TaskGraph:
    """source -> `width` parallel tasks -> join, repeated `stages` times (each join is the next
    stage's source). 1 + stages * (width + 1) tasks and 2 * width * stages edges; only the
    durations depend on the seed."""
    _count("width", width, 1)
    _count("stages", stages, 1)
    rng = random.Random(seed)
    ids = task_ids(1 + stages * (width + 1))
    deps: dict[str, list[str]] = {task_id: [] for task_id in ids}
    sync, position = ids[0], 1
    for _ in range(stages):
        branches = ids[position:position + width]
        join = ids[position + width]
        for branch in branches:
            deps[branch] = [sync]
        deps[join] = list(branches)
        sync, position = join, position + width + 1
    return _build(ids, deps, durations, rng)


def graph_stats(graph: TaskGraph) -> dict[str, float]:
    """n_tasks, n_edges, depth (tasks on the longest path counted in edges, i.e. by number of
    tasks), critical_length, total_work, parallelism = total_work / critical_length."""
    depth: dict[str, int] = {}
    for task_id in graph.topological_order():
        depth[task_id] = 1 + max((depth[p] for p in graph.predecessors(task_id)), default=0)
    critical_length, _ = graph.critical_path()
    total_work = graph.total_work()
    return {
        "n_tasks": len(graph),
        "n_edges": sum(len(graph.predecessors(task_id)) for task_id in graph),
        "depth": max(depth.values()),
        "critical_length": critical_length,
        "total_work": total_work,
        "parallelism": total_work / critical_length,
    }
