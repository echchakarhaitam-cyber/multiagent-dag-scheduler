"""Shared fixtures. The worked example's expected values are computed by hand in the tests."""

import copy
from collections.abc import Callable

import pytest

from dagsched import TaskGraph, Worker

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
