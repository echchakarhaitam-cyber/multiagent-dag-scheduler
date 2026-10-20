"""Scheduling policies implementing the dagsched.simulator.Scheduler protocol, plus a registry.

The registry maps a stable short name to a factory, so experiments and CLIs can refer to
policies by name and always get a fresh instance (schedulers hold per-run state).
"""

from collections.abc import Callable

from dagsched.schedulers.baselines import FIFOScheduler, RandomScheduler
from dagsched.schedulers.critical_path import CriticalPathScheduler
from dagsched.schedulers.heft import HEFTScheduler
from dagsched.simulator import Scheduler

SCHEDULERS: dict[str, Callable[[], Scheduler]] = {
    "fifo": FIFOScheduler,
    "random": RandomScheduler,
    "cp": CriticalPathScheduler,
    "heft": HEFTScheduler,
}


def get_scheduler(name: str) -> Scheduler:
    """Return a new instance of the scheduler registered as `name`."""
    try:
        factory = SCHEDULERS[name]
    except KeyError:
        raise KeyError(f"unknown scheduler {name!r}; valid names: {sorted(SCHEDULERS)}") from None
    return factory()


__all__ = [
    "SCHEDULERS",
    "CriticalPathScheduler",
    "FIFOScheduler",
    "HEFTScheduler",
    "RandomScheduler",
    "get_scheduler",
]
