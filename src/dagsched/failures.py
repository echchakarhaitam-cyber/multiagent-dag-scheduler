"""Failure models: decide whether, and when, one attempt of a task fails.

A model answers `fail_fraction(task_id, worker_id, attempt, u)`:
- None             -> the attempt succeeds;
- f in (0, 1]      -> the attempt fails after f * exec_time (exec_time = duration / speed),
                      i.e. some work is lost and the worker is freed early.

Common random numbers
---------------------
`u` is a uniform number in [0, 1) that the SIMULATOR draws for each (task_id, attempt) from its
own generator, random.Random(f"{seed}:{task_id}:{attempt}"), never from the scheduler's rng.
The fate of "attempt 2 of task X under seed 7" is therefore the same no matter which scheduler
runs, in which order tasks start, or how many random numbers the scheduler consumed. Comparing
two schedulers on the same seed then compares them on the same failures. This is the classic
common-random-numbers variance-reduction technique: differences in the results come from the
policies, not from luck. (For a worker-dependent model such as per-worker Bernoulli rates the
outcome can still differ when schedulers place the attempt on different workers, as it should.)

Models are pure functions of their arguments, so they hold no per-run state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


class FailureModel(Protocol):
    """Decides the fate of one attempt from a uniform draw `u` in [0, 1)."""

    def fail_fraction(self, task_id: str, worker_id: str, attempt: int, u: float) -> float | None:
        ...


def _check_probability(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{what} must be a number, got {value!r}")
    if not 0.0 <= value <= 1.0:  # also rejects NaN
        raise ValueError(f"{what} must be in [0, 1], got {value!r}")
    return float(value)


def _check_fraction(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{what} must be a number, got {value!r}")
    if not 0.0 < value <= 1.0:  # also rejects NaN and inf
        raise ValueError(f"{what} must be in (0, 1], got {value!r}")
    return float(value)


def check_fail_fraction(value: object) -> float | None:
    """Validate a model's answer; used by the simulator so a buggy model fails loudly."""
    return None if value is None else _check_fraction(value, "fail_fraction result")


@dataclass(frozen=True)
class NoFailures:
    """Every attempt succeeds (the default; reproduces the failure-free simulator exactly)."""

    def fail_fraction(self, task_id: str, worker_id: str, attempt: int, u: float) -> float | None:
        return None


@dataclass(frozen=True)
class BernoulliFailures:
    """Each attempt fails independently with probability p_w = per_worker.get(worker, p).

    The attempt fails iff u < p_w. The failure point reuses the same draw:
    f = (p_w - u) / p_w, which maps u in [0, p_w) onto (0, 1] (u = 0 fails at the very end).
    Reusing u keeps one random number per attempt, which is what makes the fate of an attempt
    a fixed function of (seed, task, attempt, worker).
    """

    p: float
    per_worker: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "p", _check_probability(self.p, "p"))
        if self.per_worker is not None:
            rates = {
                worker_id: _check_probability(rate, f"per_worker[{worker_id!r}]")
                for worker_id, rate in self.per_worker.items()
            }
            # Copy so later changes to the caller's dict cannot alter a frozen model.
            object.__setattr__(self, "per_worker", MappingProxyType(rates))

    def fail_fraction(self, task_id: str, worker_id: str, attempt: int, u: float) -> float | None:
        rate = self.p if self.per_worker is None else self.per_worker.get(worker_id, self.p)
        if u >= rate:
            return None
        # p_w - u > 0 exactly for distinct floats u < p_w, so the fraction is never 0.
        return min(1.0, (rate - u) / rate)


@dataclass(frozen=True)
class ScriptedFailures:
    """Fails exactly the listed attempts: (task_id, attempt) -> fraction. For hand-made tests."""

    mapping: Mapping[tuple[str, int], float]

    def __post_init__(self) -> None:
        checked: dict[tuple[str, int], float] = {}
        for key, fraction in self.mapping.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or not isinstance(key[0], str)
                or isinstance(key[1], bool)
                or not isinstance(key[1], int)
            ):
                raise TypeError(f"keys must be (task_id, attempt) tuples, got {key!r}")
            if key[1] < 1:
                raise ValueError(f"attempt numbers start at 1, got {key!r}")
            checked[key] = _check_fraction(fraction, f"fraction for {key!r}")
        object.__setattr__(self, "mapping", MappingProxyType(checked))

    def fail_fraction(self, task_id: str, worker_id: str, attempt: int, u: float) -> float | None:
        return self.mapping.get((task_id, attempt))
