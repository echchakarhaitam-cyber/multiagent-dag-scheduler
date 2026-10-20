"""Summaries of runs.csv: per-group statistics and paired comparisons against a baseline.

Groups are (graph_family, graph_params, k, speeds_kind, failure_p, scheduler); speeds_kind is
"homogeneous" when all worker speeds of a run are equal and "heterogeneous" otherwise.

normalized_makespan statistics use COMPLETED runs only: a partial run (some task failed for
good) stops early, so its makespan is not comparable with the lower bound of the whole graph.
The completion rate is reported separately. Likewise, a paired difference is formed only when
both the scheduler's and the baseline's run on an identical instance completed.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dagsched.stats import bootstrap_ci, holm_bonferroni, mean_std, sign_test, win_rate

SUMMARY_COLUMNS = (
    "graph_family", "graph_params", "k", "speeds_kind", "failure_p", "scheduler", "count",
    "n_completed", "completion_rate", "mean_normalized_makespan", "std_normalized_makespan",
    "ci_low", "ci_high", "mean_utilization", "mean_wasted_work",
)
PAIRED_COLUMNS = (
    "graph_family", "graph_params", "k", "speeds_kind", "failure_p", "scheduler", "baseline",
    "n_pairs", "mean_difference", "ci_low", "ci_high", "win_rate", "sign_test_p", "p_holm",
)
_REQUIRED = (
    "graph_family", "graph_params", "graph_seed", "k", "speeds", "failure_p", "max_attempts",
    "duplicate_prob", "sim_seed", "scheduler", "status", "normalized_makespan", "utilization",
    "wasted_work",
)


class SummaryError(ValueError):
    """runs.csv is missing, malformed, or cannot be paired unambiguously."""


@dataclass(frozen=True)
class Run:
    graph_family: str
    graph_params: str
    graph_seed: int
    k: int
    speeds: str
    failure_p: float
    max_attempts: int
    duplicate_prob: float
    sim_seed: int
    scheduler: str
    completed: bool
    normalized_makespan: float
    utilization: float
    wasted_work: float

    @property
    def speeds_kind(self) -> str:
        return "homogeneous" if len(set(json.loads(self.speeds))) == 1 else "heterogeneous"

    @property
    def group(self) -> tuple[str, str, int, str, float]:
        return (self.graph_family, self.graph_params, self.k, self.speeds_kind, self.failure_p)

    @property
    def instance(self) -> tuple[Any, ...]:
        """Everything that must be identical for two runs to form a pair."""
        return (self.graph_family, self.graph_params, self.graph_seed, self.k, self.speeds,
                self.failure_p, self.max_attempts, self.duplicate_prob, self.sim_seed)


def read_runs(path: str | Path) -> list[Run]:
    path = Path(path)
    if not path.is_file():
        raise SummaryError(f"runs file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in _REQUIRED if column not in (reader.fieldnames or ())]
        if missing:
            raise SummaryError(f"{path}: missing columns {missing}")
        try:
            return [_parse_run(row) for row in reader]
        except (KeyError, ValueError) as exc:
            raise SummaryError(f"{path}: malformed row (line {reader.line_num}): {exc}") from None


def _parse_run(row: dict[str, str]) -> Run:
    return Run(
        graph_family=row["graph_family"],
        graph_params=row["graph_params"],
        graph_seed=int(row["graph_seed"]),
        k=int(row["k"]),
        speeds=row["speeds"],
        failure_p=float(row["failure_p"]),
        max_attempts=int(row["max_attempts"]),
        duplicate_prob=float(row["duplicate_prob"]),
        sim_seed=int(row["sim_seed"]),
        scheduler=row["scheduler"],
        completed=row["status"] == "completed",
        normalized_makespan=float(row["normalized_makespan"]),
        utilization=float(row["utilization"]),
        wasted_work=float(row["wasted_work"]),
    )


def _interval_stats(values: list[float]) -> dict[str, Any]:
    """mean, std and bootstrap CI, or blanks when there is no value."""
    if not values:
        return {"mean": "", "std": "", "ci_low": "", "ci_high": ""}
    mean, std = mean_std(values)
    low, high = bootstrap_ci(values)
    return {"mean": mean, "std": std, "ci_low": low, "ci_high": high}


def summarize_runs(runs: Iterable[Run]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Run]] = defaultdict(list)
    for run in runs:
        groups[(*run.group, run.scheduler)].append(run)
    rows = []
    for key in sorted(groups):
        members = groups[key]
        done = [run for run in members if run.completed]
        stats = _interval_stats([run.normalized_makespan for run in done])
        rows.append({
            **dict(zip(SUMMARY_COLUMNS[:6], key)),
            "count": len(members),
            "n_completed": len(done),
            "completion_rate": len(done) / len(members),
            "mean_normalized_makespan": stats["mean"],
            "std_normalized_makespan": stats["std"],
            "ci_low": stats["ci_low"],
            "ci_high": stats["ci_high"],
            "mean_utilization": mean_std([run.utilization for run in members])[0],
            "mean_wasted_work": mean_std([run.wasted_work for run in members])[0],
        })
    return rows


def _index_by_instance(runs: Iterable[Run]) -> dict[tuple[Any, ...], dict[str, Run]]:
    index: dict[tuple[Any, ...], dict[str, Run]] = defaultdict(dict)
    for run in runs:
        by_scheduler = index[run.instance]
        if run.scheduler in by_scheduler:
            raise SummaryError(
                f"two runs of scheduler {run.scheduler!r} on the same instance {run.instance}; "
                f"pairing would be ambiguous"
            )
        by_scheduler[run.scheduler] = run
    return index


def paired_comparisons(runs: Iterable[Run], baseline: str = "fifo") -> list[dict[str, Any]]:
    """Per group, compare each scheduler with `baseline` on identical instances:
    difference = normalized_makespan(scheduler) - normalized_makespan(baseline)."""
    index = _index_by_instance(runs)
    schedulers = {name for by_scheduler in index.values() for name in by_scheduler}
    if baseline not in schedulers:
        raise SummaryError(f"baseline {baseline!r} not in runs (schedulers: {sorted(schedulers)})")
    differences: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for by_scheduler in index.values():
        base = by_scheduler.get(baseline)
        for name, run in by_scheduler.items():
            if name == baseline:
                continue
            # Register the group even without a baseline partner, so it is reported with
            # n_pairs = 0 instead of silently disappearing.
            key = (*run.group, name)
            differences.setdefault(key, [])
            if base is not None and run.completed and base.completed:
                differences[key].append(run.normalized_makespan - base.normalized_makespan)
    rows = []
    for key in sorted(differences):
        values = differences[key]
        stats = _interval_stats(values)
        rows.append({
            **dict(zip(PAIRED_COLUMNS[:6], key)),
            "baseline": baseline,
            "n_pairs": len(values),
            "mean_difference": stats["mean"],
            "ci_low": stats["ci_low"],
            "ci_high": stats["ci_high"],
            "win_rate": win_rate(values) if values else "",
            "sign_test_p": sign_test(values),
            "p_holm": "",
        })
    _add_holm(rows)
    return rows


def _add_holm(rows: list[dict[str, Any]]) -> None:
    """Holm-adjust the sign-test p-values over all comparisons passed in together (one
    experiment's runs.csv). Rows without pairs are not tests and are left blank."""
    tested = [row for row in rows if row["n_pairs"] > 0]
    for row, adjusted in zip(tested, holm_bonferroni([row["sign_test_p"] for row in tested])):
        row["p_holm"] = adjusted


def _write(rows: list[dict[str, Any]], columns: tuple[str, ...], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def summarize_directory(directory: str | Path, baseline: str = "fifo") -> tuple[int, int]:
    """Read <directory>/runs.csv, write summary.csv and paired.csv; return their row counts."""
    directory = Path(directory)
    runs = read_runs(directory / "runs.csv")
    if not runs:
        raise SummaryError(f"{directory / 'runs.csv'} contains no runs")
    summary = summarize_runs(runs)
    paired = paired_comparisons(runs, baseline)
    _write(summary, SUMMARY_COLUMNS, directory / "summary.csv")
    _write(paired, PAIRED_COLUMNS, directory / "paired.csv")
    return len(summary), len(paired)
