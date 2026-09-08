"""Convert GOMA geometry to Timeloop problem/mapping YAMLs."""
from __future__ import annotations
import copy
import shutil
from math import gcd
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import yaml

AXES = ("x", "y", "z")
AX_UP = {"x": "X", "y": "Y", "z": "Z"}
DATASPACE_OF_AXIS = {"x": "Inputs", "y": "Weights", "z": "Outputs"}

DRAM_TARGET = "DRAM"
SRAM_TARGET = "shared_glb"
PE_TARGET = "PE"
PEY_TARGET = "PEy"
RF_TARGET = "regfile"


def compute_hatL01(L0: Dict[str, int], hatL_12: Dict[str, int], hatL_23: Dict[str, int], hatL_34: Dict[str, int]) -> Dict[str, int]:
    hatL_01: Dict[str, int] = {}
    for ax in AXES:
        denom = hatL_12[ax] * hatL_23[ax] * hatL_34[ax]
        numer = L0[ax]
        if denom == 0 or numer % denom != 0:
            raise RuntimeError(f"L0[{ax}]={numer} 无法被 hatL_12*hatL_23*hatL_34={denom} 整除")
        hatL_01[ax] = numer // denom
    return hatL_01


def _perm_from_alpha(alpha: str) -> List[str]:
    alpha = alpha.lower()
    order = [alpha] + [ax for ax in AXES if ax != alpha]
    return [AX_UP[ax] for ax in order]


def _factors_list(d: Dict[str, int]) -> List[str]:
    return [f"{AX_UP[ax]}={int(d[ax])}" for ax in AXES]


def _dataspace_lists(B_entry: Dict[str, int]) -> Tuple[List[str], List[str]]:
    keep: List[str] = []
    bypass: List[str] = []
    for ax in AXES:
        ds = DATASPACE_OF_AXIS[ax]
        if int(B_entry[ax]) == 1:
            keep.append(ds)
        else:
            bypass.append(ds)
    return keep, bypass


def copy_template(template_path: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, out_path)


def _find_entry(entries: Iterable[Dict[str, Any]], target: str, map_type: Tuple[str, ...]) -> Dict[str, Any]:
    types = map_type
    if isinstance(map_type, str):  # pragma: no cover - defensive
        types = (map_type,)
    for entry in entries:
        if entry.get("target") == target and entry.get("type") in types:
            return entry
    raise RuntimeError(f"在 mapping 模板中找不到 target={target}, type={types} 的条目")


def update_problem_file(problem_path: Path, L0: Dict[str, int]) -> None:
    data = yaml.safe_load(problem_path.read_text(encoding="utf-8"))
    problem = data.setdefault("problem", {})
    instance = problem.setdefault("instance", {})
    instance["X"] = int(L0["x"])
    instance["Y"] = int(L0["y"])
    instance["Z"] = int(L0["z"])
    problem_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def update_mapping_file(mapping_path: Path,
                        hatL_01: Dict[str, int], hatL_12: Dict[str, int], hatL_23: Dict[str, int], hatL_34: Dict[str, int],
                        alpha01: str, alpha12: str,
                        B1: Dict[str, int], B3: Dict[str, int],
                        *, mesh_x: Optional[int] = None, mesh_y: Optional[int] = None) -> None:
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    entries = data.get("mapping")
    if not isinstance(entries, list):
        raise RuntimeError("mapping 模板格式错误：缺少 mapping 列表")

    # 0→1 DRAM temporal
    dram_entry = _find_entry(entries, DRAM_TARGET, ("temporal",))
    dram_entry["factors"] = _factors_list(hatL_01)
    dram_entry["permutation"] = _perm_from_alpha(alpha01)

    # 1→2 SRAM temporal + dataspace
    sram_temporal = _find_entry(entries, SRAM_TARGET, ("temporal",))
    sram_temporal["factors"] = _factors_list(hatL_12)
    sram_temporal["permutation"] = _perm_from_alpha(alpha12)

    sram_dataspace = _find_entry(entries, SRAM_TARGET, ("dataspace", "bypass"))
    keep1, bypass1 = _dataspace_lists(B1)
    sram_dataspace["keep"] = keep1
    sram_dataspace["bypass"] = bypass1

    # 2→3 PE spatial
    pe_spatial_entries = [entry for entry in entries if entry.get("target") == PE_TARGET and entry.get("type") == "spatial"]
    if not pe_spatial_entries:
        raise RuntimeError(f"在 mapping 模板中找不到 target={PE_TARGET}, type=('spatial',) 的条目")
    if len(pe_spatial_entries) > 1:
        raise RuntimeError(
            f"mapping 模板中存在多条 target={PE_TARGET}, type='spatial'，目前不支持：{len(pe_spatial_entries)} 条"
        )

    mesh_x_val = int(mesh_x) if mesh_x is not None else None
    mesh_y_val = int(mesh_y) if mesh_y is not None else None
    use_2d_mesh = (
        mesh_x_val is not None
        and mesh_y_val is not None
        and mesh_x_val > 1
        and mesh_y_val > 1
    )

    if use_2d_mesh:
        # hatL_23 的乘积即 N_PE（full_model 约束 k2x*k2y*k2z==N_PE），这里要求与 meshX*meshY 匹配。
        num_pe = int(hatL_23["x"]) * int(hatL_23["y"]) * int(hatL_23["z"])
        if mesh_x_val * mesh_y_val != num_pe:
            raise RuntimeError(
                f"meshX*meshY={mesh_x_val * mesh_y_val} 与 hatL_23 乘积 N_PE={num_pe} 不一致，"
                "无法生成等价的 2D spatial mapping。"
            )

        pe_spatial = pe_spatial_entries[0]

        pey_spatial_entries = [
            entry for entry in entries if entry.get("target") == PEY_TARGET and entry.get("type") == "spatial"
        ]
        if len(pey_spatial_entries) > 1:
            raise RuntimeError(
                f"mapping 模板中存在多条 target={PEY_TARGET}, type='spatial'，目前不支持：{len(pey_spatial_entries)} 条"
            )
        if pey_spatial_entries:
            pey_spatial = pey_spatial_entries[0]
        else:
            # 模板默认只有一条 PE spatial；这里复制一条并改名为 PEy，代表外层空间展开。
            pey_spatial = copy.deepcopy(pe_spatial)
            pey_spatial["target"] = PEY_TARGET
            pe_idx = entries.index(pe_spatial)
            entries.insert(pe_idx, pey_spatial)

        # 将 hatL_23 按 meshX 与 meshY 拆分到两条 spatial 中：
        #   Π(entry0.factors) = meshX
        #   Π(entry1.factors) = meshY
        # 且对每个轴满足 entry0[ax] * entry1[ax] = hatL_23[ax]。
        remaining = int(mesh_x_val)
        first_factors: Dict[str, int] = {}
        second_factors: Dict[str, int] = {}
        for ax in ("z", "y", "x"):  # 固定顺序，确定性拆分；优先从 Z 轴拿因子
            take = gcd(int(hatL_23[ax]), remaining)
            first_factors[ax] = int(take)
            second_factors[ax] = int(hatL_23[ax]) // int(take)
            remaining //= int(take)
        if remaining != 1:
            raise RuntimeError(
                f"无法将 meshX={mesh_x_val} 拆分到 hatL_23={hatL_23} 的各轴因子中（remaining={remaining}）。"
            )

        pey_spatial["factors"] = _factors_list(first_factors)
        pe_spatial["factors"] = _factors_list(second_factors)
        # Timeloop 在 2D mesh 上需要显式 split 才能将两条 spatial 映射到不同物理维度；
        # 否则会被当作同一维度叠乘，导致 X 展开超过硬件实例数。
        pey_spatial["split"] = 999
        pe_spatial["split"] = 0
    else:
        # 1D：只更新第一条 PE spatial 指令
        pe_spatial_entries[0]["factors"] = _factors_list(hatL_23)
        pe_spatial_entries[0].pop("split", None)
        # 若之前跑过 2D，mapping 中可能残留 PEy spatial，需要清理以兼容仅有 PE 的架构。
        for entry in list(entries):
            if entry.get("target") == PEY_TARGET and entry.get("type") == "spatial":
                entries.remove(entry)

    # 3→4 RF temporal + dataspace
    rf_temporal = _find_entry(entries, RF_TARGET, ("temporal",))
    rf_temporal["factors"] = _factors_list(hatL_34)

    rf_dataspace = _find_entry(entries, RF_TARGET, ("dataspace", "bypass"))
    keep3, bypass3 = _dataspace_lists(B3)
    rf_dataspace["keep"] = keep3
    rf_dataspace["bypass"] = bypass3

    mapping_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


