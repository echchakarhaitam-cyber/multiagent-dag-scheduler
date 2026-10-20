"""dagsched: fault-tolerant scheduling of multi-agent task graphs."""

from dagsched.bounds import lower_bound
from dagsched.collector import ResultCollector
from dagsched.failures import BernoulliFailures, FailureModel, NoFailures, ScriptedFailures
from dagsched.graph import CycleError, GraphValidationError, Task, TaskGraph
from dagsched.retry import RetryPolicy
from dagsched.schedulers import (
    SCHEDULERS,
    CriticalPathScheduler,
    FIFOScheduler,
    HEFTScheduler,
    RandomScheduler,
    get_scheduler,
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
    "SCHEDULERS",
    "Assignment",
    "BernoulliFailures",
    "CriticalPathScheduler",
    "CycleError",
    "FIFOScheduler",
    "FailureModel",
    "GraphValidationError",
    "HEFTScheduler",
    "NoFailures",
    "RandomScheduler",
    "ResultCollector",
    "RetryPolicy",
    "ScheduleViolation",
    "Scheduler",
    "SchedulerView",
    "SchedulingError",
    "ScriptedFailures",
    "SimulationResult",
    "Task",
    "TaskGraph",
    "TaskRecord",
    "Worker",
    "__version__",
    "get_scheduler",
    "lower_bound",
    "simulate",
    "validate_schedule",
]
