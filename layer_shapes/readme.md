# LLM GEMM workloads

Timeloop problem descriptions for four models at three context lengths each:

| Model | Context configurations |
|---|---|
| Llama 3.2-1B | 1k, 8k, 32k |
| Llama 3.3-70B | 2k, 32k, 128k |
| Qwen3-0.6B | 1k, 8k, 32k |
| Qwen3-32B | 2k, 32k, 128k |

Each configuration contains:

- `transformer_block/01_*.yaml` through `07_*.yaml`: seven GEMM types for attention and MLP operations.
- `transformer_block/transformer_block_workload_list.csv`: operation order and repetition counts within a block.
- `transformer_block_count.csv`: number of transformer blocks.
- `lm_head.yaml`: output projection GEMM.

`matrix_base.yaml` is the base problem description. The `merged_*` files provide
companion workload descriptions. YAML and CSV files are the executable inputs.

From the GOMA directory, run one GEMM with:

```bash
python mapping_pipeline.py --arch-yaml architecture/eyeriss_like.yaml --problem-yaml layer_shapes/Llama_3.2-1B_1k/transformer_block/01_attn_q_proj.yaml --outputs-dir outputs_llama_q_proj
```

The single-layer pipeline evaluates the selected YAML once. Repetition counts
are supplied separately for workload-level aggregation.
