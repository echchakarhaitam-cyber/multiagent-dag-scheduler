"""Generate the tables of docs/results.md from the committed results files.

docs/results.md contains marker pairs

    <!-- BEGIN GENERATED: <block> -->
    <!-- END GENERATED: <block> -->

and this script replaces what is between them; prose outside the markers is untouched. Every
number in a generated block is read from results/eX/summary.csv, paired.csv or metadata.json
(rounded for display only), so the tables cannot drift from the data.

    python scripts/make_results_tables.py            # rewrite the document in place
    python scripts/make_results_tables.py --check    # exit 1 if the document is out of date
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

SCHEDULERS = ("fifo", "random", "cp", "heft")
FAMILIES = ("layered", "erdos_renyi", "fork_join")
DISTRIBUTIONS = ("uniform", "lognormal")
SPEEDS = ("homogeneous", "heterogeneous")
EXPERIMENTS = {"e1": "e1_homogeneous", "e2": "e2_heterogeneous", "e3": "e3_failures"}
_MARKER = re.compile(
    r"(<!-- BEGIN GENERATED: (?P<name>[\w-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)",
    re.DOTALL,
)


class TableError(ValueError):
    """Missing input files or an unknown block in the document."""


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TableError(f"missing {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["dist"] = json.loads(row["graph_params"])["durations"]["dist"]
        row["k"] = int(row["k"])
        row["failure_p"] = float(row["failure_p"])
    return rows


def _order(row: dict[str, Any], keys: Sequence[str]) -> tuple[Any, ...]:
    orders = {"graph_family": FAMILIES, "dist": DISTRIBUTIONS, "speeds_kind": SPEEDS}
    return tuple(orders[key].index(row[key]) if key in orders else row[key] for key in keys)


def _p(value: str) -> str:
    p = float(value)
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def fmt_mean_ci(mean: str, low: str, high: str, digits: int = 3) -> str:
    if mean == "":
        return "n/a"
    return f"{float(mean):.{digits}f} [{float(low):.{digits}f}, {float(high):.{digits}f}]"


def is_significant(row: dict[str, Any]) -> bool:
    """p_holm < 0.05 AND the 95% CI of the mean difference excludes 0."""
    if row["p_holm"] == "" or row["ci_low"] == "":
        return False
    excludes_zero = float(row["ci_high"]) < 0 or float(row["ci_low"]) > 0
    return float(row["p_holm"]) < 0.05 and excludes_zero


def fmt_paired(row: dict[str, Any]) -> str:
    if int(row["n_pairs"]) == 0:
        return "n/a"
    text = (f"{float(row['mean_difference']):+.3f} [{float(row['ci_low']):+.3f}, "
            f"{float(row['ci_high']):+.3f}]; win {float(row['win_rate']):.2f}; "
            f"p_H {_p(row['p_holm'])}")
    return text + (" **sig.**" if is_significant(row) else "")


def markdown_table(header: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cells) + " |" for cells in body]
    return "\n".join(lines)


_LABELS = {"graph_family": "graph", "dist": "durations", "k": "k", "failure_p": "p",
           "speeds_kind": "workers"}


def pivot(rows: list[dict[str, Any]], keys: Sequence[str],
          cell: Callable[[dict[str, Any]], str]) -> tuple[list[str], list[list[str]]]:
    """One table row per distinct `keys` value, one column per scheduler present."""
    present = [s for s in SCHEDULERS if any(r["scheduler"] == s for r in rows)]
    groups: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: _order(r, keys)):
        groups.setdefault(tuple(row[key] for key in keys), {})[row["scheduler"]] = row
    body = [[str(value) for value in key] + [cell(by[s]) if s in by else "" for s in present]
            for key, by in groups.items()]
    return [_LABELS[key] for key in keys] + present, body


def _source(results: Path, experiment: str, filename: str) -> str:
    return f"Source: `{(results / experiment / filename).as_posix()}`"


def block_setup(results: Path, experiment: str) -> str:
    meta_path = results / experiment / "metadata.json"
    if not meta_path.is_file():
        raise TableError(f"missing {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return (f"- Config: `configs/{meta['config']['name']}.json`; {meta['n_runs']} runs; "
            f"wall time {meta['wall_time_s']:.1f} s; commit `{meta['git']['commit'][:7]}` "
            f"(dirty: {meta['git']['dirty']}); Python {meta['python_version']}.\n"
            f"- {_source(results, experiment, 'metadata.json')}")


def block_makespan(results: Path, experiment: str, keys: Sequence[str]) -> str:
    rows = _read_csv(results / experiment / "summary.csv")
    header, body = pivot(rows, keys, lambda r: fmt_mean_ci(
        r["mean_normalized_makespan"], r["ci_low"], r["ci_high"]))
    return _source(results, experiment, "summary.csv") + "\n\n" + markdown_table(header, body)


def block_paired(results: Path, experiment: str, keys: Sequence[str]) -> str:
    rows = _read_csv(results / experiment / "paired.csv")
    header, body = pivot(rows, keys, fmt_paired)
    return _source(results, experiment, "paired.csv") + "\n\n" + markdown_table(header, body)


def block_versus(results: Path, experiment: str, keys: Sequence[str], scheduler: str,
                 filename: str, speeds_kind: str | None = None) -> str:
    """One scheduler's paired differences against the baseline of `filename`."""
    rows = [r for r in _read_csv(results / experiment / filename)
            if r["scheduler"] == scheduler
            and (speeds_kind is None or r["speeds_kind"] == speeds_kind)]
    if not rows:
        raise TableError(f"no rows for {scheduler!r} in {results / experiment / filename}")
    header, body = pivot(rows, keys, fmt_paired)
    header[-1] = f"{scheduler} − {rows[0]['baseline']}"
    return _source(results, experiment, filename) + "\n\n" + markdown_table(header, body)


def block_reliability(results: Path, experiment: str, keys: Sequence[str]) -> str:
    rows = _read_csv(results / experiment / "summary.csv")

    def cell(r: dict[str, Any]) -> str:
        wasted = fmt_mean_ci(r["mean_wasted_work"], r["wasted_ci_low"], r["wasted_ci_high"], 1)
        return f"{float(r['completion_rate']):.2f}; {wasted}"

    header, body = pivot(rows, keys, cell)
    return _source(results, experiment, "summary.csv") + "\n\n" + markdown_table(header, body)


_GRAPH_KEYS = ("graph_family", "dist", "k")
_FAILURE_KEYS = ("speeds_kind", "graph_family", "dist", "failure_p")
BLOCKS: dict[str, Callable[[Path], str]] = {
    "setup_e1": lambda r: block_setup(r, "e1"),
    "setup_e2": lambda r: block_setup(r, "e2"),
    "setup_e3": lambda r: block_setup(r, "e3"),
    "e1_makespan": lambda r: block_makespan(r, "e1", _GRAPH_KEYS),
    "e1_paired": lambda r: block_paired(r, "e1", _GRAPH_KEYS),
    "e2_makespan": lambda r: block_makespan(r, "e2", _GRAPH_KEYS),
    "e2_paired": lambda r: block_paired(r, "e2", _GRAPH_KEYS),
    "e2_heft_vs_cp": lambda r: block_versus(r, "e2", _GRAPH_KEYS, "heft", "paired_vs_cp.csv"),
    "e3_heft_vs_cp": lambda r: block_versus(r, "e3", _FAILURE_KEYS[1:], "heft",
                                            "paired_vs_cp.csv", speeds_kind="heterogeneous"),
    "e3_makespan": lambda r: block_makespan(r, "e3", _FAILURE_KEYS),
    "e3_reliability": lambda r: block_reliability(r, "e3", _FAILURE_KEYS),
    "e3_paired": lambda r: block_paired(r, "e3", _FAILURE_KEYS),
}


def render(document: str, results: Path) -> str:
    """Return `document` with every generated block regenerated from `results`."""
    names = [match["name"] for match in _MARKER.finditer(document)]
    unknown = sorted(set(names) - set(BLOCKS))
    if unknown:
        raise TableError(f"unknown generated block(s) {unknown}; known: {sorted(BLOCKS)}")
    return _MARKER.sub(lambda m: m.group(1) + BLOCKS[m["name"]](results) + "\n" + m.group(4),
                       document)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the tables of docs/results.md.")
    parser.add_argument("--doc", type=Path, default=Path("docs/results.md"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--check", action="store_true", help="only check, do not write")
    args = parser.parse_args(argv)
    try:
        current = args.doc.read_text(encoding="utf-8")
        updated = render(current, args.results)
    except (OSError, TableError) as exc:
        print(f"error: {exc}")
        return 1
    if args.check:
        stale = updated != current
        print(f"{args.doc} is {'OUT OF DATE' if stale else 'up to date'}")
        return 1 if stale else 0
    args.doc.write_text(updated, encoding="utf-8", newline="\n")
    print(f"regenerated {len(list(_MARKER.finditer(updated)))} blocks in {args.doc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
