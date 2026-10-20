"""Experiment runner: the full grid of a config, one validated simulation per row of runs.csv.

Paired design: for each (graph, graph_seed, workers, failure p, sim_seed) the SAME graph
instance and the SAME worker set are given to every scheduler, and because failure draws are
common random numbers (see dagsched.failures) every scheduler also faces the same attempt
fates. Differences between schedulers on one such key are therefore attributable to the policy.

runs.csv contains no wall-clock values, so rerunning a config reproduces it byte for byte;
timing and provenance go to metadata.json instead.
"""

from __future__ import annotations

import csv
import json
import platform
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dagsched
from dagsched.bounds import lower_bound
from dagsched.experiments.config import ExperimentConfig, GraphSpec
from dagsched.failures import BernoulliFailures
from dagsched.generators import graph_stats
from dagsched.graph import TaskGraph
from dagsched.schedulers import get_scheduler
from dagsched.simulator import SimulationConfig, SimulationResult, Worker, simulate
from dagsched.validation import ScheduleViolation, validate_schedule

RUN_COLUMNS = (
    "experiment", "graph_family", "graph_params", "graph_seed", "n_tasks", "n_edges", "depth",
    "critical_length", "total_work", "parallelism", "k", "speeds", "failure_p", "max_attempts",
    "duplicate_prob", "sim_seed", "scheduler", "status", "makespan", "lower_bound",
    "normalized_makespan", "utilization", "wasted_work", "n_attempts", "n_failed_tasks",
    "n_cancelled_tasks", "duplicates_ignored",
)


class ExperimentError(RuntimeError):
    """An experiment could not be completed (e.g. a simulation produced an invalid schedule)."""


@dataclass(frozen=True)
class _Instance:
    """Everything shared by the schedulers of one paired comparison."""

    spec: GraphSpec
    graph_seed: int
    graph: TaskGraph
    stats: dict[str, float]
    workers: tuple[Worker, ...]
    lower_bound: float
    failure_p: float
    sim_seed: int


def _instances(config: ExperimentConfig) -> Iterator[_Instance]:
    for spec in config.graphs:
        for graph_seed in config.graph_seeds:
            graph = spec.build(graph_seed)  # one instance, reused by every scheduler below
            stats = graph_stats(graph)
            for worker_spec in config.workers:
                workers = worker_spec.build(graph_seed)  # same set for every scheduler
                bound = lower_bound(graph, workers)
                for failure_p in config.failure_ps:
                    for sim_seed in config.sim_seeds:
                        yield _Instance(spec, graph_seed, graph, stats, workers, bound,
                                        failure_p, sim_seed)


def _row(config: ExperimentConfig, inst: _Instance, result: SimulationResult) -> dict[str, Any]:
    return {
        "experiment": config.name,
        "graph_family": inst.spec.family,
        "graph_params": inst.spec.params_json(),
        "graph_seed": inst.graph_seed,
        **inst.stats,
        "k": len(inst.workers),
        "speeds": json.dumps([worker.speed for worker in inst.workers]),
        "failure_p": inst.failure_p,
        "max_attempts": config.retry.max_attempts,
        "duplicate_prob": config.duplicate_delivery_prob,
        "sim_seed": inst.sim_seed,
        "scheduler": result.scheduler_name,
        "status": result.status,
        "makespan": result.makespan,
        "lower_bound": inst.lower_bound,
        "normalized_makespan": result.makespan / inst.lower_bound,
        "utilization": result.utilization,
        "wasted_work": result.wasted_work,
        "n_attempts": len(result.attempts),
        "n_failed_tasks": len(result.failed_tasks),
        "n_cancelled_tasks": len(result.cancelled_tasks),
        "duplicates_ignored": result.duplicates_ignored,
    }


def iter_runs(config: ExperimentConfig) -> Iterator[dict[str, Any]]:
    """Yield one validated row per run, in a fixed order (grid order, then scheduler order)."""
    for inst in _instances(config):
        sim_config = SimulationConfig(
            failure_model=BernoulliFailures(inst.failure_p),
            retry=config.retry,
            duplicate_delivery_prob=config.duplicate_delivery_prob,
            duplicate_delay=config.duplicate_delay,
        )
        for name in config.schedulers:
            result = simulate(inst.graph, inst.workers, get_scheduler(name),
                              seed=inst.sim_seed, config=sim_config)
            try:
                validate_schedule(inst.graph, inst.workers, result,
                                  max_attempts=config.retry.max_attempts)
            except ScheduleViolation as exc:
                raise ExperimentError(
                    f"invalid schedule from {name!r} on {inst.spec.family} "
                    f"{inst.spec.params_json()} graph_seed={inst.graph_seed} k={len(inst.workers)} "
                    f"failure_p={inst.failure_p} sim_seed={inst.sim_seed}: {exc}"
                ) from exc
            yield _row(config, inst, result)


def write_runs_csv(rows: list[dict[str, Any]], path: Path) -> None:
    # newline="" + explicit "\n" terminator: identical bytes on every platform and rerun.
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _git_info() -> dict[str, Any]:
    """Commit hash and dirty flag of the repository containing this package, if any.

    "dirty" means tracked files differ from the commit. Untracked files are ignored on
    purpose: the outputs of earlier experiments are untracked until committed and say nothing
    about the code that ran.
    """
    where = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=where, capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=where,
            capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": "unknown"}
    return {"commit": commit, "dirty": bool(status.strip())}


def run_experiment(config: ExperimentConfig, out_dir: str | Path) -> list[dict[str, Any]]:
    """Run the whole grid; write runs.csv and metadata.json only if every run is valid."""
    started = time.perf_counter()
    rows = list(iter_runs(config))  # raises before anything is written
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_runs_csv(rows, out / "runs.csv")
    metadata = {
        "config": config.raw,
        "git": _git_info(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dagsched_version": dagsched.__version__,
        "n_runs": len(rows),
        "wall_time_s": time.perf_counter() - started,
    }
    (out / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return rows
