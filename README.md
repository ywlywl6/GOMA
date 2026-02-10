# GOMA: GEMM Mapping Framework

GOMA is a GEMM mapping framework for a series of spatial accelerators, adopting geometric abstraction and analytical modeling methods. It enables efficient searching for optimal mapping strategies.

### Requirements

1. **Dependencies and Environment**:
* Git version of `accelergy-timeloop-infrastructure`: `accelergy-timeloop-infrastructure`
* Python Version: `Python 3.12`
* Required dependency packages: `requirements.txt` (install via `pip install -r requirements.txt`)


2. **License Requirements**: In the testing environment of this repository, the code can run all instances without a Gurobi license.

### Installation Steps

1. Install the corresponding version of `accelergy-timeloop-infrastructure` following the official website:
* GitHub URL: [https://github.com/Accelergy-Project/accelergy-timeloop-infrastructure.git](https://github.com/Accelergy-Project/accelergy-timeloop-infrastructure.git)
* Version: `2cf1f23144d91118c7940ab921abdf2e491e277a`


2. Install Python dependencies:
```bash
pip install -r requirements.txt

```



---

## Directory and Code Overview

### Directory Description

* `architecture/`: Timeloop v4 architecture descriptions (`a100_like.yaml`, `eyeriss_like.yaml`, `gemmini_like.yaml`, `tpu_v1_like.yaml`).
* `inputs_my/`: Timeloop input collection (`arch.yaml / problem.yaml / mapping.yaml / mapper.yaml / variables.yaml` + `_components/` library).
* `layer_shapes/`: Workload collection (categorized by "model/context length", containing `*.yaml` and repetition count CSVs for layers under `transformer_block/` and `lm_head`).
* `templates/`: YAML templates for generation/patching (e.g., `problem_template.yaml`, `mapping_template.yaml`; mainly used during automatic input generation).

### Code File Description (Functional Overview + Usage)

> **Note**: The following commands are executed in the current directory by default (i.e., `cd timeloop-accelergy-exercises/workspace/my_designs/GOMA`).

* `normalized_energy_model.py` **(Core)**: Pure Python implementation of the normalized energy model (`pJ/compute`), used for rapid evaluation/verification.
    * **Usage**: Called as a library by scripts like `mapping_pipeline.py` (energy verification); can also be imported individually for rapid evaluation.


* `full_model.py` **(Core)**: The MILP/MIQCP mapping model body (Gurobi), exposing `build_model_full(cfg, params=...)`.
    * **Usage**: Called as a library by `main.py`/`mapping_pipeline.py`; usually not run directly.


* `main.py` **(Core)**: Minimal example entry point. Manually sets `L0/C1/C3/N_PE` and energy parameters, calls `full_model.build_model_full` to solve, and prints mapping parameters.
    * **Usage**: `python main.py` (requires `gurobipy` to be available).


* `run_mapper_any.py`: Runs `timeloop-mapper` once (Option A: must explicitly provide paths), suitable for quickly verifying a specific `arch/problem` combination.
    * **Usage**: `python run_mapper_any.py --arch <arch.yaml> --problem <problem.yaml> [--out <output_dir>]`
    * **Example**: `python run_mapper_any.py --arch architecture/a100_like.yaml --problem layer_shapes/Qwen3-32B_2k/transformer_block/01_attn_q_proj.yaml`


* `run_model_any.py`: Runs `timeloop-model` once (must explicitly provide `arch/problem`), and allows specifying `inputs-dir` (provides `mapping/variables/mapper/_components`).
    * **Usage**: `python run_model_any.py --arch <arch.yaml> --problem <problem.yaml> [--inputs-dir <inputs_dir>] [--out <output_dir>]`
    * **Example**: `python run_model_any.py --arch architecture/eyeriss_like.yaml --problem inputs_my/problem.yaml --inputs-dir inputs_my --out outputs_my`


* `mapping_pipeline.py`: End-to-end pipeline: Accelergy generates ERT → Parse arch/problem/ERT → Solve mapping via MILP → Write back to `inputs_my/mapping.yaml` → Call `timeloop-model` for evaluation.
    * **Usage**: `python mapping_pipeline.py [--inputs-dir inputs_my] [--outputs-dir outputs_my] [--arch-yaml ...] [--problem-yaml ...]`
    * **Common**: If ERT already exists, use `--ert-path <timeloop-model.ERT.yaml>` to skip the Accelergy stage; use `--generate-ert-only` to only generate the ERT.


* `inspect_spec.py`: Debug tool. Reads `top_model.jinja + outputs_my/timeloop-model.ERT.yaml` and prints architecture hierarchy, problem size, and ERT action energy.
    * **Usage**: First generate ERT using `run_model_any.py` (defaults output to `outputs_my/`), then run `python inspect_spec.py`.


* `validate_energy_flow.py`: Consistency validation pipeline. Calls Timeloop case-by-case for a batch of dataflows, comparing the energy parsed by Timeloop with the calculation results from `normalized_energy_model`.
    * **Usage**: `python validate_energy_flow.py [--tilings tilings_random.json] [--inputs-dir inputs_my] [--arch-yaml architecture/eyeriss_like.yaml] [--outdir outputs_validation]`


* `top_mapper.jinja`: Timeloop v4 top-level mapper template (Jinja), used by `run_mapper_any.py`; injects `arch/problem` via `jinja_parse_data`.
* `top_model.jinja`: Timeloop v4 top-level model template (Jinja), used by `run_model_any.py` / `mapping_pipeline.py`; supports `inputs_dir/arch/problem` injection.
* `tilings_random.json`: Random tiling/dataflow database (mainly used by `validate_energy_flow.py`); the `hardware.arch_yaml` field therein may retain old paths, but this does not affect other scripts.