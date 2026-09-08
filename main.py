#!/usr/bin/env python3
"""Minimal optimization example; needs gurobipy, without Timeloop."""
from dataclasses import asdict
from normalized_energy_model import DeviceParams, compute_normalized_total_energy
from solver import _solve_full_model, _extract_mapping_params


def make_cfg(L0_xyz, params, C1, C3, N_PE):
    return {
        "L0": dict(L0_xyz), "C1": C1, "C3": C3, "N_PE": N_PE,
        "E_DDR_r": params["E_DDR_read"], "E_DDR_w": params["E_DDR_write"],
        "E_SRAM_r": params["E_SRAM_read"], "E_SRAM_w": params["E_SRAM_write"],
        "E_RF_r": params["E_RF_read"], "E_RF_w": params["E_RF_write"],
        "E_MACC": params["E_MACC"],
        "E_SRAM_leak": params.get("E_SRAM_leak", 0.0),
        "E_RF_leak": params.get("E_RF_leak", 0.0),
    }


def main():
    params = DeviceParams(64.0, 64.0, 59.4555/8, 49.7147/8,
                          0.929094, 2.56654, 1.40883,
                          E_SRAM_leak=0.0216217, E_RF_leak=8.51136e-5)
    cfg = make_cfg({"x": 1024, "y": 2048, "z": 1024}, asdict(params),
                   C1=2048, C3=16, N_PE=64)
    model, L, k, y, B1, B3, a01, a12 = _solve_full_model(cfg)
    h01, h12, h23, h34, alpha01, alpha12, b1, b3 = _extract_mapping_params(
        cfg, L, k, B1, B3, a01, a12)
    dynamic, parts = compute_normalized_total_energy(
        L0=cfg["L0"], hatL_12=h12, hatL_23=h23, hatL_34=h34,
        alpha01=alpha01, alpha12=alpha12, B={1: b1, 3: b3},
        params=params, include_leak=False)
    if abs(dynamic - model.ObjVal) > 1e-5 + 1e-6*max(abs(dynamic), abs(model.ObjVal)):
        raise RuntimeError(f"Optimizer/formula mismatch: {model.ObjVal} vs {dynamic}")
    print(f"Independent dynamic energy: {dynamic:.12g} pJ/MAC; objective agrees")
    print("Energy components:", parts)
    model.dispose()


if __name__ == "__main__":
    main()
