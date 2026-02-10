from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, Literal, Optional, Callable

# -----------------------
# Types & basic utilities
# -----------------------

Dim = Literal["x", "y", "z"]
DIMS: Tuple[Dim, Dim, Dim] = ("x", "y", "z")

def _ensure_posint_vec(name: str, V: Dict[Dim, int]) -> None:
    for d in DIMS:
        if d not in V or int(V[d]) <= 0:
            raise ValueError(f"{name}[{d}] must be positive int")

def _bg(alpha: Dim) -> Tuple[Dim, Dim]:
    """Return the two axes orthogonal to alpha (order arbitrary)."""
    others = [d for d in DIMS if d != alpha]
    return others[0], others[1]

# -----------------------
# Device energy constants
# -----------------------

@dataclass
class DeviceParams:
    """Per-access energy constants (units arbitrary but consistent)."""
    E_DDR_read: float
    E_DDR_write: float
    E_SRAM_read: float
    E_SRAM_write: float
    E_RF_read: float
    E_RF_write: float
    E_MACC: float
    # --- per-cycle leakage energies (energy / cycle) ---
    E_SRAM_leak: float = 0.0
    E_RF_leak: float = 0.0


# -----------------------
# Length derivations
# -----------------------

def derive_L123_from_stage_hats(
    *, hatL_12: Dict[Dim, int], hatL_23: Dict[Dim, int], hatL_34: Dict[Dim, int]
) -> Tuple[Dict[Dim, int], Dict[Dim, int], Dict[Dim, int]]:
    r"""
    Given stage relative block counts (\hat L^{(1-2)}, \hat L^{(2-3)}, \hat L^{(3-4)}),
    derive absolute block lengths L^{(1)}, L^{(2)}, L^{(3)} under the convention L^{(4)}=1:
        L^{(3)} = \hat L^{(3-4)}
        L^{(2)} = \hat L^{(2-3)} * L^{(3)}
        L^{(1)} = \hat L^{(1-2)} * L^{(2)}
    All values must be positive integers.
    """
    _ensure_posint_vec("hatL_12", hatL_12)
    _ensure_posint_vec("hatL_23", hatL_23)
    _ensure_posint_vec("hatL_34", hatL_34)

    L3 = {d: int(hatL_34[d]) for d in DIMS}
    L2 = {d: int(hatL_23[d]) * L3[d] for d in DIMS}
    L1 = {d: int(hatL_12[d]) * L2[d] for d in DIMS}
    return L1, L2, L3


# -----------------------
# \tilde L & \rho helpers (per §1.3.2–1.3.3)
# -----------------------

def _tildeL_src1_z(*, L0_z: int, L1_z: int, alpha01: Dim) -> float:
    """\tilde L^{(src-1)}_z = 1 if alpha01==z else L0_z / L1_z."""
    return 1.0 if alpha01 == "z" else float(L0_z) / float(L1_z)

def _tildeL_src3_z(*, L0_z: int, L1_z: int, L2_z: int, alpha12: Dim) -> float:
    """\tilde L^{(src-3)}_z = L0_z/L1_z if alpha12==z else L0_z/L2_z."""
    return float(L0_z) / float(L1_z if alpha12 == "z" else L2_z)

def _tildeL_src4_z(*, L0_z: int, hatL23_z: int) -> float:
    r"""\tilde L^{(src-4)}_z = L0_z / \hat L^{(2-3)}_z."""
    return float(L0_z) / float(hatL23_z)

def _rho_from_tilde(t: float) -> float:
    """ρ = 1 - 1/\\tilde L; for t==1 returns 0."""
    if t <= 0:
        raise ValueError("tilde L must be positive.")
    return 0.0 if t == 1.0 else (1.0 - 1.0 / t)


# -----------------------
# Bypass matrix helpers
# -----------------------

def _extract_B_layer(B: Dict[int, Dict[Dim, int]], layer: int) -> Dict[Dim, int]:
    """Extract B^{(layer)} (per-axis 0/1). Fallback to all-ones if missing for fixed layers 0/2/4."""
    if layer in B:
        v = B[layer]
        return {d: int(v.get(d, 1)) for d in DIMS}
    # Force dwell for 0,2,4 if not explicitly given
    if layer in (0, 2, 4):
        return {d: 1 for d in DIMS}
    # Default to all-ones if unspecified (conservative)
    return {d: 1 for d in DIMS}

def _normalize_B(
    B: Dict | None
) -> Tuple[Dict[Dim, int], Dict[Dim, int]]:
    """
    Accepts either:
      - full matrix: {0:{x:1,...}, 1:{...}, 2:{...}, 3:{...}, 4:{...}}
      - or a compact form like {'L1':{...}, 'L3':{...}} or {1:{...}, 3:{...}}
    Returns (B1, B3) per-axis 0/1.
    """
    if B is None:
        # default: no bypass anywhere optional
        return ({d: 1 for d in DIMS}, {d: 1 for d in DIMS})

    # Numeric-layer keyed form
    if any(k in B for k in (0, 1, 2, 3, 4)):
        B1 = _extract_B_layer(B, 1)
        B3 = _extract_B_layer(B, 3)
        # Validate fixed layers if present
        for fixed in (0, 2, 4):
            Bl = _extract_B_layer(B, fixed)
            for d in DIMS:
                if Bl[d] != 1:
                    raise ValueError(f"B^({fixed})[{d}] must be 1 (fixed dwell at layers 0,2,4).")
        return B1, B3

    # String-keyed convenience
    if "L1" in B or "level1" in B:
        v = B.get("L1", B.get("level1"))
        B1 = {d: int(v.get(d, 1)) for d in DIMS}
    else:
        B1 = {d: 1 for d in DIMS}

    if "L3" in B or "level3" in B:
        v = B.get("L3", B.get("level3"))
        B3 = {d: int(v.get(d, 1)) for d in DIMS}
    else:
        B3 = {d: 1 for d in DIMS}

    return B1, B3


def _normalized_leak_energy(*, hatL_23: Dict[Dim, int], P: DeviceParams) -> float:
    """
    Implements §2.5 leakage energy in normalized form:
        num_pe = hatL_23[x] * hatL_23[y] * hatL_23[z]
        E_leak_norm = (E_SRAM_leak + E_RF_leak * num_pe) / num_pe
    """
    num_pe = int(hatL_23["x"]) * int(hatL_23["y"]) * int(hatL_23["z"])
    if num_pe <= 0:
        raise ValueError("num_pe must be positive (product of hatL_23).")
    per_cycle = float(P.E_SRAM_leak) + float(P.E_RF_leak) * num_pe
    return per_cycle / num_pe


# -----------------------
# Stage weights e^{(p,dir)} per §1.6 using ρ from §1.3.2
# -----------------------

def _derive_e_weights(
    *,
    L0: Dict[Dim, int], L1: Dict[Dim, int], L2: Dict[Dim, int],
    hatL_23: Dict[Dim, int],
    alpha01: Dim, alpha12: Dim,
    P: DeviceParams,
) -> Callable[[int], Dict[str, Dict[Dim, float]]]:
    """
    Return a callable e_of(p) that yields the per-face energies for 'src–p' attribution.
    p must be one of {1,3,4}. The 'z' terms internally bind to ρ^(src-p).
    Keys in the returned dict: '0down','1up','1down','3up','3down','2up','4up','2down'.
    """
    # Compute base ρ for p=1,3,4
    rho1 = _rho_from_tilde(_tildeL_src1_z(L0_z=L0["z"], L1_z=L1["z"], alpha01=alpha01))
    rho3 = _rho_from_tilde(_tildeL_src3_z(L0_z=L0["z"], L1_z=L1["z"], L2_z=L2["z"], alpha12=alpha12))
    rho4 = _rho_from_tilde(_tildeL_src4_z(L0_z=L0["z"], hatL23_z=hatL_23["z"]))

    def e_of(p: int) -> Dict[str, Dict[Dim, float]]:
        if p == 1:
            rho = rho1
        elif p == 3:
            rho = rho3
        elif p == 4:
            rho = rho4
        else:
            raise ValueError(f"unsupported src-p = {p}, expected 1|3|4")

        return {
            # DDR & 下级传输 (0,↓)
            "0down": {
                "x": float(P.E_DDR_read),
                "y": float(P.E_DDR_read),
                "z": float(P.E_DDR_write) + rho * float(P.E_DDR_read),
            },
            # SRAM & 上级传输 (1,↑) — z忽略SRAM_read，仅保留 rho*write
            "1up": {
                "x": float(P.E_SRAM_write),
                "y": float(P.E_SRAM_write),
                "z": rho * float(P.E_SRAM_write),
            },
            # SRAM & 下级传输 (1,↓)
            "1down": {
                "x": float(P.E_SRAM_read),
                "y": float(P.E_SRAM_read),
                "z": float(P.E_SRAM_write) + rho * float(P.E_SRAM_read),
            },
            # regfile & 上级传输 (3,↑) — spa_reduct=0，z仅 rho*RF_write
            "3up": {
                "x": float(P.E_RF_write),
                "y": float(P.E_RF_write),
                "z": rho * float(P.E_RF_write),
            },
            # regfile & 下级传输 (3,↓)
            "3down": {
                "x": float(P.E_RF_read),
                "y": float(P.E_RF_read),
                "z": float(P.E_RF_write) + rho * float(P.E_RF_read),
            },
            # 其余为 0
            "2up":   {"x": 0.0, "y": 0.0, "z": 0.0},
            "4up":   {"x": 0.0, "y": 0.0, "z": 0.0},
            "2down": {"x": 0.0, "y": 0.0, "z": 0.0},
        }

    return e_of


# -----------------------
# Core API (per §4.2)
# -----------------------

def compute_normalized_total_energy(
    *,
    L0: Dict[Dim, int],
    hatL_12: Dict[Dim, int],
    hatL_23: Dict[Dim, int],
    hatL_34: Dict[Dim, int],
    alpha01: Dim,
    alpha12: Dim,
    B: Optional[Dict] = None,
    params: DeviceParams,
    include_leak: bool = True,  # 是否计算并返回 Eleak（仅报告用；不会并入目标）
) -> Tuple[float, Dict[str, float]]:
    r"""
    Compute normalized energy strictly per §4.2.

    Returns:
      (phi, parts) where
        phi = \bar E^{(src-1)} + \bar E^{(src-3)} + \bar E^{(src-4)} + \bar E^{(4)}   (不含 Eleak)
        parts = {'E_src1','E_src3','E_src4','E4','Eleak'} for reporting.
    """
    _ensure_posint_vec("L0", L0)
    if alpha01 not in DIMS or alpha12 not in DIMS:
        raise ValueError("alpha01/alpha12 must be one of 'x','y','z'")

    # derive absolute lengths
    L1, L2, L3 = derive_L123_from_stage_hats(hatL_12=hatL_12, hatL_23=hatL_23, hatL_34=hatL_34)
    # normalize bypass
    B1, B3 = _normalize_B(B)

    # derive e-weights accessor per §1.6 with ρ bound by src–p
    e_of = _derive_e_weights(L0=L0, L1=L1, L2=L2, hatL_23=hatL_23, alpha01=alpha01, alpha12=alpha12, P=params)
    e1 = e_of(1)   # for \bar E^{(src-1)}
    e3 = e_of(3)   # for \bar E^{(src-3)}
    e4 = e_of(4)   # for \bar E^{(src-4)}

    # ---------- (1) \bar E^{(src-1)} per §4.2 ----------
    def _denom_src1(d: Dim) -> float:
        """L^{(0)}_d if d==alpha01 else L^{(1)}_d"""
        return float(L0[d] if d == alpha01 else L1[d])

    E_src1 = 0.0
    for d in DIMS:
        if int(B1[d]) == 1:
            # 统一使用 e1，z 项天然绑定 ρ1
            E_src1 += (e1["0down"][d] + e1["1up"][d]) / _denom_src1(d)

    # ---------- (2) \bar E^{(src-3)} per §4.2 ----------
    def _denom_src3(d: Dim) -> float:
        r"""
        Denominator from §2.2.1:
            L^{(3)}_d * ( \hat L^{(1-2)}_d )^{ [d == alpha12] }
        """
        base = float(L3[d])
        return base * (float(hatL_12[d]) if d == alpha12 else 1.0)

    E_src3 = 0.0
    for d in DIMS:
        if int(B3[d]) != 1:
            continue
        if int(B1[d]) == 1:  # 来源 SRAM
            numer = e3["3up"][d] + e3["1down"][d] / float(hatL_23[d])
        else:                # 来源 DDR
            numer = e3["3up"][d] + e3["0down"][d] / float(hatL_23[d])
        E_src3 += numer / _denom_src3(d)

    # ---------- (3) \bar E^{(src-4)} per §4.2 ----------
    E_src4 = 0.0
    for d in DIMS:
        term_reg  = (e4["3down"][d] if int(B3[d]) == 1 else 0.0)
        term_sram = ((e4["1down"][d] / float(hatL_23[d])) if (int(B1[d]) == 1 and int(B3[d]) == 0) else 0.0)
        term_ddr  = ((e4["0down"][d] / float(hatL_23[d])) if (int(B1[d]) == 0 and int(B3[d]) == 0) else 0.0)
        E_src4 += term_reg + term_sram + term_ddr

    # ---------- (4) \bar E^{(4)} per §4.2 ----------
    E4 = float(params.E_MACC)

    # ---------- leak (仅报告，不入目标) ----------
    Eleak = _normalized_leak_energy(hatL_23=hatL_23, P=params) if include_leak else 0.0

    # total objective (exclude Eleak from phi)
    phi = float(E_src1 + E_src3 + E_src4 + E4 + Eleak)
    parts = {
        "E_src1": float(E_src1),
        "E_src3": float(E_src3),
        "E_src4": float(E_src4),
        "E4": float(E4),
        "Eleak": float(Eleak),
    }
    return phi, parts


# -----------------------
# Example (if run as script)
# -----------------------
if __name__ == "__main__":
    L0 = {"x": 64, "y": 64, "z": 64}
    hatL_12 = {"x": 2, "y": 2, "z": 4}
    hatL_23 = {"x": 1, "y": 8, "z": 8}
    hatL_34 = {"x": 4, "y": 1, "z": 1}
    alpha01, alpha12 = "y", "x"
    B = {0: {"x": 1, "y": 1, "z": 1},
         1: {"x": 1, "y": 1, "z": 1},
         2: {"x": 1, "y": 1, "z": 1},
         3: {"x": 1, "y": 1, "z": 1},
         4: {"x": 1, "y": 1, "z": 1}}

    params = DeviceParams(
        E_DDR_read=64.0, E_DDR_write=64.0,
        E_SRAM_read=59.4555/8, E_SRAM_write=49.7147/8,
        E_RF_read=0.654287, E_RF_write=1.03489,
        E_MACC=1.40883,
        # per-cycle leakage energies (energy/cycle)
        E_SRAM_leak=0.0216217, E_RF_leak=0.000162319,
    )

    phi, parts = compute_normalized_total_energy(
        L0=L0, hatL_12=hatL_12, hatL_23=hatL_23, hatL_34=hatL_34,
        alpha01=alpha01, alpha12=alpha12,
        B=B, params=params,
        include_leak=True,  # leak for reporting only
    )
    print("phi =", phi)
    print("parts =", parts)
