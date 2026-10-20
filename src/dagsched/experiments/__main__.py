"""Command line:

    python -m dagsched.experiments run CONFIG --out DIR
    python -m dagsched.experiments summarize DIR [--baseline NAME]
    python -m dagsched.experiments plot DIR --out FIGURE_DIR
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dagsched.experiments.config import ConfigError, load_config
from dagsched.experiments.runner import ExperimentError, run_experiment
from dagsched.experiments.summarize import SummaryError, summarize_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dagsched.experiments",
        description="Run dagsched scheduling experiments from a JSON config and summarize them.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run every simulation of a config")
    run.add_argument("config", type=Path, help="path to the JSON config")
    run.add_argument("--out", type=Path, required=True, help="output directory")
    summarize = commands.add_parser("summarize", help="write summary.csv and paired.csv")
    summarize.add_argument("directory", type=Path, help="directory containing runs.csv")
    summarize.add_argument("--baseline", default="fifo", help="baseline scheduler (default fifo)")
    summarize.add_argument("--paired-out", default="paired.csv",
                           help="file name for the paired comparisons (default paired.csv)")
    ablation = commands.add_parser("ablation", help="run an ablation study (RQ4/RQ5)")
    ablation.add_argument("study", choices=["idempotency", "crn"], help="which ablation")
    ablation.add_argument("--out", type=Path, required=True, help="output directory")
    ablation.add_argument("--graphs", type=Path, default=Path("configs/e1_homogeneous.json"),
                          help="config whose graph specs are used (default: E1)")
    ablation.add_argument("--graph-seeds", type=int, default=30,
                          help="number of graph seeds 0..N-1 (default 30)")
    ablation.add_argument("--sim-seeds", type=int, default=10,
                          help="number of simulation seeds 0..N-1, crn only (default 10)")
    plot =commands.add_parser("plot", help="draw figures from summary.csv (needs matplotlib)")
    plot.add_argument("directory", type=Path, help="directory containing summary.csv")
    plot.add_argument("--out", type=Path, required=True, help="output directory for PNG files")
    return parser


def _run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    rows = run_experiment(config, args.out)
    print(f"{config.name}: {len(rows)} runs written to {args.out / 'runs.csv'} (+ metadata.json)")


def _summarize(args: argparse.Namespace) -> None:
    n_summary, n_paired = summarize_directory(args.directory, args.baseline, args.paired_out)
    print(f"{n_summary} groups written to {args.directory / 'summary.csv'}, "
          f"{n_paired} comparisons vs {args.baseline!r} to {args.directory / args.paired_out}")


def _plot(args: argparse.Namespace) -> None:
    # plots.py itself imports matplotlib lazily, so this import stays cheap and stdlib-only.
    from dagsched.experiments.plots import plot_directory

    for path in plot_directory(args.directory, args.out):
        print(f"wrote {path}")


def _ablation(args: argparse.Namespace) -> None:
    from dagsched.experiments.ablations import AblationError, crn_ablation, idempotency_ablation

    if args.graph_seeds < 1 or args.sim_seeds < 1:
        raise AblationError("--graph-seeds and --sim-seeds must be >= 1")
    if args.study == "idempotency":
        n_runs, problems = idempotency_ablation(args.out, args.graphs, args.graph_seeds)
    else:
        n_runs, problems = crn_ablation(args.out, args.graphs, args.graph_seeds, args.sim_seeds)
    print(f"{args.study}: {n_runs} runs written to {args.out / 'runs.csv'} "
          f"(+ summary.csv, metadata.json)")
    if problems:
        raise AblationError("; ".join(problems) + " -- this indicates a bug")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code (0 on success, 1 on a reported error)."""
    from dagsched.experiments.ablations import AblationError
    from dagsched.experiments.plots import PlotError

    args = build_parser().parse_args(argv)
    commands = {"run": _run, "summarize": _summarize, "plot": _plot, "ablation": _ablation}
    try:
        commands[args.command](args)
    except (ConfigError, ExperimentError, SummaryError, PlotError, AblationError,
            OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
