# Documentation index

Theory: what is modelled and why the algorithms work.
- [01 — Task graphs](theory/01-task-graphs.md): DAG model, Kahn's algorithm, bottom/top
  levels, critical path, the max(CP, W/k) lower bound, worked example.
- [02 — Simulation](theory/02-simulation.md): event-driven loop, equal-timestamp batching,
  work-conserving vs idling schedulers, Graham's list-scheduling bound.
- [03 — Schedulers](theory/03-schedulers.md): FIFO, Random, Highest Level First, the online
  HEFT-style scheduler, why waiting can beat working, and the proof that cp and HEFT coincide
  on identical workers.
- [04 — Failures](theory/04-failures.md): failure model, retries with exponential backoff,
  permanent failure and cancellation, idempotent collection, common random numbers.

Experiments:
- [Experimental methodology](experiments.md): configs, paired design, seeds, metrics,
  bootstrap CIs, sign test, Holm correction, CLI commands (run, summarize, plot, ablation).
- [Results](results.md): RQ1–RQ5 with generated tables, observations and threats to validity.
- [Figures](figures/): PNG figures referenced from the results.

Elsewhere in the repository: [README](../README.md) (overview and quick start),
[AGENTS.md](../AGENTS.md) (rules for coding agents working on this repository),
[CITATION.cff](../CITATION.cff), [LICENSE](../LICENSE).
