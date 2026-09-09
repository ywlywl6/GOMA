"""One count MILP jointly chooses q, actual minimum PE count, and configurations.
No variable is created per executed batch. Supports Gurobi and SciPy/HiGHS.
"""
from __future__ import annotations
import math
import time
from .core import Problem, Option, MasterResult


def solve_master(problem: Problem, options: list[Option], *, seconds=30.0,
                 backend='auto', q_bounds=None, output=False) -> MasterResult:
    start = time.monotonic()
    q_bounds = q_bounds or {}
    if seconds <= 0:
        return MasterResult(problem.universal_bound(), status='BUDGET')
    # Sparse row/column builder. w is an epigraph: max(relaxed cost, q-bound).
    cost, lb, ub, integ, rows, rlo, rhi = [], [], [], [], [], [], []
    def var(c=0.0, upper=math.inf, integer=False):
        i = len(cost); cost.append(c); lb.append(0.0); ub.append(upper); integ.append(int(integer)); return i
    def row(a, lower=-math.inf, upper=math.inf):
        rows.append(a); rlo.append(lower); rhi.append(upper)
    selectors, records = [], []
    for q, s in problem.pairs():
        if math.isinf(q_bounds.get(q, 0.0)): continue
        eligible = [p for p in options if s <= p.n <= problem.N - (q - 1) * s]
        if not any(p.n == s for p in eligible): continue
        y = var(upper=1, integer=True); selectors.append(y)
        zs = [(p, var(upper=q, integer=True)) for p in eligible]
        row({y: -q, **{i: 1 for _, i in zs}}, 0, 0)
        row({y: -problem.N, **{i: p.n for p, i in zs}}, 0, 0)
        row({y: -problem.C, **{i: p.c for p, i in zs}}, upper=0)
        row({y: -1, **{i: 1 for p, i in zs if p.n == s}}, lower=0)
        w = var(c=1.0)
        factor = 1 / q if problem.objective == 'energy' else problem.N / (q*q*s)
        leakage = problem.score(q, s, 0.0)
        row({w: 1, y: -leakage, **{i: -factor*p.e for p, i in zs}}, lower=0)
        row({w: 1, y: -max(0.0, q_bounds.get(q, 0.0))}, lower=0)
        records.append((q, s, y, zs))
    if not selectors:
        return MasterResult(math.inf, status='INFEASIBLE', runtime=time.monotonic()-start)
    row({i: 1 for i in selectors}, 1, 1)
    if backend == 'auto':
        try: import gurobipy; backend = 'gurobi'
        except ImportError: backend = 'scipy'
    if backend == 'gurobi':
        import gurobipy as gp
        from gurobipy import GRB
        m = gp.Model('goma_batch_count_master')
        try:
            m.Params.OutputFlag = int(output)
            m.Params.MIPGap = 0.0
            m.Params.MIPGapAbs = 0.0
            m.Params.FeasibilityTol = 1e-9
            m.Params.IntFeasTol = 1e-9
            vs = [m.addVar(lb=l, ub=u, obj=c, vtype=GRB.INTEGER if it else GRB.CONTINUOUS)
                  for l, u, c, it in zip(lb, ub, cost, integ)]
            for a, l, u in zip(rows, rlo, rhi):
                ex = gp.LinExpr(list(a.values()), [vs[i] for i in a])
                if l == u: m.addConstr(ex == l)
                else:
                    if math.isfinite(l): m.addConstr(ex >= l)
                    if math.isfinite(u): m.addConstr(ex <= u)
            m.Params.TimeLimit = max(0.001, seconds-(time.monotonic()-start))
            m.optimize()
            status = str(m.Status)
            if m.Status == GRB.INFEASIBLE:
                return MasterResult(math.inf, status='INFEASIBLE', runtime=time.monotonic()-start, variables=len(vs))
            if m.Status in (GRB.INF_OR_UNBD, GRB.UNBOUNDED, GRB.NUMERIC):
                raise RuntimeError(f'Unexpected Gurobi master status {m.Status}')
            bound = float(m.ObjBound) if m.Status not in (GRB.LOADED,) else -math.inf
            sol = [v.X for v in vs] if m.SolCount else None
            obj = float(m.ObjVal) if m.SolCount else None
        finally:
            m.dispose()
    elif backend == 'scipy':
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_matrix
        rr, cc, vv = [], [], []
        for r, a in enumerate(rows):
            for col, val in a.items():
                if val: rr.append(r); cc.append(col); vv.append(val)
        A = coo_matrix((vv, (rr, cc)), shape=(len(rows), len(cost))).tocsc()
        result = milp(np.asarray(cost), integrality=np.asarray(integ), bounds=Bounds(lb, ub),
                      constraints=LinearConstraint(A, rlo, rhi),
                      options={'time_limit': max(0.001, seconds-(time.monotonic()-start)),
                               'mip_rel_gap': 0.0, 'disp': output})
        if result.status == 2:
            return MasterResult(math.inf, status='INFEASIBLE', runtime=time.monotonic()-start, variables=len(cost))
        if result.status not in (0, 1):
            raise RuntimeError(f'HiGHS master failure: {result.message}')
        status, sol, obj = str(result.status), result.x, result.fun
        raw = getattr(result, 'mip_dual_bound', None)
        bound = float(raw) if raw is not None else -math.inf
        # Keep the actual dual bound even for status=0: an absolute MIP
        # tolerance may have stopped HiGHS with a nonzero residual gap.
    else:
        raise ValueError('master backend must be auto, gurobi or scipy')
    # A safe analytic bound also applies to a restricted feasible catalogue.
    lower = max(problem.universal_bound(), bound if math.isfinite(bound) else -math.inf)
    out = MasterResult(lower, status=status, runtime=time.monotonic()-start, variables=len(cost))
    if sol is None: return out
    chosen = [(q, s, y, zs) for q, s, y, zs in records if sol[y] > 0.5]
    if len(chosen) != 1: raise RuntimeError('Master did not return one active branch')
    q, s, _, zs = chosen[0]
    counts = {}
    ptotal = ctotal = ktotal = 0
    actual_s = math.inf
    score_sum = 0.0
    for p, i in zs:
        z = round(float(sol[i]))
        if abs(sol[i]-z) > 1e-5 or z < 0: raise RuntimeError('Nonintegral master incumbent')
        if z:
            counts[p.id] = counts.get(p.id, 0) + z
            ktotal += z; ptotal += p.n*z; ctotal += p.c*z
            actual_s = min(actual_s, p.n); score_sum += p.e*z
    if (ktotal, ptotal, actual_s) != (q, problem.N, s) or ctotal > problem.C:
        raise RuntimeError('Master incumbent failed exact resource checks')
    out.q, out.s, out.counts = q, s, counts
    out.objective = max(problem.score(q, s, score_sum), q_bounds.get(q, 0.0))
    if lower > out.objective + 1e-7*max(1.0, abs(out.objective)):
        raise RuntimeError('Master bound exceeds its incumbent')
    return out
