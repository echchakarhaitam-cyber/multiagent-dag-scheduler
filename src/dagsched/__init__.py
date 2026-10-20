"""dagsched: fault-tolerant scheduling of multi-agent task graphs."""

from dagsched.bounds import lower_bound
from dagsched.graph import CycleError, GraphValidationError, Task, TaskGraph
from dagsched.schedulers import (
    CriticalPathScheduler,
    FIFOScheduler,
    HEFTScheduler,
    RandomScheduler,
)
from dagsched.simulator import (
    Assignment,
    Scheduler,
    SchedulerView,
    SchedulingError,
    SimulationResult,
    TaskRecord,
    Worker,
    simulate,
)
from dagsched.validation import ScheduleViolation, validate_schedule

__version__ = "0.1.0"

__all__ = [
    "Assignment",
    "CriticalPathScheduler",
    "CycleError",
    "FIFOScheduler",
    "GraphValidationError",
    "HEFTScheduler",
    "RandomScheduler",
    "ScheduleViolation",
    "Scheduler",
    "SchedulerView",
    "SchedulingError",
    "SimulationResult",
    "Task",
    "TaskGraph",
    "TaskRecord",
    "Worker",
    "__version__",
    "lower_bound",
    "simulate",
    "validate_schedule",
]
