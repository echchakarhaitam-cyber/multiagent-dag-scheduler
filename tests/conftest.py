"""Shared fixtures. The worked example's expected values are computed by hand in the tests."""

import copy
import random
from collections.abc import Callable

import pytest

from dagsched import Task, TaskGraph, Worker

# A(2) ; B(3) deps A ; C(1) deps A ; D(4) deps B,C ; E(2) deps C ; F(1) deps D,E
WORKED_EXAMPLE = {
    "A": {"duration": 2, "deps": []},
    "B": {"duration": 3, "deps": ["A"]},
    "C": {"duration": 1, "deps": ["A"]},
    "D": {"duration": 4, "deps": ["B", "C"]},
    "E": {"duration": 2, "deps": ["C"]},
    "F": {"duration": 1, "deps": ["D", "E"]},
}


@pytest.fixture
def worked_dict() -> dict:
    """The worked example in from_dict format (a fresh copy, safe to modify)."""
    return copy.deepcopy(WORKED_EXAMPLE)


@pytest.fixture
def worked(worked_dict: dict) -> TaskGraph:
    return TaskGraph.from_dict(worked_dict)


@pytest.fixture
def make_workers() -> Callable[..., tuple[Worker, ...]]:
    """Factory for homogeneous workers W0..W{k-1}."""

    def make(k: int, speed: float = 1.0) -> tuple[Worker, ...]:
        return tuple(Worker(f"W{i}", speed) for i in range(k))

    return make


def random_dag(rng: random.Random) -> TaskGraph:
    """20-40 tasks, edges only from lower to higher index (hence acyclic), durations in [1, 10]."""
    n = rng.randint(20, 40)
    density = rng.uniform(0.05, 0.3)
    ids = [f"t{i:02d}" for i in range(n)]
    tasks = [
        Task(ids[i], rng.uniform(1, 10), tuple(ids[j] for j in range(i) if rng.random() < density))
        for i in range(n)
    ]
    return TaskGraph(tasks)


@pytest.fixture(scope="session")
def random_dags() -> list[TaskGraph]:
    """50 reproducible random DAGs (seeds 0..49). Graphs are immutable, so sharing is safe."""
    return [random_dag(random.Random(seed)) for seed in range(50)]
