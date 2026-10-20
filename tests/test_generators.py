"""Tests for the seeded DAG generators and graph statistics."""

import math

import pytest

from dagsched import DurationSpec, erdos_renyi_dag, fork_join, graph_stats, layered_dag
from dagsched.generators import MIN_LOGNORMAL_DURATION, task_ids

UNIFORM = DurationSpec("uniform", low=1, high=10)


def edges(graph):
    return {(dep, task_id) for task_id in graph for dep in graph.predecessors(task_id)}


# ----- ids and durations ---------------------------------------------------------------------


def test_task_ids_zero_padded_and_sorted():
    assert task_ids(3) == ["t000", "t001", "t002"]
    ids = task_ids(1001)
    assert ids[0] == "t0000" and ids[-1] == "t1000"
    assert ids == sorted(ids)


def test_uniform_durations_in_range():
    graph = erdos_renyi_dag(200, 0.0, DurationSpec("uniform", low=2, high=3), seed=1)
    assert all(2 <= task.duration <= 3 for task in graph.tasks.values())


def test_lognormal_durations_clipped():
    # mu = -3 puts most of the mass below 0.1, so clipping is exercised.
    spec = DurationSpec("lognormal", mu=-3.0, sigma=1.0)
    durations = [t.duration for t in erdos_renyi_dag(500, 0.0, spec, seed=2).tasks.values()]
    assert min(durations) >= MIN_LOGNORMAL_DURATION
    assert durations.count(MIN_LOGNORMAL_DURATION) > 100
    assert max(durations) > MIN_LOGNORMAL_DURATION


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"dist": "normal"}, ValueError),
        ({"dist": "uniform", "low": 0, "high": 1}, ValueError),
        ({"dist": "uniform", "low": 5, "high": 1}, ValueError),
        ({"dist": "uniform", "low": math.inf}, ValueError),
        ({"dist": "lognormal", "sigma": -1}, ValueError),
        ({"dist": "uniform", "low": "1"}, TypeError),
    ],
)
def test_duration_spec_validation(kwargs, error):
    with pytest.raises(error):
        DurationSpec(**kwargs)


def test_duration_spec_from_dict_round_trip():
    spec = DurationSpec.from_dict({"dist": "lognormal", "mu": 0.5, "sigma": 0.25})
    assert spec.to_dict() == {"dist": "lognormal", "mu": 0.5, "sigma": 0.25}
    assert DurationSpec.from_dict(UNIFORM.to_dict()) == UNIFORM


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"low": 1, "high": 2}, "missing field 'dist'"),
        ({"dist": "uniform", "low": 1}, r"missing field\(s\) \['high'\]"),
        ({"dist": "uniform", "low": 1, "high": 2, "mu": 0}, r"unknown key\(s\) \['mu'\]"),
        ({"dist": "beta"}, "dist must be one of"),
    ],
)
def test_duration_spec_from_dict_errors(data, message):
    with pytest.raises(ValueError, match=message):
        DurationSpec.from_dict(data)


# ----- layered -------------------------------------------------------------------------------


@pytest.mark.parametrize(("n_tasks", "n_layers"), [(1, 1), (30, 6), (31, 6), (10, 10), (50, 3)])
@pytest.mark.parametrize("edge_prob", [0.0, 0.3, 1.0])
def test_layered_size_and_depth(n_tasks, n_layers, edge_prob):
    graph = layered_dag(n_tasks, n_layers, edge_prob, UNIFORM, seed=3)
    stats = graph_stats(graph)
    assert stats["n_tasks"] == n_tasks
    assert stats["depth"] == n_layers


def test_layered_layers_are_balanced_and_edges_adjacent():
    # 31 tasks over 6 layers: sizes 6, 5, 5, 5, 5, 5.
    graph = layered_dag(31, 6, 0.3, UNIFORM, seed=4)
    ids = task_ids(31)
    sizes = [6, 5, 5, 5, 5, 5]
    layer_of = {}
    start = 0
    for index, size in enumerate(sizes):
        layer_of.update({task_id: index for task_id in ids[start:start + size]})
        start += size
    for u, v in edges(graph):
        assert layer_of[v] == layer_of[u] + 1
    for task_id in ids[6:]:
        assert graph.predecessors(task_id), f"{task_id} has no predecessor"


def test_layered_full_edges_between_adjacent_layers():
    graph = layered_dag(6, 2, 1.0, UNIFORM, seed=0)
    assert len(edges(graph)) == 9  # 3 x 3 complete bipartite


def test_layered_skip_edges():
    without = layered_dag(12, 4, 0.5, UNIFORM, seed=5)
    with_skips = layered_dag(12, 4, 0.5, UNIFORM, seed=5, skip_prob=1.0)
    assert graph_stats(with_skips)["depth"] == 4
    # Layer 3 (t009..t011) must then depend on every task of layers 0 and 1 (t000..t005).
    for task_id in ("t009", "t010", "t011"):
        assert set(task_ids(6)) <= set(with_skips.predecessors(task_id))
    assert len(edges(with_skips)) > len(edges(without))


def test_layered_deterministic_and_seed_sensitive():
    first = layered_dag(30, 6, 0.3, UNIFORM, seed=7)
    assert first.to_dict() == layered_dag(30, 6, 0.3, UNIFORM, seed=7).to_dict()
    assert first.to_dict() != layered_dag(30, 6, 0.3, UNIFORM, seed=8).to_dict()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"n_tasks": 3, "n_layers": 4}, ValueError),
        ({"n_tasks": 0, "n_layers": 1}, ValueError),
        ({"n_tasks": 5, "n_layers": 0}, ValueError),
        ({"n_tasks": 5, "n_layers": 2, "edge_prob": 1.5}, ValueError),
        ({"n_tasks": 5, "n_layers": 2, "skip_prob": -0.1}, ValueError),
        ({"n_tasks": 5.0, "n_layers": 2}, TypeError),
    ],
)
def test_layered_validation(kwargs, error):
    params = {"edge_prob": 0.5, "durations": UNIFORM, "seed": 0, **kwargs}
    with pytest.raises(error):
        layered_dag(**params)


# ----- Erdos-Renyi ---------------------------------------------------------------------------


def test_erdos_renyi_no_edges_when_p_zero():
    graph = erdos_renyi_dag(25, 0.0, UNIFORM, seed=1)
    assert len(graph) == 25
    assert graph_stats(graph)["n_edges"] == 0


def test_erdos_renyi_complete_when_p_one():
    graph = erdos_renyi_dag(10, 1.0, UNIFORM, seed=1)
    assert graph_stats(graph)["n_edges"] == 10 * 9 // 2
    assert graph_stats(graph)["depth"] == 10


def test_erdos_renyi_edges_go_forward_and_deterministic():
    graph = erdos_renyi_dag(40, 0.2, UNIFORM, seed=9)
    assert all(u < v for u, v in edges(graph))  # zero-padded ids: string order = index order
    assert graph.to_dict() == erdos_renyi_dag(40, 0.2, UNIFORM, seed=9).to_dict()
    assert graph.to_dict() != erdos_renyi_dag(40, 0.2, UNIFORM, seed=10).to_dict()


# ----- fork-join -----------------------------------------------------------------------------


def test_fork_join_exact_structure():
    # width=3, stages=2: source t000; stage 1 branches t001..t003, join t004; stage 2 branches
    # t005..t007, join t008. Tasks: 1 + 2 * (3 + 1) = 9. Edges per stage: 3 (sync -> branch)
    # + 3 (branch -> join) = 6, so 12 in total. Longest path: t000, branch, t004, branch, t008.
    graph = fork_join(3, 2, UNIFORM, seed=0)
    assert len(graph) == 9
    assert graph.sources() == ("t000",)
    assert graph.sinks() == ("t008",)
    for branch in ("t001", "t002", "t003"):
        assert graph.predecessors(branch) == ("t000",)
    assert graph.predecessors("t004") == ("t001", "t002", "t003")
    for branch in ("t005", "t006", "t007"):
        assert graph.predecessors(branch) == ("t004",)
    assert graph.predecessors("t008") == ("t005", "t006", "t007")
    stats = graph_stats(graph)
    assert stats["n_edges"] == 12
    assert stats["depth"] == 5


def test_fork_join_seed_changes_only_durations():
    first, second = fork_join(3, 2, UNIFORM, seed=1), fork_join(3, 2, UNIFORM, seed=2)
    assert edges(first) == edges(second)
    assert first.to_dict() != second.to_dict()


def test_fork_join_validation():
    with pytest.raises(ValueError):
        fork_join(0, 2, UNIFORM, seed=0)


# ----- graph_stats ---------------------------------------------------------------------------


def test_graph_stats_worked_example(worked):
    assert graph_stats(worked) == {
        "n_tasks": 6,
        "n_edges": 7,
        "depth": 4,
        "critical_length": 10,
        "total_work": 13,
        "parallelism": 1.3,
    }
