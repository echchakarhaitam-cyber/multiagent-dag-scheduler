"""Result collection under at-least-once delivery.

A worker reports a finished task by delivering its result. Real transports often guarantee
only AT-LEAST-ONCE delivery: a report may arrive twice (e.g. resent after a lost
acknowledgement). If every arriving report is treated as "task finished", successors are
released too early: one duplicate of P counts as P's completion and as the completion of
another dependency that has not finished yet.

The idempotent collector turns at-least-once delivery into an exactly-once EFFECT: only the
first delivery per task is accepted, later ones are counted and ignored.

The naive mode (idempotent=False) accepts every delivery. It exists ONLY to demonstrate, in
tests and in a later ablation experiment, what goes wrong without idempotency. It is not a
supported configuration for real runs.
"""

from __future__ import annotations


class ResultCollector:
    """Accepts task results; in idempotent mode each task's result is accepted at most once."""

    def __init__(self, idempotent: bool = True) -> None:
        self._idempotent = idempotent
        self._first_attempt: dict[str, int] = {}
        self._accepted = 0
        self._duplicates_ignored = 0

    @property
    def idempotent(self) -> bool:
        return self._idempotent

    @property
    def accepted(self) -> int:
        """Number of deliveries accepted (in naive mode this includes duplicates)."""
        return self._accepted

    @property
    def duplicates_ignored(self) -> int:
        """Number of deliveries rejected because the task's result was already accepted."""
        return self._duplicates_ignored

    def has_result(self, task_id: str) -> bool:
        return task_id in self._first_attempt

    def accepted_attempt(self, task_id: str) -> int | None:
        """Attempt number of the first accepted delivery for `task_id`, if any."""
        return self._first_attempt.get(task_id)

    def deliver(self, task_id: str, attempt: int) -> bool:
        """Offer one delivery; return True if it is new and accepted."""
        if self._idempotent and task_id in self._first_attempt:
            self._duplicates_ignored += 1
            return False
        self._first_attempt.setdefault(task_id, attempt)
        self._accepted += 1
        return True
