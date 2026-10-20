"""Tests for the experiment config, runner, summaries and CLI."""

import copy
import csv
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from dagsched.experiments import (
    RUN_COLUMNS,
    ConfigError,
    ExperimentError,
    load_config,
    parse_config,
    run_experiment,
)
from dagsched.experiments import runner as runner_module
from dagsched.experiments.__main__ import main
from dagsched.validation import ScheduleViolation

SMOKE_PATH = Path(__file__).resolve().parents[1] / "configs" / "smoke.json"


@pytest.fixture
def smoke_dict() -> dict:
    return json.loads(SMOKE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workdir():
    """One temporary directory for the whole module. pytest's per-test tmp_path costs about a
    second of setup each on the development machine (Windows), which alone would blow the
    test-time budget; plain tempfile directories are effectively free."""
    with tempfile.TemporaryDirectory(prefix="dagsched-exp-") as name:
        yield Path(name)


_scratch_ids = itertools.count()


@pytest.fixture
def scratch(workdir) -> Path:
    """A fresh, empty directory per test (inside `workdir`)."""
    path = workdir / f"t{next(_scratch_ids)}"
    path.mkdir()
    return path


@pytest.fixture(scope="module")
def smoke_run(workdir):
    """The smoke config run once into a temporary directory (shared: it is read-only)."""
    out = workdir / "smoke"
    rows = run_experiment(load_config(SMOKE_PATH), out)
    return out, rows


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def tiny_config(**overrides) -> dict:
    config = {
        "name": "tiny",
        "graphs": [{"family": "fork_join", "width": 2, "stages": 1,
                    "durations": {"dist": "uniform", "low": 1, "high": 2}}],
        "graph_seeds": [0],
        "workers": [{"k": 2, "speeds": "homogeneous"}],
        "schedulers": ["fifo", "cp"],
        "failures": [{"p": 0.0}],
        "sim_seeds": [0],
    }
    config.update(overrides)
    return config


# ----- runner --------------------------------------------------------------------------------


def test_smoke_config_gives_48_valid_rows(smoke_run):
    out, rows = smoke_run
    on_disk = read_csv(out / "runs.csv")
    assert len(rows) == len(on_disk) == 48
    assert list(on_disk[0]) == list(RUN_COLUMNS)
    assert {row["scheduler"] for row in on_disk} == {"fifo", "random", "cp", "heft"}
    for row in on_disk:
        assert row["status"] == "completed"
        assert float(row["normalized_makespan"]) >= 1 - 1e-9


def test_smoke_rows_are_paired(smoke_run):
    _, rows = smoke_run
    instance_keys = {}
    for row in rows:
        key = (row["graph_seed"], row["k"], row["failure_p"], row["sim_seed"])
        instance = (row["n_edges"], row["total_work"], row["speeds"], row["lower_bound"])
        assert instance_keys.setdefault(key, instance) == instance  # same graph and workers
    assert len(instance_keys) == 12


def test_rerun_is_byte_identical(smoke_run, scratch):
    out, _ = smoke_run
    run_experiment(load_config(SMOKE_PATH), scratch)
    assert (scratch / "runs.csv").read_bytes() == (out / "runs.csv").read_bytes()


def test_metadata_contents(smoke_run, smoke_dict):
    out, _ = smoke_run
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"] == smoke_dict
    assert metadata["n_runs"] == 48
    assert set(metadata) == {
        "config", "git", "python_version", "platform", "dagsched_version", "n_runs",
        "wall_time_s",
    }
    assert set(metadata["git"]) == {"commit", "dirty"}


def test_git_info_unknown_without_git(monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner_module.subprocess, "run", no_git)
    assert runner_module._git_info() == {"commit": "unknown", "dirty": "unknown"}


def test_heterogeneous_workers_are_seeded_and_shared(scratch):
    config = parse_config(tiny_config(
        workers=[{"k": 3, "speeds": "uniform", "low": 0.5, "high": 2.0}], graph_seeds=[0, 1],
    ))
    rows = run_experiment(config, scratch)
    speeds = {(row["graph_seed"], row["scheduler"]): row["speeds"] for row in rows}
    assert speeds[(0, "fifo")] == speeds[(0, "cp")]
    assert speeds[(0, "fifo")] != speeds[(1, "fifo")]
    assert all(0.5 <= s <= 2.0 for s in json.loads(speeds[(0, "fifo")]))
    rerun = run_experiment(config, scratch / "again")
    assert [row["speeds"] for row in rerun] == [row["speeds"] for row in rows]


def test_invalid_schedule_aborts_without_writing(scratch, monkeypatch):
    def reject(*args, **kwargs):
        raise ScheduleViolation("injected")

    monkeypatch.setattr(runner_module, "validate_schedule", reject)
    with pytest.raises(ExperimentError, match="invalid schedule from 'fifo'.*injected"):
        run_experiment(parse_config(tiny_config()), scratch / "out")
    assert not (scratch / "out").exists()


# ----- config validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(extra=1), r"config: unknown key\(s\) \['extra'\]"),
        (lambda c: c.pop("sim_seeds"), r"config: missing field\(s\) \['sim_seeds'\]"),
        (lambda c: c["graphs"][0].update(edge_p=0.1), r"graphs\[0\]: unknown key\(s\) \['edge_p'\]"),
        (lambda c: c["graphs"][0].pop("n_layers"), r"graphs\[0\]: missing field\(s\)"),
        (lambda c: c["graphs"][0].update(family="tree"), "unknown family 'tree'"),
        (lambda c: c["graphs"][0].update(n_layers=99), r"graphs\[0\]: n_layers \(99\)"),
        (lambda c: c["graphs"][0]["durations"].update(mu=1), r"durations: unknown key"),
        (lambda c: c["workers"][0].update(speeds="fast"), r"workers\[0\]: 'speeds' must be"),
        (lambda c: c["workers"][0].update(k=0), r"workers\[0\]: k must be"),
        (lambda c: c["workers"].append({"k": 2, "speeds": "uniform", "low": 2, "high": 1}),
         r"workers\[2\]: need finite 0 < low <= high"),
        (lambda c: c["workers"][0].update(low=1), r"workers\[0\]: unknown key"),
        (lambda c: c.update(schedulers=["fifo", "magic"]), r"schedulers: unknown \['magic'\]"),
        (lambda c: c.update(schedulers=["fifo", "fifo"]), "must be unique"),
        (lambda c: c["failures"].append({"p": 1.5}), r"failures\[2\]: p must be in"),
        (lambda c: c["failures"][0].update(q=1), r"failures\[0\]: unknown key"),
        (lambda c: c["retry"].update(max_attempts=0), "retry: max_attempts must be >= 1"),
        (lambda c: c["retry"].update(jitter=1), r"retry: unknown key\(s\) \['jitter'\]"),
        (lambda c: c.update(graph_seeds=[0, 0]), "graph_seeds: seeds must be unique"),
        (lambda c: c.update(sim_seeds=[]), "sim_seeds: expected a non-empty list"),
        (lambda c: c.update(sim_seeds=[1.5]), "seeds must be integers"),
        (lambda c: c.update(duplicate_delivery_prob=2), "duplicate_delivery_prob"),
        (lambda c: c.update(name=""), "name: expected a non-empty string"),
    ],
)
def test_config_errors_are_clear(smoke_dict, mutate, message):
    config = copy.deepcopy(smoke_dict)
    mutate(config)
    with pytest.raises(ConfigError, match=message):
        parse_config(config)


def test_config_not_an_object():
    with pytest.raises(ConfigError, match="expected an object"):
        parse_config([1, 2])


def test_load_config_file_errors(scratch):
    with pytest.raises(ConfigError, match="not found"):
        load_config(scratch / "missing.json")
    broken = scratch / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(broken)


def test_smoke_config_counts(smoke_dict):
    config = parse_config(smoke_dict)
    assert config.n_runs == 48
    assert config.retry.max_attempts == 5


# ----- CLI -----------------------------------------------------------------------------------


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_run_success(scratch, capsys):
    config = write_json(scratch / "tiny.json", tiny_config())
    assert main(["run", str(config), "--out", str(scratch / "out")]) == 0
    assert "tiny: 2 runs written" in capsys.readouterr().out
    assert len(read_csv(scratch / "out" / "runs.csv")) == 2


def test_cli_run_reports_config_error(scratch, capsys):
    config = write_json(scratch / "bad.json", tiny_config(extra=True))
    assert main(["run", str(config), "--out", str(scratch / "out")]) == 1
    assert "error: config: unknown key(s) ['extra']" in capsys.readouterr().err


def test_cli_usage_errors_exit_non_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "x.json"])  # --out is required
    assert excinfo.value.code == 2


def test_module_entry_point_exit_codes(scratch):
    missing = subprocess.run(
        [sys.executable, "-m", "dagsched.experiments", "run", str(scratch / "nope.json"),
         "--out", str(scratch / "out")],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert missing.returncode == 1
    assert "config file not found" in missing.stderr
