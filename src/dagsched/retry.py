"""Retry policy: how often a failed task is retried and how long it waits before each retry."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """At most `max_attempts` attempts per task, with exponential backoff between them.

    After failed attempt n (1-based) at time t, the task becomes ready again at
        t + backoff_base * backoff_factor ** (n - 1),
    unless n == max_attempts, in which case the task fails permanently. Backoff spreads retries
    out in time, which in real systems gives a transient cause (overload, a flaky dependency)
    time to clear; backoff_base = 0 retries immediately.
    """

    max_attempts: int = 3
    backoff_base: float = 0.0
    backoff_factor: float = 2.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise TypeError(f"max_attempts must be an int, got {self.max_attempts!r}")
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        for name, minimum in (("backoff_base", 0.0), ("backoff_factor", 1.0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number, got {value!r}")
            if not math.isfinite(value) or value < minimum:
                raise ValueError(f"{name} must be finite and >= {minimum}, got {value!r}")
            object.__setattr__(self, name, float(value))

    def backoff(self, attempt: int) -> float:
        """Delay after failed attempt `attempt` (1-based) before the task is ready again."""
        if attempt < 1:
            raise ValueError(f"attempt numbers start at 1, got {attempt}")
        if self.backoff_base == 0.0:
            return 0.0  # avoids computing a huge, irrelevant power for many attempts
        return self.backoff_base * self.backoff_factor ** (attempt - 1)

    def next_ready_time(self, fail_time: float, attempt: int) -> float | None:
        """When the task may be retried after failing `attempt`, or None if it failed for good."""
        if attempt >= self.max_attempts:
            return None
        return fail_time + self.backoff(attempt)
