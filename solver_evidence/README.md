# Solver formulation, scalability, and optimality evidence

This directory contains supplementary solver records for the GOMA paper's
`review1_bugfix` model. It supports the discussion of model size, the formulation
passed to Gurobi, and concrete solver-level optimality certificates.

**Code version:** these artifacts correspond to `review1_bugfix`. The existing
`full_model.py`, `normalized_energy_model.py`, and `mapping_pipeline.py` at the
repository root have not yet been synchronized with that revision. The exported
model in this directory can be solved directly with Gurobi using the command
below; it does not depend on those root-level implementations.

## Evaluation records

The tables contain the **192 GEMM mapping instances in the main EDP experiment**:
24 architecture–workload cases, with eight GEMM types per case. They contain only
that experiment, rather than combining it with fixed-bypass or runtime reruns.

| File | Contents |
|---|---|
| [solver_instances.csv](solver_instances.csv) | Instance inputs, original-model size, solver status, UB/LB, final gap, nodes, timings, and parameters |
| [solver_cases.csv](solver_cases.csv) | Summary for each of the 24 evaluation cases |
| [summary.json](summary.json) | Aggregate solver statistics for the 192 instances |
| [model_variables.csv](model_variables.csv) | Names, types, and bounds of the 143 variables in the exported example model |

Every recorded instance returned Gurobi `OPTIMAL` (`status=2`) with a final
printed gap of `0.0000%`. Before presolve, each main-experiment model contains
29 general integer variables, 47 binary variables, and 67 continuous variables,
with 193 linear constraints, 44 quadratic constraints, and 26 indicator
constraints. `integers_including_binary=76` already includes the 47 binaries.
The model is a non-convex MIQCP with a linear objective.

`UB_log` and `LB_log` preserve the values printed in the original solver logs.
The objective is **normalized dynamic energy in pJ/MAC**, including MACC energy
and excluding leakage. `gap_percent_log` is in percent; the Gurobi API's
`MIPGap` attribute is a ratio. The bounds are energy bounds, not EDP values.

The main experiment used Gurobi 13.0.0 with:

| Parameter | Value |
|---|---:|
| NonConvex | 2 |
| IntegralityFocus | 1 |
| IntFeasTol | 1e-9 |
| FeasibilityTol | 1e-9 |
| MIPGap | 0 |
| NumericFocus | 0 (default) |
| DualReductions | 1 (default) |

The CSV expands the last two defaults to their numeric values.
`solver_seconds_log` is the solver wall time printed in the EDP experiment log;
`stage3_seconds_log` includes model construction, solving, and reporting inside
the Stage 3 function. These are **EDP-experiment timings**, separate from the
local timing snapshot used to produce the paper's normalized Runtime figure.

## Concrete optimality certificate

The example is the Q projection of Qwen3-32B at 128k context on A100-like:

- `(X,Y,Z) = (131072,8192,5120)`.
- SRAM capacity: 37,748,736 words; RF capacity: 128 words per PE.
- PE count: 65,536.
- Normalized dynamic energy: approximately `0.2680331589774 pJ/MAC`.

| File | Contents |
|---|---|
| [practical_case_from_logs.json](practical_case_from_logs.json) | The original EDP example's inputs, mapping, solver fields, and analytical energy components |
| [practical_case_original.log](practical_case_original.log) | Its original end-to-end EDP experiment log, including `Optimal solution found`, UB/LB, gap, and mapping |
| [practical_case_current_model.lp](practical_case_current_model.lp) | Exported `review1_bugfix` MIQCP; the LP file format includes quadratic and indicator constraints |
| [practical_case_current_solution.sol](practical_case_current_solution.sol) | Full variable assignment from a separate example rerun |
| [practical_case_current_solver.log](practical_case_current_solver.log) | Gurobi log of that rerun |
| [practical_case_current_solver_record.json](practical_case_current_solver_record.json) | Rerun inputs, version, parameters, full-precision solver attributes, and mapping variables |

The rerun uses `NumericFocus=0` and `DualReductions=1`, matching the original
experiment settings above. It additionally has a 60-second time limit for this
standalone example. It returned `OPTIMAL` with `MIPGap=0`, explored 15,553 nodes,
and reproduced the mapping in the original local Runtime log for this instance.
The original EDP run returned another mapping with the same energy. Each log
and its mapping are therefore presented as one complete record. The rerun's
wall time is a new measurement, not a replacement for the paper's Runtime data.

These records demonstrate the solver-level optimality certificate: a feasible
mapping, solver status, incumbent upper bound, global lower bound, and final
gap, for the modeled problem under the solver's numerical tolerances.

## Solve the exported model

Use Python 3.12 and Gurobi 13.0.0 (`gurobipy`) to match the recorded version:

```bash
# From the repository root:
python solver_evidence/reproduce_example.py
```

The script reads the exported MIQCP and the recorded parameters, solves it,
and writes a new log, solution, and JSON record to `solver_evidence/rerun/`.
It leaves the supplied artifacts unchanged. To choose another output directory:

```bash
python solver_evidence/reproduce_example.py --output-dir /tmp/goma-solver-example
```

This directly exercises the exported formulation; it does not regenerate the
model through the older root-level pipeline. Runtime and the particular mapping
selected among equal-energy optima can depend on the execution environment.

## Provenance

The evaluation tables were prepared from the original
`outputs_mapping_pipeline/review1_bugfix/edp_evaluation/` logs in the paper
experiment workspace. The example model and rerun artifacts were prepared on
2026-09-05 using `goma/variants/review1_bugfix/optimizer.py`. The original example
log retains the source experiment's paths; the reproduction script uses only
files shipped in this directory.
