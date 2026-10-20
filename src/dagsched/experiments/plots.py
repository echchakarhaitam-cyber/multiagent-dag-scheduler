"""Figures for experiments E1-E3, drawn from a results directory's summary.csv only.

matplotlib is an optional dependency (pip install -e ".[experiments]") and is imported inside
the drawing functions, never at module import, so the core library stays standard-library only.
Every plotted value and error bar is a column of summary.csv: means with their 95% bootstrap
confidence intervals.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

DPI = 150
SCHEDULERS = ("fifo", "random", "cp", "heft")
FAMILIES = ("layered", "erdos_renyi", "fork_join")
DISTRIBUTIONS = ("uniform", "lognormal")
SPEEDS = ("homogeneous", "heterogeneous")
_RATIO = "normalized makespan\n(makespan / lower bound, ratio)"


class PlotError(ValueError):
    """The results directory cannot be plotted."""


def _number(text: str) -> float | None:
    return float(text) if text != "" else None


def load_summary(directory: Path) -> list[dict[str, Any]]:
    path = directory / "summary.csv"
    if not path.is_file():
        raise PlotError(f"summary file not found: {path} (run 'summarize' first)")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["k"] = int(row["k"])
        row["failure_p"] = float(row["failure_p"])
        row["dist"] = json.loads(row["graph_params"])["durations"]["dist"]
        for column in ("mean_normalized_makespan", "ci_low", "ci_high", "mean_wasted_work",
                       "wasted_ci_low", "wasted_ci_high"):
            row[column] = _number(row[column])
    return rows


def _pyplot() -> Any:
    try:
        import matplotlib
    except ImportError:
        raise PlotError('matplotlib is required: pip install -e ".[experiments]"') from None
    matplotlib.use("Agg")  # files only, no display
    import matplotlib.pyplot as plt

    return plt


def _select(rows: list[dict[str, Any]], **where: Any) -> list[dict[str, Any]]:
    return [row for row in rows if all(row[key] == value for key, value in where.items())]


def _series(rows: list[dict[str, Any]], x: str, mean: str, low: str, high: str):
    """(xs, means, [lower errors, upper errors]) sorted by x; rows without a mean are skipped."""
    points = sorted((row[x], row[mean], row[low], row[high]) for row in rows
                    if row[mean] is not None)
    xs = [p[0] for p in points]
    means = [p[1] for p in points]
    errors = [[p[1] - p[2] for p in points], [p[3] - p[1] for p in points]]
    return xs, means, errors


def _schedulers(rows: list[dict[str, Any]]) -> list[str]:
    present = {row["scheduler"] for row in rows}
    return [s for s in SCHEDULERS if s in present] + sorted(present - set(SCHEDULERS))


def _finish(fig: Any, axes: Any, path: Path, title: str) -> Path:
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), title="scheduler",
               bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(title, y=1.06)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    return path


def plot_e1(rows: list[dict[str, Any]], out: Path) -> list[Path]:
    """Mean normalized makespan vs k; one panel per (duration distribution, graph family)."""
    plt = _pyplot()
    fig, axes = plt.subplots(len(DISTRIBUTIONS), len(FAMILIES), figsize=(13, 7.5), squeeze=False)
    schedulers = _schedulers(rows)
    ks = sorted({row["k"] for row in rows})
    for r, dist in enumerate(DISTRIBUTIONS):
        for c, family in enumerate(FAMILIES):
            ax = axes[r][c]
            for i, name in enumerate(schedulers):
                sel = _select(rows, graph_family=family, dist=dist, scheduler=name)
                xs, means, errors = _series(sel, "k", "mean_normalized_makespan", "ci_low",
                                            "ci_high")
                dodge = 2 ** ((i - (len(schedulers) - 1) / 2) * 0.05)  # separate error bars
                ax.errorbar([x * dodge for x in xs], means, yerr=errors, marker="o",
                            capsize=3, label=name)
            ax.set_xscale("log", base=2)
            ax.set_xticks(ks, [str(k) for k in ks])
            ax.minorticks_off()
            ax.set_title(f"{family}, {dist} durations")
            ax.set_xlabel("number of workers k (workers)")
            ax.set_ylabel(_RATIO)
            ax.grid(alpha=0.3)
    path = _finish(fig, axes, out / "fig_e1_vs_k.png",
                   "E1, homogeneous workers, p = 0: mean with 95% bootstrap CI")
    plt.close(fig)
    return [path]


def plot_e2(rows: list[dict[str, Any]], out: Path) -> list[Path]:
    """Grouped bars per k and scheduler; bars start at the lower bound 1.0."""
    plt = _pyplot()
    fig, axes = plt.subplots(len(DISTRIBUTIONS), len(FAMILIES), figsize=(13, 7.5), squeeze=False)
    schedulers = _schedulers(rows)
    ks = sorted({row["k"] for row in rows})
    width = 0.8 / len(schedulers)
    for r, dist in enumerate(DISTRIBUTIONS):
        for c, family in enumerate(FAMILIES):
            ax = axes[r][c]
            for i, name in enumerate(schedulers):
                sel = _select(rows, graph_family=family, dist=dist, scheduler=name)
                xs, means, errors = _series(sel, "k", "mean_normalized_makespan", "ci_low",
                                            "ci_high")
                positions = [ks.index(x) + (i - (len(schedulers) - 1) / 2) * width for x in xs]
                ax.bar(positions, [m - 1.0 for m in means], width, bottom=1.0, yerr=errors,
                       capsize=3, label=name)
            ax.set_xticks(range(len(ks)), [f"k = {k}" for k in ks])
            ax.set_title(f"{family}, {dist} durations")
            ax.set_xlabel("number of workers (speeds ~ U[0.5, 2.0])")
            ax.set_ylabel(_RATIO + "\nbars start at 1.0")
            ax.grid(alpha=0.3, axis="y")
    path = _finish(fig, axes, out / "fig_e2_hetero.png",
                   "E2, heterogeneous workers, p = 0: mean with 95% bootstrap CI")
    plt.close(fig)
    return [path]


def _plot_vs_p(rows: list[dict[str, Any]], path: Path, mean: str, low: str, high: str,
               ylabel: str, title: str) -> Path:
    plt = _pyplot()
    specs = [(family, dist) for family in FAMILIES for dist in DISTRIBUTIONS]
    fig, axes = plt.subplots(len(SPEEDS), len(specs), figsize=(22, 7.5), squeeze=False)
    schedulers = _schedulers(rows)
    for r, speeds in enumerate(SPEEDS):
        for c, (family, dist) in enumerate(specs):
            ax = axes[r][c]
            for i, name in enumerate(schedulers):
                sel = _select(rows, graph_family=family, dist=dist, speeds_kind=speeds,
                              scheduler=name)
                xs, means, errors = _series(sel, "failure_p", mean, low, high)
                dodge = (i - (len(schedulers) - 1) / 2) * 0.004
                ax.errorbar([x + dodge for x in xs], means, yerr=errors, marker="o",
                            capsize=3, label=name)
            ax.set_title(f"{speeds} workers\n{family}, {dist}", fontsize=10)
            ax.set_xlabel("failure probability p (per attempt)")
            if c == 0:
                ax.set_ylabel(ylabel)
            ax.grid(alpha=0.3)
    result = _finish(fig, axes, path, title)
    plt.close(fig)
    return result


def plot_e3(rows: list[dict[str, Any]], out: Path) -> list[Path]:
    """Normalized makespan and wasted work vs failure probability, per speeds kind and graph."""
    return [
        _plot_vs_p(rows, out / "fig_e3_makespan_vs_p.png", "mean_normalized_makespan",
                   "ci_low", "ci_high", _RATIO,
                   "E3, k = 4: completed runs, mean with 95% bootstrap CI"),
        _plot_vs_p(rows, out / "fig_e3_wasted_vs_p.png", "mean_wasted_work", "wasted_ci_low",
                   "wasted_ci_high", "wasted work\n(time in failed attempts, time units)",
                   "E3, k = 4: all runs, mean with 95% bootstrap CI"),
    ]


PLOTTERS: dict[str, Callable[[list[dict[str, Any]], Path], list[Path]]] = {
    "e1_homogeneous": plot_e1,
    "e2_heterogeneous": plot_e2,
    "e3_failures": plot_e3,
}


def plot_directory(directory: str | Path, out: str | Path) -> list[Path]:
    """Draw the figures of the experiment in `directory` (chosen by its metadata name)."""
    directory, out = Path(directory), Path(out)
    metadata = directory / "metadata.json"
    if not metadata.is_file():
        raise PlotError(f"metadata file not found: {metadata}")
    name = json.loads(metadata.read_text(encoding="utf-8"))["config"]["name"]
    if name not in PLOTTERS:
        raise PlotError(f"no figures defined for experiment {name!r}; known: {sorted(PLOTTERS)}")
    rows = load_summary(directory)
    if not rows:
        raise PlotError(f"{directory / 'summary.csv'} has no rows")
    return PLOTTERS[name](rows, out)
