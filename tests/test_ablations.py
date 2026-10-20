"""Tests for the ablation studies (tiny grids only; the real ablations are not run here)."""

import csv
import itertools
import tempfile
from pathlib import Path

import pytest

from dagsched.experiments.__main__ import main
from dagsched.experiments.ablations import (
    IdempotencyGrid,
    check_idempotency,
    classify_violation,
    graph_specs,
    run_idempotency,
    summarize_idempotency,
)
from dagsched.validation import ScheduleViolation

E1_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "e1_homogeneous.json"


@pytest.fixture(scope="module")
def workdir():
    with tempfile.TemporaryDirectory(prefix="dagsched-ablation-") as name:
        yield Path(name)


_ids = itertools.count()


@pytest.fixture
def scratch(workdir) -> Path:
    path = workdir / f"t{next(_ids)}"
    path.mkdir()
    return path


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("precedence violated: task 'R' starts at 1 before ...", "precedence"),
        ("overlap on worker 'W0': 'A' [0, 2] and 'B' [1, 3]", "overlap"),
        ("makespan 11 != latest end time 10", "other"),
        ("", "other"),
    ],
)
def test_classify_violation(message, kind):
    assert classify_violation(ScheduleViolation(message)) == kind


@pytest.fixture(scope="module")
def idempotency_rows():
    grid = IdempotencyGrid(graph_seeds=(0, 1))
    return run_idempotency(graph_specs(E1_CONFIG)[:2], grid)


def test_idempotency_grid_size_and_pairing(idempotency_rows):
    # 2 graph specs x 2 seeds x 4 q x 2 collectors x 2 schedulers.
    assert len(idempotency_rows) == 2 * 2 * 4 * 2 * 2
    seeds = {(r["graph_family"], r["graph_params"], r["graph_seed"]): set()
             for r in idempotency_rows}
    for r in idempotency_rows:
        seeds[(r["graph_family"], r["graph_params"], r["graph_seed"])].add(r["run_seed"])
    assert all(len(s) == 1 for s in seeds.values())  # one seed shared by every arm


def test_idempotent_collection_always_valid(idempotency_rows):
    assert all(r["valid"] for r in idempotency_rows if r["idempotent"])
    assert all(r["valid"] for r in idempotency_rows if r["duplicate_prob"] == 0.0)
    assert all(r["duplicates_received"] == r["duplicates_ignored"]
               for r in idempotency_rows if r["idempotent"])


def test_naive_collection_produces_precedence_violations(idempotency_rows):
    naive = [r for r in idempotency_rows if not r["idempotent"] and r["duplicate_prob"] == 0.2]
    assert any(not r["valid"] for r in naive)
    assert {r["violation"] for r in naive if not r["valid"]} <= {"precedence", "overlap", "other"}


def test_summarize_idempotency(idempotency_rows):
    summary = summarize_idempotency(idempotency_rows)
    assert len(summary) == 4 * 2 * 2
    assert [(s["duplicate_prob"], s["idempotent"]) for s in summary[:4]] == [
        (0.0, True), (0.0, True), (0.0, False), (0.0, False)]
    for row in summary:
        assert row["n_runs"] == 4
        assert row["ci_low"] <= row["invalid_fraction"] <= row["ci_high"]
        assert row["n_invalid"] == row["n_precedence"] + row["n_overlap"] + row["n_other"]
    assert check_idempotency(summary) == []


def test_check_idempotency_reports_bug():
    summary = [{"idempotent": True, "n_invalid": 2, "scheduler": "cp", "duplicate_prob": 0.1},
               {"idempotent": False, "n_invalid": 9, "scheduler": "cp", "duplicate_prob": 0.1}]
    assert check_idempotency(summary) == [
        "idempotent collection produced 2 invalid runs for cp at q = 0.1"]


def test_cli_idempotency_smoke(scratch, capsys):
    out = scratch / "a1"
    args = ["ablation", "idempotency", "--out", str(out), "--graphs", str(E1_CONFIG),
            "--graph-seeds", "2"]
    assert main(args) == 0
    assert "idempotency: 192 runs" in capsys.readouterr().out
    summary = read_csv(out / "summary.csv")
    assert len(summary) == 16
    assert all(row["n_invalid"] == "0" for row in summary if row["idempotent"] == "True")
    assert (out / "runs.csv").is_file() and (out / "metadata.json").is_file()


def test_cli_ablation_errors(scratch, capsys):
    assert main(["ablation", "idempotency", "--out", str(scratch),
                 "--graphs", str(scratch / "missing.json")]) == 1
    assert "config file not found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["ablation", "nonsense", "--out", str(scratch)])
