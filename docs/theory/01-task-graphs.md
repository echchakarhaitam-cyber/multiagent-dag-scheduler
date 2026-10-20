# 01 — Task graphs

Implemented in `src/dagsched/graph.py`; every number below is asserted in `tests/test_graph.py`.

## Model
A task graph is a directed acyclic graph G = (V, E). Each task v ∈ V has a duration w(v) > 0
(finite). An edge u → v means v depends on u: v may start only after u has finished.
Workers are identical and run one task at a time; communication costs are not modelled.

**Why acyclic.** A cycle v₁ → v₂ → … → v₁ requires each task on it to start after itself has
finished, so no schedule exists. The constructor therefore rejects cycles (`CycleError`, which
reports one concrete cycle), as well as empty graphs, duplicate ids, unknown dependencies and
self-dependencies.

## Worked example
```
A(2) ; B(3) deps A ; C(1) deps A ; D(4) deps B,C ; E(2) deps C ; F(1) deps D,E

        ┌─> B(3) ─┐
  A(2) ─┤         ├─> D(4) ─┐
        └─> C(1) ─┤         ├─> F(1)
                  └─> E(2) ─┘
```

## Topological order — Kahn's algorithm
Keep, for each task, the number of unfinished predecessors. Repeatedly remove a task whose count
is 0, append it to the order, and decrement its successors' counts. If the ready set empties
before all tasks are removed, the remaining tasks contain a cycle. (Each remaining task still has
a remaining predecessor; walking backwards along them must revisit a node, which yields the
reported cycle.)

Each task and edge is handled once: O(V + E). We keep the ready set in a min-heap keyed by task
id, which makes the order unique (smallest ready id first) at a cost of O((V + E) log V).

Example: A, then B and C are ready → B; then C; then D and E are ready → D; then E; then F.
Order: **A, B, C, D, E, F**.

## Bottom and top levels
- Bottom level: b(v) = w(v) + max over successors s of b(s); b(v) = w(v) for sinks. It is the
  length of the longest path from v to the end, including v. Computed in reverse topological
  order.
- Top level: t(v) = 0 for sources, else max over predecessors p of t(p) + w(p). It is the earliest
  start time of v with unlimited workers. Computed in topological order.

Both are O(V + E) and computed without recursion.

| task | w | t(v) | b(v) | t(v) + b(v) |
|------|---|------|------|-------------|
| A    | 2 | 0    | 10   | 10          |
| B    | 3 | 2    | 8    | 10          |
| C    | 1 | 2    | 6    | 8           |
| D    | 4 | 5    | 5    | 10          |
| E    | 2 | 3    | 3    | 6           |
| F    | 1 | 9    | 1    | 10          |

## Critical path
The critical path is a longest path in G; its length CP = max_v b(v). We construct it by
starting at the source with the largest b(v) and repeatedly moving to the successor with the
largest b(v), breaking ties by the smallest id. For every task t(v) + b(v) ≤ CP, with equality
exactly on longest paths.

Example: CP = 10, path **A → B → D → F** (the tasks with t(v) + b(v) = 10 in the table).

## Makespan lower bound
With k workers, let W = Σ w(v) be the total work. Every schedule has makespan

    T ≥ LB(k) = max(CP, W / k)

- **CP:** the tasks on the critical path must run one after another, so their durations add up
  no matter how many workers are available.
- **W / k:** k workers provide at most k·T units of busy time by time T, and all W units of work
  must fit, so k·T ≥ W.

Example (W = 13): LB(1) = max(10, 13) = 13; LB(2) = max(10, 6.5) = 10; LB(100) = 10.
This is only a lower bound. Whether a given scheduler reaches it is a question for later
milestones (NOT MEASURED here).
