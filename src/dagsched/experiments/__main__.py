"""Command line: python -m dagsched.experiments run CONFIG --out DIR"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dagsched.experiments.config import ConfigError, load_config
from dagsched.experiments.runner import ExperimentError, run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dagsched.experiments",
        description="Run dagsched scheduling experiments from a JSON config.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run every simulation of a config")
    run.add_argument("config", type=Path, help="path to the JSON config")
    run.add_argument("--out", type=Path, required=True, help="output directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code (0 on success, 1 on a reported error)."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        rows = run_experiment(config, args.out)
        print(f"{config.name}: {len(rows)} runs written to {args.out / 'runs.csv'} "
              f"(+ metadata.json)")
    except (ConfigError, ExperimentError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
