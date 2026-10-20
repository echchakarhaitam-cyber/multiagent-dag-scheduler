"""Tests for the informed schedulers. Expected schedules were computed by hand."""

import random

import pytest

from dagsched import (
    SCHEDULERS,
    BernoulliFailures,
    CriticalPathScheduler,
    FIFOScheduler,
    HEFTScheduler,
    RetryPolicy,
    SimulationConfig,
    Task,
    TaskGraph,
    TaskRecord,
    Worker,
    get_scheduler,
    lower_bound,
    simulate,
    validate_schedule,
)


@pytest.fixture
def hidden_chain() -> TaskGraph:
    """Case (a): the long chain z -> y hides behind short tasks a and b that sort first by id."""
    return TaskGraph([Task("a", 1), Task("b", 1), Task("z", 1), Task("y", 5, ("z",))])


@pytest.fixture
def fast_and_slow():
    """Case (b): two independent 4s tasks, one fast (2.0) and one slow (0.5) worker."""
    graph = TaskGraph([Task("t1", 4), Task("t2", 4)])
    return graph, (Worker("fast", 2.0), Worker("slow", 0.5))


# ----- case (a): long chain hidden behind short tasks ----------------------------------------


def test_fifo_misses_hidden_chain(hidden_chain, make_workers):
    result = simulate(hidden_chain, make_workers(2), FIFOScheduler())
    assert result.records == (
        TaskRecord("a", "W0", 0, 1),
        TaskRecord("b", "W1", 0, 1),
        TaskRecord("z", "W0", 1, 2),
        TaskRecord("y", "W0", 2, 7),
    )
    assert result.makespan == 7


def test_cp_starts_hidden_chain_first(hidden_chain, make_workers):
    workers = make_workers(2)
    result = simulate(hidden_chain, workers, CriticalPathScheduler())
    assert result.records == (
        TaskRecord("a", "W1", 0, 1),
        TaskRecord("z", "W0", 0, 1),
        TaskRecord("b", "W1", 1, 2),
        TaskRecord("y", "W0", 1, 6),
    )
    assert result.makespan == 6 == lower_bound(hidden_chain, workers)


# ----- case (b): fast and slow worker --------------------------------------------------------


def test_fifo_uses_slow_worker(fast_and_slow):
    graph, workers = fast_and_slow
    result = simulate(graph, workers, FIFOScheduler())
    assert result.makespan == 8
    assert TaskRecord("t2", "slow", 0, 8) in result.records


def test_cp_uses_slow_worker(fast_and_slow):
    graph, workers = fast_and_slow
    result = simulate(graph, workers, CriticalPathScheduler())
    assert result.makespan == 8


# ----- case (c) and (d) ----------------------------------------------------------------------


def test_cp_worked_example_two_workers(worked, make_workers):
    assert simulate(worked, make_workers(2), CriticalPathScheduler()).makespan == 10


def test_cp_equal_bottom_levels_smallest_id_first():
    graph = TaskGraph([Task("q", 2), Task("p", 2)])
    result = simulate(graph, [Worker("W0")], CriticalPathScheduler())
    assert [r.task_id for r in result.records] == ["p", "q"]


def test_cp_equal_speeds_smallest_worker_id_first():
    graph = TaskGraph([Task("only", 1)])
    result = simulate(graph, [Worker("w2"), Worker("w1")], CriticalPathScheduler())
    assert result.records[0].worker_id == "w1"


def test_cp_highest_level_goes_to_fastest_worker():
    # Worker id order (a_slow < b_fast) disagrees with speed order, so this checks the speed key.
    graph = TaskGraph([Task("long", 3), Task("short", 1)])
    workers = [Worker("a_slow", 1.0), Worker("b_fast", 3.0)]
    result = simulate(graph, workers, CriticalPathScheduler())
    assert TaskRecord("long", "b_fast", 0, 1) in result.records
    assert TaskRecord("short", "a_slow", 0, 1) in result.records


def test_cp_select_before_reset_fails():
    with pytest.raises(RuntimeError, match="reset"):
        CriticalPathScheduler().select(None)  # type: ignore[arg-type]


# ----- HEFT ----------------------------------------------------------------------------------


def test_heft_ranks_equal_bottom_levels_for_unit_speeds(worked, make_workers):
    scheduler = HEFTScheduler()
    simulate(worked, make_workers(2), scheduler)
    assert scheduler.upward_ranks() == worked.bottom_levels()


def test_heft_ranks_use_mean_inverse_speed(worked):
    # mean(1/2, 1/0.5) = 1.25, so every rank is 1.25 x the bottom level.
    scheduler = HEFTScheduler()
    simulate(worked, [Worker("fast", 2.0), Worker("slow", 0.5)], scheduler)
    expected = {task_id: 1.25 * level for task_id, level in worked.bottom_levels().items()}
    assert scheduler.upward_ranks() == pytest.approx(expected)


def test_heft_hidden_chain_is_optimal(hidden_chain, make_workers):
    result = simulate(hidden_chain, make_workers(2), HEFTScheduler())
    assert result.makespan == 6


def test_heft_waits_for_fast_worker(fast_and_slow):
    graph, workers = fast_and_slow
    result = simulate(graph, workers, HEFTScheduler())
    assert result.records == (TaskRecord("t1", "fast", 0, 2), TaskRecord("t2", "fast", 2, 4))
    assert result.makespan == 4
    assert result.worker_busy["slow"] == 0
    assert lower_bound(graph, workers) == 3.2


def test_heft_worked_example_two_workers(worked, make_workers):
    assert simulate(worked, make_workers(2), HEFTScheduler()).makespan == 10


def test_heft_equal_finish_time_smallest_worker_id():
    graph = TaskGraph([Task("only", 1)])
    result = simulate(graph, [Worker("w2"), Worker("w1")], HEFTScheduler())
    assert result.records[0].worker_id == "w1"


def test_heft_equal_rank_smallest_task_id_first():
    graph = TaskGraph([Task("q", 2), Task("p", 2)])
    result = simulate(graph, [Worker("W0")], HEFTScheduler())
    assert [r.task_id for r in result.records] == ["p", "q"]


def test_heft_reservation_steers_later_task_in_same_decision():
    # Ranks: x = 4 x mean(1/2, 1/1) = 3, w = 2.25, so x goes first and takes "fast" (EFT 2).
    # For w, the reserved fast worker would finish at
    # 2 + 1.5 = 3.5 and slow at 3, so w goes to slow now instead of waiting.
    graph = TaskGraph([Task("x", 4), Task("w", 3)])
    workers = [Worker("fast", 2.0), Worker("slow", 1.0)]
    result = simulate(graph, workers, HEFTScheduler())
    assert result.records == (TaskRecord("w", "slow", 0, 3), TaskRecord("x", "fast", 0, 2))


def test_heft_select_before_reset_fails():
    with pytest.raises(RuntimeError, match="reset"):
        HEFTScheduler().select(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("k", [2, 4, 8])
@pytest.mark.parametrize("p", [0.0, 0.2])
def test_cp_and_heft_identical_on_homogeneous_workers(random_dags, make_workers, k, p):
    # Proof: docs/theory/03-schedulers.md, "On identical workers, cp and HEFT produce
    # identical schedules". Failures do not break it: attempt fates do not depend on the
    # scheduler (common random numbers).
    workers = make_workers(k)
    config = SimulationConfig(failure_model=BernoulliFailures(p), retry=RetryPolicy(5))
    for index, graph in enumerate(random_dags):
        cp = simulate(graph, workers, CriticalPathScheduler(), seed=index, config=config)
        heft = simulate(graph, workers, HEFTScheduler(), seed=index, config=config)
        assert cp.records == heft.records, f"graph {index}"
        assert cp.attempts == heft.attempts, f"graph {index}"


# ----- registry ------------------------------------------------------------------------------

ALL_NAMES = sorted(SCHEDULERS)


def test_registry_contains_expected_names():
    assert set(SCHEDULERS) == {"fifo", "random", "cp", "heft"}


@pytest.mark.parametrize("name", ALL_NAMES)
def test_get_scheduler_builds_fresh_instances(name):
    first, second = get_scheduler(name), get_scheduler(name)
    assert first is not second
    assert first.name == name


def test_get_scheduler_unknown_name_lists_valid_names():
    with pytest.raises(KeyError, match="valid names: \\['cp', 'fifo', 'heft', 'random'\\]"):
        get_scheduler("nope")


# ----- randomized comparisons ----------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_NAMES)
@pytest.mark.parametrize("k", [1, 2, 4, 8])
def test_homogeneous_random_dags_valid_and_within_bounds(random_dags, make_workers, name, k):
    # Graham's bound needs a work-conserving policy. FIFO, Random and CP start
    # min(#ready, #idle) tasks by construction. HEFT does too when all speeds are equal: its EFT
    # max(now, avail[w]) + d/s is then minimized by the smallest avail[w]. A free idle worker
    # has avail == now, while a running worker ends strictly after now (completions at `now`
    # were applied before the decision) and a worker taken or reserved in this decision ends at
    # >= now + d > now. So while a free idle worker exists, every ready task's best worker is
    # one, and HEFT never holds a task back.
    workers = make_workers(k)
    for index, graph in enumerate(random_dags):
        result = simulate(graph, workers, get_scheduler(name), seed=index)
        validate_schedule(graph, workers, result)
        assert lower_bound(graph, workers) == graph.makespan_lower_bound(k)
        assert result.makespan >= lower_bound(graph, workers) - 1e-9
        critical_length, _ = graph.critical_path()
        graham = graph.total_work() / k + (1 - 1 / k) * critical_length
        assert result.makespan <= graham + 1e-9, f"graph {index}: Graham bound violated"


def heterogeneous_workers(seed: int, k: int) -> tuple[Worker, ...]:
    rng = random.Random(seed)
    return tuple(Worker(f"W{i}", rng.uniform(0.5, 2.0)) for i in range(k))


@pytest.mark.parametrize("name", ALL_NAMES)
@pytest.mark.parametrize("k", [2, 4])
def test_heterogeneous_random_dags_valid_and_above_lower_bound(random_dags, name, k):
    for index, graph in enumerate(random_dags):
        workers = heterogeneous_workers(1000 + index, k)
        result = simulate(graph, workers, get_scheduler(name), seed=index)
        validate_schedule(graph, workers, result)
        assert result.makespan >= lower_bound(graph, workers) - 1e-9


@pytest.mark.parametrize("name", ALL_NAMES)
def test_same_seed_gives_identical_result(random_dags, name):
    for index, graph in enumerate(random_dags[:10]):
        workers = heterogeneous_workers(index, 3)
        first = simulate(graph, workers, get_scheduler(name), seed=index)
        second = simulate(graph, workers, get_scheduler(name), seed=index)
        assert first == second
