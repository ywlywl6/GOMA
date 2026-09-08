#!/usr/bin/env python3
"""Single-layer workflow: Accelergy -> reviewed GOMA -> Timeloop-model.

Run ``python mapping_pipeline.py --help``. Generated mapping and timing files
are written under --outputs-dir; source inputs are read-only.
"""
from __future__ import annotations

import argparse
import json
import hashlib
from datetime import datetime, timezone
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

import yaml

import pytimeloop.timeloopfe.v4 as tl
from pytimeloop.timeloopfe.v4.arch import Storage, Container
from pytimeloop.timeloopfe.common import backend_calls
from pytimeloop.timeloopfe.v4.output_parsing import parse_stats_file

import full_model as optimizer_module
import normalized_energy_model as energy_module
from gen_problem_mapping import copy_template, update_problem_file, update_mapping_file
from solver import _solve_full_model, _extract_mapping_params
from timeloop_utils import prepare_environment

PROJECT_ROOT = Path(__file__).resolve().parent


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
    - 将返回的 ERT/ART 写入公共路径，供后续阶段统一使用。
    """
    tmp_out_dir.mkdir(parents=True, exist_ok=True)

    spec = tl.Specification.from_yaml_files(str(top_model), jinja_parse_data=jinja_parse_data or {})
    result = backend_calls.accelergy_app(specification=spec, output_dir=str(tmp_out_dir))

    ert_out_path.parent.mkdir(parents=True, exist_ok=True)
    ert_out_path.write_text(result.ert, encoding="utf-8")
    ert_out_path.with_name("timeloop-model.ART.yaml").write_text(
        result.art,
        encoding="utf-8",
    )
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
    device_params_cls: type,
) -> Any:
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

    return device_params_cls(
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
    params: Any,
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


def _update_problem_and_mapping(
    here: Path,
    output_dir: Path,
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

    - problem.yaml 不由此函数生成；返回路径只用于兼容旧接口。
    - mapping.yaml：从模板复制并调用 update_mapping_file 写入 factors/permutation/keep。
    """
    templates_dir = here / "templates"
    mapping_tmpl = templates_dir / "mapping_template.yaml"

    problem_path = output_dir / "problem.yaml"
    mapping_path = output_dir / "mapping.yaml"

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
) -> float:
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
    stats_path = out_dir / "timeloop-model.stats.txt"
    stats_before = (
        (stats_path.stat().st_mtime_ns, stats_path.stat().st_size)
        if stats_path.exists()
        else None
    )

    prepare_environment()
    # Reuse both architecture-level tables.  Older case outputs only kept the
    # normalized ERT next to ``accelergy_tmp/ART.yaml``; accept that layout so
    # they do not need to regenerate Accelergy data.  The ART is also copied to
    # the filename expected by timeloopfe's output parser.
    art_candidates = [
        ert_path.with_name("timeloop-model.ART.yaml"),
        ert_path.parent / "accelergy_tmp" / "ART.yaml",
        out_dir / "timeloop-model.ART.yaml",
    ]
    art_path = next((path for path in art_candidates if path.is_file()), None)
    extra_input_files = None
    if art_path is not None:
        output_art_path = out_dir / "timeloop-model.ART.yaml"
        if art_path.resolve() != output_art_path.resolve():
            shutil.copy2(art_path, output_art_path)
        extra_input_files = [str(ert_path.resolve()), str(art_path.resolve())]
    else:
        print(
            "[Timeloop] 外部 ERT 缺少配套 ART；回退为 Timeloop/Accelergy "
            "生成本层能量与面积表。"
        )

    tl.call_model(
        spec,
        output_dir=str(out_dir),
        # Loading these tables into the front-end Specification is needed by
        # Stage 2, but the v4 -> v3 model input does not serialize them.  Pass
        # them to Timeloop separately; otherwise it sees only the compound
        # components and invokes Accelergy again for every layer.
        extra_input_files=extra_input_files,
    )

    if not stats_path.exists():
        raise RuntimeError(f"Timeloop 未生成 stats 文件：{stats_path}")
    stats_after = (stats_path.stat().st_mtime_ns, stats_path.stat().st_size)
    if stats_before is not None and stats_after == stats_before:
        raise RuntimeError(
            "Timeloop 调用后 stats 文件未更新，拒绝复用旧结果："
            f"{stats_path}"
        )

    cycles, computes, util, energy_J, accesses = parse_stats_file(str(stats_path))
    total_energy_J = sum(float(v) for v in energy_J.values())
    if computes <= 0:
        raise RuntimeError("Timeloop stats contains no computes")

    pJ_per_compute = total_energy_J / computes * 1e12
    print(f"[Timeloop] pJ/compute = {pJ_per_compute:.6f}")
    print(f"[Timeloop] cycles = {cycles}, computes = {computes}, util = {util:.4f}")
    return pJ_per_compute


def _check_python_energy(
    L0: Dict[str, int],
    hatL_12: Dict[str, int],
    hatL_23: Dict[str, int],
    hatL_34: Dict[str, int],
    alpha01: str,
    alpha12: str,
    B1: Dict[str, int],
    B3: Dict[str, int],
    params: Any,
    compute_normalized_total_energy: Callable,
    objective: float,
) -> float:
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
    dynamic = phi - parts["Eleak"]
    if abs(dynamic - objective) > 1e-5 + 1e-6 * max(abs(dynamic), abs(objective)):
        raise RuntimeError(f"Optimizer/formula mismatch: {objective} vs {dynamic}")
    print(f"[PythonEnergy] phi (含 leak) = {phi:.12g}, parts = {parts}")
    return phi


def parse_args() -> argparse.Namespace:
    here = PROJECT_ROOT
    default_inputs = here / "inputs_my"
    default_outputs = here / "outputs_my"

    parser = argparse.ArgumentParser(description="最终映射求解流水线：Accelergy→MIQCP→Timeloop")
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
        help="兼容旧入口；默认已重新生成 ERT，显式复用请用 --ert-path。",
    )
    parser.add_argument(
        "--skip-python-energy-check",
        action="store_true",
        help="跳过自研 normalized_energy_model 的能量校验（默认开启，检查目标与公式一致性）。",
    )
    parser.add_argument(
        "--mip-gap",
        type=float,
        default=None,
        help="覆盖 Gurobi 相对 MIP gap；未指定时默认使用 0（证明最优）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prepare_environment()
    if args.mip_gap is not None and not 0.0 <= args.mip_gap <= 1.0:
        raise ValueError("--mip-gap 必须位于 [0, 1]。")
    here = PROJECT_ROOT
    inputs_dir = args.inputs_dir.expanduser().resolve()
    outputs_dir = args.outputs_dir.expanduser().resolve()
    timing_only = _env_flag(ENV_STAGE3_TIME_ONLY)

    print("[Model] review1_bugfix")

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

    generated_mapping = (outputs_dir / "mapping.yaml").resolve()
    if generated_mapping in {arch_yaml.resolve(), problem_yaml.resolve(), (inputs_dir / "mapping.yaml").resolve()}:
        raise ValueError("--outputs-dir would overwrite a source input; use a separate directory")
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
        print("[Stage1] 调用 Accelergy 生成 ERT …")
        ert_path = _generate_ert_with_accelergy(
            top_model, outputs_dir / "accelergy_tmp", ert_path,
            jinja_parse_data=jinja_parse_data,
        )
        print(f"[Stage1] ERT 已生成：{ert_path}")
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
    dev_params = _device_params_from_ert_path(
        ert_path,
        storage_width_datawidth=storage_width_datawidth,
        device_params_cls=energy_module.DeviceParams,
    )
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
    # 阶段 3：MIQCP 求解
    # --------------------
    print("[Stage3] 构建并求解 MIQCP 映射模型 …")
    t_stage3 = time.perf_counter()
    model, L, k, y, B1, B3, a01, a12 = _solve_full_model(
        cfg,
        build_model_full=optimizer_module.build_model_full,
        verbose=True,
        mip_gap=args.mip_gap,
    )
    stage3_solve_seconds = time.perf_counter() - t_stage3
    timing_path = _write_stage3_timing(outputs_dir, stage3_solve_seconds)
    print(f"[Stage3] _solve_full_model() 耗时: {stage3_solve_seconds:.6f} s（已写入 {timing_path}）")

    import gurobipy as gp
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_variant": "review1_bugfix", "gurobi_version": list(gp.gurobi.version()),
        "python_version": sys.version, "cfg": cfg,
        "solver_parameters": {name: getattr(model.Params, name) for name in (
            "NonConvex", "IntegralityFocus", "IntFeasTol", "FeasibilityTol",
            "NumericFocus", "DualReductions", "MIPGap")},
        "solver": {"status": model.Status, "objective_dynamic_pJ_per_MAC": model.ObjVal,
                   "bound_dynamic_pJ_per_MAC": model.ObjBound, "gap": model.MIPGap},
        "stage3_solve_seconds": stage3_solve_seconds,
        "source_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                          for name in ("full_model.py", "normalized_energy_model.py", "solver.py")},
        "inputs": {name: {"path": str(path.resolve()),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for name, path in (("architecture", arch_yaml), ("problem", problem_yaml), ("ERT", ert_path))},
    }
    (outputs_dir / "run_record.json").write_text(json.dumps(record, indent=2) + "\n")
    if timing_only:
        model.dispose()
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

    python_energy = None
    # 可选：用 Python 能量模型再算一遍，做 sanity check
    if not args.skip_python_energy_check:
        print("[Stage3] 使用自研 normalized_energy_model 做能量校验 …")
        python_energy = _check_python_energy(
            L0=L0,
            hatL_12=hatL_12,
            hatL_23=hatL_23,
            hatL_34=hatL_34,
            alpha01=alpha01,
            alpha12=alpha12,
            B1=B1_map,
            B3=B3_map,
            params=dev_params,
            compute_normalized_total_energy=energy_module.compute_normalized_total_energy,
            objective=float(model.ObjVal),
        )

    # --------------------
    # 阶段 4：生成 Timeloop 映射文件
    # --------------------
    print("[Stage4] 生成 Timeloop mapping.yaml（不改动 problem.yaml） …")
    problem_path, mapping_path = _update_problem_and_mapping(
        here,
        outputs_dir,
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
    jinja_parse_data["mapping"] = str(mapping_path.resolve())
    print(f"[Stage4] 沿用已有 problem.yaml：{problem_yaml}")
    print(f"[Stage4] mapping.yaml 已更新：{mapping_path}")

    # --------------------
    # 阶段 5：Timeloop 评估
    # --------------------
    print("[Stage5] 调用 timeloop-model 做最终评估 …")
    timeloop_energy = _run_timeloop_model(top_model, ert_path, outputs_dir, jinja_parse_data=jinja_parse_data)
    if python_energy is not None:
        error = abs(python_energy - timeloop_energy)
        match = error <= 1e-5 + 1e-6 * max(abs(python_energy), abs(timeloop_energy))
    else:
        error, match = None, None
    record["energy"] = {
        "units": "pJ/MAC", "python_total_including_leakage": python_energy,
        "timeloop_total_including_leakage": timeloop_energy,
        "absolute_error": error, "match": match, "atol": 1e-5, "rtol": 1e-6,
    }
    (outputs_dir / "run_record.json").write_text(json.dumps(record, indent=2) + "\n")
    model.dispose()
    if match is False:
        raise RuntimeError(f"Python/Timeloop energy mismatch; see {outputs_dir / 'run_record.json'}")

    print("[Done] 映射求解与评估流程完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
