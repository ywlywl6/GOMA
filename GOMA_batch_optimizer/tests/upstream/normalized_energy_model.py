#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewed closed-form normalized energy model for GOMA.

This module is the phase-one implementation of
the reviewed equations described in ``MODEL_NOTES.md``. It is the energy-model component of the
``review1_bugfix`` variant and is paired with the optimizer in this package.

The main differences from the original evaluator are:

* receiver-side update traffic and source-side service traffic are explicit;
* boundary coefficients are derived from ungated active-path traffic;
* src--3 supports continuous regfile residency across SRAM tile boundaries;
* per-event energy weights are indexed by receiver path;
* an all-unit stage 1--2 uses alpha_01 as its canonical walking axis.

Traffic volumes are word-level event volumes.  A reduction event is expanded
into write-back and optional read-old accesses by the path-specific energy
weights, rather than by multiplying the traffic volume itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Mapping, Optional, Tuple


Dim = Literal["x", "y", "z"]
PathId = Literal[1, 3, 4]

DIMS: Tuple[Dim, Dim, Dim] = ("x", "y", "z")
PATHS: Tuple[PathId, PathId, PathId] = (1, 3, 4)

IntVec = Dict[Dim, int]
FloatVec = Dict[Dim, float]
PathFloatVec = Dict[PathId, FloatVec]
EnergyWeights = Dict[str, FloatVec]
PathEnergyWeights = Dict[PathId, EnergyWeights]


def _as_posint_vec(name: str, values: Mapping[Dim, int]) -> IntVec:
    """Validate and copy a positive-integer x/y/z vector."""
    result: IntVec = {}
    for d in DIMS:
        if d not in values:
            raise ValueError(f"{name}[{d}] is required")
        raw = values[d]
        value = int(raw)
        if isinstance(raw, bool) or value <= 0 or value != raw:
            raise ValueError(f"{name}[{d}] must be a positive integer, got {raw!r}")
        result[d] = value
    return result


def _as_binary_vec(name: str, values: Mapping[Dim, int]) -> IntVec:
    """Validate and copy a binary x/y/z vector."""
    result: IntVec = {}
    for d in DIMS:
        raw = values.get(d, 1)
        value = int(raw)
        if value not in (0, 1) or value != raw:
            raise ValueError(f"{name}[{d}] must be 0 or 1, got {raw!r}")
        result[d] = value
    return result


def _ensure_dim(name: str, value: str) -> Dim:
    if value not in DIMS:
        raise ValueError(f"{name} must be one of {DIMS}, got {value!r}")
    return value  # type: ignore[return-value]


@dataclass
class DeviceParams:
    """Per-access energy constants in one consistent energy unit."""

    E_DDR_read: float
    E_DDR_write: float
    E_SRAM_read: float
    E_SRAM_write: float
    E_RF_read: float
    E_RF_write: float
    E_MACC: float
    E_SRAM_leak: float = 0.0
    E_RF_leak: float = 0.0


@dataclass(frozen=True)
class TrafficModel:
    """All geometry and traffic quantities needed by the reviewed model.

    ``N_receiver_active`` is the ungated receiver-side volume denoted by
    ``widehat N^(r)`` in the patch.  ``N_receiver`` and ``N_source`` are the
    bypass-gated actual receiver- and source-side volumes.
    """

    V: int
    L0: IntVec
    L1: IntVec
    L2: IntVec
    L3: IntVec
    hatL_01: IntVec
    hatL_12: IntVec
    hatL_23: IntVec
    hatL_34: IntVec
    alpha01: Dim
    alpha12: Dim
    B1: IntVec
    B3: IntVec
    chi3: IntVec
    N_receiver_active: PathFloatVec
    N_receiver: PathFloatVec
    N_source: PathFloatVec
    F_z: Dict[PathId, float]
    tildeL_z: Dict[PathId, float]
    rho_z: Dict[PathId, float]


def derive_L123_from_stage_hats(
    *,
    hatL_12: Mapping[Dim, int],
    hatL_23: Mapping[Dim, int],
    hatL_34: Mapping[Dim, int],
) -> Tuple[IntVec, IntVec, IntVec]:
    """Derive absolute L1/L2/L3 lengths under L4[d] = 1."""
    h12 = _as_posint_vec("hatL_12", hatL_12)
    h23 = _as_posint_vec("hatL_23", hatL_23)
    h34 = _as_posint_vec("hatL_34", hatL_34)

    L3 = {d: h34[d] for d in DIMS}
    L2 = {d: h23[d] * L3[d] for d in DIMS}
    L1 = {d: h12[d] * L2[d] for d in DIMS}
    return L1, L2, L3


def _derive_hatL_01(*, L0: IntVec, L1: IntVec) -> IntVec:
    """Derive and validate the level-0--1 temporal traversal ratios."""
    hatL_01: IntVec = {}
    for d in DIMS:
        if L1[d] > L0[d] or L0[d] % L1[d] != 0:
            raise ValueError(
                f"invalid hierarchy on {d}: L0={L0[d]} must be divisible by L1={L1[d]}"
            )
        hatL_01[d] = L0[d] // L1[d]
    return hatL_01


def _extract_B_layer(B: Mapping, layer: int) -> IntVec:
    if layer in B:
        values = B[layer]
        if not isinstance(values, Mapping):
            raise ValueError(f"B[{layer}] must be a mapping")
        return _as_binary_vec(f"B^({layer})", values)
    return {d: 1 for d in DIMS}


def _normalize_B(B: Optional[Mapping]) -> Tuple[IntVec, IntVec]:
    """Accept the same full or compact bypass forms as the original model."""
    if B is None:
        return ({d: 1 for d in DIMS}, {d: 1 for d in DIMS})

    if any(layer in B for layer in (0, 1, 2, 3, 4)):
        B1 = _extract_B_layer(B, 1)
        B3 = _extract_B_layer(B, 3)
        for fixed in (0, 2, 4):
            values = _extract_B_layer(B, fixed)
            for d in DIMS:
                if values[d] != 1:
                    raise ValueError(
                        f"B^({fixed})[{d}] must be 1; levels 0, 2, and 4 are fixed resident"
                    )
        return B1, B3

    B1_values = B.get("L1", B.get("level1", {}))
    B3_values = B.get("L3", B.get("level3", {}))
    if not isinstance(B1_values, Mapping) or not isinstance(B3_values, Mapping):
        raise ValueError("compact B entries must be mappings")
    return (
        _as_binary_vec("B^(1)", B1_values),
        _as_binary_vec("B^(3)", B3_values),
    )


def canonicalize_walking_axes(
    *,
    alpha01: str,
    alpha12: str,
    hatL_12: Mapping[Dim, int],
) -> Tuple[Dim, Dim]:
    """Apply the reviewed convention for a fully degenerate stage 1--2.

    For a non-degenerate stage, callers must pass the innermost non-unit
    temporal traversal axis.  If all stage-1--2 ratios are one, there is no
    temporal movement and alpha_12 is canonically set to alpha_01.
    """
    a01 = _ensure_dim("alpha01", alpha01)
    a12 = _ensure_dim("alpha12", alpha12)
    h12 = _as_posint_vec("hatL_12", hatL_12)
    if all(h12[d] == 1 for d in DIMS):
        a12 = a01
    return a01, a12


def _rho_from_tilde(tilde: float, *, path: PathId) -> float:
    """Return rho = 1 - 1/tilde, rejecting invalid chain geometry."""
    tolerance = 1e-12
    if tilde < 1.0 - tolerance:
        raise ValueError(
            f"tildeL_z^(src-{path}) must be at least one, got {tilde}"
        )
    if abs(tilde - 1.0) <= tolerance:
        return 0.0
    rho = 1.0 - 1.0 / tilde
    if rho < -tolerance or rho > 1.0 + tolerance:
        raise ValueError(f"rho_z^(src-{path}) is outside [0, 1]: {rho}")
    return min(1.0, max(0.0, rho))


def derive_traffic_model(
    *,
    L0: Mapping[Dim, int],
    hatL_12: Mapping[Dim, int],
    hatL_23: Mapping[Dim, int],
    hatL_34: Mapping[Dim, int],
    alpha01: str,
    alpha12: str,
    B: Optional[Mapping] = None,
) -> TrafficModel:
    """Derive reviewed receiver/source traffic, chain counts, tilde-L, and rho."""
    L0_i = _as_posint_vec("L0", L0)
    h12 = _as_posint_vec("hatL_12", hatL_12)
    h23 = _as_posint_vec("hatL_23", hatL_23)
    h34 = _as_posint_vec("hatL_34", hatL_34)
    L1, L2, L3 = derive_L123_from_stage_hats(
        hatL_12=h12,
        hatL_23=h23,
        hatL_34=h34,
    )
    h01 = _derive_hatL_01(L0=L0_i, L1=L1)
    a01, a12 = canonicalize_walking_axes(
        alpha01=alpha01,
        alpha12=alpha12,
        hatL_12=h12,
    )
    B1, B3 = _normalize_B(B)

    V = L0_i["x"] * L0_i["y"] * L0_i["z"]

    chi3: IntVec = {}
    for d in DIMS:
        orthogonal = tuple(u for u in DIMS if u != d)
        chi3[d] = int(
            d == a01
            and d == a12
            and all(h12[u] == 1 for u in orthogonal)
        )

    active: PathFloatVec = {path: {} for path in PATHS}
    receiver: PathFloatVec = {path: {} for path in PATHS}
    source: PathFloatVec = {path: {} for path in PATHS}

    # src--1: DRAM <-> SRAM.  There is no PE-array fanout on this path.
    for d in DIMS:
        denom = L0_i[d] if d == a01 else L1[d]
        active[1][d] = float(V) / float(denom)
        receiver[1][d] = float(B1[d]) * active[1][d]
        source[1][d] = receiver[1][d]

    # src--3: nearest upper resident endpoint <-> regfile.
    for d in DIMS:
        denom = L3[d]
        if d == a12:
            denom *= h12[d]
        if chi3[d] == 1:
            denom *= h01[d]
        active[3][d] = float(V) / float(denom)
        receiver[3][d] = float(B3[d]) * active[3][d]
        source[3][d] = receiver[3][d] / float(h23[d])

    # src--4: the receiver is a MACC trigger; only source service is shared.
    for d in DIMS:
        active[4][d] = float(V)
        receiver[4][d] = float(V)
        source[4][d] = float(V) * (
            float(B3[d]) + float(1 - B3[d]) / float(h23[d])
        )

    F1 = float(L0_i["x"] * L0_i["y"])
    F34 = F1 * float(h23["z"])
    F_z: Dict[PathId, float] = {1: F1, 3: F34, 4: F34}

    tildeL_z: Dict[PathId, float] = {}
    rho_z: Dict[PathId, float] = {}
    for path in PATHS:
        # Deliberately use ungated receiver traffic so bypassed paths remain defined.
        tilde = active[path]["z"] / F_z[path]
        if abs(tilde - 1.0) <= 1e-12:
            tilde = 1.0
        tildeL_z[path] = tilde
        rho_z[path] = _rho_from_tilde(tilde, path=path)

    return TrafficModel(
        V=V,
        L0=L0_i,
        L1=L1,
        L2=L2,
        L3=L3,
        hatL_01=h01,
        hatL_12=h12,
        hatL_23=h23,
        hatL_34=h34,
        alpha01=a01,
        alpha12=a12,
        B1=B1,
        B3=B3,
        chi3=chi3,
        N_receiver_active=active,
        N_receiver=receiver,
        N_source=source,
        F_z=F_z,
        tildeL_z=tildeL_z,
        rho_z=rho_z,
    )


def _weights_for_path(*, rho: float, P: DeviceParams) -> EnergyWeights:
    """Build e_(d|p) weights for one receiver path."""
    return {
        "0down": {
            "x": float(P.E_DDR_read),
            "y": float(P.E_DDR_read),
            "z": float(P.E_DDR_write) + rho * float(P.E_DDR_read),
        },
        "1up": {
            "x": float(P.E_SRAM_write),
            "y": float(P.E_SRAM_write),
            "z": rho * float(P.E_SRAM_write),
        },
        "1down": {
            "x": float(P.E_SRAM_read),
            "y": float(P.E_SRAM_read),
            "z": float(P.E_SRAM_write) + rho * float(P.E_SRAM_read),
        },
        "3up": {
            "x": float(P.E_RF_write),
            "y": float(P.E_RF_write),
            # The reviewed paper model fixes spatial-reduction energy to zero.
            "z": rho * float(P.E_RF_write),
        },
        "3down": {
            "x": float(P.E_RF_read),
            "y": float(P.E_RF_read),
            "z": float(P.E_RF_write) + rho * float(P.E_RF_read),
        },
        "2up": {d: 0.0 for d in DIMS},
        "2down": {d: 0.0 for d in DIMS},
        "4up": {d: 0.0 for d in DIMS},
    }


def derive_path_energy_weights(
    *,
    traffic: TrafficModel,
    params: DeviceParams,
) -> PathEnergyWeights:
    """Return path-indexed per-event weights e_(d|p)."""
    return {
        path: _weights_for_path(rho=traffic.rho_z[path], P=params)
        for path in PATHS
    }


def _normalized_leak_energy(*, hatL_23: Mapping[Dim, int], P: DeviceParams) -> float:
    num_pe = int(hatL_23["x"]) * int(hatL_23["y"]) * int(hatL_23["z"])
    if num_pe <= 0:  # pragma: no cover - validated earlier
        raise ValueError("num_pe must be positive")
    per_cycle = float(P.E_SRAM_leak) + float(P.E_RF_leak) * float(num_pe)
    return per_cycle / float(num_pe)


def compute_normalized_total_energy(
    *,
    L0: Mapping[Dim, int],
    hatL_12: Mapping[Dim, int],
    hatL_23: Mapping[Dim, int],
    hatL_34: Mapping[Dim, int],
    alpha01: str,
    alpha12: str,
    B: Optional[Mapping] = None,
    params: DeviceParams,
    include_leak: bool = True,
) -> Tuple[float, Dict[str, float]]:
    """Compute reviewed normalized energy.

    The return shape is compatible with the original evaluator.  ``phi`` is
    dynamic energy plus ``Eleak`` when ``include_leak`` is true.  Leakage is a
    reporting term and is not part of the reviewed traffic equations.
    """
    traffic = derive_traffic_model(
        L0=L0,
        hatL_12=hatL_12,
        hatL_23=hatL_23,
        hatL_34=hatL_34,
        alpha01=alpha01,
        alpha12=alpha12,
        B=B,
    )
    weights = derive_path_energy_weights(traffic=traffic, params=params)
    V = float(traffic.V)

    # src--1: source and receiver volumes are equal but remain explicit.
    E_src1 = 0.0
    e1 = weights[1]
    for d in DIMS:
        E_src1 += traffic.N_source[1][d] / V * e1["0down"][d]
        E_src1 += traffic.N_receiver[1][d] / V * e1["1up"][d]

    # src--3: regfile-side energy uses receiver traffic; nearest-upper-level
    # energy uses spatially aggregated source traffic.
    E_src3 = 0.0
    e3 = weights[3]
    for d in DIMS:
        E_src3 += traffic.N_receiver[3][d] / V * e3["3up"][d]
        if traffic.B1[d] == 1:
            E_src3 += traffic.N_source[3][d] / V * e3["1down"][d]
        else:
            E_src3 += traffic.N_source[3][d] / V * e3["0down"][d]

    # src--4: MACC is a pure compute endpoint, so only source storage energy
    # appears in this transfer term.
    E_src4 = 0.0
    e4 = weights[4]
    for d in DIMS:
        if traffic.B3[d] == 1:
            source_weight = e4["3down"][d]
        elif traffic.B1[d] == 1:
            source_weight = e4["1down"][d]
        else:
            source_weight = e4["0down"][d]
        E_src4 += traffic.N_source[4][d] / V * source_weight

    E4 = float(params.E_MACC)
    Eleak = (
        _normalized_leak_energy(hatL_23=traffic.hatL_23, P=params)
        if include_leak
        else 0.0
    )
    phi = float(E_src1 + E_src3 + E_src4 + E4 + Eleak)
    parts = {
        "E_src1": float(E_src1),
        "E_src3": float(E_src3),
        "E_src4": float(E_src4),
        "E4": E4,
        "Eleak": float(Eleak),
    }
    return phi, parts


if __name__ == "__main__":
    example_L0 = {"x": 8, "y": 8, "z": 64}
    example_hatL_12 = {"x": 1, "y": 1, "z": 4}
    example_hatL_23 = {"x": 1, "y": 2, "z": 2}
    example_hatL_34 = {"x": 2, "y": 1, "z": 2}
    example_B = {
        1: {"x": 1, "y": 1, "z": 1},
        3: {"x": 1, "y": 1, "z": 1},
    }
    example_params = DeviceParams(
        E_DDR_read=64.0,
        E_DDR_write=64.0,
        E_SRAM_read=59.4555 / 8.0,
        E_SRAM_write=49.7147 / 8.0,
        E_RF_read=0.654287,
        E_RF_write=1.03489,
        E_MACC=1.40883,
    )

    example_traffic = derive_traffic_model(
        L0=example_L0,
        hatL_12=example_hatL_12,
        hatL_23=example_hatL_23,
        hatL_34=example_hatL_34,
        alpha01="z",
        alpha12="z",
        B=example_B,
    )
    example_phi, example_parts = compute_normalized_total_energy(
        L0=example_L0,
        hatL_12=example_hatL_12,
        hatL_23=example_hatL_23,
        hatL_34=example_hatL_34,
        alpha01="z",
        alpha12="z",
        B=example_B,
        params=example_params,
        include_leak=False,
    )
    print("chi3 =", example_traffic.chi3)
    print("N_receiver(src-3,z) =", example_traffic.N_receiver[3]["z"])
    print("N_source(src-3,z) =", example_traffic.N_source[3]["z"])
    print("tildeL(src-3,z) =", example_traffic.tildeL_z[3])
    print("rho(src-3,z) =", example_traffic.rho_z[3])
    print("phi =", example_phi)
    print("parts =", example_parts)
