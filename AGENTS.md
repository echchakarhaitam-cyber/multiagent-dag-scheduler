# AGENTS.md — rules for any coding agent working in this repository

## Project
dagsched: a public, independent research project on scheduling task graphs (DAGs) across
multiple worker agents under failures. Components: task-graph model, discrete-event simulator,
scheduling policies, failure/retry model, idempotent result collection, experiments.

## Hard rules
1. Original code only. Never copy, recreate or reference code, configs, names, schemas or data
   from any private or company project. Use textbook algorithms and public documentation.
2. Never fabricate results. Every number in docs must come from a script in this repo that was
   actually run. If something was not run, write "NOT MEASURED".
3. No network calls, paid APIs or LLM calls unless the task explicitly says so.
4. Core library (src/dagsched) uses only the Python standard library unless a task says otherwise.
5. Every new behaviour gets pytest tests, including edge cases and failure cases.
6. Before finishing any task run: `pytest -q` and `ruff check .`. Both must pass. If a test fails,
   fix the code (not the test) unless the test itself is wrong, and explain why.
7. Determinism: anything random takes an explicit seed (random.Random(seed)); never use the global
   random state.
8. Never commit secrets, .env files, virtual environments or large generated results.
9. One milestone = one or more small commits with clear imperative messages.
10. Do not start the next milestone on your own.

## Code style
- Python >= 3.10, type hints everywhere, dataclasses for data, docstrings explaining the "why".
- Readable over clever. Line length 100. Ruff must pass.

## Final report format (always end every task with this)
1. Files created/modified (list).
2. Exact commands run and their full output for: pytest -q, ruff check ., git log --oneline -5.
3. Any problem, assumption or deviation from the task.
