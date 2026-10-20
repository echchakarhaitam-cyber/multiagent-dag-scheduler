"""Tests for failure models, retry policy and failure handling in the simulator."""

import math
import random
from collections import Counter

import pytest

from dagsched import (
    SCHEDULERS,
    AttemptRecord,
    BernoulliFailures,
    FIFOScheduler,
    NoFailures,
    RetryPolicy,
    ScheduleViolation,
    SchedulingError,
    ScriptedFailures,
    SimulationConfig,
    Task,
    TaskGraph,
    TaskRecord,
    get_scheduler,
    lower_bound,
    simulate,
    validate_schedule,
)
from dagsched.failures import check_fail_fraction

# ----- failure models ------------------------------------------------------------------------


def test_no_failures_never_fails():
    model = NoFailures()
    assert all(model.fail_fraction("A", "W0", n, u) is None for n in (1, 2) for u in (0.0, 0.99))


@pytest.mark.parametrize(
    ("u", "expected"),
    [(0.0, 1.0), (0.1, 0.5), (0.15, 0.25), (0.2, None), (0.7, None)],
)
def test_bernoulli_fails_below_p_with_fraction_from_u(u, expected):
    result = BernoulliFailures(0.2).fail_fraction("A", "W0", 1, u)
    assert result == (None if expected is None else pytest.approx(expected))


def test_bernoulli_fraction_always_in_unit_interval():
    model = BernoulliFailures(0.9)
    rng = random.Random(0)
    for _ in range(10_000):
        fraction = model.fail_fraction("A", "W0", 1, rng.random())
        assert fraction is None or 0 < fraction <= 1
    # Values just below p must still give a strictly positive fraction.
    assert model.fail_fraction("A", "W0", 1, math.nextafter(0.9, 0.0)) > 0


def test_bernoulli_extremes():
    assert BernoulliFailures(0.0).fail_fraction("A", "W0", 1, 0.0) is None
    always = BernoulliFailures(1.0)
    assert all(always.fail_fraction("A", "W0", 1, u) is not None for u in (0.0, 0.5, 0.999))


def test_bernoulli_per_worker_rates_override_default():
    model = BernoulliFailures(0.0, per_worker={"bad": 1.0})
    assert model.fail_fraction("A", "bad", 1, 0.5) == pytest.approx(0.5)
    assert model.fail_fraction("A", "good", 1, 0.5) is None


def test_bernoulli_copies_per_worker_mapping():
    rates = {"bad": 1.0}
    model = BernoulliFailures(0.0, per_worker=rates)
    rates["bad"] = 0.0
    assert model.fail_fraction("A", "bad", 1, 0.5) is not None


@pytest.mark.parametrize("p", [-0.1, 1.1, math.nan])
def test_bernoulli_rejects_bad_probability(p):
    with pytest.raises(ValueError):
        BernoulliFailures(p)


@pytest.mark.parametrize("p", [True, "0.5", None])
def test_bernoulli_rejects_non_numeric_probability(p):
    with pytest.raises(TypeError):
        BernoulliFailures(p)


def test_bernoulli_rejects_bad_per_worker_rate():
    with pytest.raises(ValueError, match="per_worker"):
        BernoulliFailures(0.1, per_worker={"W0": 2.0})


def test_scripted_failures_only_listed_attempts():
    model = ScriptedFailures({("A", 1): 0.5, ("B", 2): 1})
    assert model.fail_fraction("A", "W0", 1, 0.99) == 0.5
    assert model.fail_fraction("A", "W0", 2, 0.0) is None
    assert model.fail_fraction("B", "W1", 2, 0.5) == 1.0
    assert model.fail_fraction("C", "W0", 1, 0.0) is None


@pytest.mark.parametrize("fraction", [0, -0.5, 1.5, math.nan])
def test_scripted_failures_rejects_bad_fraction(fraction):
    with pytest.raises(ValueError):
        ScriptedFailures({("A", 1): fraction})


@pytest.mark.parametrize("key", [("A",), ("A", "1"), (1, 1), "A1", ("A", True)])
def test_scripted_failures_rejects_bad_key(key):
    with pytest.raises(TypeError):
        ScriptedFailures({key: 0.5})


def test_scripted_failures_rejects_attempt_zero():
    with pytest.raises(ValueError, match="start at 1"):
        ScriptedFailures({("A", 0): 0.5})


@pytest.mark.parametrize("value", [0.25, 1, None])
def test_check_fail_fraction_accepts_valid(value):
    assert check_fail_fraction(value) == value


@pytest.mark.parametrize("value", [0.0, 1.01, -1, math.inf, math.nan])
def test_check_fail_fraction_rejects_invalid(value):
    with pytest.raises(ValueError):
        check_fail_fraction(value)


# ----- retry policy --------------------------------------------------------------------------


def test_retry_defaults():
    policy = RetryPolicy()
    assert (policy.max_attempts, policy.backoff_base, policy.backoff_factor) == (3, 0.0, 2.0)
    assert policy.next_ready_time(4.0, 1) == 4.0
    assert policy.next_ready_time(4.0, 2) == 4.0
    assert policy.next_ready_time(4.0, 3) is None


def test_exponential_backoff_schedule():
    policy = RetryPolicy(max_attempts=5, backoff_base=1.0, backoff_factor=2.0)
    assert [policy.backoff(n) for n in (1, 2, 3, 4)] == [1, 2, 4, 8]
    assert policy.next_ready_time(10.0, 1) == 11.0
    assert policy.next_ready_time(10.0, 3) == 14.0
    assert policy.next_ready_time(10.0, 5) is None


def test_constant_backoff_with_factor_one():
    policy = RetryPolicy(max_attempts=4, backoff_base=0.5, backoff_factor=1.0)
    assert [policy.backoff(n) for n in (1, 2, 3)] == [0.5, 0.5, 0.5]


def test_single_attempt_never_retries():
    assert RetryPolicy(max_attempts=1).next_ready_time(0.0, 1) is None


def test_zero_base_with_many_attempts_does_not_overflow():
    assert RetryPolicy(max_attempts=10_000, backoff_factor=10.0).backoff(5_000) == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_base": -1.0},
        {"backoff_base": math.inf},
        {"backoff_factor": 0.5},
        {"backoff_factor": math.nan},
    ],
)
def test_retry_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


@pytest.mark.parametrize(
    "kwargs", [{"max_attempts": 2.0}, {"max_attempts": True}, {"backoff_base": "1"}]
)
def test_retry_rejects_bad_types(kwargs):
    with pytest.raises(TypeError):
        RetryPolicy(**kwargs)


def test_backoff_rejects_attempt_zero():
    with pytest.raises(ValueError, match="start at 1"):
        RetryPolicy().backoff(0)


# ----- simulator: hand-computed failure cases (worked example, 1 worker, FIFO) ---------------


def fifo_run(graph, workers, seed=0, **config):
    return simulate(graph, workers, FIFOScheduler(), seed=seed, config=SimulationConfig(**config))


def test_a_retry_immediately(worked, make_workers):
    result = fifo_run(
        worked, make_workers(1),
        failure_model=ScriptedFailures({("A", 1): 0.5}), retry=RetryPolicy(backoff_base=0),
    )
    assert result.attempts[:2] == (
        AttemptRecord("A", "W0", 1, 0, 1, "failed"),
        AttemptRecord("A", "W0", 2, 1, 3, "success"),
    )
    assert result.records[0] == TaskRecord("A", "W0", 1, 3)
    assert result.makespan == 14
    assert result.wasted_work == 1
    assert result.status == "completed"
    assert result.failed_tasks == result.cancelled_tasks == ()
    assert result.utilization == 1.0  # the failed attempt counts as busy time


def test_b_retry_with_backoff(worked, make_workers):
    result = fifo_run(
        worked, make_workers(1),
        failure_model=ScriptedFailures({("A", 1): 0.5}),
        retry=RetryPolicy(backoff_base=1, backoff_factor=2),
    )
    assert result.attempts[1] == AttemptRecord("A", "W0", 2, 2, 4, "success")
    assert result.makespan == 15
    assert result.utilization == pytest.approx(14 / 15)  # idle during the backoff [1, 2]


def test_c_permanent_failure_cancels_descendants(worked, make_workers):
    result = fifo_run(
        worked, make_workers(1),
        failure_model=ScriptedFailures({("C", 1): 0.5}), retry=RetryPolicy(max_attempts=1),
    )
    assert result.records == (TaskRecord("A", "W0", 0, 2), TaskRecord("B", "W0", 2, 5))
    assert result.attempts[-1] == AttemptRecord("C", "W0", 1, 5, 5.5, "failed")
    assert result.failed_tasks == ("C",)
    assert result.cancelled_tasks == ("D", "E", "F")
    assert result.status == "partial"
    assert result.makespan == 5.5
    assert result.wasted_work == 0.5


def test_d_everything_fails_and_run_terminates(worked, make_workers):
    result = fifo_run(
        worked, make_workers(1),
        failure_model=BernoulliFailures(1.0), retry=RetryPolicy(max_attempts=3),
    )
    assert [(a.task_id, a.attempt, a.outcome) for a in result.attempts] == [
        ("A", 1, "failed"), ("A", 2, "failed"), ("A", 3, "failed"),
    ]
    assert result.records == ()
    assert result.failed_tasks == ("A",)
    assert result.cancelled_tasks == ("B", "C", "D", "E", "F")
    assert result.status == "partial"
    assert result.wasted_work == pytest.approx(result.makespan)


# ----- simulator: duplicate delivery and idempotency -----------------------------------------


@pytest.fixture
def join_graph() -> TaskGraph:
    """P(1) and Q(5) are sources; R(1) needs both."""
    return TaskGraph([Task("P", 1), Task("Q", 5), Task("R", 1, ("P", "Q"))])


def test_e_duplicates_ignored_with_idempotent_collection(join_graph, make_workers):
    workers = make_workers(2)
    baseline = fifo_run(join_graph, workers)
    result = fifo_run(join_graph, workers, duplicate_delivery_prob=1.0)
    assert result.records == baseline.records
    assert result.makespan == baseline.makespan == 6
    assert result.duplicates_received == result.duplicates_ignored == 3
    validate_schedule(join_graph, workers, result)


def test_f_naive_collection_releases_successor_too_early(join_graph, make_workers):
    # Timeline: P ends at t=1 and its duplicate arrives at t=1. The naive collector counts
    # both, so R's two unmet dependencies drop to zero although Q still runs until t=5.
    workers = make_workers(2)
    result = fifo_run(
        join_graph, workers, duplicate_delivery_prob=1.0, idempotent_collection=False
    )
    assert TaskRecord("R", "W0", 1, 2) in result.records
    assert TaskRecord("Q", "W1", 0, 5) in result.records
    assert result.duplicates_received == 3
    assert result.duplicates_ignored == 0
    with pytest.raises(ScheduleViolation, match="precedence"):
        validate_schedule(join_graph, workers, result)


def test_delayed_duplicates_do_not_extend_makespan(join_graph, make_workers):
    result = fifo_run(join_graph, make_workers(2), duplicate_delivery_prob=1.0, duplicate_delay=10)
    assert result.makespan == 6
    assert result.duplicates_received == result.duplicates_ignored == 3


def test_partial_duplicate_probability_is_seeded(join_graph, make_workers):
    runs = [
        fifo_run(join_graph, make_workers(2), seed=s, duplicate_delivery_prob=0.5)
        for s in range(20)
    ]
    counts = {run.duplicates_received for run in runs}
    assert counts <= {0, 1, 2, 3} and len(counts) > 1
    again = fifo_run(join_graph, make_workers(2), seed=3, duplicate_delivery_prob=0.5)
    assert again == runs[3]


# ----- simulator: other failure behaviour ----------------------------------------------------


class Recorder:
    """Wraps a scheduler and keeps every view it was shown."""

    def __init__(self, inner, decide=None):
        self.inner = inner
        self.name = inner.name
        self.views = []
        self.decide = decide

    def reset(self, graph, workers, rng):
        self.inner.reset(graph, workers, rng)

    def select(self, view):
        self.views.append(view)
        return self.inner.select(view) if self.decide is None else self.decide(view)


def test_view_reports_attempts_and_nominal_end(make_workers):
    # X(4) fails at half-time (t=2); at t=1 (Y done) the view still shows X's nominal end 4.
    graph = TaskGraph([Task("X", 4), Task("Y", 1)])
    recorder = Recorder(FIFOScheduler())
    simulate(
        graph, make_workers(2), recorder,
        config=SimulationConfig(failure_model=ScriptedFailures({("X", 1): 0.5})),
    )
    at = {view.time: view for view in recorder.views}
    assert at[0].attempts == {}
    assert at[1].running == (("W0", "X", 4.0),)
    assert at[1].attempts == {"X": 1, "Y": 1}
    assert at[2].ready == (("X", 2.0),)


def test_deadlock_detected_after_retry_becomes_ready(make_workers):
    # Idling at t=1 is fine (X's retry is pending until t=2); at t=2 nothing is pending.
    graph = TaskGraph([Task("X", 2)])
    first_only = Recorder(FIFOScheduler(), decide=lambda v: FIFOScheduler().select(v)
                          if v.time == 0 else [])
    config = SimulationConfig(
        failure_model=ScriptedFailures({("X", 1): 0.5}), retry=RetryPolicy(backoff_base=1)
    )
    with pytest.raises(SchedulingError, match="deadlock: at time 2"):
        simulate(graph, make_workers(1), first_only, config=config)
    assert [view.time for view in first_only.views] == [0, 1, 2]


def test_invalid_fail_fraction_from_model_rejected(worked, make_workers):
    class Broken:
        def fail_fraction(self, task_id, worker_id, attempt, u):
            return 0.0

    with pytest.raises(ValueError, match="fail_fraction"):
        fifo_run(worked, make_workers(1), failure_model=Broken())


def test_default_config_reproduces_failure_free_run(worked, make_workers):
    workers = make_workers(2)
    implicit = simulate(worked, workers, FIFOScheduler())
    explicit = simulate(worked, workers, FIFOScheduler(), config=SimulationConfig())
    assert implicit == explicit
    assert implicit.status == "completed"
    assert implicit.wasted_work == 0
    assert implicit.duplicates_received == implicit.duplicates_ignored == 0
    assert [(a.task_id, a.worker_id, a.start, a.end) for a in implicit.attempts] == [
        (r.task_id, r.worker_id, r.start, r.end) for r in implicit.records
    ]
    assert all(a.attempt == 1 and a.outcome == "success" for a in implicit.attempts)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"duplicate_delivery_prob": 1.5}, ValueError),
        ({"duplicate_delivery_prob": -0.1}, ValueError),
        ({"duplicate_delay": -1}, ValueError),
        ({"duplicate_delay": math.inf}, ValueError),
        ({"duplicate_delivery_prob": "0.5"}, TypeError),
        ({"retry": 3}, TypeError),
        ({"failure_model": object()}, TypeError),
    ],
)
def test_simulation_config_validation(kwargs, error):
    with pytest.raises(error):
        SimulationConfig(**kwargs)


def test_simulate_rejects_non_config(worked, make_workers):
    with pytest.raises(TypeError, match="SimulationConfig"):
        simulate(worked, make_workers(1), FIFOScheduler(), config={"retry": None})


# ----- validation of the hand-computed cases -------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "retry"),
    [
        ({("A", 1): 0.5}, RetryPolicy(backoff_base=0)),
        ({("A", 1): 0.5}, RetryPolicy(backoff_base=1, backoff_factor=2)),
        ({("C", 1): 0.5}, RetryPolicy(max_attempts=1)),
    ],
    ids=["a", "b", "c"],
)
def test_hand_computed_cases_validate(worked, make_workers, failures, retry):
    workers = make_workers(1)
    result = fifo_run(worked, workers, failure_model=ScriptedFailures(failures), retry=retry)
    validate_schedule(worked, workers, result, max_attempts=retry.max_attempts)


def test_case_d_validates(worked, make_workers):
    workers = make_workers(1)
    result = fifo_run(
        worked, workers, failure_model=BernoulliFailures(1.0), retry=RetryPolicy(max_attempts=3)
    )
    validate_schedule(worked, workers, result, max_attempts=3)


# ----- randomized ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCHEDULERS))
@pytest.mark.parametrize("p", [0.1, 0.3])
@pytest.mark.parametrize("k", [2, 4])
def test_random_dags_with_failures(random_dags, make_workers, name, p, k):
    workers = make_workers(k)
    config = SimulationConfig(
        failure_model=BernoulliFailures(p),
        retry=RetryPolicy(max_attempts=5),
        duplicate_delivery_prob=0.2,  # idempotent collection must make these harmless
        duplicate_delay=0.5,
    )
    for index, graph in enumerate(random_dags):
        result = simulate(graph, workers, get_scheduler(name), seed=index, config=config)
        validate_schedule(graph, workers, result, max_attempts=5)
        if result.status == "completed":
            assert result.makespan >= lower_bound(graph, workers) - 1e-9
        assert len(result.attempts) >= len(result.records)
        assert result.wasted_work >= 0
        again = simulate(graph, workers, get_scheduler(name), seed=index, config=config)
        assert again == result, f"graph {index}: not deterministic"


# ----- common random numbers -----------------------------------------------------------------


def attempts_per_task(graph, workers, name, seed, config):
    result = simulate(graph, workers, get_scheduler(name), seed=seed, config=config)
    return tuple(sorted(Counter(a.task_id for a in result.attempts).items()))


def test_crn_default_is_on_and_explicit_true_changes_nothing(random_dags, make_workers):
    assert SimulationConfig().common_random_numbers is True
    implicit = SimulationConfig(failure_model=BernoulliFailures(0.3), retry=RetryPolicy(5))
    explicit = SimulationConfig(failure_model=BernoulliFailures(0.3), retry=RetryPolicy(5),
                                common_random_numbers=True)
    workers = make_workers(2)
    for index, graph in enumerate(random_dags[:5]):
        assert (simulate(graph, workers, get_scheduler("cp"), seed=index, config=implicit)
                == simulate(graph, workers, get_scheduler("cp"), seed=index, config=explicit))


def test_without_crn_schedulers_face_independent_failures(random_dags, make_workers):
    config = SimulationConfig(failure_model=BernoulliFailures(0.3), retry=RetryPolicy(5),
                              common_random_numbers=False)
    workers = make_workers(2)
    differing = [
        index for index, graph in enumerate(random_dags[:10])
        if attempts_per_task(graph, workers, "fifo", index, config)
        != attempts_per_task(graph, workers, "cp", index, config)
    ]
    assert differing  # some graph where the two schedulers saw different attempt counts
    # Still deterministic per scheduler and seed.
    graph = random_dags[0]
    assert (attempts_per_task(graph, workers, "fifo", 0, config)
            == attempts_per_task(graph, workers, "fifo", 0, config))


@pytest.mark.parametrize("value", [0, 1, "yes", None])
def test_crn_flag_must_be_bool(value):
    with pytest.raises(TypeError, match="common_random_numbers must be a bool"):
        SimulationConfig(common_random_numbers=value)


def test_attempt_fates_identical_across_schedulers(random_dags, make_workers):
    config = SimulationConfig(failure_model=BernoulliFailures(0.3), retry=RetryPolicy(5))
    workers = make_workers(2)
    for index, graph in enumerate(random_dags[:10]):
        per_scheduler = {
            name: Counter(
                a.task_id
                for a in simulate(graph, workers, get_scheduler(name), seed=index,
                                  config=config).attempts
            )
            for name in ("fifo", "cp", "heft", "random")
        }
        assert len({tuple(sorted(c.items())) for c in per_scheduler.values()}) == 1, index
