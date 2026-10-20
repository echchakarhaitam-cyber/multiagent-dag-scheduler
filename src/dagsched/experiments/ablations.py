"""Ablation studies.

- idempotency (RQ4): is idempotent result collection necessary? Duplicate deliveries are
  injected with probability q, once with the idempotent collector and once with the naive one,
  and every run is checked by the independent validator.

  RECORDING MODE: unlike the experiment runner, which aborts on the first invalid schedule,
  this ablation records whether each run is valid and the kind of violation. Invalid schedules
  are the measured outcome here (the naive collector is expected to produce them). This mode
  exists only in this ablation.
- crn (RQ5): how much variance do common random numbers remove? The same paired comparison
  (cp - fifo) is run with and without common random numbers (SimulationConfig.
  common_random_numbers) and the variances of the paired differences are compared. Validation
  aborts here as usual.

The graph specifications are read from the E1 config so that the ablations use exactly the
graphs of the main experiments. Every instance uses the runner's per-instance seed, shared by
all arms of the comparison (paired design).
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import platform
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dagsched
from dagsched.bounds import lower_bound
from dagsched.experiments.config import GraphSpec, load_config
from dagsched.experiments.runner import _git_info, instance_seed
from dagsched.failures import BernoulliFailures
from dagsched.retry import RetryPolicy
from dagsched.schedulers import get_scheduler
from dagsched.simulator import SimulationConfig, Worker, simulate
from dagsched.stats import bootstrap_ci, mean_std
from dagsched.validation import ScheduleViolation, validate_schedule

DEFAULT_GRAPHS = Path("configs/e1_homogeneous.json")

IDEMPOTENCY_RUN_COLUMNS = (
    "graph_family", "graph_params", "graph_seed", "run_seed", "duplicate_prob", "idempotent",
    "scheduler", "valid", "violation", "makespan", "duplicates_received", "duplicates_ignored",
)
IDEMPOTENCY_SUMMARY_COLUMNS = (
    "duplicate_prob", "idempotent", "scheduler", "n_runs", "n_invalid", "invalid_fraction",
    "ci_low", "ci_high", "n_precedence", "n_overlap", "n_other", "mean_duplicates_received",
)


class AblationError(RuntimeError):
    """The ablation could not run, or produced a result that indicates a bug."""


@dataclass(frozen=True)
class IdempotencyGrid:
    graph_seeds: tuple[int, ...] = tuple(range(30))
    duplicate_probs: tuple[float, ...] = (0.0, 0.05, 0.1, 0.2)
    idempotent: tuple[bool, ...] = (True, False)
    schedulers: tuple[str, ...] = ("fifo", "cp")
    k: int = 4
    duplicate_delay: float = 0.5


def classify_violation(exc: ScheduleViolation) -> str:
    """precedence / overlap / other, from the first line of the validator's message."""
    lines = str(exc).splitlines()
    first = lines[0] if lines else ""
    if first.startswith("precedence"):
        return "precedence"
    if first.startswith("overlap"):
        return "overlap"
    return "other"


def graph_specs(config_path: str | Path = DEFAULT_GRAPHS) -> tuple[GraphSpec, ...]:
    return load_config(config_path).graphs


def run_idempotency(specs: Iterable[GraphSpec], grid: IdempotencyGrid) -> list[dict[str, Any]]:
    workers = tuple(Worker(f"W{i}") for i in range(grid.k))
    rows = []
    for spec in specs:
        for graph_seed in grid.graph_seeds:
            graph = spec.build(graph_seed)
            seed = instance_seed(spec, graph_seed, 0)
            for q in grid.duplicate_probs:
                for idempotent in grid.idempotent:
                    config = SimulationConfig(duplicate_delivery_prob=q,
                                              duplicate_delay=grid.duplicate_delay,
                                              idempotent_collection=idempotent)
                    for name in grid.schedulers:
                        result = simulate(graph, workers, get_scheduler(name), seed=seed,
                                          config=config)
                        try:
                            validate_schedule(graph, workers, result)
                            violation = ""
                        except ScheduleViolation as exc:  # recorded, not raised (see module doc)
                            violation = classify_violation(exc)
                        rows.append({
                            "graph_family": spec.family, "graph_params": spec.params_json(),
                            "graph_seed": graph_seed, "run_seed": seed, "duplicate_prob": q,
                            "idempotent": idempotent, "scheduler": name,
                            "valid": violation == "", "violation": violation,
                            "makespan": result.makespan,
                            "duplicates_received": result.duplicates_received,
                            "duplicates_ignored": result.duplicates_ignored,
                        })
    return rows


def summarize_idempotency(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[float, bool, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["duplicate_prob"], row["idempotent"], row["scheduler"])].append(row)
    summary = []
    # Idempotent arm first for each q, then scheduler name.
    for key in sorted(groups, key=lambda k: (k[0], not k[1], k[2])):
        members = groups[key]
        invalid = [0.0 if row["valid"] else 1.0 for row in members]
        low, high = bootstrap_ci(invalid)
        kinds = Counter(row["violation"] for row in members)
        summary.append({
            "duplicate_prob": key[0], "idempotent": key[1], "scheduler": key[2],
            "n_runs": len(members), "n_invalid": int(sum(invalid)),
            "invalid_fraction": sum(invalid) / len(members), "ci_low": low, "ci_high": high,
            "n_precedence": kinds["precedence"], "n_overlap": kinds["overlap"],
            "n_other": kinds["other"],
            "mean_duplicates_received":
                sum(row["duplicates_received"] for row in members) / len(members),
        })
    return summary


def check_idempotency(summary: Iterable[dict[str, Any]]) -> list[str]:
    """Problems that indicate a bug: any invalid run with the idempotent collector."""
    return [f"idempotent collection produced {row['n_invalid']} invalid runs for "
            f"{row['scheduler']} at q = {row['duplicate_prob']}"
            for row in summary if row["idempotent"] and row["n_invalid"] > 0]


def write_csv(rows: Iterable[dict[str, Any]], columns: Sequence[str], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_metadata(out: Path, name: str, parameters: dict[str, Any], n_runs: int,
                   started: float) -> None:
    metadata = {
        "ablation": name, "parameters": parameters, "git": _git_info(),
        "python_version": platform.python_version(), "platform": platform.platform(),
        "dagsched_version": dagsched.__version__, "n_runs": n_runs,
        "wall_time_s": time.perf_counter() - started,
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")


def idempotency_ablation(out: str | Path, graphs: str | Path = DEFAULT_GRAPHS,
                         n_graph_seeds: int = 30) -> tuple[int, list[str]]:
    """Run, write runs.csv / summary.csv / metadata.json; return (runs, bug reports)."""
    started = time.perf_counter()
    grid = IdempotencyGrid(graph_seeds=tuple(range(n_graph_seeds)))
    specs = graph_specs(graphs)
    rows = run_idempotency(specs, grid)
    summary = summarize_idempotency(rows)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, IDEMPOTENCY_RUN_COLUMNS, out / "runs.csv")
    write_csv(summary, IDEMPOTENCY_SUMMARY_COLUMNS, out / "summary.csv")
    parameters = {"graphs_from": Path(graphs).as_posix(), **_grid_dict(grid)}
    write_metadata(out, "idempotency", parameters, len(rows), started)
    return len(rows), check_idempotency(summary)


# ----- RQ5: common random numbers ------------------------------------------------------------

CRN_RUN_COLUMNS = (
    "graph_family", "graph_params", "graph_seed", "sim_seed", "run_seed", "failure_p", "crn",
    "scheduler", "status", "makespan", "lower_bound", "normalized_makespan", "n_attempts",
)
_ARM_FIELDS = ("n_pairs", "n_dropped", "mean_d", "ci_low", "ci_high", "std_d", "ci_width")
CRN_SUMMARY_COLUMNS = (
    "graph_family", "graph_params", "failure_p",
    *[f"{field}_{arm}" for arm in ("crn", "indep") for field in _ARM_FIELDS],
    "n_ratio_pairs", "var_ratio", "var_ratio_ci_low", "var_ratio_ci_high",
)


@dataclass(frozen=True)
class CrnGrid:
    graph_seeds: tuple[int, ...] = tuple(range(30))
    sim_seeds: tuple[int, ...] = tuple(range(10))
    failure_ps: tuple[float, ...] = (0.1, 0.2)
    crn: tuple[bool, ...] = (True, False)
    schedulers: tuple[str, ...] = ("fifo", "cp")
    k: int = 4
    max_attempts: int = 10
    backoff_base: float = 1.0
    backoff_factor: float = 2.0


def run_crn(specs: Iterable[GraphSpec], grid: CrnGrid) -> list[dict[str, Any]]:
    """Normal (aborting) validation here: an invalid schedule would be a bug."""
    workers = tuple(Worker(f"W{i}") for i in range(grid.k))
    retry = RetryPolicy(grid.max_attempts, grid.backoff_base, grid.backoff_factor)
    rows = []
    for spec in specs:
        for graph_seed in grid.graph_seeds:
            graph = spec.build(graph_seed)
            bound = lower_bound(graph, workers)
            for p, crn, sim_seed in itertools.product(grid.failure_ps, grid.crn, grid.sim_seeds):
                config = SimulationConfig(failure_model=BernoulliFailures(p), retry=retry,
                                          common_random_numbers=crn)
                seed = instance_seed(spec, graph_seed, sim_seed)  # same for both crn arms
                for name in grid.schedulers:
                    result = simulate(graph, workers, get_scheduler(name), seed=seed,
                                      config=config)
                    try:
                        validate_schedule(graph, workers, result, max_attempts=grid.max_attempts)
                    except ScheduleViolation as exc:
                        raise AblationError(f"invalid schedule in crn ablation: {exc}") from exc
                    rows.append({
                        "graph_family": spec.family, "graph_params": spec.params_json(),
                        "graph_seed": graph_seed, "sim_seed": sim_seed, "run_seed": seed,
                        "failure_p": p, "crn": crn, "scheduler": name, "status": result.status,
                        "makespan": result.makespan, "lower_bound": bound,
                        "normalized_makespan": result.makespan / bound,
                        "n_attempts": len(result.attempts),
                    })
    return rows


def variance_ratio(pairs: Sequence[tuple[float, float]]) -> float:
    """var(second) / var(first) over (d_with_crn, d_without_crn) pairs; inf if var(first) = 0."""
    with_crn = statistics.variance([pair[0] for pair in pairs])
    without = statistics.variance([pair[1] for pair in pairs])
    return without / with_crn if with_crn > 0 else math.inf


def _paired_d(runs: dict[tuple[int, int], dict[str, dict[str, Any]]], other: str,
              baseline: str) -> tuple[dict[tuple[int, int], float], int]:
    differences, dropped = {}, 0
    for key, by_scheduler in runs.items():
        a, b = by_scheduler[other], by_scheduler[baseline]
        if a["status"] == "completed" and b["status"] == "completed":
            differences[key] = a["normalized_makespan"] - b["normalized_makespan"]
        else:
            dropped += 1
    return differences, dropped


def _arm_stats(differences: list[float], dropped: int, arm: str) -> dict[str, Any]:
    stats: dict[str, Any] = {f"n_pairs_{arm}": len(differences), f"n_dropped_{arm}": dropped}
    if len(differences) < 2:
        return {**stats, **{f"{field}_{arm}": "" for field in _ARM_FIELDS[2:]}}
    mean, std = mean_std(differences)
    low, high = bootstrap_ci(differences)
    return {**stats, f"mean_d_{arm}": mean, f"ci_low_{arm}": low, f"ci_high_{arm}": high,
            f"std_d_{arm}": std, f"ci_width_{arm}": high - low}


def summarize_crn(rows: Iterable[dict[str, Any]], other: str = "cp",
                  baseline: str = "fifo") -> list[dict[str, Any]]:
    """Per (graph spec, p): d = normalized_makespan(other) - normalized_makespan(baseline) on
    every (graph_seed, sim_seed) where both runs completed, once with and once without common
    random numbers, plus var(d | without) / var(d | with) with a bootstrap CI over the pairs
    present in both arms (resampled jointly)."""
    index: dict[tuple[Any, ...], dict[tuple[int, int], dict[str, dict[str, Any]]]] = (
        defaultdict(lambda: defaultdict(dict)))
    for row in rows:
        group = (row["graph_family"], row["graph_params"], row["failure_p"], row["crn"])
        index[group][(row["graph_seed"], row["sim_seed"])][row["scheduler"]] = row
    summary = []
    for family, params, p in sorted({group[:3] for group in index}):
        arms = {crn: _paired_d(index[(family, params, p, crn)], other, baseline)
                for crn in (True, False)}
        row: dict[str, Any] = {"graph_family": family, "graph_params": params, "failure_p": p}
        for crn, arm in ((True, "crn"), (False, "indep")):
            differences, dropped = arms[crn]
            row.update(_arm_stats(list(differences.values()), dropped, arm))
        common = sorted(set(arms[True][0]) & set(arms[False][0]))
        pairs = [(arms[True][0][key], arms[False][0][key]) for key in common]
        row["n_ratio_pairs"] = len(pairs)
        if len(pairs) >= 2:
            low, high = bootstrap_ci(pairs, statistic=variance_ratio)
            row.update(var_ratio=variance_ratio(pairs), var_ratio_ci_low=low,
                       var_ratio_ci_high=high)
        else:
            row.update(var_ratio="", var_ratio_ci_low="", var_ratio_ci_high="")
        summary.append(row)
    return summary


def crn_ablation(out: str | Path, graphs: str | Path = DEFAULT_GRAPHS, n_graph_seeds: int = 30,
                 n_sim_seeds: int = 10) -> tuple[int, list[str]]:
    started = time.perf_counter()
    grid = CrnGrid(graph_seeds=tuple(range(n_graph_seeds)), sim_seeds=tuple(range(n_sim_seeds)))
    rows = run_crn(graph_specs(graphs), grid)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, CRN_RUN_COLUMNS, out / "runs.csv")
    write_csv(summarize_crn(rows), CRN_SUMMARY_COLUMNS, out / "summary.csv")
    parameters = {"graphs_from": Path(graphs).as_posix(), **_grid_dict(grid)}
    write_metadata(out, "crn", parameters, len(rows), started)
    return len(rows), []


def _grid_dict(grid: Any) -> dict[str, Any]:
    return {key: list(value) if isinstance(value, tuple) else value
            for key, value in grid.__dict__.items()}
