# -*- coding: utf-8 -*-
from typing import Dict, Tuple, Any
import gurobipy as gp
from gurobipy import GRB

Dims = ('x', 'y', 'z')

def _and_bin(m: gp.Model, b1: gp.Var, b2: gp.Var, name: str) -> gp.Var:
    """w = b1 AND b2"""
    w = m.addVar(vtype=GRB.BINARY, name=name)
    m.addConstr(w <= b1)
    m.addConstr(w <= b2)
    m.addConstr(w >= b1 + b2 - 1)
    return w

def _and_bin_neg(m: gp.Model, b1: gp.Var, b2: gp.Var, name: str) -> gp.Var:
    """w = b1 AND (1-b2)"""
    w = m.addVar(vtype=GRB.BINARY, name=name)
    # w <= b1; w <= 1-b2; w >= b1 - b2
    m.addConstr(w <= b1)
    m.addConstr(w <= 1 - b2)
    m.addConstr(w >= b1 - b2)
    return w

def _gate_prod(m: gp.Model, b: gp.Var, x: gp.Var, ub_x: float, name: str) -> gp.Var:
    """g = b * x, with 0 <= x <= ub_x"""
    g = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=ub_x, name=name)
    m.addConstr(g <= x)
    m.addConstr(g <= b * ub_x)
    m.addConstr(g >= x - (1 - b) * ub_x)
    return g

def build_model_full(cfg: Dict[str, Any], params: Dict[str, Any] = None) -> Tuple[
    gp.Model,
    Dict[Tuple[int, str], gp.Var], Dict[Tuple[int, str], gp.Var], Dict[Tuple[int, str], gp.Var],
    Dict[str, gp.Var], Dict[str, gp.Var], Dict[str, gp.Var], Dict[str, gp.Var]
]:
    """
    返回:
      - m: gurobi Model
      - L[(p,i)], k[(p,i)], y[(p,i)]  (p=1,2,3 / p=0..3 / p=1,2,3)
      - B1[i], B3[i]  驻留开关 (binary)
      - a01[i], a12[i] 行走轴 one-hot (binary, sum=1)
    目标函数:  \bar E^{(src-1)} + \bar E^{(src-3)} + \bar E^{(src-4)} + \bar E^{(4)}
      （均为归一化/每体素能量，见 §4.2）
    """
    # ---- unpack ----
    L0 = cfg['L0']                          # {'x':..,'y':..,'z':..}
    C1, C3 = cfg['C1'], cfg['C3']           # SRAM/regfile capacity
    N_PE = cfg['N_PE']                      # PE 数
    # energies (DeviceParams 命名对齐)
    E_DDR_r = cfg['E_DDR_r']; E_DDR_w = cfg['E_DDR_w']
    E_SRAM_r = cfg['E_SRAM_r']; E_SRAM_w = cfg['E_SRAM_w']
    E_RF_r = cfg['E_RF_r'];   E_RF_w = cfg['E_RF_w']
    E_MACC = cfg['E_MACC']

    # ---- model ----
    m = gp.Model("mapper_full_axes_B")
    m.Params.NonConvex = 2
    if params:
        for kpar, vpar in params.items():
            setattr(m.Params, kpar, vpar)

    # ---- vars: L/k/y ----
    L: Dict[Tuple[int, str], gp.Var] = {}
    k: Dict[Tuple[int, str], gp.Var] = {}
    y: Dict[Tuple[int, str], gp.Var] = {}

    for p in (1, 2, 3):
        for i in Dims:
            L[(p, i)] = m.addVar(vtype=GRB.INTEGER, lb=1, ub=L0[i], name=f"L_{p}_{i}")
            y[(p, i)] = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name=f"y_{p}_{i}")  # y=1/L

    for p in (0, 1, 2, 3):
        for i in Dims:
            k[(p, i)] = m.addVar(vtype=GRB.INTEGER, lb=1, ub=L0[i], name=f"k_{p}_{i}")

    # ---- B 矩阵 (only L1 & L3 are switchable per §1.2/§4.1) ----
    B1 = {i: m.addVar(vtype=GRB.BINARY, name=f"B1_{i}") for i in Dims}
    B3 = {i: m.addVar(vtype=GRB.BINARY, name=f"B3_{i}") for i in Dims}

    # ---- 行走轴 one-hot: alpha_{0-1}, alpha_{1-2} ----
    a01 = {i: m.addVar(vtype=GRB.BINARY, name=f"a01_{i}") for i in Dims}
    a12 = {i: m.addVar(vtype=GRB.BINARY, name=f"a12_{i}") for i in Dims}
    m.addConstr(gp.quicksum(a01.values()) == 1, name="onehot_a01")
    m.addConstr(gp.quicksum(a12.values()) == 1, name="onehot_a12")

    m.update()

    # ---- hierarchy / divisibility ----  (§3.3)
    for i in Dims:
        m.addQConstr(L0[i] == k[(0, i)] * L[(1, i)], name=f"div_0_{i}")  # L0 = k0 * L1
        m.addQConstr(L[(1, i)] == k[(1, i)] * L[(2, i)], name=f"div_1_{i}")  # L1 = k1 * L2
        m.addQConstr(L[(2, i)] == k[(2, i)] * L[(3, i)], name=f"div_2_{i}")  # L2 = k2 * L3
        m.addConstr(L[(3, i)] == k[(3, i)], name=f"anchor_3_{i}")            # L3 = k3 (L4=1)

    # ---- reciprocal constraints: y * L = 1 ----  (Objective must be linear/quadratic)
    for p in (1, 2, 3):
        for i in Dims:
            m.addQConstr(y[(p, i)] * L[(p, i)] == 1.0, name=f"inv_L_{p}_{i}")  # y=1/L

    # for rho in src-1 (z uses k0_z): v0z = 1 / k0_z
    v0z = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name="v0_z")
    m.addQConstr(v0z * k[(0, 'z')] == 1.0, name="inv_k0_z")

    # inv k1, k2 for later usages
    invk1 = {i: m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name=f"inv_k1_{i}") for i in Dims}
    invk2 = {i: m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name=f"inv_k2_{i}") for i in Dims}
    for i in Dims:
        m.addQConstr(invk1[i] * k[(1, i)] == 1.0, name=f"invk1_{i}")
        m.addQConstr(invk2[i] * k[(2, i)] == 1.0, name=f"invk2_{i}")

    # r3k1 = 1/(L3 * k1) to avoid triple products in src-3 denominator
    r3k1 = {}
    for i in Dims:
        t3k1 = m.addVar(vtype=GRB.INTEGER, lb=1, ub=L0[i] * L0[i], name=f"t3k1_{i}")
        r3k1[i] = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name=f"r3k1_{i}")
        m.addQConstr(L[(3, i)] * k[(1, i)] == t3k1, name=f"mul_L3_k1_{i}")
        m.addQConstr(r3k1[i] * t3k1 == 1.0, name=f"inv_L3k1_{i}")

    # ---- capacity constraints (SRAM / regfile) ---- (§3.1, with bypass)
    # C^(p) >= B_y^(p)*L_x^(p)L_z^(p) + B_x^(p)*L_y^(p)L_z^(p) + B_z^(p)*L_x^(p)L_y^(p)
    def _add_capacity_constr(
        p: int,
        C: float,
        Bp: Dict[str, gp.Var],
        name: str,
    ) -> None:
        Lx, Ly, Lz = L[(p, 'x')], L[(p, 'y')], L[(p, 'z')]
        ub_xz = float(L0['x'] * L0['z'])
        ub_yz = float(L0['y'] * L0['z'])
        ub_xy = float(L0['x'] * L0['y'])

        # 投影面积（面法向分别为 y/x/z）
        A_y = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=ub_xz, name=f"A_{p}_y")
        A_x = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=ub_yz, name=f"A_{p}_x")
        A_z = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=ub_xy, name=f"A_{p}_z")
        m.addQConstr(A_y == Lx * Lz, name=f"def_A_{p}_y")
        m.addQConstr(A_x == Ly * Lz, name=f"def_A_{p}_x")
        m.addQConstr(A_z == Lx * Ly, name=f"def_A_{p}_z")

        # 旁路 (B=0) 时不计入容量；驻留 (B=1) 时计入对应投影面积
        occ_y = _gate_prod(m, Bp['y'], A_y, ub_xz, name=f"occ_{p}_y")
        occ_x = _gate_prod(m, Bp['x'], A_x, ub_yz, name=f"occ_{p}_x")
        occ_z = _gate_prod(m, Bp['z'], A_z, ub_xy, name=f"occ_{p}_z")
        m.addConstr(occ_y + occ_x + occ_z <= C, name=name)

    _add_capacity_constr(p=1, C=C1, Bp=B1, name="cap_lvl1")
    _add_capacity_constr(p=3, C=C3, Bp=B3, name="cap_lvl3")

    # ---- PE resource: k2x*k2y*k2z == N_PE ---- (§3.2)
    k2x, k2y, k2z = k[(2, 'x')], k[(2, 'y')], k[(2, 'z')]
    t12 = m.addVar(vtype=GRB.INTEGER, lb=1, ub=max(1, N_PE), name="t_pe_xy")
    m.addQConstr(k2x * k2y == t12, name="pe_xy")
    m.addQConstr(t12 * k2z == N_PE, name="pe_xyz")

    # -------------------------------
    #   Objective: §4.2  (normalized)
    # -------------------------------
    obj = gp.LinExpr(0.0)

    # === 准备工作: 辅助变量构建 ===

    # 1. Src-1 辅助变量: w01 (B1 & a01), phi01 (B1 & ~a01 & y1)
    w01_alpha = {}
    w01_bg = {}
    phi01_bg = {}
    for i in Dims:
        w01_alpha[i] = _and_bin(m, B1[i], a01[i], f"w01a_{i}")
        w01_bg[i] = _and_bin_neg(m, B1[i], a01[i], f"w01bg_{i}")
        phi01_bg[i] = _gate_prod(m, w01_bg[i], y[(1, i)], 1.0, f"phi01bg_{i}")

    # 2. Src-3 辅助变量: g3 (B3 & a12), phi3
    g3_alpha, g3_bg, phi3_alpha, phi3_bg = {}, {}, {}, {}
    for i in Dims:
        g3_alpha[i] = _and_bin(m, B3[i], a12[i], f"g3a_{i}")
        g3_bg[i] = _and_bin_neg(m, B3[i], a12[i], f"g3bg_{i}")
        phi3_alpha[i] = _gate_prod(m, g3_alpha[i], r3k1[i], 1.0, f"phi3a_{i}")
        phi3_bg[i] = _gate_prod(m, g3_bg[i], y[(3, i)], 1.0, f"phi3bg_{i}")

        # 3. Src-3 计数与来源切换: counts3, tau3 (B1 * counts3)
    tau3counts_B1 = {}
    counts3_map = {}
    for i in Dims:
        counts3 = phi3_alpha[i] + phi3_bg[i]
        counts3_map[i] = counts3
        tau3 = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name=f"tau3cntB1_{i}")
        # tau3 = B1 * counts3 (Big-M linearization, M=1)
        m.addConstr(tau3 <= counts3)
        m.addConstr(tau3 <= B1[i])
        m.addConstr(tau3 >= counts3 - (1 - B1[i]))
        tau3counts_B1[i] = tau3

    # =========================================================
    # (1) Src-1: 上级→SRAM (§2.1, §4.2-1)
    # =========================================================
    # X/Y: 能量 = counts * (E_DDR_r + E_SRAM_w)
    for i in ('x', 'y'):
        counts = w01_alpha[i] * (1.0 / float(L0[i])) + phi01_bg[i]
        obj += (E_DDR_r + E_SRAM_w) * counts

    # Z: 特殊处理 rho = 1 - 1/k0_z = 1 - v0z
    # E_z = counts_z * E_DDR_w + (counts_z_eff_read) * (E_DDR_r + E_SRAM_w)
    # 其中 counts_z_eff_read = w01_alpha/L0 + phi01_bg * rho
    counts_z = w01_alpha['z'] * (1.0 / float(L0['z'])) + phi01_bg['z']
    obj += E_DDR_w * counts_z
    # 只有 bypass 部分 (phi01_bg) 受 rho 影响; 列首 (w01_alpha) 总是 1/L0
    # 修正 src-1 z轴: 若行走轴(w01_alpha=1)则 rho=0, 该项为0; 若 bypass(phi01_bg=1)则 rho=1-v0z
    # 原代码错误地加上了 w01_alpha 部分 (相当于隐含 rho=1)
    obj += (E_DDR_r + E_SRAM_w) * (phi01_bg['z'] * (1.0 - v0z))

    # =========================================================
    # (2) Src-3: 上级→regfile (§2.2, §4.2-2)
    # =========================================================

    # --- X/Y 轴 (无 rho 修正, 线性/双线性) ---
    for i in ('x', 'y'):
        c3 = counts3_map[i]
        # Upstream (RegFile Write)
        obj += E_RF_w * c3
        # Downstream (SRAM/DDR Read via invk2)
        # 若 B1=1 (SRAM): E_SRAM_r; 若 B1=0 (DDR): E_DDR_r
        obj += E_DDR_r * invk2[i] * c3
        obj += (E_SRAM_r - E_DDR_r) * invk2[i] * tau3counts_B1[i]

    # --- Z 轴 (含 rho 修正) ---
    # 逻辑推导:
    # E_src3_z = counts3 * [ rho*E_RF_w + (E_src_w + rho*E_src_r)/k2 ]
    # 提取公因式 rho: = counts3*rho * (E_RF_w + E_src_r/k2) + counts3 * (E_src_w/k2)
    # 代换 rho*counts3 = counts3 - CF_z (CF_z 为扣除项)
    # CF_z = B3_z * [ a12_z*(1/L0) + (1-a12_z)*(k2/L0) ]

    # 1. 构建 CF_z (Correction Factor)
    CF_z = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name="CF_src3_z")
    # 修正 src-3 z轴: 理论推导表明 CF_z = (B3 * k2) / L0，无论是否行走轴
    # (原代码在行走轴分支漏乘了 k2，导致修正项偏小，能量偏大)
    m.addQConstr(CF_z == B3['z'] * k[(2, 'z')] * (1.0 / float(L0['z'])), name="def_CF_z")

    # 2. 构建 B1 * CF_z (用于区分 SRAM/DDR 的扣除项)
    B1_CF_z = m.addVar(vtype=GRB.CONTINUOUS, lb=0.0, ub=1.0, name="B1_CF_z")
    m.addQConstr(B1_CF_z == B1['z'] * CF_z, name="QC_B1_CF_z")

    c3z = counts3_map['z']
    tau3z = tau3counts_B1['z']

    # 3. 计算能量
    # (A) RegFile Write (Upstream): counts3 * rho * E_RF_w => (c3z - CF_z) * E_RF_w
    obj += E_RF_w * (c3z - CF_z)

    # (B) DDR Source (B1=0): E_w/k2 + rho*E_r/k2
    # 贡献量: (1-B1)*[ c3z * E_DDR_w + (c3z - CF_z) * E_DDR_r ] * invk2
    # 展开 (1-B1)*c3z = c3z - tau3z;  (1-B1)*CF_z = CF_z - B1_CF_z
    term_ddr_w = (c3z - tau3z) * E_DDR_w
    term_ddr_r = (c3z - tau3z - (CF_z - B1_CF_z)) * E_DDR_r
    obj += (term_ddr_w + term_ddr_r) * invk2['z']

    # (C) SRAM Source (B1=1): E_w/k2 + rho*E_r/k2
    # 贡献量: B1*[ c3z * E_SRAM_w + (c3z - CF_z) * E_SRAM_r ] * invk2
    term_sram_w = tau3z * E_SRAM_w
    term_sram_r = (tau3z - B1_CF_z) * E_SRAM_r
    obj += (term_sram_w + term_sram_r) * invk2['z']

    # =========================================================
    # (3) Src-4: 上级→MACC (§2.3, §4.2-3)
    # =========================================================
    # 辅助变量: 互斥选择 z14 (SRAM->MACC), z04 (DDR->MACC)
    z14 = {i: _and_bin_neg(m, B1[i], B3[i], f"z14_{i}") for i in Dims}  # B1 & ~B3
    z04 = {i: _and_bin_neg(m, 1 - B1[i], B3[i], f"z04_{i}") for i in Dims}  # ~B1 & ~B3

    # --- X/Y 轴 (标准) ---
    for i in ('x', 'y'):
        obj += B3[i] * E_RF_r  # RegFile->MACC
        obj += z14[i] * (E_SRAM_r * invk2[i])  # SRAM->MACC
        obj += z04[i] * (E_DDR_r * invk2[i])  # DDR->MACC

    # --- Z 轴 (含 rho 修正) ---
    # 1. RegFile Source: E = B3 * (E_w + rho*E_r)
    # rho_4 = 1 - k2/L0. => E = B3*(E_w+E_r) - B3*k2 * (E_r/L0)
    obj += B3['z'] * (E_RF_w + E_RF_r)
    obj += -(E_RF_r / float(L0['z'])) * (B3['z'] * k[(2, 'z')])  # Quadratic: Bin*Int

    # 2. SRAM Source: E = z14 * (E_w + rho*E_r) / k2
    # = z14 * [ (E_w+E_r)/k2 - (E_r/L0) ]  (因 rho/k2 = 1/k2 - 1/L0)
    term_s4_sram = z14['z'] * (E_SRAM_w + E_SRAM_r) * invk2['z']
    term_s4_sram -= z14['z'] * (E_SRAM_r / float(L0['z']))
    obj += term_s4_sram

    # 3. DDR Source: 同理
    term_s4_ddr = z04['z'] * (E_DDR_w + E_DDR_r) * invk2['z']
    term_s4_ddr -= z04['z'] * (E_DDR_r / float(L0['z']))
    obj += term_s4_ddr

    # =========================================================
    # (4) MACC: 每体素 E_MACC (§2.4, §4.2-4)
    # =========================================================
    obj += E_MACC

    m.setObjective(obj, GRB.MINIMIZE)
    return m, L, k, y, B1, B3, a01, a12
