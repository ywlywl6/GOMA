# Solver formulation, scalability, and optimality evidence

This directory provides supplementary solver records for the GOMA paper:
model sizes, the formulation passed to Gurobi, and solver-level optimality
certificates. The supplied example can be reproduced directly from the exported
model using the command below.

The root-level implementation is now synchronized with the reviewed equations.
Its current solve settings differ from the historical settings below; see
[model notes](../MODEL_NOTES.md). The supplied records remain unchanged.

## Evaluation records

The tables contain the **192 GEMM mapping instances in the main EDP experiment**:
24 architecture–workload cases, with eight GEMM types per case.

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
the Stage 3 function. The paper's Runtime figure uses a separate timing benchmark.

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
| [practical_case_current_model.lp](practical_case_current_model.lp) | Exported example MIQCP, including quadratic and indicator constraints |
| [practical_case_current_solution.sol](practical_case_current_solution.sol) | Full variable assignment from a separate example rerun |
| [practical_case_current_solver.log](practical_case_current_solver.log) | Gurobi log of that rerun |
| [practical_case_current_solver_record.json](practical_case_current_solver_record.json) | Rerun inputs, version, parameters, full-precision solver attributes, and mapping variables |

The standalone rerun uses the experiment settings above with a 60-second time
limit. It returned `OPTIMAL` with `MIPGap=0`. The original experiment and the
standalone rerun each include their own mapping and solver record.

These records demonstrate the solver-level optimality certificate: a feasible
mapping, solver status, incumbent upper bound, global lower bound, and final
gap, for the modeled problem under the solver's numerical tolerances.

## Solve the exported model

Use Python 3.12 and Gurobi 13.0.0 (`gurobipy`) to match the recorded version:

```bash
# From the GOMA repository root:
python solver_evidence/reproduce_example.py
```

The script reads the exported MIQCP and the recorded parameters, solves it,
and writes a new log, solution, and JSON record to `solver_evidence/rerun/`.
It leaves the supplied artifacts unchanged. To choose another output directory:

```bash
python solver_evidence/reproduce_example.py --output-dir /tmp/goma-solver-example
```

The script solves the supplied formulation using only files in this directory.
Runtime and the mapping selected among equal-energy optima may vary across runs.
