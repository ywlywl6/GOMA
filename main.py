import time

# -*- coding: utf-8 -*-
import gurobipy as gp
from gurobipy import GRB
from full_model import build_model_full

def make_cfg(L0_xyz, params, C1, C3, N_PE):
    # 容量与 N_PE 从主函数传入
    cfg = {
        'L0': {'x': L0_xyz['x'], 'y': L0_xyz['y'], 'z': L0_xyz['z']},
        'C1': C1,    # SRAM total capacity (only resident tensors count, §3.1)
        'C3': C3,    # regfile total capacity (only resident tensors count, §3.1)
        'N_PE': N_PE,  # PE 数量
        # energies (map from DeviceParams)
        'E_DDR_r': params['E_DDR_read'],  'E_DDR_w': params['E_DDR_write'],
        'E_SRAM_r': params['E_SRAM_read'],'E_SRAM_w': params['E_SRAM_write'],
        'E_RF_r': params['E_RF_read'],    'E_RF_w': params['E_RF_write'],
        'E_MACC': params['E_MACC'],
        # 可用于报告（泄露不进目标，§2.5）
        'E_SRAM_leak': params.get('E_SRAM_leak', 0.0),
        'E_RF_leak'  : params.get('E_RF_leak',   0.0),
    }
    return cfg

if __name__ == "__main__":
    start_time = time.time()

    # === 示例参数 ===
    L0 = {"x": 1024, "y": 2048, "z": 1024}

    # 从主函数配置的硬件资源参数
    C1 = 2048   # SRAM capacity
    C3 = 16     # regfile capacity
    N_PE = 64    # PE 数量

    params = dict(
        E_DDR_read=64.0, E_DDR_write=64.0,
        E_SRAM_read=59.4555 / 8, E_SRAM_write=49.7147 / 8,
        E_RF_read=0.929094, E_RF_write=2.56654,
        E_MACC=1.40883,
        # per-cycle leakage energies (energy/cycle)
        E_SRAM_leak=0.0216217, E_RF_leak=8.51136e-05,
    )

    cfg = make_cfg(L0, params, C1=C1, C3=C3, N_PE=N_PE)
    grb_params = {'OutputFlag': 1, 'NonConvex': 2}

    m, L, k, y, B1, B3, a01, a12 = build_model_full(cfg, params=grb_params)
    m.optimize()

    if m.Status in (GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT):
        # 1. 先计算泄露能量（§2.5）
        num_pe = int(k[(2, 'x')].X) * int(k[(2, 'y')].X) * int(k[(2, 'z')].X)
        E_leak_per_cycle = cfg['E_SRAM_leak'] + cfg['E_RF_leak'] * num_pe
        E_leak_norm = E_leak_per_cycle / num_pe if num_pe > 0 else 0.0

        # 2. 打印总能量 = 优化目标(动态) + 泄露(静态)
        print(f"status={m.Status}, Total Normalized Energy={m.ObjVal + E_leak_norm:.6f} "
              f"(Dynamic={m.ObjVal:.6f} + Leak={E_leak_norm:.6f})")

        # 行走轴
        ax01 = max(a01, key=lambda d: a01[d].X)
        ax12 = max(a12, key=lambda d: a12[d].X)
        print(f"alpha_0-1 = {ax01}, alpha_1-2 = {ax12}")
        # B 驻留
        print("B^(1):", {d: int(B1[d].X) for d in B1})
        print("B^(3):", {d: int(B3[d].X) for d in B3})

        # 容量使用（§3.1）：bypass(B=0) 的张量不占用容量
        L1x, L1y, L1z = (int(L[(1, 'x')].X), int(L[(1, 'y')].X), int(L[(1, 'z')].X))
        L3x, L3y, L3z = (int(L[(3, 'x')].X), int(L[(3, 'y')].X), int(L[(3, 'z')].X))
        used_C1 = int(B1['y'].X) * L1x * L1z + int(B1['x'].X) * L1y * L1z + int(B1['z'].X) * L1x * L1y
        used_C3 = int(B3['y'].X) * L3x * L3z + int(B3['x'].X) * L3y * L3z + int(B3['z'].X) * L3x * L3y
        print(f"capacity: SRAM {used_C1}/{cfg['C1']}, RF {used_C3}/{cfg['C3']}")
        # 三级块长/整除比
        for i in ('x', 'y', 'z'):
            print(f"{i}: L1={int(L[(1, i)].X)}, L2={int(L[(2, i)].X)}, L3={int(L[(3, i)].X)} | "
                  f"k0={int(k[(0, i)].X)}, k1={int(k[(1, i)].X)}, k2={int(k[(2, i)].X)}, k3={int(k[(3, i)].X)}")
    else:
        print(f"solver status={m.Status} (infeasible or other)")

    end_time = time.time()
    print(f"总用时: {end_time - start_time:.3f} 秒")
