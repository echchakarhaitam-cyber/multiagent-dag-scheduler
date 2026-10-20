"""Builders for hand-made runs.csv files, shared by the experiment and script tests.

Importable as `helpers` because pytest puts the tests directory on sys.path (rootdir-relative
"prepend" import mode, no tests/__init__.py).
"""

import csv
from pathlib import Path

from dagsched.experiments import RUN_COLUMNS


def make_run(**overrides) -> dict:
    """A runs.csv row with neutral defaults; only the fields under test vary."""
    row = {column: "0" for column in RUN_COLUMNS}
    row.update({
        "experiment": "hand", "graph_family": "layered",
        "graph_params": '{"durations":{"dist":"uniform"}}', "graph_seed": "0",
        "k": "2", "speeds": "[1.0, 1.0]", "failure_p": "0.0", "max_attempts": "3",
        "duplicate_prob": "0.0", "sim_seed": "0", "scheduler": "fifo", "status": "completed",
        "normalized_makespan": "1.0", "utilization": "0.5", "wasted_work": "0.0",
    })
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def write_runs(directory: Path, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "runs.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path
