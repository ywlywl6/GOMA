#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("outputs_my")
DEFAULT_INPUTS_DIR = Path("inputs_my")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="运行一次 timeloop-model（用户显式指定 arch/problem 路径）",
        epilog=(
            "示例：\n"
            "  python run_model_any.py --arch architecture/a100_like.yaml "
            "--problem layer_shapes/Qwen3-32B_2k/transformer_block/01_attn_q_proj.yaml\n"
            "  python run_model_any.py --arch /abs/path/arch.yaml --problem /abs/path/problem.yaml --out /tmp/out\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--arch", type=Path, required=True, help="architecture YAML 文件路径（可为相对路径）")
    p.add_argument("--problem", type=Path, required=True, help="problem YAML 文件路径（可为相对路径）")
    p.add_argument(
        "--inputs-dir",
        type=Path,
        default=DEFAULT_INPUTS_DIR,
        help="inputs 目录（含 mapping.yaml/mapper.yaml/variables.yaml/_components 等；相对路径按脚本目录解析）",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="输出目录（相对路径将按脚本所在目录解析，默认：outputs_my）",
    )
    return p.parse_args()


def _resolve_under_here(p: Path, *, here: Path) -> Path:
    p = Path(os.path.expanduser(str(p)))
    if p.is_absolute():
        return p.resolve()
    return (here / p).resolve()


def main() -> None:
    here = Path(__file__).resolve().parent
    args = parse_args()
    import pytimeloop.timeloopfe.v4 as tl

    top = here / "top_model.jinja"
    if not top.is_file():
        raise FileNotFoundError(f"未找到 top_model.jinja：{top}")

    arch_path = args.arch.expanduser().resolve()
    problem_path = args.problem.expanduser().resolve()
    out_dir = _resolve_under_here(args.out, here=here)

    if not arch_path.is_file():
        raise FileNotFoundError(f"未找到 arch 文件：{arch_path}")
    if not problem_path.is_file():
        raise FileNotFoundError(f"未找到 problem 文件：{problem_path}")

    start_time = time.perf_counter()

    inputs_dir = _resolve_under_here(args.inputs_dir, here=here)
    if not inputs_dir.is_dir():
        raise FileNotFoundError(f"未找到默认 inputs 目录：{inputs_dir}")
    mapping_yaml = inputs_dir / "mapping.yaml"
    if not mapping_yaml.is_file():
        raise FileNotFoundError(f"未找到默认 mapping.yaml：{mapping_yaml}")

    spec = tl.Specification.from_yaml_files(
        str(top),
        jinja_parse_data={
            "arch": str(arch_path),
            "problem": str(problem_path),
            # 固定 mapping/variables/mapper 等 inputs 均来自该目录
            "inputs_dir": str(inputs_dir),
        },
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    tl.call_model(spec, output_dir=str(out_dir))

    elapsed = time.perf_counter() - start_time
    if elapsed < 60:
        print(f"Total runtime: {elapsed:.2f} s")
    else:
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(int(minutes), 60)
        if hours:
            print(f"Total runtime: {hours:d}h {minutes:d}m {seconds:.1f}s")
        else:
            print(f"Total runtime: {minutes:d}m {seconds:.1f}s")


if __name__ == "__main__":
    main()
