"""Tests for scripts/check_results.py and scripts/make_results_tables.py on hand-made data."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest
from helpers import make_run, write_runs

from dagsched.experiments import summarize_directory

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_results = load_script("check_results")
tables = load_script("make_results_tables")


@pytest.fixture
def scratch():
    with tempfile.TemporaryDirectory(prefix="dagsched-scripts-") as name:
        yield Path(name)


def healthy_results(root: Path, e1_overrides=None, e2_overrides=None) -> Path:
    """e1: k=2 and k=4 homogeneous; e2: heterogeneous; e3: one partial run at p = 0.3."""
    e1 = [make_run(k=k, graph_seed=s, scheduler=name, normalized_makespan=1.2)
          for k in (2, 4) for s in (0, 1) for name in ("fifo", "cp")]
    e1 += [make_run(**overrides) for overrides in (e1_overrides or [])]
    e2 = [make_run(speeds="[0.5, 2.0]", graph_seed=s, scheduler=name, normalized_makespan=1.1)
          for s in (0, 1) for name in ("fifo", "cp")]
    e2 += [make_run(**overrides) for overrides in (e2_overrides or [])]
    e3 = [make_run(failure_p=p, graph_seed=s, normalized_makespan=1.3) for p in (0.0, 0.3)
          for s in (0, 1)]
    e3.append(make_run(failure_p=0.3, graph_seed=2, status="partial", normalized_makespan=0.4))
    for name, rows in (("e1", e1), ("e2", e2), ("e3", e3)):
        write_runs(root / name, rows)
    return root


# ----- check_results -------------------------------------------------------------------------


def test_check_results_passes_on_healthy_data(scratch, capsys):
    assert check_results.main(["--results", str(healthy_results(scratch))]) == 0
    out = capsys.readouterr().out
    assert "ALL CHECKS PASSED" in out
    assert "e1: k= 2: max normalized_makespan 1.200000 <= 2 - 1/k = 1.500000" in out
    assert "e3: 1 partial runs not checked (1 of them below 1)" in out
    assert "e3: p=0.3 : 1 partial of 3 runs (homogeneous 1, heterogeneous 0)" in out


@pytest.mark.parametrize(
    ("e1_extra", "e2_extra", "message"),
    [
        ([{"normalized_makespan": 0.99, "graph_seed": 9}], [], "e1: normalized_makespan >= 1"),
        ([{"k": 2, "normalized_makespan": 1.6, "graph_seed": 9}], [], "e1: k= 2: max"),
        ([{"status": "partial", "graph_seed": 9, "normalized_makespan": 1.1}], [],
         "e1: completion rate"),
        ([], [{"status": "partial", "graph_seed": 9}], "e2: completion rate"),
        ([{"failure_p": 0.1, "graph_seed": 9}], [], "not homogeneous with p = 0"),
    ],
    ids=["below-lower-bound", "above-graham", "e1-partial", "e2-partial", "e1-off-grid"],
)
def test_check_results_detects_failures(scratch, capsys, e1_extra, e2_extra, message):
    results = healthy_results(scratch, e1_extra, e2_extra)
    assert check_results.main(["--results", str(results)]) == 1
    out = capsys.readouterr().out
    assert "SOME CHECKS FAILED" in out
    assert any(line.startswith("[FAIL]") and message in line for line in out.splitlines())


def test_check_results_missing_file(scratch, capsys):
    assert check_results.main(["--results", str(scratch)]) == 1
    assert "[FAIL] e1: runs file not found" in capsys.readouterr().out


# ----- make_results_tables -------------------------------------------------------------------


def e1_results(root: Path) -> Path:
    rows = [make_run(k=k, graph_seed=s, scheduler="fifo", normalized_makespan=1.5)
            for k in (2, 4) for s in range(5)]
    rows += [make_run(k=k, graph_seed=s, scheduler="cp", normalized_makespan=1.25)
             for k in (2, 4) for s in range(5)]
    write_runs(root / "e1", rows)
    summarize_directory(root / "e1")
    metadata = {"config": {"name": "e1_homogeneous"}, "n_runs": 20, "wall_time_s": 1.234,
                "git": {"commit": "abcdef0123", "dirty": False}, "python_version": "3.10.11"}
    (root / "e1" / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return root


DOC = """# Results

<!-- BEGIN GENERATED: setup_e1 -->
stale
<!-- END GENERATED: setup_e1 -->

prose stays

<!-- BEGIN GENERATED: e1_makespan -->
<!-- END GENERATED: e1_makespan -->

<!-- BEGIN GENERATED: e1_paired -->
<!-- END GENERATED: e1_paired -->
"""


def test_render_fills_blocks_from_csv(scratch):
    results = e1_results(scratch)
    rendered = tables.render(DOC, results)
    assert "stale" not in rendered and "prose stays" in rendered
    assert "configs/e1_homogeneous.json`; 20 runs; wall time 1.2 s; commit `abcdef0`" in rendered
    assert "| graph | durations | k | fifo | cp |" in rendered
    # All 5 cp runs beat fifo by exactly 0.25: CI collapses, win rate 1, sign test 2 / 32.
    assert "| layered | uniform | 2 | 1.500 [1.500, 1.500] | 1.250 [1.250, 1.250] |" in rendered
    assert "-0.250 [-0.250, -0.250]; win 1.00; p_H 0.125" in rendered
    assert "**sig.**" not in rendered  # p_holm 0.125 >= 0.05
    assert tables.render(rendered, results) == rendered  # idempotent


def test_significance_rule():
    row = {"p_holm": "0.01", "ci_low": "-0.2", "ci_high": "-0.1"}
    assert tables.is_significant(row)
    assert not tables.is_significant({**row, "ci_high": "0.05"})  # CI overlaps 0
    assert not tables.is_significant({**row, "p_holm": "0.05"})
    assert not tables.is_significant({**row, "p_holm": ""})


def test_main_check_mode_and_errors(scratch, capsys):
    results = e1_results(scratch)
    doc = scratch / "results.md"
    doc.write_text(DOC, encoding="utf-8")
    args = ["--doc", str(doc), "--results", str(results)]
    assert tables.main([*args, "--check"]) == 1
    assert tables.main(args) == 0
    assert tables.main([*args, "--check"]) == 0
    assert "up to date" in capsys.readouterr().out
    doc.write_text("<!-- BEGIN GENERATED: nope -->\n<!-- END GENERATED: nope -->\n",
                   encoding="utf-8")
    assert tables.main(args) == 1
    assert "unknown generated block(s) ['nope']" in capsys.readouterr().out
    doc.write_text("<!-- BEGIN GENERATED: setup_e2 -->\n<!-- END GENERATED: setup_e2 -->\n",
                   encoding="utf-8")
    assert tables.main(args) == 1
    assert "missing" in capsys.readouterr().out
