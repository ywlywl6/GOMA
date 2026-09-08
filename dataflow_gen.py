# -*- coding: utf-8 -*-
"""
问题 & 数据流描述生成器（第二版）
---------------------------------

根据《Timeloop 一致性验证—实验设计方案》定义：
  1. 读取预先生成的分块方案（tilings_random.json）。
  2. 枚举 Problem Shape × Iteration Axes × Level Bypass × Tiling Scheme。
  3. 输出完整的数据结构，供后续转换为 Timeloop 约束输入。

运行示例：
    python dataflow_gen.py --output workloads.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Tuple

AXES = ("x", "y", "z")
LEVEL_NAMES = {
    0: "DDR",
    1: "SRAM",
    2: "PE_ARRAY",
    3: "REGFILE",
    4: "MACC",
}

AxisOrder = Tuple[str, str]  # (alpha01, alpha12)
Vec = Mapping[str, int]
MutableVec = MutableMapping[str, int]

# 9 组行走轴（α01, α12）
AXIS_ORDERS: List[AxisOrder] = [(a01, a12) for a01 in "xyz" for a12 in "xyz"]

# 16 组 Level Bypass 方案：表格里的 x(B1,B3)/y(B1,B3)/z(B1,B3)
B_COMBOS: Dict[str, Dict[str, Tuple[int, int]]] = {
    "B1":  {"x": (1, 1), "y": (1, 1), "z": (1, 1)},  # All-Reside
    "B2":  {"x": (0, 0), "y": (0, 0), "z": (0, 0)},  # All-Bypass
    "B3":  {"x": (1, 1), "y": (0, 1), "z": (1, 1)},  # B 仅 RF
    "B4":  {"x": (1, 1), "y": (1, 0), "z": (1, 1)},  # B 仅 SRAM
    "B5":  {"x": (1, 1), "y": (1, 1), "z": (0, 1)},  # C 仅 RF
    "B6":  {"x": (1, 1), "y": (1, 1), "z": (1, 0)},  # C 仅 SRAM
    "B7":  {"x": (0, 1), "y": (1, 1), "z": (1, 1)},  # A 仅 RF
    "B8":  {"x": (1, 0), "y": (1, 1), "z": (1, 1)},  # A 仅 SRAM
    "B9":  {"x": (1, 0), "y": (1, 0), "z": (1, 1)},  # A/B SRAM；C RF+MAC
    "B10": {"x": (0, 1), "y": (0, 1), "z": (0, 1)},  # A/B/C 仅 RF
    "B11": {"x": (1, 0), "y": (1, 0), "z": (0, 1)},  # A/B SRAM；C RF
    "B12": {"x": (1, 0), "y": (0, 0), "z": (1, 0)},  # A/C SRAM；B 流式
    "B13": {"x": (0, 0), "y": (1, 0), "z": (1, 0)},  # B/C SRAM；A 流式
    "B14": {"x": (0, 0), "y": (0, 0), "z": (0, 1)},  # 仅 C RF
    "B15": {"x": (0, 0), "y": (0, 1), "z": (0, 0)},  # 仅 B RF
    "B16": {"x": (0, 1), "y": (0, 0), "z": (0, 0)},  # 仅 A RF
}


@dataclass(frozen=True)
class TilingDB:
    seed: int
    pe_num: int
    shapes: Dict[str, Dict[str, int]]
    tilings: Dict[str, List[Dict[str, Dict[str, int]]]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Problem & Dataflow descriptions.")
    parser.add_argument(
        "--tilings",
        default=str(Path(__file__).resolve().parent / "tilings_random.json"),
        help="Path to tilings_random.json (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        help="Optional output path. Print to stdout when omitted.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation width (default: %(default)s).",
    )
    return parser.parse_args()


def load_tiling_db(path: Path) -> TilingDB:
    data = json.loads(path.read_text(encoding="utf-8"))
    required_keys = {"seed", "pe_num", "shapes", "tilings"}
    missing = required_keys - data.keys()
    if missing:
        raise KeyError(f"{path} 缺少字段: {', '.join(sorted(missing))}")

    shapes = data["shapes"]
    tilings = data["tilings"]
    for shape_id, entries in tilings.items():
        if shape_id not in shapes:
            raise ValueError(f"tilings 中的 {shape_id} 未在 shapes 定义")
        for idx, entry in enumerate(entries):
            _validate_tiling_entry(shape_id, idx, entry, shapes[shape_id], data["pe_num"])

    return TilingDB(
        seed=data["seed"],
        pe_num=data["pe_num"],
        shapes=shapes,
        tilings=tilings,
    )


def _validate_tiling_entry(
    shape_id: str,
    idx: int,
    entry: Mapping[str, Mapping[str, int]],
    expected_shape: Mapping[str, int],
    pe_num: int,
) -> None:
    required = {"L0", "hatL_01", "hatL_12", "hatL_23", "hatL_34"}
    missing = required - entry.keys()
    if missing:
        raise KeyError(f"{shape_id}[{idx}] 缺少字段: {', '.join(sorted(missing))}")

    if dict(entry["L0"]) != dict(expected_shape):
        raise ValueError(f"{shape_id}[{idx}] 的 L0 与 shapes 不一致: {entry['L0']} vs {expected_shape}")

    prev = entry["L0"]
    for hat_name in ("hatL_01", "hatL_12", "hatL_23", "hatL_34"):
        hat = entry[hat_name]
        for axis in AXES:
            if prev[axis] % hat[axis] != 0:
                raise ValueError(f"{shape_id}[{idx}] {axis} 轴 {hat_name} 不整除: {prev[axis]} % {hat[axis]}")
        prev = {axis: prev[axis] // hat[axis] for axis in AXES}

    parallelism = 1
    for axis in AXES:
        parallelism *= entry["hatL_23"][axis]
    if parallelism != pe_num:
        raise ValueError(
            f"{shape_id}[{idx}] 并行层约束不满足: hatL_23 xyz 乘积={parallelism} ≠ pe_num={pe_num}"
        )


def expand_bypass(combo_key: str) -> Dict[str, Dict[str, int]]:
    pair = B_COMBOS[combo_key]
    levels: Dict[str, Dict[str, int]] = {}
    for level_idx, level_name in LEVEL_NAMES.items():
        if level_idx in (0, 2, 4):
            levels[level_name] = {axis: 1 for axis in AXES}
        elif level_idx == 1:
            levels[level_name] = {axis: pair[axis][0] for axis in AXES}
        else:  # level_idx == 3
            levels[level_name] = {axis: pair[axis][1] for axis in AXES}
    return levels


def derive_levels(entry: Mapping[str, Mapping[str, int]]) -> Dict[str, Dict[str, int]]:
    """根据 hatL_0i 连续整除关系推导 L1/L2/L3/L4。"""
    levels: Dict[str, Dict[str, int]] = {"L0": dict(entry["L0"])}
    prev = entry["L0"]
    for idx, hat_name in enumerate(("hatL_01", "hatL_12", "hatL_23", "hatL_34"), start=1):
        hat = entry[hat_name]
        level = {axis: prev[axis] // hat[axis] for axis in AXES}
        levels[f"L{idx}"] = level
        prev = level
    return levels


def iter_workloads(
    db: TilingDB,
    axis_orders: Iterable[AxisOrder],
    bypass_keys: Iterable[str],
) -> Iterator[Dict[str, object]]:
    for shape_id, tiling_list in db.tilings.items():
        shape = db.shapes[shape_id]
        for alpha01_alpha12, bypass_key in product(axis_orders, bypass_keys):
            alpha01, alpha12 = alpha01_alpha12
            bypass = {
                "key": bypass_key,
                "levels": expand_bypass(bypass_key),
            }
            for tiling_idx, tiling_entry in enumerate(tiling_list):
                yield {
                    "problem_id": shape_id,
                    "problem_shape": shape,
                    "iteration_axes": {"alpha01": alpha01, "alpha12": alpha12},
                    "level_bypass": bypass,
                    "tiling": {
                        "id": tiling_idx,
                        "scheme": tiling_entry,
                        "derived_levels": derive_levels(tiling_entry),
                    },
                }


def main() -> None:
    args = parse_args()
    tilings_path = Path(args.tilings)
    db = load_tiling_db(tilings_path)

    axis_orders = AXIS_ORDERS
    bypass_keys = list(B_COMBOS.keys())

    workloads = list(iter_workloads(db, axis_orders, bypass_keys))
    metadata = {
        "tilings_file": str(tilings_path),
        "seed": db.seed,
        "pe_num": db.pe_num,
        "problems": sorted(db.shapes.keys()),
        "axis_orders": [{"alpha01": a01, "alpha12": a12} for a01, a12 in axis_orders],
        "bypass_keys": bypass_keys,
        "total_workloads": len(workloads),
        "components": {
            "problem_shapes": len(db.shapes),
            "iteration_axes": len(axis_orders),
            "level_bypass": len(bypass_keys),
            "tilings_per_shape": {k: len(v) for k, v in db.tilings.items()},
        },
    }

    payload = {"metadata": metadata, "workloads": workloads}
    text = json.dumps(payload, ensure_ascii=False, indent=args.indent)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
