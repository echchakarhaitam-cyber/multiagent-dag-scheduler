# 04 — Failures, retries and idempotent results

Implemented in `failures.py`, `retry.py`, `collector.py` and `simulator.py`. Every timeline
below is asserted in `tests/test_failures.py`. With the default `SimulationConfig` (no
failures, no duplicates) the simulator reproduces the earlier failure-free results exactly.

## Failure model
Each execution **attempt** of a task either succeeds or fails after a fraction f ∈ (0, 1] of
its execution time. A failed attempt frees its worker at the failure time. The time already
spent is **wasted work**, and it still counts as busy time in utilization. The scheduler is
not told in advance: it sees the nominal expected end of every running attempt.
`BernoulliFailures(p)` fails each attempt independently with probability p, optionally per
worker. `ScriptedFailures` fails exactly the listed (task, attempt) pairs.

## Retries with exponential backoff
After failed attempt n (1-based) at time t, the task is ready again at
t + base · factor^(n−1), unless n = max_attempts. Backoff spreads retries out in time, which in
real systems gives a transient cause time to clear. Each retry is a new decision point.

Worked example, 1 worker, FIFO, A's first attempt fails at half-time:
- base 0: A fails at t = 1 and is retried at once, [1, 3]. Makespan 13 + 1 = **14**, wasted
  work 1.
- base 1, factor 2: A is ready again at t = 2. The worker idles in [1, 2], A runs [2, 4], and
  the makespan is **15**.

## Permanent failure and cancellation
When the last allowed attempt fails, the task **fails permanently**. Its descendants can never
run, because their inputs will never exist. They are marked **cancelled** and never started,
and the run ends with status "partial" once no event is pending. This is not a deadlock: the
remaining tasks are resolved, not stuck.

Example (max_attempts 1, C fails at half-time): A [0, 2], B [2, 5], C fails at 5.5.
failed = (C), cancelled = (D, E, F), makespan **5.5**. If every attempt fails
(Bernoulli p = 1), the source A fails 3 times, everything else is cancelled, and the run still
terminates.

## At-least-once delivery vs exactly-once effect
A finished attempt reports its result by a **delivery**. Transports typically guarantee
*at-least-once* delivery: a report can arrive twice, e.g. when it is resent after a lost
acknowledgement. The simulator models this with `duplicate_delivery_prob` and
`duplicate_delay`. Only *accepted* deliveries count as completions and release successors.

The idempotent `ResultCollector` accepts the first delivery per task and ignores the rest.
Delivery stays at-least-once, but the **effect** is exactly-once.

Example (f), with naive collection. P(1), Q(5), R(1) where R depends on P and Q; 2 workers,
FIFO, every completion delivered twice with no delay:

| t | event                                     | R's unmet dependencies |
|---|-------------------------------------------|------------------------|
| 0 | P starts on W0, Q on W1                   | 2                      |
| 1 | P completes, delivered and accepted       | 1                      |
| 1 | duplicate of P, **accepted again**        | 0 → R is "ready"       |
| 1 | R starts on W0                            |                        |
| 5 | Q completes                               | −1                     |

R ran during [1, 2] although its input Q only existed at t = 5. The independent validator
reports this as a precedence violation. With idempotent collection the duplicate at t = 1 is
ignored, and R starts at t = 5 exactly as without duplicates. That run reports 3 duplicates
received and 3 ignored, and it validates. The naive mode exists only to demonstrate this bug.

## Common random numbers
The fate of attempt n of task v is decided by u drawn from
`random.Random(f"{seed}:{v}:{n}")`, owned by the simulator and not by the scheduler. So on a
given seed every scheduler faces the same failures: attempt 2 of task X fails, or succeeds,
whatever policy runs and whatever random numbers the policy consumed. Duplicate deliveries use
`random.Random(f"{seed}:dup:{v}:{n}")` in the same way.

This is **common random numbers**, a standard variance-reduction technique for comparing
simulated systems. Differences between two policies on the same seed come from the policies,
not from different luck. The variance of the *difference* of their makespans is then usually
much smaller than with independent draws, so fewer seeds are needed. The tests check that on
10 random DAGs (p = 0.3, up to 5 attempts, identical workers) FIFO, Random, CP and HEFT make
exactly the same number of attempts per task. How much variance this saves is NOT MEASURED yet.

## Limitations
- Failures are independent per attempt. Correlated failures (a worker crashing and taking all
  its future attempts with it) are not modelled.
- A failure is detected the moment it happens; there are no timeouts.
- Duplicates only concern successful completions. Failure reports are never duplicated.
