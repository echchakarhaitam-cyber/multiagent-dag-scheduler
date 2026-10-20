# Experimental methodology

How scheduler comparisons are run and summarized. This page describes the design only; no
results are reported here (NOT MEASURED yet).

## Reproducing
```
python -m dagsched.experiments run configs/smoke.json --out results/smoke
python -m dagsched.experiments summarize results/smoke            # --baseline fifo (default)
```
`run` writes `runs.csv` (one row per simulation) and `metadata.json` (config, git commit and
dirty flag, Python version, platform, dagsched version, number of runs, wall time).
`summarize` writes `summary.csv` and `paired.csv`. `runs.csv` holds no wall-clock values, so
rerunning a config reproduces it byte for byte. Configs are strict JSON: unknown keys and
missing fields are errors.

## Grid and paired design
A config defines graph families × graph seeds × worker sets × failure rates × simulation
seeds × schedulers, and every combination is run. Within one **instance**, i.e. a fixed
(graph, graph seed, workers, failure rate, simulation seed), every scheduler gets:
- the **same graph instance** (built once from the family parameters and the graph seed);
- the **same workers**. Heterogeneous speeds are drawn from
  `random.Random(f"speeds:{graph_seed}:{k}")`, independently of the scheduler;
- the **same failures**: common random numbers. Attempt n of task v fails or succeeds
  according to `random.Random(f"{sim_seed}:{v}:{n}")`, whatever the scheduler does
  (docs/theory/04-failures.md).

Comparisons are therefore made on per-instance differences, which removes the
instance-to-instance variation that would otherwise dominate. Every run is checked by the
independent validator. An invalid schedule aborts the experiment and is never recorded.

Failure draws are keyed by the simulation seed and the task id. Different graph instances with
the same simulation seed therefore share draws for equally named tasks. Their structure and
durations differ, so this is a mild correlation between instances, not between schedulers.

## Graph families (`dagsched.generators`, all seeded)
- **layered**: n tasks spread evenly over L layers. Edges go to the next layer with
  probability p, and each task gets at least one predecessor, so the depth is exactly L.
  Optional skip edges.
- **erdos_renyi**: each edge i → j (i < j) exists with probability p.
- **fork_join**: a source, `width` parallel tasks and a join, repeated `stages` times.

Durations are uniform U(low, high), or lognormal with values clipped to ≥ 0.1.

## Metrics (per run)
- makespan T; lower bound LB = max(CP / s_max, W / Σ s_w) (docs/theory/03-schedulers.md);
- **normalized makespan** T / LB ≥ 1. It removes the scale of each instance, so instances of
  different sizes can be averaged;
- utilization = Σ busy time (failed attempts included) / (k · T);
- wasted work = total time of failed attempts; attempts, failed and cancelled tasks, ignored
  duplicates;
- status: "completed" or "partial". Partial runs stop early, so their T is not comparable with
  LB. Normalized-makespan statistics and paired differences use completed runs only, and the
  completion rate is reported separately.

## Summaries
`summary.csv`, one row per (family, parameters, k, speeds kind, failure rate, scheduler):
run count, completion rate, mean and sample standard deviation of T / LB, a 95% bootstrap CI,
mean utilization and mean wasted work.

`paired.csv`, one row per group and non-baseline scheduler. With
dᵢ = (T/LB)_scheduler − (T/LB)_baseline over instances where both runs completed, it reports:
- the mean of dᵢ and its 95% bootstrap CI (negative means better than the baseline);
- the win rate, the fraction of dᵢ < 0;
- the sign-test p-value.

**Bootstrap CI** (percentile method). The dᵢ are resampled with replacement 2000 times using
a fixed seed, and the 2.5% and 97.5% quantiles of the resampled means form the interval. It
makes no normality assumption, and it is deterministic.

**Sign test** (exact, two-sided). Under H₀ "the scheduler is as likely to beat the baseline as
to lose to it", the number of negative dᵢ among the n non-zero ones is Binomial(n, ½), so
p = min(1, 2 · P(X ≤ min(#neg, #pos))). Ties (dᵢ = 0) carry no information about the
direction and are dropped. The test uses only signs, so it is robust to outliers but ignores
effect size. The mean difference and its CI cover that.

## Caveats
- With few instances per group, bootstrap intervals are crude and the sign test has little
  power. The smoke config (3 graph seeds) is a pipeline check, not an experiment.
- Many groups are compared at once; the p-values are not corrected for multiple testing.
