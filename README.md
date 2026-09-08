# GOMA: GEMM Mapping Framework

GOMA searches GEMM mappings for spatial accelerators using a geometric abstraction
and a non-convex MIQCP, with an independent analytical energy model and
Timeloop/Accelergy evaluation.

[中文说明](README_zh.md) · [Model and environment notes](MODEL_NOTES.md) ·
[Paper solver evidence](solver_evidence/README.md)

## Contents

The repository provides the optimizer, independent energy evaluator, a single-layer
Accelergy → GOMA → Timeloop-model pipeline, dataflow validation, and four
architecture templates. The existing solver evidence contains the paper's 192
main EDP instances and an independently runnable exported model.

[layer_shapes/](layer_shapes/readme.md) contains 12 model/context configurations
for Llama 3.2-1B, Llama 3.3-70B, Qwen3-0.6B and Qwen3-32B, with GEMM shapes
and repetition counts. Both the bundled example and these workloads can be
passed to the single-layer pipeline.

## Requirements

- Python 3.12; install the Python-only dependencies with
  `python -m pip install -r requirements.txt`.
- For the pipeline and validation: an installed Timeloop/Accelergy infrastructure
  with `pytimeloop.timeloopfe.v4`, `timeloop-model`, `timeloop-mapper`, `accelergy`,
  and the CACTI/Aladdin estimators used by the supplied components.
  `requirements.txt` does not install these compiled tools.
- Gurobi 13.0.0 with a usable license configuration.

See [MODEL_NOTES.md](MODEL_NOTES.md) for environment versions and numerical settings.

## Quick start

Run these commands from this directory, with the evaluation environment active:

```bash
python normalized_energy_model.py
python main.py
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --outputs-dir outputs_eyeriss
python validate_energy_flow.py --limit 16 --outdir outputs_validation_smoke
python -m unittest discover -s tests -v
```

`main.py` needs only Gurobi and the local model files; its independently evaluated
dynamic energy is checked against the optimization objective. The other three
architectures can use the same supplied problem with `--arch-yaml
architecture/{gemmini_like,tpu_v1_like,a100_like}.yaml` (choose one filename per run).
Use a separate output directory for each run/configuration.

For a supplied LLM GEMM:

```bash
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --problem-yaml layer_shapes/Llama_3.2-1B_1k/transformer_block/01_attn_q_proj.yaml --outputs-dir outputs_llama_q_proj
```

The pipeline writes `mapping.yaml`, `stage3_timing.json`, `run_record.json`, ERT/ART
and Timeloop outputs under `--outputs-dir`. It leaves source inputs unchanged.
`run_record.json` records model hashes, parsed configuration, input hashes,
solver parameters/status/bounds/gap, timing, and the Python/Timeloop energy comparison.
Energy-check failures produce a nonzero exit status.

By default the pipeline generates ERT/ART anew. To generate tables alone or
explicitly reuse tables from the **same architecture and component configuration**:

```bash
python mapping_pipeline.py --generate-ert-only --arch-yaml architecture/eyeriss_like.yaml --outputs-dir outputs_ert
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --ert-path outputs_ert/timeloop-model.ERT.yaml --outputs-dir outputs_reuse
```

Keep the matching `timeloop-model.ART.yaml` beside the ERT. `--force-regenerate-ert`
is retained for compatibility; regeneration is already the default.
`--mip-gap` overrides the default relative solver gap of zero.
`--skip-python-energy-check` skips both formula comparisons.

## Tools and files

| Entry | Purpose |
|---|---|
| `full_model.py` | Optimizer; exposes `build_model_full(cfg, params=...)` and its eight-element return tuple |
| `normalized_energy_model.py` | Independent dynamic/total energy and traffic evaluator |
| `solver.py` | Shared current solver settings, incumbent checks and integer mapping extraction |
| `mapping_pipeline.py` | Single-layer optimization and Timeloop evaluation |
| `gen_problem_mapping.py` | Problem/mapping conversion, including two-dimensional PE meshes |
| `dataflow_gen.py`, `tilings_random.json` | Existing random-tiling database and dataflow enumeration |
| `validate_energy_flow.py` | Python/Timeloop energy comparison for the bundled Eyeriss dataflows |
| `run_model_any.py`, `run_mapper_any.py` | Standalone Timeloop model/mapper utilities |
| `inspect_spec.py` | Inspect default architecture/problem and the ERT in `outputs_my/` |
| `architecture/`, `inputs_my/`, `templates/` | Architecture descriptions, example inputs, components and YAML templates |

Replay a generated mapping, or invoke Timeloop's mapper:

```bash
python run_model_any.py --arch architecture/eyeriss_like.yaml --problem inputs_my/problem.yaml --mapping outputs_eyeriss/mapping.yaml --out outputs_replay
python run_mapper_any.py --arch architecture/eyeriss_like.yaml --problem inputs_my/problem.yaml --out outputs_mapper
```

The mapper utility reads its search settings from `inputs_my/mapper.yaml`.
Paper experiment settings are documented with the corresponding experiment records.
For these two utilities, explicit arch/problem/mapping paths resolve against the
working directory; relative `--out` and `--inputs-dir` resolve against the script
directory. Pipeline and validator explicit paths resolve against the working
directory; their defaults are relative to the script directory.

## Validation and solver evidence

Validation copies inputs under its output directory, reuses one ERT/ART pair,
preserves measured errors in CSV/JSONL, and fails if any selected case fails.
The criterion is `abs_err <= 1e-5 + 1e-6 * max(abs(E_python), abs(E_timeloop))`,
with energy in pJ/MAC including leakage. `--atol` and `--rtol` override these values.
Use a new, empty output directory; `--start` and `--limit` select a subset.
Without `--limit`, the bundled database expands to 8064 Timeloop evaluations.
A different architecture requires a compatible tiling database with matching PE count.

[solver_evidence/](solver_evidence/README.md) provides the experiment parameters,
solver records and an exported model. Run the standalone example with:

```bash
python solver_evidence/reproduce_example.py --output-dir outputs_solver_example
```
