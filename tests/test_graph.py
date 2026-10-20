"""Tests for dagsched.graph. Worked-example values were computed by hand (see docs/theory)."""

import dataclasses
import itertools
import math

import pytest

from dagsched import CycleError, GraphValidationError, Task, TaskGraph

# The worked example (fixtures `worked` and `worked_dict`) lives in tests/conftest.py.

def edges(graph: TaskGraph) -> list[tuple[str, str]]:
    return [(dep, task.id) for task in graph.tasks.values() for dep in task.deps]


def assert_real_cycle(tasks: list[Task], cycle: list[str]) -> None:
    by_id = {task.id: task for task in tasks}
    assert len(cycle) >= 3
    assert cycle[0] == cycle[-1]
    assert len(set(cycle[:-1])) == len(cycle) - 1, "cycle must not repeat inner nodes"
    for u, v in itertools.pairwise(cycle):
        assert u in by_id[v].deps, f"{u} -> {v} is not a dependency edge"


# ----- worked example ------------------------------------------------------------------------


def test_worked_example_topological_order(worked):
    assert worked.topological_order() == ("A", "B", "C", "D", "E", "F")
    assert tuple(worked) == worked.topological_order()


def test_worked_example_bottom_levels(worked):
    assert worked.bottom_levels() == {"A": 10, "B": 8, "C": 6, "D": 5, "E": 3, "F": 1}


def test_worked_example_top_levels(worked):
    assert worked.top_levels() == {"A": 0, "B": 2, "C": 2, "D": 5, "E": 3, "F": 9}


def test_worked_example_critical_path(worked):
    assert worked.critical_path() == (10, ("A", "B", "D", "F"))


def test_worked_example_total_work(worked):
    assert worked.total_work() == 13


def test_worked_example_makespan_lower_bound(worked):
    assert worked.makespan_lower_bound(1) == 13
    assert worked.makespan_lower_bound(2) == 10
    assert worked.makespan_lower_bound(100) == 10


@pytest.mark.parametrize("bad", [0, -1])
def test_makespan_lower_bound_rejects_non_positive_workers(worked, bad):
    with pytest.raises(ValueError):
        worked.makespan_lower_bound(bad)


@pytest.mark.parametrize("bad", [1.5, "2", True, None])
def test_makespan_lower_bound_rejects_non_int_workers(worked, bad):
    with pytest.raises(TypeError):
        worked.makespan_lower_bound(bad)


def test_worked_example_structure_queries(worked):
    assert worked.sources() == ("A",)
    assert worked.sinks() == ("F",)
    assert worked.predecessors("D") == ("B", "C")
    assert worked.successors("C") == ("D", "E")
    assert worked.predecessors("A") == ()
    assert worked.successors("F") == ()


def test_unknown_id_queries_raise_key_error(worked):
    with pytest.raises(KeyError):
        worked.predecessors("Z")
    with pytest.raises(KeyError):
        worked.successors("Z")


def test_len_and_contains(worked):
    assert len(worked) == 6
    assert "A" in worked
    assert "Z" not in worked


def test_topological_order_respects_every_edge(worked):
    index = {task_id: i for i, task_id in enumerate(worked.topological_order())}
    for u, v in edges(worked):
        assert index[u] < index[v]


def test_top_plus_bottom_level_bounded_by_critical_length(worked):
    length, path = worked.critical_path()
    top, bottom = worked.top_levels(), worked.bottom_levels()
    for task_id in worked:
        assert top[task_id] + bottom[task_id] <= length
    for task_id in path:
        assert top[task_id] + bottom[task_id] == length


def test_order_independent_of_input_order(worked):
    tasks = list(worked.tasks.values())
    reversed_graph = TaskGraph(reversed(tasks))
    assert reversed_graph.topological_order() == ("A", "B", "C", "D", "E", "F")
    assert reversed_graph.critical_path() == (10, ("A", "B", "D", "F"))


# ----- Task validation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": "", "duration": 1},
        {"id": 7, "duration": 1},
        {"id": "A", "duration": 0},
        {"id": "A", "duration": -1},
        {"id": "A", "duration": math.nan},
        {"id": "A", "duration": math.inf},
        {"id": "A", "duration": -math.inf},
        {"id": "A", "duration": "2"},
        {"id": "A", "duration": True},
        {"id": "A", "duration": 1, "deps": ["B", "B"]},
        {"id": "A", "duration": 1, "deps": "B"},
        {"id": "A", "duration": 1, "deps": [""]},
        {"id": "A", "duration": 1, "deps": [3]},
        {"id": "A", "duration": 1, "deps": 5},
    ],
    ids=[
        "empty-id", "non-str-id", "zero-duration", "negative-duration", "nan-duration",
        "inf-duration", "neg-inf-duration", "str-duration", "bool-duration", "duplicate-dep",
        "string-deps", "empty-dep-id", "non-str-dep", "non-iterable-deps",
    ],
)
def test_invalid_task_rejected(kwargs):
    with pytest.raises(GraphValidationError):
        Task(**kwargs)


def test_task_normalises_list_deps_and_int_duration():
    task = Task("A", 2, ["C", "B"])
    assert task.deps == ("C", "B")
    assert isinstance(task.duration, float)


def test_task_is_immutable():
    task = Task("A", 1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.duration = 5.0  # type: ignore[misc]


# ----- TaskGraph validation ------------------------------------------------------------------


def test_empty_graph_rejected():
    with pytest.raises(GraphValidationError, match="at least one task"):
        TaskGraph([])


def test_duplicate_id_rejected():
    with pytest.raises(GraphValidationError, match="duplicate task id"):
        TaskGraph([Task("A", 1), Task("A", 2)])


def test_unknown_dependency_rejected():
    with pytest.raises(GraphValidationError, match="unknown task 'Z'"):
        TaskGraph([Task("A", 1), Task("B", 1, ("Z",))])


def test_self_dependency_rejected():
    with pytest.raises(GraphValidationError, match="depends on itself") as excinfo:
        TaskGraph([Task("A", 1, ("A",))])
    assert not isinstance(excinfo.value, CycleError)


def test_unknown_dependency_reported_before_self_dependency():
    with pytest.raises(GraphValidationError, match="unknown task"):
        TaskGraph([Task("A", 1, ("A",)), Task("B", 1, ("Z",))])


def test_non_task_rejected():
    with pytest.raises(GraphValidationError):
        TaskGraph([{"id": "A", "duration": 1}])  # type: ignore[list-item]


def test_three_node_cycle_with_unrelated_task():
    tasks = [Task("A", 1, ("C",)), Task("B", 1, ("A",)), Task("C", 1, ("B",)), Task("X", 1)]
    with pytest.raises(CycleError) as excinfo:
        TaskGraph(tasks)
    cycle = excinfo.value.cycle
    assert_real_cycle(tasks, cycle)
    assert "X" not in cycle
    assert cycle == ["A", "B", "C", "A"]
    assert "A -> B -> C -> A" in str(excinfo.value)
    assert isinstance(excinfo.value, GraphValidationError)


def test_two_node_cycle():
    tasks = [Task("A", 1, ("B",)), Task("B", 1, ("A",))]
    with pytest.raises(CycleError) as excinfo:
        TaskGraph(tasks)
    assert_real_cycle(tasks, excinfo.value.cycle)
    assert excinfo.value.cycle == ["A", "B", "A"]


def test_cycle_not_reachable_from_any_source():
    # S and T form a valid component; P <-> Q have no path from any source.
    tasks = [Task("S", 1), Task("T", 1, ("S",)), Task("P", 1, ("Q",)), Task("Q", 1, ("P",))]
    with pytest.raises(CycleError) as excinfo:
        TaskGraph(tasks)
    cycle = excinfo.value.cycle
    assert_real_cycle(tasks, cycle)
    assert set(cycle) == {"P", "Q"}


def test_cycle_excludes_nodes_downstream_of_cycle():
    # "A" is stuck only because it depends on the cycle; it must not appear in the report.
    tasks = [
        Task("S", 1),
        Task("M", 1, ("S", "N")),
        Task("N", 1, ("M",)),
        Task("A", 1, ("N",)),
    ]
    with pytest.raises(CycleError) as excinfo:
        TaskGraph(tasks)
    cycle = excinfo.value.cycle
    assert_real_cycle(tasks, cycle)
    assert set(cycle) == {"M", "N"}


# ----- other shapes --------------------------------------------------------------------------


def test_single_task_graph():
    graph = TaskGraph([Task("only", 2.5)])
    assert graph.topological_order() == ("only",)
    assert graph.sources() == graph.sinks() == ("only",)
    assert graph.bottom_levels() == {"only": 2.5}
    assert graph.top_levels() == {"only": 0}
    assert graph.critical_path() == (2.5, ("only",))
    assert graph.makespan_lower_bound(4) == 2.5


def test_two_independent_components():
    graph = TaskGraph([Task("C", 5), Task("B", 2, ("A",)), Task("A", 1)])
    assert graph.topological_order() == ("A", "B", "C")
    assert graph.sources() == ("A", "C")
    assert graph.sinks() == ("B", "C")
    assert graph.critical_path() == (5, ("C",))
    assert graph.total_work() == 8
    assert graph.makespan_lower_bound(1) == 8
    assert graph.makespan_lower_bound(2) == 5


def test_diamond_with_tied_bottom_levels_picks_smallest_id():
    # Both middle tasks have bottom level 3; the larger id is inserted first on purpose.
    graph = TaskGraph(
        [Task("S", 1), Task("M2", 2, ("S",)), Task("M1", 2, ("S",)), Task("T", 1, ("M2", "M1"))]
    )
    assert graph.bottom_levels()["M1"] == graph.bottom_levels()["M2"] == 3
    assert graph.critical_path() == (4, ("S", "M1", "T"))


def test_tied_sources_pick_smallest_id():
    graph = TaskGraph([Task("Q", 2), Task("P", 2)])
    assert graph.critical_path() == (2, ("P",))


def test_from_dict_to_dict_round_trip(worked, worked_dict):
    data = worked.to_dict()
    assert data == worked_dict
    again = TaskGraph.from_dict(data)
    assert again.to_dict() == data
    assert dict(again.tasks) == dict(worked.tasks)


def test_to_dict_sorts_ids_and_deps():
    graph = TaskGraph([Task("b", 1, ("c", "a")), Task("c", 1), Task("a", 1)])
    data = graph.to_dict()
    assert list(data) == ["a", "b", "c"]
    assert data["b"]["deps"] == ["a", "c"]


def test_from_dict_deps_optional():
    graph = TaskGraph.from_dict({"A": {"duration": 1}})
    assert graph.tasks["A"].deps == ()


@pytest.mark.parametrize(
    "data",
    [
        ["A"],
        {"A": 3},
        {"A": {"deps": []}},
        {"A": {"duration": 1, "weight": 2}},
        {"A": {"duration": 0}},
    ],
    ids=["not-mapping", "spec-not-mapping", "missing-duration", "unknown-key", "bad-duration"],
)
def test_from_dict_rejects_malformed_input(data):
    with pytest.raises(GraphValidationError):
        TaskGraph.from_dict(data)


def test_long_chain_is_handled_iteratively():
    n = 10_000
    ids = [f"t{i:05d}" for i in range(n)]
    tasks = [Task(ids[0], 1)] + [Task(ids[i], 1, (ids[i - 1],)) for i in range(1, n)]
    graph = TaskGraph(reversed(tasks))
    assert graph.topological_order() == tuple(ids)
    length, path = graph.critical_path()
    assert length == n
    assert path == tuple(ids)
    assert graph.bottom_levels()[ids[0]] == n
    assert graph.top_levels()[ids[-1]] == n - 1


def test_long_cycle_is_detected_iteratively():
    n = 10_000
    ids = [f"t{i:05d}" for i in range(n)]
    tasks = [Task(ids[i], 1, (ids[i - 1],)) for i in range(n)]  # ids[-1] closes the loop
    with pytest.raises(CycleError) as excinfo:
        TaskGraph(tasks)
    assert excinfo.value.cycle == ids + [ids[0]]


def test_graph_is_read_only(worked):
    with pytest.raises(TypeError):
        worked.tasks["Z"] = Task("Z", 1)  # type: ignore[index]
    levels = worked.bottom_levels()
    levels["A"] = -1
    assert worked.bottom_levels()["A"] == 10
