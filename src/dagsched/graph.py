"""Immutable, validated task graph (DAG) plus the classic static scheduling metrics.

The simulator, schedulers and experiments all read task structure from here. The graph is
validated once at construction and never changes afterwards, so derived results (orders, levels,
critical path) can be computed once and cached, and callers never have to re-check ids,
dependencies or durations.

All algorithms are iterative so very deep graphs (e.g. a 10,000-task chain) never hit Python's
recursion limit, and every tie is broken by ascending task id so results are reproducible
regardless of the order in which tasks were supplied.

Edge convention: an edge u -> v means "u is in v.deps", i.e. u must finish before v starts.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Any

_DICT_KEYS = frozenset({"duration", "deps"})


class GraphValidationError(ValueError):
    """Raised when tasks or their dependencies do not form a valid task graph."""


class CycleError(GraphValidationError):
    """Raised when the dependencies contain a cycle.

    `cycle` lists the ids on one cycle in edge order (each id is a dependency of the next one),
    starting at the smallest id and with that id repeated at the end, e.g. ["A", "B", "C", "A"].
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"dependency cycle detected: {' -> '.join(cycle)}")


@dataclass(frozen=True)
class Task:
    """One unit of work. `deps` are the ids of tasks that must finish before this one starts."""

    id: str
    duration: float
    deps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise GraphValidationError(f"task id must be a non-empty string, got {self.id!r}")
        # bool is an int subclass; True as a duration is almost certainly a bug, so reject it.
        if isinstance(self.duration, bool) or not isinstance(self.duration, (int, float)):
            raise GraphValidationError(
                f"task {self.id!r}: duration must be a number, got {self.duration!r}"
            )
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise GraphValidationError(
                f"task {self.id!r}: duration must be finite and > 0, got {self.duration!r}"
            )
        deps = self._normalise_deps()
        # Frozen dataclass: object.__setattr__ is the standard way to normalise fields once.
        object.__setattr__(self, "duration", float(self.duration))
        object.__setattr__(self, "deps", deps)

    def _normalise_deps(self) -> tuple[str, ...]:
        """Accept any iterable of ids (e.g. a list from JSON) and return a validated tuple."""
        # A bare string is iterable but would silently become one dependency per character.
        if isinstance(self.deps, str):
            raise GraphValidationError(
                f"task {self.id!r}: deps must be a sequence of ids, not a string"
            )
        try:
            deps = tuple(self.deps)
        except TypeError:
            raise GraphValidationError(
                f"task {self.id!r}: deps must be an iterable of ids, got {self.deps!r}"
            ) from None
        for dep in deps:
            if not isinstance(dep, str) or not dep:
                raise GraphValidationError(
                    f"task {self.id!r}: dependency ids must be non-empty strings, got {dep!r}"
                )
        duplicates = sorted(dep for dep, count in Counter(deps).items() if count > 1)
        if duplicates:
            raise GraphValidationError(f"task {self.id!r}: duplicate dependencies {duplicates}")
        return deps


class TaskGraph:
    """A validated, read-only DAG of tasks.

    Construction validates (in order): non-empty, unique ids, known dependencies, no
    self-dependency, acyclic. The public API only hands out tuples, read-only mappings or fresh
    dict copies, so callers cannot mutate the graph or its caches.
    """

    def __init__(self, tasks: Iterable[Task]) -> None:
        by_id = self._index_tasks(list(tasks))
        self._check_dependencies(by_id)

        self._tasks: Mapping[str, Task] = MappingProxyType(by_id)
        self._preds: dict[str, tuple[str, ...]] = {
            task_id: tuple(sorted(task.deps)) for task_id, task in by_id.items()
        }
        succ_lists: dict[str, list[str]] = {task_id: [] for task_id in by_id}
        for task_id, preds in self._preds.items():
            for pred in preds:
                succ_lists[pred].append(task_id)
        self._succs: dict[str, tuple[str, ...]] = {
            task_id: tuple(sorted(succs)) for task_id, succs in succ_lists.items()
        }
        self._order: tuple[str, ...] = self._kahn_order()

    @staticmethod
    def _index_tasks(task_list: list[Task]) -> dict[str, Task]:
        if not task_list:
            raise GraphValidationError("a task graph needs at least one task")
        by_id: dict[str, Task] = {}
        for task in task_list:
            if not isinstance(task, Task):
                raise GraphValidationError(f"expected Task instances, got {task!r}")
            if task.id in by_id:
                raise GraphValidationError(f"duplicate task id {task.id!r}")
            by_id[task.id] = task
        return by_id

    @staticmethod
    def _check_dependencies(by_id: Mapping[str, Task]) -> None:
        # Two passes so the documented validation order holds even within a single task.
        for task in by_id.values():
            for dep in task.deps:
                if dep not in by_id:
                    raise GraphValidationError(
                        f"task {task.id!r} depends on unknown task {dep!r}"
                    )
        for task in by_id.values():
            if task.id in task.deps:
                raise GraphValidationError(f"task {task.id!r} depends on itself")

    def _kahn_order(self) -> tuple[str, ...]:
        """Kahn's algorithm; a min-heap of ready ids makes the order unique (smallest id first)."""
        unmet = {task_id: len(preds) for task_id, preds in self._preds.items()}
        ready = [task_id for task_id, count in unmet.items() if count == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            task_id = heapq.heappop(ready)
            order.append(task_id)
            for succ in self._succs[task_id]:
                unmet[succ] -= 1
                if unmet[succ] == 0:
                    heapq.heappush(ready, succ)
        if len(order) < len(unmet):
            stuck = {task_id for task_id, count in unmet.items() if count > 0}
            raise CycleError(self._find_cycle(stuck))
        return tuple(order)

    def _find_cycle(self, stuck: set[str]) -> list[str]:
        """Return one real cycle among the nodes Kahn's algorithm could not schedule.

        Every stuck node still has at least one stuck predecessor (otherwise its unmet count
        would have reached zero). Walking backwards along stuck predecessors therefore never
        dead-ends and, the set being finite, must revisit a node: the walk from that node's
        first visit is a cycle. Nodes merely downstream of a cycle are never on the result.
        """
        position: dict[str, int] = {}
        walk: list[str] = []
        node = min(stuck)
        while node not in position:
            position[node] = len(walk)
            walk.append(node)
            node = min(pred for pred in self._preds[node] if pred in stuck)
        # walk[k:] follows edges backwards; reverse it so each id is a dependency of the next.
        cycle = list(reversed(walk[position[node]:]))
        start = cycle.index(min(cycle))
        cycle = cycle[start:] + cycle[:start]
        return cycle + [cycle[0]]

    # ----- basic accessors -------------------------------------------------------------------

    @property
    def tasks(self) -> Mapping[str, Task]:
        """Read-only view of id -> Task."""
        return self._tasks

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._tasks

    def __iter__(self) -> Iterator[str]:
        """Iterate task ids in topological order."""
        return iter(self._order)

    def __repr__(self) -> str:
        return f"TaskGraph({len(self)} tasks)"

    def predecessors(self, task_id: str) -> tuple[str, ...]:
        """Direct dependencies of `task_id`, sorted. Raises KeyError for an unknown id."""
        return self._preds[task_id]

    def successors(self, task_id: str) -> tuple[str, ...]:
        """Tasks that directly depend on `task_id`, sorted. Raises KeyError for an unknown id."""
        return self._succs[task_id]

    def sources(self) -> tuple[str, ...]:
        """Tasks with no dependencies, sorted."""
        return tuple(sorted(t for t, preds in self._preds.items() if not preds))

    def sinks(self) -> tuple[str, ...]:
        """Tasks nothing depends on, sorted."""
        return tuple(sorted(t for t, succs in self._succs.items() if not succs))

    def topological_order(self) -> tuple[str, ...]:
        """The unique Kahn order with ties broken by ascending id (computed at construction)."""
        return self._order

    # ----- static metrics (cached; the graph is immutable) -----------------------------------

    @cached_property
    def _bottom_levels(self) -> Mapping[str, float]:
        levels: dict[str, float] = {}
        # Reverse topological order guarantees every successor is computed first.
        for task_id in reversed(self._order):
            tail = max((levels[s] for s in self._succs[task_id]), default=0.0)
            levels[task_id] = self._tasks[task_id].duration + tail
        return MappingProxyType({task_id: levels[task_id] for task_id in self._order})

    @cached_property
    def _top_levels(self) -> Mapping[str, float]:
        levels: dict[str, float] = {}
        for task_id in self._order:
            levels[task_id] = max(
                (levels[p] + self._tasks[p].duration for p in self._preds[task_id]),
                default=0.0,
            )
        return MappingProxyType(levels)

    @cached_property
    def _critical_path(self) -> tuple[float, tuple[str, ...]]:
        levels = self._bottom_levels

        def best(candidates: Iterable[str]) -> str:
            # Largest bottom level wins; equal levels fall back to the smallest id.
            return min(candidates, key=lambda task_id: (-levels[task_id], task_id))

        node = best(self.sources())
        path = [node]
        while self._succs[node]:
            node = best(self._succs[node])
            path.append(node)
        return levels[path[0]], tuple(path)

    @cached_property
    def _total_work(self) -> float:
        return math.fsum(task.duration for task in self._tasks.values())

    def bottom_levels(self) -> dict[str, float]:
        """Longest path length from each task to the end of the graph, including the task.

        Returned as a fresh dict (keys in topological order) so callers may modify it freely.
        """
        return dict(self._bottom_levels)

    def top_levels(self) -> dict[str, float]:
        """Earliest start time of each task with unlimited workers (longest path to it)."""
        return dict(self._top_levels)

    def critical_path(self) -> tuple[float, tuple[str, ...]]:
        """(length, ids) of a longest path; ties broken by the smallest id at every step."""
        return self._critical_path

    def total_work(self) -> float:
        """Sum of all task durations."""
        return self._total_work

    def makespan_lower_bound(self, num_workers: int) -> float:
        """max(critical path length, total work / num_workers); no schedule can beat this."""
        if isinstance(num_workers, bool) or not isinstance(num_workers, int):
            raise TypeError(f"num_workers must be an int, got {num_workers!r}")
        if num_workers < 1:
            raise ValueError(f"num_workers must be >= 1, got {num_workers}")
        return max(self._critical_path[0], self._total_work / num_workers)

    # ----- serialisation ---------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Mapping[str, Any]]) -> TaskGraph:
        """Build from {"A": {"duration": 2, "deps": []}, ...}; "deps" may be omitted."""
        if not isinstance(data, Mapping):
            raise GraphValidationError(f"expected a mapping of id -> task spec, got {data!r}")
        tasks: list[Task] = []
        for task_id, spec in data.items():
            if not isinstance(spec, Mapping):
                raise GraphValidationError(f"task {task_id!r}: spec must be a mapping")
            unknown = sorted(set(spec) - _DICT_KEYS)
            if unknown:
                raise GraphValidationError(f"task {task_id!r}: unknown keys {unknown}")
            if "duration" not in spec:
                raise GraphValidationError(f"task {task_id!r}: missing 'duration'")
            tasks.append(Task(task_id, spec["duration"], spec.get("deps", ())))
        return cls(tasks)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Inverse of from_dict: ids in ascending order, deps sorted."""
        return {
            task_id: {"duration": self._tasks[task_id].duration, "deps": list(self._preds[task_id])}
            for task_id in sorted(self._tasks)
        }
