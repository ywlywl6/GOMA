# GOMA batch-level optimization

See [README_zh.md](README_zh.md) for the implementation, model scope, source lock,
installation, command-line usage, and the exact validation status.

The default is adaptive configuration generation with complete lower-bound
coverage and optional direct joint-MIQCP probes. A standalone direct joint
backend is also supplied. Both reuse the user's unmodified root GOMA models at
commit `b4015e465d78a8dbeb25ec9220cfe7c34883a865`.

**Validation update (2026-09-08):** all 36 tests, including the six real-Gurobi
integration tests, passed on Windows Python 3.12 / Gurobi 12.0.3. The original
artifact-generation environment had skipped those six tests. See the
[local benchmark report](results/local_speed_20260908/REPORT_zh.md) for the
three-method synthetic-workload comparison and its validation limits.
