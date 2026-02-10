#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终映射求解流程（一键脚本）
--------------------------------

阶段：
  1) 通过 Accelergy 从体系结构生成 ERT（Energy Reference Table）（可复用已有 ERT）。
  2) 从 problem.yaml / arch.yaml / ERT 解析 L0、C1、C3、N_PE 与能量参数，构造 make_cfg 等价配置。
  3) 调用 full_model.build_model_full 求解最优数据流（L / k / B / alpha）。
  4) 将求得的数据流转换为 Timeloop 映射格式，只生成 / 覆盖 inputs_my/mapping.yaml（不改动 problem.yaml）。
  5) 复用同一 ERT 调用 timeloop-model，输出访存次数、能耗等性能指标。

使用方式（建议在工程根执行，并已激活 venv）::

    cd <项目根目录>
    # 可选：激活你自己的 Python 环境（示例）
    # source .venv/bin/activate
    python timeloop-accelergy-exercises/workspace/my_designs/GOMA/mapping_pipeline.py

如需强制重新跑 Accelergy 生成 ERT，可加上::

    python .../mapping_pipeline.py --force-regenerate-ert
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

import pytimeloop.timeloopfe.v4 as tl
from pytimeloop.timeloopfe.v4.arch import Storage, Container
from pytimeloop.timeloopfe.common import backend_calls
from pytimeloop.timeloopfe.v4.output_parsing import parse_stats_file

from full_model import build_model_full
from normalized_energy_model import DeviceParams, compute_normalized_total_energy
from gen_problem_mapping import (
    copy_template,
    update_problem_file,
    update_mapping_file,
)


AXES = ("x", "y", "z")

STAGE3_TIMING_FILENAME = "stage3_timing.json"
ENV_STAGE3_TIME_ONLY = "TL_MAPPER_STAGE3_TIME_ONLY"


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _write_stage3_timing(outputs_dir: Path, stage3_solve_seconds: float) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    out_path = outputs_dir / STAGE3_TIMING_FILENAME
    out_path.write_text(
        json.dumps({"stage3_solve_seconds": float(stage3_solve_seconds)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def _generate_ert_with_accelergy(
    top_model: Path,
    tmp_out_dir: Path,
    ert_out_path: Path,
    jinja_parse_data: Optional[Dict[str, str]] = None,
) -> Path:
    """
    阶段 1：调用 Accelergy 生成 ERT。

    - 通过 timeloopfe v4 构造 Specification（负责解析 Jinja 与 include）。
    - 使用 backend_calls.accelergy_app(spec, tmp_out_dir) 调用 Accelergy
      （内部会通过 pytimeloop.accelergy_interface.invoke_accelergy 调用 CLI）。
    - 将返回的 ert_str 写入 ert_out_path，供后续阶段统一使用。
    """
    tmp_out_dir.mkdir(parents=True, exist_ok=True)

    spec = tl.Specification.from_yaml_files(str(top_model), jinja_parse_data=jinja_parse_data or {})
    result = backend_calls.accelergy_app(specification=spec, output_dir=str(tmp_out_dir))

    ert_out_path.parent.mkdir(parents=True, exist_ok=True)
    ert_out_path.write_text(result.ert, encoding="utf-8")
    return ert_out_path


def _load_problem_arch_from_spec(
    top_model: Path,
    ert_path: Path,
    jinja_parse_data: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, int], int, int, int, Dict[str, Tuple[int, int]], int, int]:
    """
    使用 timeloopfe v4 的 Specification 统一解析 problem / arch：

      - L0 来自 spec.problem.instance.X/Y/Z；
      - C1_words = shared_glb.depth * shared_glb.width / datawidth；
      - C3_words = regfile.depth * regfile.width / datawidth；
      - N_PE = PE.meshX * PE.meshY（若 meshY 缺失则按 1），并额外返回 (meshX, meshY)。

    这样可以正确处理 arch.yaml 中的 !Container / !Component 等自定义标签，
    避免直接用 yaml.safe_load 带来的解析问题。
    """
    spec = tl.Specification.from_yaml_files(
        str(top_model),
        str(ert_path),
        jinja_parse_data=jinja_parse_data or {},
    )

    # Problem instance → L0
    inst = spec.problem.instance
    try:
        L0 = {"x": int(inst["X"]), "y": int(inst["Y"]), "z": int(inst["Z"])}
    except KeyError as exc:
        raise RuntimeError(f"problem.instance 缺少维度 {exc.args[0]}") from exc

    # Architecture → Storage params / C1 / C3
    # 约定：容量按 word 计，word 的位宽为 datawidth（通常 8bit）。
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

    # Architecture → N_PE / meshX / meshY
    #
    # 兼容两种写法：
    #   1) 单一容器：PE.spatial 同时给出 meshX/meshY
    #   2) 分拆容器：PEy.spatial 提供 meshX，PE.spatial 提供 meshY（用于规避 meshX+meshY 同容器的已知问题）
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

    return L0, C1, C3, N_PE, storage_width_datawidth, mesh_x, mesh_y


def _device_params_from_ert_path(
    ert_path: Path,
    storage_width_datawidth: Dict[str, Tuple[int, int]],
) -> DeviceParams:
    """
    阶段 2：从 ERT 解析能量参数。

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


def _build_cfg(
    L0: Dict[str, int],
    C1: int,
    C3: int,
    N_PE: int,
    params: DeviceParams,
) -> Dict[str, object]:
    """
    构造与 main.make_cfg 等价的配置字典，用于 full_model.build_model_full。
    """
    cfg = {
        "L0": {"x": int(L0["x"]), "y": int(L0["y"]), "z": int(L0["z"])},
        "C1": int(C1),
        "C3": int(C3),
        "N_PE": int(N_PE),
        "E_DDR_r": float(params.E_DDR_read),
        "E_DDR_w": float(params.E_DDR_write),
        "E_SRAM_r": float(params.E_SRAM_read),
        "E_SRAM_w": float(params.E_SRAM_write),
        "E_RF_r": float(params.E_RF_read),
        "E_RF_w": float(params.E_RF_write),
        "E_MACC": float(params.E_MACC),
        "E_SRAM_leak": float(params.E_SRAM_leak),
        "E_RF_leak": float(params.E_RF_leak),
    }
    return cfg


def _solve_full_model(cfg: Dict[str, object], verbose: bool = True):
    """
    阶段 3：基于 cfg 与能量模型，求解最优数据流（L/k/B/alpha）。

    返回：
      - model, L, k, y, B1, B3, a01, a12  （与 full_model.build_model_full 一致）
    并在 verbose 时打印一份简要解读信息。
    """
    import gurobipy as gp  # 本地导入，避免在未安装 gurobi 时影响模块导入

    grb_params = {"OutputFlag": 1, "NonConvex": 2}
    model, L, k, y, B1, B3, a01, a12 = build_model_full(cfg, params=grb_params)
    model.optimize()

    if model.Status not in (gp.GRB.OPTIMAL, gp.GRB.SUBOPTIMAL, gp.GRB.TIME_LIMIT):
        raise RuntimeError(f"Gurobi 求解失败，状态码={model.Status}")

    if verbose:
        # 泄露能量（仅报告用，不进目标）
        num_pe = int(k[(2, "x")].X) * int(k[(2, "y")].X) * int(k[(2, "z")].X)
        E_leak_per_cycle = cfg["E_SRAM_leak"] + cfg["E_RF_leak"] * num_pe
        E_leak_norm = E_leak_per_cycle / num_pe if num_pe > 0 else 0.0
        total_energy = float(model.ObjVal) + float(E_leak_norm)
        print(
            f"[MILP] status={model.Status}, Total Normalized Energy={total_energy:.6f} "
            f"(Dynamic={model.ObjVal:.6f} + Leak={E_leak_norm:.6f})"
        )

        # 找出行走轴
        alpha01 = max(a01, key=lambda d: a01[d].X)
        alpha12 = max(a12, key=lambda d: a12[d].X)
        print(f"[MILP] alpha_0-1 = {alpha01}, alpha_1-2 = {alpha12}")

        # B1/B3 驻留开关
        print("[MILP] B1:", {d: int(B1[d].X) for d in AXES})
        print("[MILP] B3:", {d: int(B3[d].X) for d in AXES})

        # 三级块长 / 整除比
        for i in AXES:
            print(
                f"[MILP] {i}: "
                f"L1={int(L[(1, i)].X)}, L2={int(L[(2, i)].X)}, L3={int(L[(3, i)].X)} | "
                f"k0={int(k[(0, i)].X)}, k1={int(k[(1, i)].X)}, "
                f"k2={int(k[(2, i)].X)}, k3={int(k[(3, i)].X)}"
            )

    return model, L, k, y, B1, B3, a01, a12


def _extract_mapping_params(
    cfg: Dict[str, object],
    L,
    k,
    B1,
    B3,
    a01,
    a12,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int], str, str, Dict[str, int], Dict[str, int]]:
    """
    阶段 3→4 之间的桥接：把 MILP 解翻译为自研数据流参数：

      - hatL_01 / hatL_12 / hatL_23 / hatL_34  （整除关系：L0 = k0*k1*k2*k3）
      - alpha01 / alpha12                       （行走轴）
      - B1 / B3                                 （逐轴驻留开关）
    """
    # k[(p, dim)] 即各阶段的整除因子，直接对应 hatL_{0-1}, hatL_{1-2}, hatL_{2-3}, hatL_{3-4}
    hatL_01 = {d: int(k[(0, d)].X) for d in AXES}
    hatL_12 = {d: int(k[(1, d)].X) for d in AXES}
    hatL_23 = {d: int(k[(2, d)].X) for d in AXES}
    hatL_34 = {d: int(k[(3, d)].X) for d in AXES}

    # 行走轴 one-hot
    alpha01 = max(a01, key=lambda d: a01[d].X)
    alpha12 = max(a12, key=lambda d: a12[d].X)

    B1_map = {d: int(B1[d].X) for d in AXES}
    B3_map = {d: int(B3[d].X) for d in AXES}

    # 简单一致性检查：L0 == k0*k1*k2*k3
    for d in AXES:
        L0_d = int(cfg["L0"][d])
        prod = hatL_01[d] * hatL_12[d] * hatL_23[d] * hatL_34[d]
        if L0_d != prod:
            raise RuntimeError(
                f"轴 {d} 上 L0={L0_d} 与 k0*k1*k2*k3={prod} 不一致（模型约束应保证相等，"
                "若触发此错误说明求解结果可能无效）。"
            )

    return hatL_01, hatL_12, hatL_23, hatL_34, alpha01, alpha12, B1_map, B3_map


def _update_problem_and_mapping(
    here: Path,
    inputs_dir: Path,
    L0: Dict[str, int],
    hatL_01: Dict[str, int],
    hatL_12: Dict[str, int],
    hatL_23: Dict[str, int],
    hatL_34: Dict[str, int],
    alpha01: str,
    alpha12: str,
    B1: Dict[str, int],
    B3: Dict[str, int],
    mesh_x: int,
    mesh_y: int,
) -> Tuple[Path, Path]:
    """
    阶段 4：基于求得的数据流参数，生成/覆盖 Timeloop 的 mapping.yaml。

    - problem.yaml：保留用户已有 problem.yaml，不做改动；
    - mapping.yaml：从模板复制并调用 update_mapping_file 写入 factors/permutation/keep。
    """
    templates_dir = here / "templates"
    mapping_tmpl = templates_dir / "mapping_template.yaml"

    problem_path = inputs_dir / "problem.yaml"
    mapping_path = inputs_dir / "mapping.yaml"

    copy_template(mapping_tmpl, mapping_path)

    update_mapping_file(
        mapping_path,
        hatL_01,
        hatL_12,
        hatL_23,
        hatL_34,
        alpha01,
        alpha12,
        B1,
        B3,
        mesh_x=mesh_x,
        mesh_y=mesh_y,
    )

    return problem_path, mapping_path


def _run_timeloop_model(
    top_model: Path,
    ert_path: Path,
    out_dir: Path,
    jinja_parse_data: Optional[Dict[str, str]] = None,
) -> None:
    """
    阶段 5：调用 timeloop-model，使用同一 ERT 评估最终映射。

    - 通过 Spec.from_yaml_files(top_model, ert_path) 注入 ERT。
    - 调用 tl.call_model；输出写入 out_dir。
    - 解析 stats 文件，打印 pJ/compute 与关键统计。
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = tl.Specification.from_yaml_files(
        str(top_model),
        str(ert_path),
        jinja_parse_data=jinja_parse_data or {},
    )
    tl.call_model(spec, output_dir=str(out_dir))

    stats_path = out_dir / "timeloop-model.stats.txt"
    if not stats_path.exists():
        print(f"[Timeloop] 未找到 stats 文件：{stats_path}")
        return

    cycles, computes, util, energy_J, accesses = parse_stats_file(str(stats_path))
    total_energy_J = sum(float(v) for v in energy_J.values())
    if computes <= 0:
        print("[Timeloop] stats 中 computes <= 0，无法计算归一化能量。")
        return

    pJ_per_compute = total_energy_J / computes * 1e12
    print(f"[Timeloop] pJ/compute = {pJ_per_compute:.6f}")
    print(f"[Timeloop] cycles = {cycles}, computes = {computes}, util = {util:.4f}")


def _check_python_energy(
    L0: Dict[str, int],
    hatL_12: Dict[str, int],
    hatL_23: Dict[str, int],
    hatL_34: Dict[str, int],
    alpha01: str,
    alpha12: str,
    B1: Dict[str, int],
    B3: Dict[str, int],
    params: DeviceParams,
) -> None:
    """
    可选：使用自研 normalized_energy_model 计算一次归一化能量，作为额外 sanity check。
    """
    B_full = {
        0: {"x": 1, "y": 1, "z": 1},
        1: dict(B1),
        2: {"x": 1, "y": 1, "z": 1},
        3: dict(B3),
        4: {"x": 1, "y": 1, "z": 1},
    }
    phi, parts = compute_normalized_total_energy(
        L0=L0,
        hatL_12=hatL_12,
        hatL_23=hatL_23,
        hatL_34=hatL_34,
        alpha01=alpha01,
        alpha12=alpha12,
        B=B_full,
        params=params,
        include_leak=True,
    )
    print(f"[PythonEnergy] phi (含 leak) = {phi:.6f}, parts = {parts}")


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    default_inputs = here / "inputs_my"
    default_outputs = here / "outputs_my"

    parser = argparse.ArgumentParser(description="最终映射求解流水线：Accelergy→MILP→Timeloop")
    parser.add_argument(
        "--arch-yaml",
        type=Path,
        default=None,
        help="指定 arch.yaml 路径（默认使用 <inputs-dir>/arch.yaml）",
    )
    parser.add_argument(
        "--problem-yaml",
        type=Path,
        default=None,
        help="指定 problem.yaml 路径（默认使用 <inputs-dir>/problem.yaml）",
    )
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        default=default_inputs,
        help="Timeloop 输入目录（包含 arch/problem/mapping/variables 等）",
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=default_outputs,
        help="Timeloop 输出目录（stats / map / ERT 等），默认为 outputs_my",
    )
    parser.add_argument(
        "--ert-path",
        type=Path,
        default=None,
        help=(
            "复用已有 ERT 文件路径（指定后将跳过 Stage1；适用于同一 arch 下批量跑多个 problem）。"
        ),
    )
    parser.add_argument(
        "--generate-ert-only",
        action="store_true",
        help="仅执行 Stage1 生成 ERT（或检查 --ert-path 存在），然后退出。",
    )
    parser.add_argument(
        "--force-regenerate-ert",
        action="store_true",
        help="无论是否已有 ERT 文件，均强制调用 Accelergy 重新生成。",
    )
    parser.add_argument(
        "--skip-python-energy-check",
        action="store_true",
        help="跳过自研 normalized_energy_model 的能量校验（默认开启，仅打印）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    here = Path(__file__).resolve().parent
    inputs_dir = args.inputs_dir
    outputs_dir = args.outputs_dir
    timing_only = _env_flag(ENV_STAGE3_TIME_ONLY)

    top_model = here / "top_model.jinja"
    arch_yaml = args.arch_yaml if args.arch_yaml is not None else (inputs_dir / "arch.yaml")
    problem_yaml = (
        args.problem_yaml if args.problem_yaml is not None else (inputs_dir / "problem.yaml")
    )

    if not top_model.exists():
        raise FileNotFoundError(f"未找到 top_model.jinja：{top_model}")
    if not arch_yaml.exists():
        raise FileNotFoundError(f"未找到 arch.yaml：{arch_yaml}")
    if not problem_yaml.exists():
        raise FileNotFoundError(f"未找到 problem.yaml：{problem_yaml}")

    jinja_parse_data = {
        # 让 top_model.jinja 优先从 inputs_dir 下找 mapping/variables/mapper/_components
        "inputs_dir": str(inputs_dir.resolve()),
        # 允许从任意位置指定 arch/problem（绝对路径最稳妥）
        "arch": str(arch_yaml.resolve()),
        "problem": str(problem_yaml.resolve()),
    }

    # --------------------
    # 阶段 1：ERT 生成
    # --------------------
    if args.ert_path is not None and args.force_regenerate_ert:
        raise ValueError("不能同时指定 --ert-path 与 --force-regenerate-ert（语义冲突）。")

    if args.ert_path is not None:
        ert_path = args.ert_path
        if not ert_path.exists():
            raise FileNotFoundError(f"--ert-path 指向的文件不存在：{ert_path}")
        print(f"[Stage1] 使用外部 ERT：{ert_path}")
        if args.generate_ert_only:
            print("[Stage1] generate-ert-only：ERT 已就绪，退出。")
            return 0
    else:
        ert_path = outputs_dir / "timeloop-model.ERT.yaml"
        if args.force_regenerate_ert or not ert_path.exists():
            print("[Stage1] 调用 Accelergy 生成 ERT …")
            tmp_accelergy_dir = outputs_dir / "accelergy_tmp"
            ert_path = _generate_ert_with_accelergy(
                top_model,
                tmp_accelergy_dir,
                ert_path,
                jinja_parse_data=jinja_parse_data,
            )
            print(f"[Stage1] ERT 已生成：{ert_path}")
        else:
            print(f"[Stage1] 复用已有 ERT：{ert_path}")
        if args.generate_ert_only:
            print("[Stage1] generate-ert-only：ERT 已就绪，退出。")
            return 0

    # --------------------
    # 阶段 2：装载配置
    # --------------------
    print("[Stage2] 解析 problem/arch/ERT …")
    # 使用 timeloopfe 的 Specification 解析 L0 / C1 / C3 / N_PE，避免自定义标签问题
    L0, C1, C3, N_PE, storage_width_datawidth, mesh_x, mesh_y = _load_problem_arch_from_spec(
        top_model,
        ert_path,
        jinja_parse_data=jinja_parse_data,
    )
    dev_params = _device_params_from_ert_path(ert_path, storage_width_datawidth=storage_width_datawidth)
    cfg = _build_cfg(L0, C1, C3, N_PE, dev_params)

    print(f"[Stage2] L0 = {L0}, C1={C1}, C3={C3}, N_PE={N_PE} (meshX={mesh_x}, meshY={mesh_y})")
    print(
        "[Stage2] DeviceParams: "
        f"DDR_r={dev_params.E_DDR_read}, DDR_w={dev_params.E_DDR_write}, "
        f"SRAM_r={dev_params.E_SRAM_read}, SRAM_w={dev_params.E_SRAM_write}, "
        f"RF_r={dev_params.E_RF_read}, RF_w={dev_params.E_RF_write}, "
        f"MACC={dev_params.E_MACC}"
    )

    # --------------------
    # 阶段 3：MILP 求解
    # --------------------
    print("[Stage3] 构建并求解 MILP 映射模型 …")
    t_stage3 = time.perf_counter()
    model, L, k, y, B1, B3, a01, a12 = _solve_full_model(cfg, verbose=True)
    stage3_solve_seconds = time.perf_counter() - t_stage3
    timing_path = _write_stage3_timing(outputs_dir, stage3_solve_seconds)
    print(f"[Stage3] _solve_full_model() 耗时: {stage3_solve_seconds:.6f} s（已写入 {timing_path}）")

    if timing_only:
        print("[Stage3] timing-only：已完成 Stage3，跳过 Python 能量校验与 Stage4/5。")
        return 0

    (
        hatL_01,
        hatL_12,
        hatL_23,
        hatL_34,
        alpha01,
        alpha12,
        B1_map,
        B3_map,
    ) = _extract_mapping_params(cfg, L, k, B1, B3, a01, a12)

    print("[Stage3] 提取到的数据流参数：")
    print("  hatL_01 =", hatL_01)
    print("  hatL_12 =", hatL_12)
    print("  hatL_23 =", hatL_23)
    print("  hatL_34 =", hatL_34)
    print("  alpha01 =", alpha01, " alpha12 =", alpha12)
    print("  B1 =", B1_map)
    print("  B3 =", B3_map)

    # 可选：用 Python 能量模型再算一遍，做 sanity check
    if not args.skip_python_energy_check:
        print("[Stage3] 使用自研 normalized_energy_model 做能量校验 …")
        _check_python_energy(
            L0=L0,
            hatL_12=hatL_12,
            hatL_23=hatL_23,
            hatL_34=hatL_34,
            alpha01=alpha01,
            alpha12=alpha12,
            B1=B1_map,
            B3=B3_map,
            params=dev_params,
        )

    # --------------------
    # 阶段 4：生成 Timeloop 映射文件
    # --------------------
    print("[Stage4] 生成 Timeloop mapping.yaml（不改动 problem.yaml） …")
    problem_path, mapping_path = _update_problem_and_mapping(
        here,
        inputs_dir,
        L0,
        hatL_01,
        hatL_12,
        hatL_23,
        hatL_34,
        alpha01,
        alpha12,
        B1_map,
        B3_map,
        mesh_x,
        mesh_y,
    )
    print(f"[Stage4] 沿用已有 problem.yaml：{problem_path}")
    print(f"[Stage4] mapping.yaml 已更新：{mapping_path}")

    # --------------------
    # 阶段 5：Timeloop 评估
    # --------------------
    print("[Stage5] 调用 timeloop-model 做最终评估 …")
    _run_timeloop_model(top_model, ert_path, outputs_dir, jinja_parse_data=jinja_parse_data)

    print("[Done] 映射求解与评估流程完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
