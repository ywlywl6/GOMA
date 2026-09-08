# GOMA：GEMM 映射框架

GOMA 采用几何抽象与非凸 MIQCP 搜索 GEMM 映射，配套独立能量模型和
单层 Accelergy → GOMA → Timeloop-model 评估流程。

[English](README.md) · [模型与环境说明](MODEL_NOTES.md) ·
[论文求解器附件](solver_evidence/README.md)

## 仓库内容

包含核心优化器、独立能量模型、单层 pipeline、数据流验证、四种架构模板和
求解器附件。[layer_shapes/](layer_shapes/readme.md) 提供 Llama 3.2-1B、
Llama 3.3-70B、Qwen3-0.6B 和 Qwen3-32B 的 12 组模型/上下文配置，
包含 GEMM 形状及重复次数，可通过 `--problem-yaml` 指定其中的单层文件。

## 环境

- Python 3.12；用 `python -m pip install -r requirements.txt` 安装 Python 核心依赖。
- pipeline 与验证还需要安装好的 Timeloop/Accelergy 基础设施，包含
  `pytimeloop.timeloopfe.v4`、`timeloop-model`、`timeloop-mapper`、`accelergy`
  和示例组件使用的 CACTI/Aladdin 估算插件。`requirements.txt` 不安装这些编译工具。
- Gurobi 13.0.0 和可用的许可配置。

环境版本与数值设置见 [MODEL_NOTES.md](MODEL_NOTES.md)。

## 最短运行路径

在本目录执行，使用已经配置好评估工具的 Python 环境：

```bash
python normalized_energy_model.py
python main.py
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --outputs-dir outputs_eyeriss
python validate_energy_flow.py --limit 16 --outdir outputs_validation_smoke
python -m unittest discover -s tests -v
```

`main.py` 仅依赖 Gurobi 和本地模型代码，会比较求解目标与独立动态能量公式。
单层 pipeline 也支持 `gemmini_like.yaml`、`tpu_v1_like.yaml`、`a100_like.yaml`，
替换 `--arch-yaml` 即可；各配置使用独立输出目录。

运行随附的 LLM GEMM：

```bash
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --problem-yaml layer_shapes/Llama_3.2-1B_1k/transformer_block/01_attn_q_proj.yaml --outputs-dir outputs_llama_q_proj
```

pipeline 将生成的 `mapping.yaml`、`stage3_timing.json`、`run_record.json`、ERT/ART
和 Timeloop 输出写到 `--outputs-dir`，不覆盖源输入。运行记录包含核心代码与输入
哈希、解析后的配置、求解参数、状态、上下界、gap、计时和 Python/Timeloop 能量
比较；能量检查失败会返回非零退出码。

默认重新生成 ERT/ART。仅生成表或显式复用**相同架构与组件配置**的表：

```bash
python mapping_pipeline.py --generate-ert-only --arch-yaml architecture/eyeriss_like.yaml --outputs-dir outputs_ert
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --ert-path outputs_ert/timeloop-model.ERT.yaml --outputs-dir outputs_reuse
```

配套 `timeloop-model.ART.yaml` 应与 ERT 放在一起。`--force-regenerate-ert` 为兼容
旧入口保留，当前默认已经重新生成。`--mip-gap` 可覆盖默认的零相对 gap；
`--skip-python-energy-check` 会跳过独立公式与优化目标、Timeloop 的两项比较。

## 文件和工具

| 文件 | 职责 |
|---|---|
| `full_model.py` | 优化器，提供 `build_model_full(cfg, params=...)` 和八元素返回接口 |
| `normalized_energy_model.py` | 配套流量及动态/总能量公式 |
| `solver.py` | 统一求解设置、可行解检查和整数 mapping 提取 |
| `mapping_pipeline.py` | 单层求解与 Timeloop 评估 |
| `gen_problem_mapping.py` | problem/mapping 转换，支持二维 PE mesh |
| `dataflow_gen.py`、`tilings_random.json` | 原有随机 tiling 数据库与数据流枚举 |
| `validate_energy_flow.py` | 已有 Eyeriss 数据流的 Python/Timeloop 能量验证 |
| `run_model_any.py`、`run_mapper_any.py` | 独立 Timeloop model/mapper 工具 |
| `inspect_spec.py` | 查看默认架构、problem 及 `outputs_my/` 下的 ERT |
| `architecture/`、`inputs_my/`、`templates/` | 架构、示例输入、组件与模板 |

重放生成的 mapping，或运行 Timeloop mapper：

```bash
python run_model_any.py --arch architecture/eyeriss_like.yaml --problem inputs_my/problem.yaml --mapping outputs_eyeriss/mapping.yaml --out outputs_replay
python run_mapper_any.py --arch architecture/eyeriss_like.yaml --problem inputs_my/problem.yaml --out outputs_mapper
```

mapper 工具从 `inputs_my/mapper.yaml` 读取搜索设置；论文实验参数见对应实验记录。
这两个工具的显式 arch/problem/mapping 相对路径按工作目录解析，`--out` 和
`--inputs-dir` 的相对路径按脚本目录解析。pipeline 与验证器的显式路径按工作目录
解析，默认路径按脚本目录解析。

## 验证与求解器附件

验证器在输出目录复制输入、复用一份 ERT/ART，以 CSV/JSONL 保存实际误差；任一
用例失败则返回非零退出码。默认比较含泄漏能量（pJ/MAC），判据为：

```text
abs_err <= 1e-5 + 1e-6 * max(abs(E_python), abs(E_timeloop))
```

`--atol`、`--rtol` 可覆盖容差；使用新的空输出目录，`--start`、`--limit` 选择
子集。不指定 `--limit` 将展开 8064 例 Timeloop 评估。切换架构时需要提供 PE 数
匹配的 tiling 数据库。

[求解器附件](solver_evidence/README.md) 提供实验参数、求解记录和导出的模型，
可独立运行：

```bash
python solver_evidence/reproduce_example.py --output-dir outputs_solver_example
```
