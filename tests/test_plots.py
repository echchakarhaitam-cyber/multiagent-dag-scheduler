"""Tests for the plotting command (skipped when matplotlib is not installed)."""

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from dagsched.experiments import SUMMARY_COLUMNS
from dagsched.experiments.__main__ import main
from dagsched.experiments.plots import PlotError, plot_directory

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def scratch():
    with tempfile.TemporaryDirectory(prefix="dagsched-plot-") as name:
        yield Path(name)


def fake_results(directory: Path, name: str, ks, ps, speeds_kinds) -> Path:
    """A results directory with metadata.json and a synthetic summary.csv covering the grid."""
    rows = []
    for family in ("layered", "erdos_renyi", "fork_join"):
        for dist in ("uniform", "lognormal"):
            params = json.dumps({"durations": {"dist": dist}})
            for k in ks:
                for p in ps:
                    for speeds in speeds_kinds:
                        for i, scheduler in enumerate(("fifo", "random", "cp", "heft")):
                            mean = 1.1 + 0.05 * i + p
                            row = {column: "" for column in SUMMARY_COLUMNS}
                            row.update(graph_family=family, graph_params=params, k=k,
                                       speeds_kind=speeds, failure_p=p, scheduler=scheduler,
                                       mean_normalized_makespan=mean, ci_low=mean - 0.02,
                                       ci_high=mean + 0.02, mean_wasted_work=10 * p,
                                       wasted_ci_low=9 * p, wasted_ci_high=11 * p)
                            rows.append(row)
    rows[0]["mean_normalized_makespan"] = ""  # a group without completed runs is skipped
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (directory / "metadata.json").write_text(json.dumps({"config": {"name": name}}),
                                             encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    ("name", "ks", "ps", "speeds", "expected"),
    [
        ("e1_homogeneous", [2, 4, 8, 16], [0.0], ["homogeneous"], ["fig_e1_vs_k.png"]),
        ("e2_heterogeneous", [4, 8], [0.0], ["heterogeneous"], ["fig_e2_hetero.png"]),
        ("e3_failures", [4], [0.0, 0.1, 0.3], ["homogeneous", "heterogeneous"],
         ["fig_e3_makespan_vs_p.png", "fig_e3_wasted_vs_p.png"]),
    ],
)
def test_plot_writes_expected_pngs(scratch, name, ks, ps, speeds, expected):
    pytest.importorskip("matplotlib")
    results = fake_results(scratch / "results", name, ks, ps, speeds)
    paths = plot_directory(results, scratch / "figures")
    assert [path.name for path in paths] == expected
    for path in paths:
        assert path.read_bytes()[:8] == PNG_SIGNATURE


def test_plot_unknown_experiment(scratch):
    results = fake_results(scratch / "r", "smoke", [2], [0.0], ["homogeneous"])
    with pytest.raises(PlotError, match="no figures defined for experiment 'smoke'"):
        plot_directory(results, scratch / "f")


def test_plot_missing_files(scratch):
    with pytest.raises(PlotError, match="metadata file not found"):
        plot_directory(scratch, scratch / "f")
    (scratch / "metadata.json").write_text(json.dumps({"config": {"name": "e1_homogeneous"}}),
                                           encoding="utf-8")
    with pytest.raises(PlotError, match="summary file not found"):
        plot_directory(scratch, scratch / "f")


def test_cli_plot_exit_codes(scratch, capsys):
    pytest.importorskip("matplotlib")
    results = fake_results(scratch / "r", "e2_heterogeneous", [4, 8], [0.0], ["heterogeneous"])
    assert main(["plot", str(results), "--out", str(scratch / "figs")]) == 0
    assert "fig_e2_hetero.png" in capsys.readouterr().out
    assert main(["plot", str(scratch / "missing"), "--out", str(scratch / "figs")]) == 1
    assert "metadata file not found" in capsys.readouterr().err


def test_cli_import_does_not_load_matplotlib():
    code = ("import sys, dagsched, dagsched.experiments.__main__; "
            "print('matplotlib' in sys.modules)")
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=60, check=True).stdout.strip()
    assert output == "False"
