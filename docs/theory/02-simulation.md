# 02 — Simulation model and Graham's bound

Implemented in `src/dagsched/simulator.py`; checked by `src/dagsched/validation.py` and the tests.

## Model
k workers; worker w has speed s_w > 0, so task v on w takes w(v)/s_w. Execution is
non-preemptive, one task per worker at a time, and communication costs are not modelled
(a known limitation). A task is *ready* once all its dependencies have completed.

## Event-driven loop
Time only matters at the moments when the state changes, so the simulator jumps from event to
event instead of advancing a clock in small steps:

```
decide(t = 0)
while some task is unfinished:
    t ← time of the earliest pending event
    apply every event with timestamp exactly t    # completions: free worker, release successors
    decide(t)                                     # one scheduler call → list of assignments
```

Events sit in a heap keyed by (time, sequence number). The sequence number makes the order of
simultaneous events deterministic. Every event goes through a single `_apply` method, so new
event kinds (worker failures in a later milestone) can be added without changing the loop.

**Why all equal-timestamp events come first.** If B and E both finish at t = 5, a scheduler
called after only B's completion would see one idle worker and might commit it to a poor choice,
when two workers are in fact free. Processing the whole batch first gives the scheduler the true
state at t, with one call per distinct time. It also makes the result independent of the order
in which simultaneous events were inserted. In the worked example on 2 workers (FIFO), the
scheduler is called at t = 0, 2, 3, 5, 9, and at t = 5 it sees both workers idle and D ready.

## Work-conserving vs idling schedulers
A scheduler is **work-conserving** if it never leaves a worker idle while a task is ready.
Ready tasks and idle workers appear only at completion events, so it suffices to start
min(#ready, #idle) tasks at every decision. FIFO and Random do this.

An **idling** scheduler may hold a task back, e.g. to wait for a faster worker. The simulator
allows that as long as something is running, because a future completion will trigger another
decision. If nothing is running, no event can ever occur again, so holding back is a
**deadlock** and raises `SchedulingError`.

## Graham's list-scheduling bound
For k identical workers and any work-conserving (list) schedule with makespan T,

    T ≤ W/k + (1 − 1/k)·CP          (W = total work, CP = critical-path length)

and since T ≥ max(CP, W/k), every list schedule is within a factor 2 − 1/k of optimal.

*Proof sketch.* Build a chain backwards from the task v₁ that finishes last. Given vᵢ, let vᵢ₊₁
be the dependency of vᵢ that finishes last. Stop at a source. Between the end of vᵢ₊₁ and the
start of vᵢ, vᵢ is ready and waiting, so (work-conserving) **all k workers are busy**. While vᵢ
runs, at least one worker is busy **with a chain task**. So at every moment in [0, T] either
all workers are busy or a chain task is running. Idle worker-time therefore accrues only while
the chain runs, at most (k − 1) per unit of time, and the chain is a path, so it is no longer
than CP. Total capacity k·T equals busy time W plus idle time, hence
k·T ≤ W + (k − 1)·CP, which is the bound.

Example: for the worked example with k = 2 the bound gives 13/2 + ½·10 = 11.5. FIFO achieves
10 (asserted in `tests/test_baselines.py`). The randomized test checks
max(CP, W/k) ≤ T ≤ Graham's bound for FIFO and Random on 50 random DAGs with k ∈ {1, 2, 4, 8}.

With heterogeneous speeds the bound in this form does not apply. The simulator supports
speeds, but this milestone makes no claim about them.

**Reference.** R. L. Graham, "Bounds on Multiprocessing Timing Anomalies",
*SIAM Journal on Applied Mathematics* 17(2), 1969.
