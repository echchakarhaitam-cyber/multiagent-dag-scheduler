"""Experiment configuration: a JSON file parsed into a validated, immutable ExperimentConfig.

Parsing is strict: unknown keys and missing fields are errors with the exact location
(e.g. "graphs[0]: unknown key(s) ['edge_p']"), because a silently ignored typo in a config
would produce a different experiment than the one described.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dagsched.failures import BernoulliFailures
from dagsched.generators import DurationSpec, erdos_renyi_dag, fork_join, layered_dag
from dagsched.graph import TaskGraph
from dagsched.retry import RetryPolicy
from dagsched.schedulers import SCHEDULERS
from dagsched.simulator import Worker


class ConfigError(ValueError):
    """The experiment configuration is malformed."""


# family -> (generator, required params, optional params); "durations" is always required.
_FAMILIES: dict[str, tuple[Callable[..., TaskGraph], frozenset[str], frozenset[str]]] = {
    "layered": (layered_dag, frozenset({"n_tasks", "n_layers", "edge_prob"}),
                frozenset({"skip_prob"})),
    "erdos_renyi": (erdos_renyi_dag, frozenset({"n_tasks", "edge_prob"}), frozenset()),
    "fork_join": (fork_join, frozenset({"width", "stages"}), frozenset()),
}
_TOP_REQUIRED = frozenset(
    {"name", "graphs", "graph_seeds", "workers", "schedulers", "failures", "sim_seeds"}
)
_TOP_OPTIONAL = frozenset({"retry", "duplicate_delivery_prob", "duplicate_delay"})


def _check_keys(where: str, data: object, required: frozenset[str],
                optional: frozenset[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ConfigError(f"{where}: expected an object, got {type(data).__name__}")
    unknown = sorted(set(data) - required - optional)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(required | optional)}")
    missing = sorted(required - set(data))
    if missing:
        raise ConfigError(f"{where}: missing field(s) {missing}")
    return data


def _non_empty_list(where: str, value: object) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: expected a non-empty list")
    return value


def _seeds(where: str, value: object) -> tuple[int, ...]:
    seeds = _non_empty_list(where, value)
    if any(isinstance(s, bool) or not isinstance(s, int) for s in seeds):
        raise ConfigError(f"{where}: seeds must be integers, got {seeds}")
    if len(set(seeds)) != len(seeds):
        raise ConfigError(f"{where}: seeds must be unique, got {seeds}")
    return tuple(seeds)


@dataclass(frozen=True)
class GraphSpec:
    """A graph family plus its parameters; build(seed) gives one graph instance."""

    family: str
    params: Mapping[str, Any]  # generator keyword arguments except durations and seed
    durations: DurationSpec

    def build(self, seed: int) -> TaskGraph:
        generator = _FAMILIES[self.family][0]
        return generator(**self.params, durations=self.durations, seed=seed)

    def params_json(self) -> str:
        """Canonical JSON of the parameters (family excluded), used as a grouping key."""
        params = {**self.params, "durations": self.durations.to_dict()}
        return json.dumps(params, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class WorkerSpec:
    """k workers, either all speed 1 ("homogeneous") or speeds ~ U(low, high) ("uniform")."""

    k: int
    speeds: str
    low: float = 1.0
    high: float = 1.0

    def build(self, graph_seed: int) -> tuple[Worker, ...]:
        """Worker speeds depend only on (graph_seed, k), never on the scheduler: every
        scheduler of a paired comparison sees the same workers."""
        if self.speeds == "homogeneous":
            return tuple(Worker(f"W{i}") for i in range(self.k))
        rng = random.Random(f"speeds:{graph_seed}:{self.k}")
        return tuple(Worker(f"W{i}", rng.uniform(self.low, self.high)) for i in range(self.k))


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    graphs: tuple[GraphSpec, ...]
    graph_seeds: tuple[int, ...]
    workers: tuple[WorkerSpec, ...]
    schedulers: tuple[str, ...]
    failure_ps: tuple[float, ...]
    retry: RetryPolicy
    duplicate_delivery_prob: float
    duplicate_delay: float
    sim_seeds: tuple[int, ...]
    raw: Mapping[str, Any]  # the parsed JSON, recorded verbatim in metadata.json

    @property
    def n_runs(self) -> int:
        return (len(self.graphs) * len(self.graph_seeds) * len(self.workers)
                * len(self.failure_ps) * len(self.sim_seeds) * len(self.schedulers))


def _parse_graph(where: str, data: object) -> GraphSpec:
    if not isinstance(data, Mapping) or "family" not in data:
        raise ConfigError(f"{where}: expected an object with a 'family' field")
    family = data["family"]
    if family not in _FAMILIES:
        raise ConfigError(f"{where}: unknown family {family!r}; known: {sorted(_FAMILIES)}")
    _, required, optional = _FAMILIES[family]
    _check_keys(where, data, required | {"family", "durations"}, optional)
    try:
        spec = GraphSpec(
            family,
            {key: value for key, value in data.items() if key not in ("family", "durations")},
            DurationSpec.from_dict(data["durations"]),
        )
        spec.build(0)  # surfaces invalid parameter values now, with the config location
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from None
    return spec


def _parse_workers(where: str, data: object) -> WorkerSpec:
    if not isinstance(data, Mapping) or data.get("speeds") not in ("homogeneous", "uniform"):
        raise ConfigError(f"{where}: 'speeds' must be \"homogeneous\" or \"uniform\"")
    if data["speeds"] == "homogeneous":
        _check_keys(where, data, frozenset({"k", "speeds"}))
    else:
        _check_keys(where, data, frozenset({"k", "speeds", "low", "high"}))
    k = data["k"]
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ConfigError(f"{where}: k must be an integer >= 1, got {k!r}")
    if data["speeds"] == "homogeneous":
        return WorkerSpec(k, "homogeneous")
    low, high = data["low"], data["high"]
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (low, high))
    if not numbers or not 0 < low <= high or high == float("inf"):
        raise ConfigError(f"{where}: need finite 0 < low <= high, got low={low!r}, high={high!r}")
    return WorkerSpec(k, "uniform", float(low), float(high))


def _parse_failure(where: str, data: object) -> float:
    _check_keys(where, data, frozenset({"p"}))
    try:
        return BernoulliFailures(data["p"]).p
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from None


def _parse_schedulers(value: object) -> tuple[str, ...]:
    names = _non_empty_list("schedulers", value)
    unknown = sorted(str(name) for name in names if name not in SCHEDULERS)
    if unknown:
        raise ConfigError(f"schedulers: unknown {unknown}; known: {sorted(SCHEDULERS)}")
    if len(set(names)) != len(names):
        raise ConfigError(f"schedulers: names must be unique, got {names}")
    return tuple(names)


def _parse_delivery(data: Mapping[str, Any]) -> tuple[float, float]:
    prob = data.get("duplicate_delivery_prob", 0.0)
    delay = data.get("duplicate_delay", 0.0)
    for name, value in (("duplicate_delivery_prob", prob), ("duplicate_delay", delay)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{name}: expected a number, got {value!r}")
    if not 0 <= prob <= 1:
        raise ConfigError(f"duplicate_delivery_prob: must be in [0, 1], got {prob}")
    if not 0 <= delay < float("inf"):
        raise ConfigError(f"duplicate_delay: must be finite and >= 0, got {delay}")
    return float(prob), float(delay)


def parse_config(data: object) -> ExperimentConfig:
    """Validate a parsed JSON object and turn it into an ExperimentConfig."""
    top = _check_keys("config", data, _TOP_REQUIRED, _TOP_OPTIONAL)
    name = top["name"]
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("name: expected a non-empty string")
    retry_data = _check_keys(
        "retry", top.get("retry", {}), frozenset(),
        frozenset({"max_attempts", "backoff_base", "backoff_factor"}),
    )
    try:
        retry = RetryPolicy(**retry_data)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"retry: {exc}") from None
    prob, delay = _parse_delivery(top)
    return ExperimentConfig(
        name=name,
        graphs=tuple(_parse_graph(f"graphs[{i}]", g)
                     for i, g in enumerate(_non_empty_list("graphs", top["graphs"]))),
        graph_seeds=_seeds("graph_seeds", top["graph_seeds"]),
        workers=tuple(_parse_workers(f"workers[{i}]", w)
                      for i, w in enumerate(_non_empty_list("workers", top["workers"]))),
        schedulers=_parse_schedulers(top["schedulers"]),
        failure_ps=tuple(_parse_failure(f"failures[{i}]", f)
                         for i, f in enumerate(_non_empty_list("failures", top["failures"]))),
        retry=retry,
        duplicate_delivery_prob=prob,
        duplicate_delay=delay,
        sim_seeds=_seeds("sim_seeds", top["sim_seeds"]),
        raw=data,
    )


def load_config(path: str | Path) -> ExperimentConfig:
    """Read and validate a JSON config file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from None
    return parse_config(data)
