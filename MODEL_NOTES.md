# Reviewed model and environment notes

## Model revision

The optimizer and energy evaluator are a matched `review1_bugfix` pair, ported
from the reviewed implementation on 2026-09-08. Their numerical equations are
unchanged by this release. The public filenames and `build_model_full` interface
are retained. The mapping converter and dataflow enumerator supply dependencies
that the previous public scripts referenced but did not contain.

The reviewed equations distinguish receiver update traffic from source service
traffic. Reduction old-value reads use ungated active-path geometry, rather than
bypass-gated counts. Level-3 RF residency can span SRAM tiles: `chi3[d]` is true
when both walking axes are `d` and all orthogonal stage-1→2 factors are one.
Energy weights are indexed by receiver path. A fully unit stage-1→2 inherits
the stage-0→1 walking axis. See `derive_traffic_model`, `derive_path_energy_weights`
and `compute_normalized_total_energy` in `normalized_energy_model.py`, and the
corresponding constraints in `full_model.py`.

The non-convex MIQCP minimizes **dynamic energy in pJ/MAC**, including MAC compute
energy. Leakage is a separate reporting term; it is constant in this formulation
because all mappings use the fixed PE count. The model does not optimize EDP.
The Timeloop comparison uses total energy including leakage in pJ/MAC
(`pJ/compute` in the scripts).

## Solver settings and historical records

| Parameter | Current public entry points | Historical main experiment |
|---|---:|---:|
| NonConvex | 2 | 2 |
| IntegralityFocus | 1 | 1 |
| IntFeasTol | 1e-9 | 1e-9 |
| FeasibilityTol | 1e-9 | 1e-9 |
| MIPGap | 0 | 0 |
| NumericFocus | 3 | 0 |
| DualReductions | 0 | 1 |

`solver.py` applies the current settings to `main.py` and the pipeline.
Direct library callers can pass their own `params` to `build_model_full`.
The pipeline records effective settings and energy bounds in `run_record.json`.
Its Stage 3 timer covers model construction, optimization and reporting inside
`_solve_full_model`; ERT generation, Timeloop and postprocessing are excluded.
New timing measurements do not replace the saved paper Runtime benchmark.

Integer solutions are rounded only within a checked numerical tolerance;
factor products are checked against the original dimensions. A time limit
without a feasible incumbent is an error. A feasible non-optimal termination
retains its actual status and gap in the run record.

## Tested environment

Local checks used Python 3.12, Gurobi 13.0.0 and PyYAML 6.0.3, with:

| Component | Installed package version | Source HEAD |
|---|---|---|
| Timeloop | compiled executable | `4cf6d4cd043bc2a5d2eb02afa9063d7117a4dc11` |
| PyTimeloop | `pytimeloop 0.0.1` | `b8aaf2cde220df7c2ba9d051984fc360f20b57b2` |
| Accelergy | `0.4` | `4719207c2e0e40f33ecba72700f0d93f95f871bd` |
| CACTI / Aladdin plugins | `0.1` / `0.1` | Existing infrastructure installation |

These source trees contain local changes; the HEADs are identification records,
not a claim that pristine checkouts reproduce the complete installation. A fresh
infrastructure build was not performed for this release. Install the compiled
evaluation infrastructure separately and verify that the active Python can import
`pytimeloop.timeloopfe.v4`. Put its executables and estimators on PATH. The scripts
prepend the active Python environment's executable directory so that invoking an
absolute Python path also finds its `accelergy` console script.

For very large output tiles, Timeloop's partition-size intermediate multiplication
can overflow 64-bit `size_t`. The reviewed validation environment fixes
`ComputePartitionSizes` in `src/loop-analysis/tiling.cpp` by multiplying in
`unsigned __int128`, dividing there, checking the result against
`std::numeric_limits<std::size_t>::max()`, then converting back. The matching
Timeloop binaries must be rebuilt when applying that dependency fix. This public
repository does not vendor Timeloop or that infrastructure build.

Timeloop stats text precision also limits comparisons. The validator retains
full Python values and actual parsed errors, and uses a combined absolute/relative
tolerance. Tighter thresholds require sufficiently precise Timeloop output; they
cannot recover digits omitted from an existing stats file.

## Release checks

The release was exercised from a separate copy outside the infrastructure tree:

- Minimal optimizer example and independent formula agreement.
- Full single-layer pipeline on Eyeriss, Gemmini, TPU and A100, using the supplied
  problem; total analytical/Timeloop energies agree within the documented tolerance.
- First 16 bundled energy-flow cases: 16 matched.
- Five regression tests covering eight fixed-geometry/bypass combinations,
  degenerate walking axes, the published certificate mapping, integer extraction,
  missing incumbents and 1D/2D mesh conversion.

These are release checks, not a rerun of the paper's 192-instance benchmark or
12960-case model-fidelity study. Historical solver evidence is preserved.
