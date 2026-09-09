# Provenance and scope

The batch extension code in `goma_batch/` and the new formalization are newly
written for this task. They integrate, rather than replace, the GOMA models.

`tests/upstream/normalized_energy_model.py` is an unmodified snapshot of the
GOMA main-root analytical evaluator at commit
`b4015e465d78a8dbeb25ec9220cfe7c34883a865`.
Its original MIT license, Copyright (c) 2026 Yang Wulve, is retained next to it.
The corresponding optimizer is loaded from the explicitly selected GOMA root,
not included as a reconstructed substitute.

`tests/legacy_reference.py` is the small reference enumerator delivered with the
preceding GOMA batch theoretical extension in this conversation. An observer
hook was added to compare every generated mapping with the exact upstream
analytical evaluator. It is test-only code, not a production GOMA oracle.

`results/` contains actual validation records generated for this artifact.
All allocation-oracle runs in these records use a test-only exact catalogue.
The SciPy/HiGHS master and original analytical evaluator were actually executed.
No Gurobi/Timeloop execution or large end-to-end benchmark is claimed.
