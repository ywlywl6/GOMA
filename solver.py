"""Current GOMA solve policy and validated integer mapping extraction."""
from __future__ import annotations
import math
from typing import Callable, Dict, Optional, Tuple
from full_model import build_model_full

AXES = ("x", "y", "z")

def integer_value(value: float) -> int:
    if not math.isfinite(value) or abs(value - round(value)) > 1e-6:
        raise RuntimeError(f"Non-integral mapping value: {value!r}")
    return int(round(value))

def _solve_full_model(
    cfg: Dict[str, object],
    build_model_full: Callable = build_model_full,
    verbose: bool = True,
    mip_gap: Optional[float] = None,
):
    """
    阶段 3：基于 cfg 与能量模型，求解最优数据流（L/k/B/alpha）。

    返回：
      - model, L, k, y, B1, B3, a01, a12  （与 full_model.build_model_full 一致）
    并在 verbose 时打印一份简要解读信息。
    """
    import gurobipy as gp  # 本地导入，避免在未安装 gurobi 时影响模块导入

    # Avoid accepting trickle-flow incumbents whose binary variables are only
    # integral within the default tolerance but whose rounded mapping has a
    # different traffic/energy value.  IntegralityFocus reduces this risk but
    # does not guarantee it; IntFeasTol=1e-9 is needed for the long-sequence
    # cases whose smallest normalized traffic is below the default 1e-5.
    # FeasibilityTol likewise prevents a nominally nonnegative normalized-flow
    # variable from becoming slightly negative and reducing the objective.
    grb_params = {
        "OutputFlag": int(verbose),
        "NonConvex": 2,
        "IntegralityFocus": 1,
        "IntFeasTol": 1e-9,
        "FeasibilityTol": 1e-9,
        # The non-convex MIQCP can otherwise become presolve-start dependent:
        # on a known A100 layer, the default dual reductions discarded a
        # feasible incumbent that was strictly better than the solution later
        # reported as globally optimal.  NumericFocus makes the bilinear
        # processing more conservative, while disabling dual reductions
        # removes the specific invalid elimination path.  Keep the remaining
        # presolve machinery enabled because it is both useful and safe under
        # these settings in the audited cases.
        "NumericFocus": 3,
        "DualReductions": 0,
        # Require a proven optimum by default.  Experiments may still
        # override this through --mip-gap when intentionally benchmarking a
        # relaxed termination criterion.
        "MIPGap": 0.0,
    }
    if mip_gap is not None and not 0 <= mip_gap <= 1:
        raise ValueError("mip_gap must be in [0, 1]")
    if mip_gap is not None:
        grb_params["MIPGap"] = float(mip_gap)
    model, L, k, y, B1, B3, a01, a12 = build_model_full(cfg, params=grb_params)

    model.optimize()

    if model.Status not in (gp.GRB.OPTIMAL, gp.GRB.SUBOPTIMAL, gp.GRB.TIME_LIMIT):
        raise RuntimeError(f"Gurobi 求解失败，状态码={model.Status}")

    if model.SolCount == 0:
        raise RuntimeError(f"No feasible incumbent (status={model.Status})")

    if verbose:
        # 泄露能量（仅报告用，不进目标）
        num_pe = integer_value(k[(2, "x")].X) * integer_value(k[(2, "y")].X) * integer_value(k[(2, "z")].X)
        E_leak_per_cycle = cfg["E_SRAM_leak"] + cfg["E_RF_leak"] * num_pe
        E_leak_norm = E_leak_per_cycle / num_pe if num_pe > 0 else 0.0
        total_energy = float(model.ObjVal) + float(E_leak_norm)
        print(
            f"[MIQCP] status={model.Status}, Total Normalized Energy={total_energy:.6f} "
            f"(Dynamic={model.ObjVal:.6f} + Leak={E_leak_norm:.6f})"
        )

        # 找出行走轴
        alpha01 = max(a01, key=lambda d: a01[d].X)
        alpha12 = max(a12, key=lambda d: a12[d].X)
        print(f"[MIQCP] alpha_0-1 = {alpha01}, alpha_1-2 = {alpha12}")

        # B1/B3 驻留开关
        print("[MIQCP] B1:", {d: integer_value(B1[d].X) for d in AXES})
        print("[MIQCP] B3:", {d: integer_value(B3[d].X) for d in AXES})

        # 三级块长 / 整除比
        for i in AXES:
            print(
                f"[MIQCP] {i}: "
                f"L1={integer_value(L[(1, i)].X)}, L2={integer_value(L[(2, i)].X)}, L3={integer_value(L[(3, i)].X)} | "
                f"k0={integer_value(k[(0, i)].X)}, k1={integer_value(k[(1, i)].X)}, "
                f"k2={integer_value(k[(2, i)].X)}, k3={integer_value(k[(3, i)].X)}"
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
    阶段 3→4 之间的桥接：把 MIQCP 解翻译为自研数据流参数：

      - hatL_01 / hatL_12 / hatL_23 / hatL_34  （整除关系：L0 = k0*k1*k2*k3）
      - alpha01 / alpha12                       （行走轴）
      - B1 / B3                                 （逐轴驻留开关）
    """
    # k[(p, dim)] 即各阶段的整除因子，直接对应 hatL_{0-1}, hatL_{1-2}, hatL_{2-3}, hatL_{3-4}
    hatL_01 = {d: integer_value(k[(0, d)].X) for d in AXES}
    hatL_12 = {d: integer_value(k[(1, d)].X) for d in AXES}
    hatL_23 = {d: integer_value(k[(2, d)].X) for d in AXES}
    hatL_34 = {d: integer_value(k[(3, d)].X) for d in AXES}

    # 行走轴 one-hot
    alpha01 = max(a01, key=lambda d: a01[d].X)
    alpha12 = max(a12, key=lambda d: a12[d].X)

    B1_map = {d: integer_value(B1[d].X) for d in AXES}
    B3_map = {d: integer_value(B3[d].X) for d in AXES}

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


