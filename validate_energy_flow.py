#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
能量模型一致性验证脚本（新增，保持现有文件不变）
------------------------------------------------

功能：
  1) 基于 tilings_random.json 生成 8064 组数据流（调用现有 dataflow_gen 逻辑）。
  2) 为每一组数据流从模板生成 Timeloop 的 problem.yaml 与 mapping.yaml。
  3) 参考 run_model.py 的方式调用 Timeloop 仿真，解析输出拿到归一化能量（pJ/compute）。
  4) 调用自研 normalized_energy_model 计算归一化能量（pJ/compute）。
  5) 对比两者，输出汇总 CSV/JSON 日志。

注意：
  - 禁止修改现有文件；本脚本仅新增，并尽量复用/拷贝已有模块的函数实现。
  - 运行时按顺序复用 inputs_my/problem.yaml 与 inputs_my/mapping.yaml（逐例覆盖），
    但每个用例的 Timeloop 输出会写到独立的输出目录，避免相互覆盖。

用法示例：
  python validate_energy_flow.py \
    --tilings tilings_random.json \
    --outdir outputs_validation \
    --limit 100   # 可选，调试时先跑前 100 例

依赖：需在虚拟环境中安装 pytimeloop，本仓库已有 .venv 可用。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Tuple, Optional

# 引用本仓库内的 pytimeloop
import pytimeloop.timeloopfe.v4 as tl

# 解析 YAML
import yaml

# 解析 Timeloopfe v4 架构节点（与 mapping_pipeline.py 同口径）
from pytimeloop.timeloopfe.v4.arch import Storage, Container

# 复用现有模块：数据流生成 & 模板补丁
from dataflow_gen import load_tiling_db, AXIS_ORDERS, B_COMBOS, expand_bypass
from gen_problem_mapping import (
    copy_template,
    update_problem_file,
    update_mapping_file,
)

# 自研能量模型
from normalized_energy_model import (
    DeviceParams,
    compute_normalized_total_energy,
)

# 读取 Timeloop stats（pytimeloop 的解析工具）
from pytimeloop.timeloopfe.v4.output_parsing import parse_stats_file


AXES = ("x", "y", "z")
AXES_UP = {"x": "X", "y": "Y", "z": "Z"}
DEFAULT_ARCH_YAML = Path(__file__).resolve().parent / "architecture" / "eyeriss_like.yaml"


def _load_arch_params_from_spec(
    top_model: Path,
    *,
    jinja_parse_data: Dict[str, str],
) -> Tuple[int, int, int, Dict[str, Tuple[int, int]], int, int]:
    """
    参考 mapping_pipeline._load_problem_arch_from_spec 的实现，使用 timeloopfe v4 的 Specification
    统一解析架构参数，避免直接 yaml.safe_load 解析 !Container/!Component 带来的标签问题。

    返回：
      - C1_words: shared_glb.depth * shared_glb.width / datawidth
      - C3_words: regfile.depth * regfile.width / datawidth
      - N_PE: meshX * meshY（兼容 PE/PEy 分拆写法）
      - storage_width_datawidth: {storage_name: (width, datawidth)}，用于 ERT 能量按 word 口径换算
      - mesh_x, mesh_y: 2D mesh 的两个维度
    """
    spec = tl.Specification.from_yaml_files(str(top_model), jinja_parse_data=jinja_parse_data)

    # Architecture → Storage params / C1 / C3
    C1 = C3 = None
    storage_width_datawidth: Dict[str, Tuple[int, int]] = {}
    for buf in spec.architecture.get_nodes_of_type(Storage):
        name = buf.name
        depth = int(buf.attributes.depth)
        width = int(buf.attributes.width)
        datawidth = int(buf.attributes.datawidth)

        storage_width_datawidth[name] = (width, datawidth)
        if name == "shared_glb":
            C1 = depth * width // datawidth
        elif name == "regfile":
            C3 = depth * width // datawidth

    if C1 is None or C3 is None:
        raise RuntimeError("在 architecture 中未找到 shared_glb 或 regfile 存储层。")

    # Architecture → N_PE / meshX / meshY（兼容两种写法）
    pe_mesh_x = pe_mesh_y = None
    pey_mesh_x = pey_mesh_y = None
    for cont in spec.architecture.get_nodes_of_type(Container):
        if cont.name == "PE":
            pe_mesh_x = int(getattr(cont.spatial, "meshX", 1))
            pe_mesh_y = int(getattr(cont.spatial, "meshY", 1))
        elif cont.name == "PEy":
            pey_mesh_x = int(getattr(cont.spatial, "meshX", 1))
            pey_mesh_y = int(getattr(cont.spatial, "meshY", 1))

    if pe_mesh_x is None or pe_mesh_y is None:
        raise RuntimeError("在 architecture 中未找到名称为 'PE' 的 Container。")

    mesh_x = int(pe_mesh_x) * int(pey_mesh_x or 1)
    mesh_y = int(pe_mesh_y) * int(pey_mesh_y or 1)
    N_PE = int(mesh_x) * int(mesh_y)

    if mesh_x <= 0 or mesh_y <= 0:  # pragma: no cover - defensive
        raise RuntimeError(f"解析得到的 meshX/meshY 非法：meshX={mesh_x}, meshY={mesh_y}")

    return int(C1), int(C3), int(N_PE), storage_width_datawidth, int(mesh_x), int(mesh_y)


def _device_params_from_ert_path(
    ert_path: Path,
    storage_width_datawidth: Dict[str, Tuple[int, int]],
) -> DeviceParams:
    """
    参考 mapping_pipeline._device_params_from_ert_path 的实现，从 ERT 解析能量参数。

    统一按 word 口径换算：
      - 对每个存储层：E_word = E_ert / (width / datawidth)
      - leak 不做修改（保持 ERT/Timeloop 口径）
    """
    if not ert_path.exists():
        raise FileNotFoundError(f"未找到 ERT 文件：{ert_path}")

    data = yaml.safe_load(ert_path.read_text(encoding="utf-8"))
    tables = data.get("ERT", {}).get("tables", [])
    table_map = {t["name"].split(".", 1)[1].split("[", 1)[0]: t for t in tables}

    def require_table(name: str) -> Dict:
        try:
            return table_map[name]
        except KeyError as exc:
            raise KeyError(f"ERT 中缺少 {name} 能量表") from exc

    def get_action_energy(table: Dict, action_name: str) -> float:
        for action in table.get("actions", []):
            if action.get("name") == action_name:
                return float(action["energy"])
        raise KeyError(f"{table.get('name', '<unknown>')} 中缺少动作 {action_name}")

    def word_scale(storage_name: str) -> float:
        try:
            width, datawidth = storage_width_datawidth[storage_name]
        except KeyError as exc:
            raise KeyError(
                f"无法获得 {storage_name} 的 (width,datawidth)，无法按 word 口径换算能量。"
            ) from exc
        return float(width) / float(datawidth)

    dram = require_table("DRAM")
    glb = require_table("shared_glb")
    rf = require_table("regfile")
    mac = require_table("mac")

    E_DDR_read = get_action_energy(dram, "read") / word_scale("DRAM")
    E_DDR_write = get_action_energy(dram, "write") / word_scale("DRAM")
    E_SRAM_read = get_action_energy(glb, "read") / word_scale("shared_glb")
    E_SRAM_write = get_action_energy(glb, "write") / word_scale("shared_glb")
    E_RF_read = get_action_energy(rf, "read") / word_scale("regfile")
    E_RF_write = get_action_energy(rf, "write") / word_scale("regfile")

    return DeviceParams(
        E_DDR_read=E_DDR_read,
        E_DDR_write=E_DDR_write,
        E_SRAM_read=E_SRAM_read,
        E_SRAM_write=E_SRAM_write,
        E_RF_read=E_RF_read,
        E_RF_write=E_RF_write,
        E_MACC=get_action_energy(mac, "compute"),
        E_SRAM_leak=get_action_energy(glb, "leak"),
        E_RF_leak=get_action_energy(rf, "leak"),
    )


@dataclass
class WorkItem:
    wid: int
    problem_id: str
    tiling_idx: int
    L0: Dict[str, int]
    hatL_01: Dict[str, int]
    hatL_12: Dict[str, int]
    hatL_23: Dict[str, int]
    hatL_34: Dict[str, int]
    alpha01: str
    alpha12: str
    B1: Dict[str, int]
    B3: Dict[str, int]


def _load_workloads(
    tilings_path: Path,
    first_two_only: bool = False,
    *,
    db: Optional[TilingDB] = None,
) -> List[WorkItem]:
    db = db or load_tiling_db(tilings_path)
    axis_orders = AXIS_ORDERS
    bypass_keys = list(B_COMBOS.keys())

    # dataflow_gen.iter_workloads 产出的字段结构：
    # {
    #   'problem_id': 'S1', 'problem_shape': {...},
    #   'iteration_axes': {'alpha01': 'x', 'alpha12': 'y'},
    #   'level_bypass': {'key': 'B1', 'levels': {'DDR':{...}, 'SRAM':{...}, 'REGFILE':{...}, ...}},
    #   'tiling': {'id': 0, 'scheme': {'L0':{...}, 'hatL_01':{...}, 'hatL_12':{...}, 'hatL_23':{...}, 'hatL_34':{...}}}
    # }

    items: List[WorkItem] = []
    wid = 0
    for shape_id, tiling_list in db.tilings.items():
        for a01, a12 in axis_orders:
            for bkey in bypass_keys:
                levels = expand_bypass(bkey)  # level 名→各轴 0/1
                B1 = levels["SRAM"]
                B3 = levels["REGFILE"]
                for t_idx, tiling in enumerate(tiling_list):
                    if first_two_only and t_idx >= 2:
                        break
                    scheme = tiling
                    items.append(
                        WorkItem(
                            wid=wid,
                            problem_id=shape_id,
                            tiling_idx=t_idx,
                            L0={k: int(scheme["L0"][k]) for k in AXES},
                            hatL_01={k: int(scheme["hatL_01"][k]) for k in AXES},
                            hatL_12={k: int(scheme["hatL_12"][k]) for k in AXES},
                            hatL_23={k: int(scheme["hatL_23"][k]) for k in AXES},
                            hatL_34={k: int(scheme["hatL_34"][k]) for k in AXES},
                            alpha01=str(a01),
                            alpha12=str(a12),
                            B1={k: int(B1[k]) for k in AXES},
                            B3={k: int(B3[k]) for k in AXES},
                        )
                    )
                    wid += 1
    return items


def _prepare_inputs(here: Path,
                    problem_yaml: Path,
                    mapping_yaml: Path,
                    work: WorkItem,
                    *,
                    mesh_x: Optional[int] = None,
                    mesh_y: Optional[int] = None) -> None:
    """从模板复制，并用 work 的参数更新 problem/mapping 文件。"""
    templates_dir = here / "templates"
    problem_tmpl = templates_dir / "problem_template.yaml"
    mapping_tmpl = templates_dir / "mapping_template.yaml"

    copy_template(problem_tmpl, problem_yaml)
    copy_template(mapping_tmpl, mapping_yaml)

    update_problem_file(problem_yaml, work.L0)
    update_mapping_file(
        mapping_yaml,
        work.hatL_01,
        work.hatL_12,
        work.hatL_23,
        work.hatL_34,
        work.alpha01,
        work.alpha12,
        work.B1,
        work.B3,
        mesh_x=mesh_x,
        mesh_y=mesh_y,
    )


def _choose_new_axis(current: str, hats: Mapping[str, int]) -> str:
    """从其余两轴中选择一个新的行走轴，优先选择 hat>1 的轴；若均无则取第一候选。
    保持确定性（AXES 次序）。"""
    others = [a for a in AXES if a != current]
    for a in others:
        if int(hats[a]) > 1:
            return a
    return others[0]


def _run_timeloop_once(
    here: Path,
    out_dir: Path,
    inputs_dir: Path,
    *,
    arch_yaml: Path,
    problem_yaml: Path,
) -> Tuple[Optional[float], Optional[int], Optional[str]]:
    """
    运行一次 Timeloop model，返回 (pJ/compute, computes)。
    top 模板固定为 top_model.jinja，会从 inputs_dir 下读取 arch/problem/mapping。
    输出写到 out_dir（不覆盖同名目录）。
    """
    # 使用原有 top 模板（arch/problem/mapping 均来自 inputs_dir）
    top = here / "top_model.jinja"
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = tl.Specification.from_yaml_files(
        str(top),
        jinja_parse_data={
            "inputs_dir": str(inputs_dir),
            "arch": str(arch_yaml.resolve()),
            "problem": str(problem_yaml.resolve()),
        },
    )
    try:
        tl.call_model(spec, output_dir=str(out_dir))
    except Exception as e:
        # 返回错误信息，调用方决定是否跳过
        return None, None, str(e)

    stats_path = out_dir / "timeloop-model.stats.txt"
    if not stats_path.exists():
        return None, None, f"未发现 stats 文件：{stats_path}"

    cycles, computes, util, energy_J, accesses = parse_stats_file(str(stats_path))
    total_energy_J = sum(float(v) for v in energy_J.values())
    if computes <= 0:
        raise RuntimeError("stats 中 computes=0（非法）")
    pJ_per_compute = total_energy_J / computes * 1e12
    return pJ_per_compute, computes, None

def _run_python_energy(work: WorkItem, params: DeviceParams) -> Tuple[float, Dict[str, float]]:
    phi, parts = compute_normalized_total_energy(
        L0=work.L0,
        hatL_12=work.hatL_12,
        hatL_23=work.hatL_23,
        hatL_34=work.hatL_34,
        alpha01=work.alpha01,
        alpha12=work.alpha12,
        B={
            0: {"x": 1, "y": 1, "z": 1},
            1: work.B1,
            2: {"x": 1, "y": 1, "z": 1},
            3: work.B3,
            4: {"x": 1, "y": 1, "z": 1},
        },
        params=params,
        include_leak=True,
    )
    # 直接比较包含泄漏的能量
    return float(phi), parts


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="能量模型一致性验证流水线")
    parser.add_argument("--tilings", type=Path, default=here / "tilings_random.json", help="tilings_random.json 路径")
    parser.add_argument("--outdir", type=Path, default=here / "outputs_validation", help="Timeloop 输出与对比结果目录")
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        default=here / "inputs_my",
        help="Timeloop 输入目录（包含 mapping.yaml/problem.yaml/mapper.yaml/_components 等；arch 将由 --arch-yaml 指定）",
    )
    parser.add_argument(
        "--arch-yaml",
        type=Path,
        default=DEFAULT_ARCH_YAML,
        help="架构描述文件（默认：Eyeriss-like）",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅运行前 N 例（调试用）")
    parser.add_argument("--start", type=int, default=0, help="从第 start 个用例开始（用于断点续跑）")
    parser.add_argument("--progress-every", type=int, default=1, help="进度输出频率（每 N 例打印一次，默认: 1）")
    parser.add_argument("--rtol", type=float, default=1e-6, help="(已弃用) 相对误差容忍度；当前仅基于绝对误差判断")
    parser.add_argument("--atol", type=float, default=1e-5, help="绝对误差容忍度 pJ/compute (默认: 1e-5)")
    parser.add_argument("--first-two-tilings-only", action="store_true", default=False, help="仅使用每个问题的前两种分块方式")
    return parser.parse_args()


def main() -> int:
    t0 = time.perf_counter()
    here = Path(__file__).resolve().parent
    args = parse_args()
    inputs_dir = args.inputs_dir.expanduser().resolve()
    arch_yaml = args.arch_yaml.expanduser().resolve()
    if not arch_yaml.exists():
        raise FileNotFoundError(f"--arch-yaml 指向的文件不存在：{arch_yaml}")

    problem_yaml = inputs_dir / "problem.yaml"
    mapping_yaml = inputs_dir / "mapping.yaml"
    top_model = here / "top_model.jinja"
    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    # 解析硬件参数（mesh/C1/C3/N_PE）与存储位宽，用于：
    #   - 生成 2D spatial mapping（PEy+PE）
    #   - 从 ERT 提取 DeviceParams 时按 word 口径换算能量
    jinja_parse_data = {
        "inputs_dir": str(inputs_dir),
        "arch": str(arch_yaml),
        "problem": str(problem_yaml),
    }
    C1_words, C3_words, N_PE, storage_width_datawidth, mesh_x, mesh_y = _load_arch_params_from_spec(
        top_model,
        jinja_parse_data=jinja_parse_data,
    )
    print(
        f"[Init] arch={arch_yaml.name}, N_PE={N_PE} (meshX={mesh_x}, meshY={mesh_y}), "
        f"C1(shared_glb)={C1_words}, C3(regfile)={C3_words}",
        flush=True,
    )

    # 准备工作集
    tiling_db = load_tiling_db(args.tilings)
    if int(tiling_db.pe_num) != int(N_PE):
        raise RuntimeError(
            f"tilings_random.json 的 pe_num={tiling_db.pe_num} 与架构解析得到的 N_PE={N_PE} 不一致；"
            "请先用与当前架构匹配的 gen_random_tilings.py 重新生成 tilings。"
        )
    items = _load_workloads(args.tilings, first_two_only=args.first_two_tilings_only, db=tiling_db)
    total = len(items)
    start = max(0, args.start)
    end = min(total, start + args.limit) if args.limit else total
    items = items[start:end]
    progress_every = max(1, int(getattr(args, "progress_every", 1)))

    # 延迟一次性读取 ERT：首个用例跑完 Timeloop 后，从该用例输出目录读取
    # 保证与当前 arch/ERT 完全一致（且 DDR/SRAM 自动按 /8 口径处理）。
    params_global: DeviceParams | None = None

    # 汇总输出文件
    summary_json = outdir / "summary.jsonl"
    with open(summary_json, "w", encoding="utf-8"):
        pass  # truncate file before this run
    summary_csv = outdir / "summary.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8") as f_csv:
        w = csv.writer(f_csv)
        w.writerow([
            "wid", "problem_id", "alpha01", "alpha12", "B_keyless",
            "pJ_per_compute_timeloop", "pJ_per_compute_python",
            "abs_err", "rel_err", "match"
        ])

    ok_cnt = 0
    for i, work in enumerate(items, 1):
        wid = work.wid
        tag = f"case_{wid:05d}_{work.problem_id}_{work.alpha01}{work.alpha12}"
        case_out = outdir / "timeloop_run"
        if i == 1 or i % progress_every == 0 or i == len(items):
            print(f"[{i}/{len(items)}] START wid={wid} {work.problem_id} {work.alpha01}{work.alpha12}", flush=True)
        # 0) 若 hatL_01[alpha01]==1 或 hatL_12[alpha12]==1，调整行走轴
        if int(work.hatL_01[work.alpha01]) == 1:
            work.alpha01 = _choose_new_axis(work.alpha01, work.hatL_01)
        if int(work.hatL_12[work.alpha12]) == 1:
            work.alpha12 = _choose_new_axis(work.alpha12, work.hatL_12)
        if case_out.exists():
            shutil.rmtree(case_out)

        # 1) 生成 inputs
        _prepare_inputs(here, problem_yaml, mapping_yaml, work, mesh_x=mesh_x, mesh_y=mesh_y)

        # 2) Timeloop 仿真
        tl_pj, computes, tl_err = _run_timeloop_once(
            here,
            case_out,
            inputs_dir,
            arch_yaml=arch_yaml,
            problem_yaml=problem_yaml,
        )
        if tl_err is not None:
            # 保留该轮输出并记录错误，继续下一例
            preserve_dir = outdir / tag
            if preserve_dir.exists():
                shutil.rmtree(preserve_dir)
            shutil.copytree(case_out, preserve_dir)

            rec = {
                "wid": wid,
                "problem_id": work.problem_id,
                "alpha01": work.alpha01,
                "alpha12": work.alpha12,
                "L0": work.L0,
                "hatL_01": work.hatL_01,
                "hatL_12": work.hatL_12,
                "hatL_23": work.hatL_23,
                "hatL_34": work.hatL_34,
                "B1": work.B1,
                "B3": work.B3,
                "error": tl_err,
                "match": False,
            }
            with open(summary_json, "a", encoding="utf-8") as fj:
                fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
            with open(summary_csv, "a", newline="", encoding="utf-8") as f_csv:
                w = csv.writer(f_csv)
                w.writerow([
                    wid, work.problem_id, work.alpha01, work.alpha12, "-",
                    "", "", "", "", 0,
                ])
            eta = (time.perf_counter() - t0) / i * (len(items) - i)
            print(f"[{i}/{len(items)}] wid={wid} ERROR: {tl_err} ETA~{eta/60:.1f}m")
            continue

        # 3) 自研能量：在首个用例完成 Timeloop 后读取 ERT（一次），后续复用
        if params_global is None:
            params_global = _device_params_from_ert_path(
                case_out / "timeloop-model.ERT.yaml",
                storage_width_datawidth=storage_width_datawidth,
            )
        # 使用全局参数；无需每例读取 ERT
        py_pj, parts = _run_python_energy(work, params_global)
        py_pj = round(py_pj, 5)

        # 4) 对比
        abs_err = abs(tl_pj - py_pj)
        denom = max(abs(tl_pj), abs(py_pj), 1e-12)
        rel_err = abs_err / denom
        # 误差判定规则：仅基于绝对误差；当 abs_err <= 1e-5（默认）视为无误差，写入文件统一记为 0
        # 注意：浮点数在边界处可能出现 1e-5 -> 1.0000000003e-5 的数值抖动；
        # 为避免“打印看起来等于 1e-5 但被判定为 mismatch”，这里加入一个极小的容差修正。
        eps = max(1e-12, float(args.atol) * 1e-9)
        match = (abs_err <= float(args.atol) + eps)
        abs_err_out = 0.0 if match else abs_err
        rel_err_out = 0.0 if match else rel_err
        ok_cnt += 1 if match else 0

        # 若不一致，则保留该轮 timeloop 输出到独立目录（覆盖旧同名目录）
        if not match:
            preserve_dir = outdir / tag
            if preserve_dir.exists():
                shutil.rmtree(preserve_dir)
            shutil.copytree(case_out, preserve_dir)

        # 5) 记录
        rec = {
            "wid": wid,
            "problem_id": work.problem_id,
            "alpha01": work.alpha01,
            "alpha12": work.alpha12,
            "L0": work.L0,
            "hatL_01": work.hatL_01,
            "hatL_12": work.hatL_12,
            "hatL_23": work.hatL_23,
            "hatL_34": work.hatL_34,
            "B1": work.B1,
            "B3": work.B3,
            "computes": computes,
            "timeloop_pJ_per_compute": tl_pj,
            "python_pJ_per_compute": py_pj,
            "abs_err": abs_err_out,
            "rel_err": rel_err_out,
            "match": match,
        }
        with open(summary_json, "a", encoding="utf-8") as fj:
            fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(summary_csv, "a", newline="", encoding="utf-8") as f_csv:
            w = csv.writer(f_csv)
            w.writerow([
                wid, work.problem_id, work.alpha01, work.alpha12, "-",
                f"{tl_pj:.6f}", f"{py_pj:.6f}", f"{abs_err_out:.6f}", f"{rel_err_out:.3e}",
                int(match),
            ])

        # 进度提示
        if i % progress_every == 0 or i == len(items):
            eta = (time.perf_counter() - t0) / i * (len(items) - i)
            print(f"[{i}/{len(items)}] wid={wid} tl={tl_pj:.4f} p={py_pj:.4f} "
                  f"abs={abs_err_out:.4f} rel={rel_err_out:.2e} match={match} ETA~{eta/60:.1f}m", flush=True)

    print(f"Done. matched {ok_cnt}/{len(items)} cases. Results under: {outdir}")
    # 总运行时间
    elapsed = time.perf_counter() - t0
    if elapsed < 60:
        print(f"Total runtime: {elapsed:.2f} s")
    else:
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(int(minutes), 60)
        if hours:
            print(f"Total runtime: {hours:d}h {minutes:d}m {seconds:.1f}s")
        else:
            print(f"Total runtime: {minutes:d}m {seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
