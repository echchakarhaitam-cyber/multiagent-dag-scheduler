"""Simulator tests using small fake schedulers (the real baselines are tested separately)."""

import math
import random

import pytest

from dagsched import (
    Assignment,
    SchedulerView,
    SchedulingError,
    Task,
    TaskGraph,
    TaskRecord,
    Worker,
    simulate,
)


def greedy(view: SchedulerView) -> list[Assignment]:
    """Pair ready tasks with idle workers in the order the view lists them."""
    return [Assignment(task, worker) for (task, _), worker in zip(view.ready, view.idle_workers)]


class ScriptedScheduler:
    """Delegates each decision to `decide(view)` and records everything it was shown."""

    name = "scripted"

    def __init__(self, decide=greedy):
        self.decide = decide
        self.views: list[SchedulerView] = []
        self.reset_calls = 0
        self.first_draw: float | None = None

    def reset(self, graph, workers, rng):
        self.reset_calls += 1
        self.reset_args = (graph, workers)
        self.first_draw = rng.random()

    def select(self, view):
        self.views.append(view)
        return self.decide(view)


@pytest.fixture
def small() -> TaskGraph:
    """P(1) and Q(5) are sources; R(1) depends on P."""
    return TaskGraph([Task("P", 1), Task("Q", 5), Task("R", 1, ("P",))])


# ----- correct executions --------------------------------------------------------------------


def test_single_worker_runs_everything_back_to_back(worked, make_workers):
    result = simulate(worked, make_workers(1), ScriptedScheduler())
    assert result.makespan == 13
    assert result.utilization == 1.0
    assert [r.task_id for r in result.records] == ["A", "B", "C", "D", "E", "F"]
    assert result.worker_busy == {"W0": 13}


def test_worker_speed_scales_durations(worked):
    result = simulate(worked, [Worker("fast", 2.0)], ScriptedScheduler())
    assert result.makespan == 6.5


def test_equal_timestamps_processed_before_single_decision(worked, make_workers):
    scheduler = ScriptedScheduler()
    result = simulate(worked, make_workers(2), scheduler)
    # B and E both complete at t=5: one decision there, seeing both workers idle.
    assert [view.time for view in scheduler.views] == [0, 2, 3, 5, 9]
    assert result.num_decisions == 5
    at_five = scheduler.views[3]
    assert at_five.idle_workers == ("W0", "W1")
    assert at_five.ready == (("D", 5.0),)
    assert at_five.completed == frozenset("ABCE")
    assert at_five.running == ()


def test_view_contents_mid_run(worked, make_workers):
    scheduler = ScriptedScheduler()
    simulate(worked, make_workers(2), scheduler)
    at_two, at_three = scheduler.views[1], scheduler.views[2]
    assert at_two.ready == (("B", 2.0), ("C", 2.0))
    assert at_two.idle_workers == ("W0", "W1")
    assert at_three.ready == (("E", 3.0),)
    assert at_three.idle_workers == ("W1",)
    assert at_three.running == (("W0", "B", 5.0),)
    assert at_three.graph is worked


def test_ready_sorted_by_ready_time_then_id():
    # Z becomes ready at 0, Y at 1, X at 2 (ids sort opposite to ready time).
    graph = TaskGraph(
        [
            Task("Z", 9), Task("P", 1), Task("Q", 2),
            Task("Y", 1, ("P",)), Task("X", 1, ("Q",)),
        ]
    )

    def wait_for_x(view):
        # Hold Z, Y and X back until X is ready, then start everything.
        if not any(task == "X" for task, _ in view.ready):
            return [Assignment(t, w) for (t, _), w in zip(view.ready, view.idle_workers)
                    if t in {"P", "Q"}]
        return greedy(view)

    scheduler = ScriptedScheduler(wait_for_x)
    simulate(graph, [Worker(f"W{i}") for i in range(3)], scheduler)
    at_two = next(view for view in scheduler.views if view.time == 2)
    assert at_two.ready == (("Z", 0.0), ("Y", 1.0), ("X", 2.0))


def test_reset_receives_seeded_rng_and_is_called_once(worked, make_workers):
    scheduler = ScriptedScheduler()
    workers = make_workers(2)
    result = simulate(worked, list(workers), scheduler, seed=42)
    assert scheduler.reset_calls == 1
    assert scheduler.reset_args == (worked, workers)
    assert scheduler.first_draw == random.Random(42).random()
    assert result.seed == 42
    assert result.scheduler_name == "scripted"


def test_result_records_sorted_and_unused_worker_reported(worked, make_workers):
    result = simulate(worked, make_workers(8), ScriptedScheduler())
    keys = [(r.start, r.task_id) for r in result.records]
    assert keys == sorted(keys)
    assert set(result.worker_busy) == {f"W{i}" for i in range(8)}
    assert result.worker_busy["W7"] == 0
    assert math.isclose(result.utilization, 13 / (8 * 10))


def test_idling_while_something_runs_is_allowed():
    # Only ever use the fast worker; B waits even though "slow" is idle.
    graph = TaskGraph([Task("A", 2), Task("B", 2)])
    workers = [Worker("fast", 2.0), Worker("slow", 1.0)]

    def fast_only(view):
        if "fast" in view.idle_workers and view.ready:
            return [Assignment(view.ready[0][0], "fast")]
        return []

    result = simulate(graph, workers, ScriptedScheduler(fast_only))
    assert result.records == (TaskRecord("A", "fast", 0, 1), TaskRecord("B", "fast", 1, 2))
    assert result.makespan == 2
    assert result.worker_busy == {"fast": 2, "slow": 0}
    assert result.utilization == 0.5


# ----- invalid decisions ---------------------------------------------------------------------


def at_time_one(assignments):
    """Greedy at t=0 (P->W0, Q->W1), then the given assignments at t=1 (P done, R ready)."""
    return lambda view: greedy(view) if view.time == 0 else assignments


@pytest.mark.parametrize(
    ("decide", "message"),
    [
        (lambda view: [Assignment("R", "W0")], "not ready"),
        (lambda view: [Assignment("nope", "W0")], "unknown task"),
        (lambda view: [Assignment("P", "W9")], "unknown worker"),
        (lambda view: [Assignment("P", "W0"), Assignment("P", "W1")], "assigned twice"),
        (lambda view: [Assignment("P", "W0"), Assignment("Q", "W0")], "assigned twice"),
        (lambda view: ["P->W0"], "expected Assignment"),
        (lambda view: None, "must return a list"),
        (at_time_one([Assignment("R", "W1")]), "not idle"),
        (at_time_one([Assignment("Q", "W0")]), "already started"),
        (at_time_one([Assignment("P", "W0")]), "already started"),
    ],
    ids=[
        "non-ready", "unknown-task", "unknown-worker", "duplicate-task", "duplicate-worker",
        "not-an-assignment", "not-iterable", "busy-worker", "running-task", "completed-task",
    ],
)
def test_invalid_assignment_rejected(small, make_workers, decide, message):
    with pytest.raises(SchedulingError, match=message):
        simulate(small, make_workers(2), ScriptedScheduler(decide))


def test_scheduler_that_never_assigns_deadlocks(small, make_workers):
    with pytest.raises(SchedulingError, match="deadlock"):
        simulate(small, make_workers(2), ScriptedScheduler(lambda view: []))


def test_deadlock_detected_once_nothing_runs(small, make_workers):
    # Idling at t=1 is fine (Q still runs); at t=5 nothing runs and R is still waiting.
    scheduler = ScriptedScheduler(at_time_one([]))
    with pytest.raises(SchedulingError, match="deadlock: at time 5"):
        simulate(small, make_workers(2), scheduler)
    assert [view.time for view in scheduler.views] == [0, 1, 5]


# ----- worker validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": ""},
        {"id": "W", "speed": 0},
        {"id": "W", "speed": -1},
        {"id": "W", "speed": math.nan},
        {"id": "W", "speed": math.inf},
    ],
)
def test_invalid_worker_value_rejected(kwargs):
    with pytest.raises(ValueError):
        Worker(**kwargs)


@pytest.mark.parametrize(
    "kwargs", [{"id": 3}, {"id": "W", "speed": True}, {"id": "W", "speed": "1"}]
)
def test_invalid_worker_type_rejected(kwargs):
    with pytest.raises(TypeError):
        Worker(**kwargs)


def test_worker_speed_normalised_to_float():
    assert isinstance(Worker("W", 2).speed, float)


def test_simulate_requires_a_worker(worked):
    with pytest.raises(ValueError, match="at least one worker"):
        simulate(worked, [], ScriptedScheduler())


def test_simulate_rejects_duplicate_worker_ids(worked):
    with pytest.raises(ValueError, match="duplicate worker ids"):
        simulate(worked, [Worker("W"), Worker("W", 2.0)], ScriptedScheduler())


def test_simulate_rejects_non_worker(worked):
    with pytest.raises(TypeError, match="Worker instances"):
        simulate(worked, ["W0"], ScriptedScheduler())
