# 03 — Scheduling policies

Implemented in `src/dagsched/schedulers/`. Every number below is asserted in
`tests/test_informed_schedulers.py` or `tests/test_bounds.py`.

## Baselines: FIFO and Random
FIFO gives ready tasks in (ready_time, id) order to idle workers in id order. Random pairs a
seeded random permutation of the ready tasks with one of the idle workers. Both are
work-conserving list schedulers, so Graham's bound (02-simulation.md) applies to them on
identical workers. They ignore task structure and worker speed. That makes them the reference
any informed policy has to beat.

## Highest Level First (critical-path scheduling)
Hu's rule: always start the ready task with the largest **bottom level**, i.e. the longest
remaining chain of work, including itself (ties: smallest id). The idea is that the chain that
is longest now is the one that will set the makespan if it is delayed. `CriticalPathScheduler`
computes bottom levels once from nominal durations and gives the highest-level tasks to the
fastest idle workers (ties: smallest worker id). Like FIFO, it is work-conserving.

Hu showed this rule is optimal for unit-time tasks whose graph is an in-forest on identical
machines (to verify: exact statement and conditions). In general it is a heuristic.

**Case (a): a long chain hidden behind short tasks.** Two speed-1 workers; tasks a(1), b(1),
z(1), y(5) with y depending on z. FIFO starts a and b (smallest ids), reaches z only at t = 1
and y at t = 2: makespan **7**. HLF sees bottom levels z = 6, y = 5, a = b = 1 and starts z
first: makespan **6**, which equals the lower bound max(CP, W/k) = max(6, 4), so it is optimal.

## Online HEFT-style scheduling
HEFT (Topcuoglu, Hariri & Wu) targets heterogeneous processors in two phases.

1. **Upward rank.** rank(v) = mean_cost(v) + max over successors s of rank(s) (0 for sinks),
   where mean_cost(v) = duration(v) · mean_w(1/s_w). This is the bottom level, with each task's
   cost averaged over the workers.
2. **Earliest finish time (EFT).** Tasks in decreasing rank are each placed on the processor
   where they would finish first.

The original algorithm is **static**: it builds the whole schedule offline before execution,
and it models communication costs. `HEFTScheduler` is an **online adaptation** without
communication costs. At every decision point:
- avail[w] = now for idle workers, or the expected end of their current task;
- ready tasks are taken in (−rank, id) order, and each picks
  argmin_w max(now, avail[w]) + duration/s_w (ties: smallest id);
- if that worker is idle and not yet taken in this decision, the task starts now. Otherwise the
  task waits, and the worker is tentatively reserved (avail[w] grows) for later tasks in the
  same decision;
- reservations are thrown away and the plan is rebuilt at the next decision.

HEFT cannot deadlock: if nothing is running, every worker is idle, so the first task's best
worker is idle and the task starts. On identical workers a free idle worker always has the
earliest finish time, so HEFT is then work-conserving and Graham's bound holds. The randomized
tests check this.

## Why waiting can beat working
**Case (b).** Workers fast (speed 2) and slow (speed 0.5); two independent tasks t1(4), t2(4).
The lower bound is max(4/2, 8/2.5) = 3.2.
- FIFO and HLF are work-conserving. They put t2 on the slow worker at t = 0, where it takes
  4/0.5 = 8: makespan **8**.
- HEFT puts t1 on fast (finish 2). For t2, waiting for fast gives finish 2 + 2 = 4, versus 8
  on slow. So t2 waits, the slow worker stays idle, and t2 runs on fast during [2, 4]:
  makespan **4**.

On heterogeneous workers, being work-conserving can therefore be a factor-2 mistake in this
example. That is why the simulator allows idling, and only forbids it when nothing is running.
On the worked example with 2 identical workers both HLF and HEFT reach makespan 10, the
critical-path length.

## Limitations
- **No communication costs.** Data transfer between dependent tasks on different workers is
  free, which favours spreading work out more than a real system would.
- **Online adaptation.** HEFT here re-plans at each completion from the current state and
  never commits to a future placement. It is not the published static algorithm, and results
  should not be reported as "HEFT" without that qualification.
- **Heuristics, not optimal algorithms.** Minimizing makespan with precedence constraints is
  NP-hard in general, even on identical machines (to verify: Ullman, 1975). None of these
  policies is optimal in general. The only guarantees here are lower bounds and, for
  work-conserving policies on identical workers, Graham's bound. Systematic comparisons across
  graph families are NOT MEASURED yet.

## References
- T. C. Hu, "Parallel Sequencing and Assembly Line Problems", *Operations Research* 9(6), 1961.
- H. Topcuoglu, S. Hariri, M.-Y. Wu, "Performance-Effective and Low-Complexity Task Scheduling
  for Heterogeneous Computing", *IEEE Trans. Parallel and Distributed Systems* 13(3), 2002.
- R. L. Graham, "Bounds on Multiprocessing Timing Anomalies", *SIAM J. Appl. Math.*, 1969.
