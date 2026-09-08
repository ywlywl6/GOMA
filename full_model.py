# -*- coding: utf-8 -*-
"""Gurobi optimizer for the reviewed ``review1_bugfix`` GOMA model.

This module implements the traffic and energy equations in
the reviewed equations described in ``MODEL_NOTES.md``.  In particular, it keeps receiver-side
and source-side traffic separate, derives reduction old-read traffic from
ungated active-path geometry, and models the level-3 cross-SRAM-tile reuse
indicator ``chi3``.

The public ``build_model_full`` interface and its eight-element return tuple
match the original public optimizer interface.  The objective contains
normalized dynamic energy only.  Leakage remains a fixed reporting term
because ``N_PE`` is fixed by the model constraints.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Tuple

import gurobipy as gp
from gurobipy import GRB


Dims = ("x", "y", "z")

# Normalized traffic variables can be O(1e-6), while their energy weights can
# be O(1e2).  Writing their defining rows in milli-count units keeps absolute
# feasibility residuals from becoming visible objective errors.
_NORMALIZED_FLOW_SCALE = 1000.0


def _and_bins(
    model: gp.Model,
    bits: Iterable[gp.Var],
    name: str,
) -> gp.Var:
    """Return a binary variable equal to the AND of positive literals."""
    operands = tuple(bits)
    if not operands:
        raise ValueError("_and_bins requires at least one operand")
    result = model.addVar(vtype=GRB.BINARY, name=name)
    for index, bit in enumerate(operands):
        model.addConstr(result <= bit, name=f"{name}_ub_{index}")
    model.addConstr(
        result >= gp.quicksum(operands) - (len(operands) - 1),
        name=f"{name}_lb",
    )
    return result


def _and_bin_neg(
    model: gp.Model,
    positive: gp.Var,
    negative: gp.Var,
    name: str,
) -> gp.Var:
    """Return ``positive AND (NOT negative)``."""
    result = model.addVar(vtype=GRB.BINARY, name=name)
    model.addConstr(result <= positive, name=f"{name}_ub_pos")
    model.addConstr(result <= 1 - negative, name=f"{name}_ub_neg")
    model.addConstr(result >= positive - negative, name=f"{name}_lb")
    return result


def _nor_bins(
    model: gp.Model,
    first: gp.Var,
    second: gp.Var,
    name: str,
) -> gp.Var:
    """Return ``(NOT first) AND (NOT second)``."""
    result = model.addVar(vtype=GRB.BINARY, name=name)
    model.addConstr(result <= 1 - first, name=f"{name}_ub_first")
    model.addConstr(result <= 1 - second, name=f"{name}_ub_second")
    model.addConstr(result >= 1 - first - second, name=f"{name}_lb")
    return result


def _gate_product(
    model: gp.Model,
    gate: gp.Var,
    value: gp.Var,
    ub_value: float,
    name: str,
) -> gp.Var:
    """Model ``result = gate * value`` for ``0 <= value <= ub_value``.

    The convex-hull linearization is already well scaled when ``ub_value`` is
    at most one.  For larger domains, indicator constraints avoid injecting
    ``ub_value`` as a Big-M matrix coefficient.
    """
    use_indicator = float(ub_value) > 1.0
    result_vtype = (
        value.VType
        if use_indicator and value.VType in (GRB.INTEGER, GRB.BINARY)
        else GRB.CONTINUOUS
    )
    result = model.addVar(
        vtype=result_vtype,
        lb=0.0,
        ub=float(ub_value),
        name=name,
    )
    if not use_indicator:
        # These variables are normalized counts and can be much smaller than
        # one, while their objective weights can be hundreds of energy units.
        # Express the identical hull in milli-count units so a row residual at
        # FeasibilityTol cannot be magnified into a visible energy error.
        model.addConstr(
            _NORMALIZED_FLOW_SCALE * result <= _NORMALIZED_FLOW_SCALE * value,
            name=f"{name}_ub_value",
        )
        model.addConstr(
            _NORMALIZED_FLOW_SCALE * result
            <= _NORMALIZED_FLOW_SCALE * float(ub_value) * gate,
            name=f"{name}_ub_gate",
        )
        model.addConstr(
            _NORMALIZED_FLOW_SCALE * result
            >= _NORMALIZED_FLOW_SCALE * value
            - _NORMALIZED_FLOW_SCALE * float(ub_value) * (1 - gate),
            name=f"{name}_lb",
        )
    else:
        # Preserve integrality when the gated value is integral.  Otherwise a
        # continuous result could drift within FeasibilityTol and the error can
        # be amplified by downstream energy coefficients.
        model.addGenConstrIndicator(
            gate,
            0,
            result == 0.0,
            name=f"{name}_off",
        )
        model.addGenConstrIndicator(
            gate,
            1,
            result == value,
            name=f"{name}_on",
        )
    return result


def _is_one_indicator(
    model: gp.Model,
    value: gp.Var,
    ub_value: int,
    name: str,
) -> gp.Var:
    """Return a binary variable that is one exactly when integer ``value`` is 1."""
    indicator = model.addVar(vtype=GRB.BINARY, name=name)
    # value has lb=1.  Indicators encode both directions without using its
    # potentially large upper bound as a Big-M coefficient.
    model.addGenConstrIndicator(
        indicator,
        1,
        value == 1,
        name=f"{name}_force_one",
    )
    model.addGenConstrIndicator(
        indicator,
        0,
        value >= 2,
        name=f"{name}_force_nonunit",
    )
    return indicator


def build_model_full(
    cfg: Dict[str, Any],
    params: Dict[str, Any] | None = None,
) -> Tuple[
    gp.Model,
    Dict[Tuple[int, str], gp.Var],
    Dict[Tuple[int, str], gp.Var],
    Dict[Tuple[int, str], gp.Var],
    Dict[str, gp.Var],
    Dict[str, gp.Var],
    Dict[str, gp.Var],
    Dict[str, gp.Var],
]:
    r"""Build the complete reviewed non-convex integer optimization model.

    Returns ``(model, L, k, y, B1, B3, a01, a12)`` exactly like the submission
    optimizer.  Here ``k[(p,d)]`` is :math:`\hat L_d^{(p-(p+1))}` and
    ``y[(p,d)]`` is ``1 / L[(p,d)]``.
    """
    L0_raw = cfg["L0"]
    L0 = {d: int(L0_raw[d]) for d in Dims}
    if any(L0[d] <= 0 or L0[d] != L0_raw[d] for d in Dims):
        raise ValueError("cfg['L0'] must contain positive integer x/y/z lengths")

    C1 = float(cfg["C1"])
    C3 = float(cfg["C3"])
    N_PE = int(cfg["N_PE"])
    if C1 < 0 or C3 < 0:
        raise ValueError("C1 and C3 must be non-negative")
    if N_PE <= 0 or N_PE != cfg["N_PE"]:
        raise ValueError("N_PE must be a positive integer")

    E_DDR_r = float(cfg["E_DDR_r"])
    E_DDR_w = float(cfg["E_DDR_w"])
    E_SRAM_r = float(cfg["E_SRAM_r"])
    E_SRAM_w = float(cfg["E_SRAM_w"])
    E_RF_r = float(cfg["E_RF_r"])
    E_RF_w = float(cfg["E_RF_w"])
    E_MACC = float(cfg["E_MACC"])

    model = gp.Model("mapper_review1_bugfix")
    model.Params.NonConvex = 2
    if params:
        for key, value in params.items():
            setattr(model.Params, key, value)

    # ------------------------------------------------------------------
    # Hierarchical tile lengths, adjacent ratios, and public reciprocals.
    # ------------------------------------------------------------------
    L: Dict[Tuple[int, str], gp.Var] = {}
    k: Dict[Tuple[int, str], gp.Var] = {}
    y: Dict[Tuple[int, str], gp.Var] = {}

    for level in (1, 2, 3):
        for d in Dims:
            L[(level, d)] = model.addVar(
                vtype=GRB.INTEGER,
                lb=1,
                ub=L0[d],
                name=f"L_{level}_{d}",
            )
            y[(level, d)] = model.addVar(
                vtype=GRB.CONTINUOUS,
                lb=1.0 / float(L0[d]),
                ub=1.0,
                name=f"y_{level}_{d}",
            )

    for stage in (0, 1, 2, 3):
        for d in Dims:
            k[(stage, d)] = model.addVar(
                vtype=GRB.INTEGER,
                lb=1,
                ub=L0[d],
                name=f"k_{stage}_{d}",
            )

    B1 = {d: model.addVar(vtype=GRB.BINARY, name=f"B1_{d}") for d in Dims}
    B3 = {d: model.addVar(vtype=GRB.BINARY, name=f"B3_{d}") for d in Dims}
    a01 = {d: model.addVar(vtype=GRB.BINARY, name=f"a01_{d}") for d in Dims}
    a12 = {d: model.addVar(vtype=GRB.BINARY, name=f"a12_{d}") for d in Dims}
    model.addConstr(gp.quicksum(a01.values()) == 1, name="onehot_a01")
    model.addConstr(gp.quicksum(a12.values()) == 1, name="onehot_a12")

    model.update()

    for d in Dims:
        model.addQConstr(
            k[(0, d)] * L[(1, d)] == L0[d],
            name=f"hierarchy_01_{d}",
        )
        model.addQConstr(
            k[(1, d)] * L[(2, d)] == L[(1, d)],
            name=f"hierarchy_12_{d}",
        )
        model.addQConstr(
            k[(2, d)] * L[(3, d)] == L[(2, d)],
            name=f"hierarchy_23_{d}",
        )
        model.addConstr(L[(3, d)] == k[(3, d)], name=f"hierarchy_34_{d}")

    for level in (1, 2, 3):
        for d in Dims:
            model.addQConstr(
                y[(level, d)] * L[(level, d)] == 1.0,
                name=f"reciprocal_L_{level}_{d}",
            )

    invk2: Dict[str, gp.Var] = {}
    reciprocal_L3k1: Dict[str, gp.Var] = {}
    reciprocal_L3k1k0: Dict[str, gp.Var] = {}
    for d in Dims:
        invk2[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=1.0 / float(L0[d]),
            ub=1.0,
            name=f"inv_k2_{d}",
        )
        model.addQConstr(
            invk2[d] * k[(2, d)] == 1.0,
            name=f"reciprocal_k2_{d}",
        )

        product_L3k1 = model.addVar(
            vtype=GRB.INTEGER,
            lb=1,
            ub=L0[d],
            name=f"product_L3k1_{d}",
        )
        product_L3k1k0 = model.addVar(
            vtype=GRB.INTEGER,
            lb=1,
            ub=L0[d],
            name=f"product_L3k1k0_{d}",
        )
        reciprocal_L3k1[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=1.0 / float(L0[d]),
            ub=1.0,
            name=f"inv_L3k1_{d}",
        )
        reciprocal_L3k1k0[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=1.0 / float(L0[d]),
            ub=1.0,
            name=f"inv_L3k1k0_{d}",
        )
        model.addQConstr(
            product_L3k1 == L[(3, d)] * k[(1, d)],
            name=f"define_product_L3k1_{d}",
        )
        model.addQConstr(
            product_L3k1k0 == product_L3k1 * k[(0, d)],
            name=f"define_product_L3k1k0_{d}",
        )
        model.addQConstr(
            reciprocal_L3k1[d] * product_L3k1 == 1.0,
            name=f"reciprocal_product_L3k1_{d}",
        )
        model.addQConstr(
            reciprocal_L3k1k0[d] * product_L3k1k0 == 1.0,
            name=f"reciprocal_product_L3k1k0_{d}",
        )

    # ---------------------------------------------------------------
    # Walking axes after unit-trip temporal loops have been removed.
    # ---------------------------------------------------------------
    unit_k0 = {
        d: _is_one_indicator(model, k[(0, d)], L0[d], f"unit_k0_{d}")
        for d in Dims
    }
    unit_k1 = {
        d: _is_one_indicator(model, k[(1, d)], L0[d], f"unit_k1_{d}")
        for d in Dims
    }
    all_unit_01 = _and_bins(model, unit_k0.values(), "all_unit_01")
    all_unit_12 = _and_bins(model, unit_k1.values(), "all_unit_12")

    for d in Dims:
        # A non-degenerate stage may select only a non-unit loop.
        model.addConstr(
            a01[d] <= 1 - unit_k0[d] + all_unit_01,
            name=f"physical_a01_{d}",
        )
        model.addConstr(
            a12[d] <= 1 - unit_k1[d] + all_unit_12,
            name=f"physical_a12_{d}",
        )
        # Reviewed convention: a fully degenerate stage 1--2 inherits a01.
        model.addConstr(
            a12[d] - a01[d] <= 1 - all_unit_12,
            name=f"canonical_a12_upper_{d}",
        )
        model.addConstr(
            a01[d] - a12[d] <= 1 - all_unit_12,
            name=f"canonical_a12_lower_{d}",
        )

    # The patch does not need a physical axis when stage 0--1 is all-unit.
    # Select x deterministically to satisfy the one-hot public interface.
    model.addConstr(a01["x"] >= all_unit_01, name="canonical_all_unit_a01_x")

    # chi3[d] = [d=a01] [d=a12] product_{u!=d} [k1[u]=1].
    chi3: Dict[str, gp.Var] = {}
    for d in Dims:
        orthogonal = tuple(u for u in Dims if u != d)
        chi3[d] = _and_bins(
            model,
            (a01[d], a12[d], unit_k1[orthogonal[0]], unit_k1[orthogonal[1]]),
            f"chi3_{d}",
        )

    # ------------------------------------------
    # Capacity and full-PE-utilization limits.
    # ------------------------------------------
    def add_capacity_constraint(
        level: int,
        capacity: float,
        residency: Dict[str, gp.Var],
        name: str,
    ) -> None:
        if capacity == 0.0:
            # Every physical footprint is at least one word, hence zero
            # capacity permits only complete bypass at this level.
            for d in Dims:
                model.addConstr(
                    residency[d] == 0,
                    name=f"{name}_zero_{d}",
                )
            return

        # For datum d, its physical footprint spans the other two axes.  Gate
        # one length with indicators and multiply it by the other length.  This
        # is exactly B[d] * L[u] * L[v], but avoids area upper bounds and Big-M
        # coefficients as large as L0[u] * L0[v].
        footprint_axes = {
            "x": ("y", "z"),
            "y": ("x", "z"),
            "z": ("x", "y"),
        }

        # Measure occupied area in scaled word units.  sqrt(C) balances the
        # quadratic coefficient 1/scale against the capacity RHS C/scale; it
        # changes only numerical units, not the feasible mappings.
        scale = max(1.0, math.sqrt(float(capacity)))
        scaled_capacity = float(capacity) / scale
        occupied_scaled: Dict[str, gp.Var] = {}

        for d, (u, v) in footprint_axes.items():
            selected_u = model.addVar(
                vtype=GRB.CONTINUOUS,
                lb=0.0,
                ub=float(L0[u]),
                name=f"selected_length_{level}_{d}",
            )
            model.addGenConstrIndicator(
                residency[d],
                0,
                selected_u == 0.0,
                name=f"selected_length_{level}_{d}_off",
            )
            model.addGenConstrIndicator(
                residency[d],
                1,
                selected_u == L[(level, u)],
                name=f"selected_length_{level}_{d}_on",
            )

            occupied_scaled[d] = model.addVar(
                vtype=GRB.CONTINUOUS,
                lb=0.0,
                ub=scaled_capacity,
                name=f"occupied_scaled_{level}_{d}",
            )
            model.addQConstr(
                occupied_scaled[d]
                == selected_u * L[(level, v)] / scale,
                name=f"define_occupied_scaled_{level}_{d}",
            )

        model.addConstr(
            gp.quicksum(occupied_scaled.values()) <= scaled_capacity,
            name=name,
        )

    add_capacity_constraint(1, C1, B1, "capacity_level_1")
    add_capacity_constraint(3, C3, B3, "capacity_level_3")

    product_k2_xy = model.addVar(
        vtype=GRB.INTEGER,
        lb=1,
        ub=max(1, N_PE),
        name="product_k2_xy",
    )
    model.addQConstr(
        product_k2_xy == k[(2, "x")] * k[(2, "y")],
        name="pe_product_xy",
    )
    model.addQConstr(
        product_k2_xy * k[(2, "z")] == N_PE,
        name="pe_product_xyz",
    )

    # -----------------------------------------------------------------
    # src--1: normalized receiver/source traffic and reduction boundary.
    # -----------------------------------------------------------------
    count1: Dict[str, gp.Var] = {}
    for d in Dims:
        resident_alpha = _and_bins(model, (B1[d], a01[d]), f"src1_resident_alpha_{d}")
        resident_background = _and_bin_neg(
            model,
            B1[d],
            a01[d],
            f"src1_resident_background_{d}",
        )
        background_count = _gate_product(
            model,
            resident_background,
            y[(1, d)],
            1.0,
            f"src1_background_count_{d}",
        )
        count1[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=0.0,
            ub=1.0,
            name=f"Nnorm_receiver_src1_{d}",
        )
        model.addConstr(
            _NORMALIZED_FLOW_SCALE * count1[d]
            == _NORMALIZED_FLOW_SCALE
            * (
                resident_alpha * (1.0 / float(L0[d]))
                + background_count
            ),
            name=f"define_Nnorm_receiver_src1_{d}",
        )

    old_count1_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_src1_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_count1_z
        == _NORMALIZED_FLOW_SCALE
        * (count1["z"] - B1["z"] / float(L0["z"])),
        name="define_Nnorm_oldread_src1_z",
    )

    E_src1 = gp.LinExpr(0.0)
    for d in ("x", "y"):
        E_src1 += (E_DDR_r + E_SRAM_w) * count1[d]
    E_src1 += E_DDR_w * count1["z"]
    E_src1 += (E_DDR_r + E_SRAM_w) * old_count1_z

    # -----------------------------------------------------------------
    # src--3: receiver traffic, source traffic, chi3, and path rho.
    # -----------------------------------------------------------------
    receiver3: Dict[str, gp.Var] = {}
    source3: Dict[str, gp.Var] = {}
    source3_sram: Dict[str, gp.Var] = {}
    for d in Dims:
        alpha_without_chi = model.addVar(
            vtype=GRB.BINARY,
            name=f"a12_without_chi3_{d}",
        )
        model.addConstr(
            alpha_without_chi == a12[d] - chi3[d],
            name=f"define_a12_without_chi3_{d}",
        )
        case_chi = _and_bins(model, (B3[d], chi3[d]), f"src3_case_chi_{d}")
        case_alpha = _and_bins(
            model,
            (B3[d], alpha_without_chi),
            f"src3_case_alpha_{d}",
        )
        case_background = _and_bin_neg(
            model,
            B3[d],
            a12[d],
            f"src3_case_background_{d}",
        )
        model.addConstr(
            case_chi + case_alpha + case_background == B3[d],
            name=f"src3_case_partition_{d}",
        )

        count_chi = _gate_product(
            model,
            case_chi,
            reciprocal_L3k1k0[d],
            1.0,
            f"src3_receiver_chi_count_{d}",
        )
        count_alpha = _gate_product(
            model,
            case_alpha,
            reciprocal_L3k1[d],
            1.0,
            f"src3_receiver_alpha_count_{d}",
        )
        count_background = _gate_product(
            model,
            case_background,
            y[(3, d)],
            1.0,
            f"src3_receiver_background_count_{d}",
        )
        receiver3[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=0.0,
            ub=1.0,
            name=f"Nnorm_receiver_src3_{d}",
        )
        model.addConstr(
            _NORMALIZED_FLOW_SCALE * receiver3[d]
            == _NORMALIZED_FLOW_SCALE
            * (count_chi + count_alpha + count_background),
            name=f"define_Nnorm_receiver_src3_{d}",
        )

        source3[d] = model.addVar(
            vtype=GRB.CONTINUOUS,
            lb=0.0,
            ub=1.0,
            name=f"Nnorm_source_src3_{d}",
        )
        model.addQConstr(
            _NORMALIZED_FLOW_SCALE * source3[d] * k[(2, d)]
            == _NORMALIZED_FLOW_SCALE * receiver3[d],
            name=f"define_Nnorm_source_src3_{d}",
        )
        source3_sram[d] = _gate_product(
            model,
            B1[d],
            source3[d],
            1.0,
            f"Nnorm_source_src3_sram_{d}",
        )

    B3_k2_z = _gate_product(
        model,
        B3["z"],
        k[(2, "z")],
        float(L0["z"]),
        "B3_times_k2_z",
    )
    old_receiver3_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_receiver_src3_z",
    )
    old_source3_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_source_src3_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_receiver3_z
        == _NORMALIZED_FLOW_SCALE
        * (receiver3["z"] - B3_k2_z / float(L0["z"])),
        name="define_Nnorm_oldread_receiver_src3_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_source3_z
        == _NORMALIZED_FLOW_SCALE
        * (source3["z"] - B3["z"] / float(L0["z"])),
        name="define_Nnorm_oldread_source_src3_z",
    )
    old_source3_sram_z = _gate_product(
        model,
        B1["z"],
        old_source3_z,
        1.0,
        "Nnorm_oldread_source_src3_sram_z",
    )

    E_src3 = gp.LinExpr(0.0)
    for d in ("x", "y"):
        source_sram = source3_sram[d]
        source_ddr = source3[d] - source_sram
        E_src3 += E_RF_w * receiver3[d]
        E_src3 += E_SRAM_r * source_sram + E_DDR_r * source_ddr

    source3_sram_z = source3_sram["z"]
    source3_ddr_z = source3["z"] - source3_sram_z
    old_source3_ddr_z = old_source3_z - old_source3_sram_z
    E_src3 += E_RF_w * old_receiver3_z
    E_src3 += E_SRAM_w * source3_sram_z + E_SRAM_r * old_source3_sram_z
    E_src3 += E_DDR_w * source3_ddr_z + E_DDR_r * old_source3_ddr_z

    # -----------------------------------------------------------------
    # src--4: mutually exclusive nearest source and spatial aggregation.
    # -----------------------------------------------------------------
    source4_sram: Dict[str, gp.Var] = {}
    source4_ddr: Dict[str, gp.Var] = {}
    src4_sram_selected: Dict[str, gp.Var] = {}
    src4_ddr_selected: Dict[str, gp.Var] = {}
    for d in Dims:
        src4_sram_selected[d] = _and_bin_neg(
            model,
            B1[d],
            B3[d],
            f"src4_select_sram_{d}",
        )
        src4_ddr_selected[d] = _nor_bins(
            model,
            B1[d],
            B3[d],
            f"src4_select_ddr_{d}",
        )
        model.addConstr(
            B3[d] + src4_sram_selected[d] + src4_ddr_selected[d] == 1,
            name=f"src4_source_partition_{d}",
        )
        source4_sram[d] = _gate_product(
            model,
            src4_sram_selected[d],
            invk2[d],
            1.0,
            f"Nnorm_source_src4_sram_{d}",
        )
        source4_ddr[d] = _gate_product(
            model,
            src4_ddr_selected[d],
            invk2[d],
            1.0,
            f"Nnorm_source_src4_ddr_{d}",
        )

    E_src4 = gp.LinExpr(0.0)
    for d in ("x", "y"):
        E_src4 += E_RF_r * B3[d]
        E_src4 += E_SRAM_r * source4_sram[d]
        E_src4 += E_DDR_r * source4_ddr[d]

    old_source4_rf_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_source_src4_rf_z",
    )
    old_source4_sram_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_source_src4_sram_z",
    )
    old_source4_ddr_z = model.addVar(
        vtype=GRB.CONTINUOUS,
        lb=0.0,
        ub=1.0,
        name="Nnorm_oldread_source_src4_ddr_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_source4_rf_z
        == _NORMALIZED_FLOW_SCALE
        * (B3["z"] - B3_k2_z / float(L0["z"])),
        name="define_Nnorm_oldread_source_src4_rf_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_source4_sram_z
        == _NORMALIZED_FLOW_SCALE
        * (
            source4_sram["z"]
            - src4_sram_selected["z"] / float(L0["z"])
        ),
        name="define_Nnorm_oldread_source_src4_sram_z",
    )
    model.addConstr(
        _NORMALIZED_FLOW_SCALE * old_source4_ddr_z
        == _NORMALIZED_FLOW_SCALE
        * (
            source4_ddr["z"]
            - src4_ddr_selected["z"] / float(L0["z"])
        ),
        name="define_Nnorm_oldread_source_src4_ddr_z",
    )

    E_src4 += E_RF_w * B3["z"] + E_RF_r * old_source4_rf_z
    E_src4 += E_SRAM_w * source4_sram["z"] + E_SRAM_r * old_source4_sram_z
    E_src4 += E_DDR_w * source4_ddr["z"] + E_DDR_r * old_source4_ddr_z

    objective = E_src1 + E_src3 + E_src4 + E_MACC
    model.setObjective(objective, GRB.MINIMIZE)
    return model, L, k, y, B1, B3, a01, a12
