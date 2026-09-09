"""Thin, hash-checked integration with GOMA's unmodified main-branch modules."""
from __future__ import annotations
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import time
from dataclasses import asdict
from .core import DIMS, Problem, Profile, OracleResult, integer

COMMIT = 'b4015e465d78a8dbeb25ec9220cfe7c34883a865'
BLOBS = {'normalized_energy_model.py': '67195293bb88906047ffbcb4c629a812fdb2c350',
         'full_model.py': '8979f7b0c2812ea3b5012d27b748e900e4938f65'}
PARAMS = dict(NonConvex=2, MIPGap=0.0, MIPGapAbs=0.0, IntegralityFocus=1,
              IntFeasTol=1e-9, FeasibilityTol=1e-9, NumericFocus=3,
              DualReductions=0, OutputFlag=0)


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


class Upstream:
    def __init__(self, root, *, allow_change=False, evaluator_only=False):
        self.root = Path(root).resolve()
        self.hashes = {}
        def load(filename):
            path = self.root / filename
            if not path.is_file(): raise FileNotFoundError(f'Required GOMA source missing: {path}')
            # Account for checkout CRLF without modifying the source file.
            data = path.read_bytes().replace(b'\r\n', b'\n')
            sha = blob_sha(data)
            if sha != BLOBS[filename] and not allow_change:
                raise ValueError(f'{filename} does not match reviewed main commit {COMMIT}; '
                                 'use its pinned checkout, or explicitly allow a new source revision')
            self.hashes[filename] = sha
            name = '_goma_upstream_' + filename[:-3] + '_' + sha
            if name in sys.modules: return sys.modules[name]
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try: spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(name, None)
                raise
            return module
        self.energy = load('normalized_energy_model.py')
        self.full = None if evaluator_only else load('full_model.py')

    def evaluate(self, problem: Problem, mapping: dict, *, n=None, capacity=None) -> Profile:
        cfg = problem.cfg
        h12, h23, h34 = ({d: integer(mapping[key][d], key+'.'+d, 1) for d in DIMS}
                         for key in ('hatL_12', 'hatL_23', 'hatL_34'))
        L3 = h34
        L2 = {d: h23[d]*L3[d] for d in DIMS}
        L1 = {d: h12[d]*L2[d] for d in DIMS}
        if any(cfg['L0'][d] % L1[d] for d in DIMS): raise ValueError('Hierarchy divisibility failed')
        h01 = {d: cfg['L0'][d] // L1[d] for d in DIMS}
        b1 = {d: integer(mapping['B1'][d], 'B1.'+d) for d in DIMS}
        b3 = {d: integer(mapping['B3'][d], 'B3.'+d) for d in DIMS}
        if any(v > 1 for v in [*b1.values(), *b3.values()]): raise ValueError('Nonbinary residency')
        a01, a12 = mapping['alpha01'], mapping['alpha12']
        if a01 not in DIMS or a12 not in DIMS: raise ValueError('Invalid walking axis')
        if any(v > 1 for v in h01.values()):
            if h01[a01] == 1: raise ValueError('Stage 01 selected a unit loop')
        elif a01 != 'x': raise ValueError('Noncanonical all-unit stage 01')
        if any(v > 1 for v in h12.values()):
            if h12[a12] == 1: raise ValueError('Stage 12 selected a unit loop')
        elif a12 != a01: raise ValueError('Stage 12 must inherit stage 01')
        footprint = lambda L, b: sum(b[d]*math.prod(L[u] for u in DIMS if u != d) for d in DIMS)
        c1, c3 = footprint(L1, b1), footprint(L3, b3)
        n_actual = math.prod(h23.values())
        cap = problem.C if capacity is None else capacity
        if c1 > cap or c3 > cfg['C3']: raise ValueError('Mapping capacity violation')
        if n is not None and n_actual != n: raise ValueError('Mapping exact PE mismatch')
        if n_actual > problem.N: raise ValueError('Mapping uses too many PEs')
        p = self.energy.DeviceParams(
            E_DDR_read=cfg['E_DDR_r'], E_DDR_write=cfg['E_DDR_w'],
            E_SRAM_read=cfg['E_SRAM_r'], E_SRAM_write=cfg['E_SRAM_w'],
            E_RF_read=cfg['E_RF_r'], E_RF_write=cfg['E_RF_w'], E_MACC=cfg['E_MACC'])
        phi, parts = self.energy.compute_normalized_total_energy(
            L0=cfg['L0'], hatL_12=h12, hatL_23=h23, hatL_34=h34,
            alpha01=a01, alpha12=a12, B={1: b1, 3: b3}, params=p, include_leak=False)
        clean = dict(hatL_12=h12, hatL_23=h23, hatL_34=h34, alpha01=a01,
                     alpha12=a12, B1=b1, B3=b3, L1=L1, L2=L2, L3=L3, energy_parts=parts)
        return Profile(n_actual, c1, float(phi), clean)

    def seeds(self, problem: Problem) -> list[Profile]:
        """Cheap feasible all-bypass starts, not an exhaustive mapping search."""
        profiles = []
        for n in problem.ns:
            choices = []
            for order in itertools.permutations(DIMS):
                left, spatial = n, {}
                for d in order:
                    spatial[d] = math.gcd(left, problem.cfg['L0'][d])
                    left //= spatial[d]
                if left != 1: raise AssertionError('Divisor factor assignment failed')
                h01 = {d: problem.cfg['L0'][d] // spatial[d] for d in DIMS}
                a = next((d for d in DIMS if h01[d] > 1), 'x')
                one = dict.fromkeys(DIMS, 1); zero = dict.fromkeys(DIMS, 0)
                m = dict(hatL_12=one, hatL_23=spatial, hatL_34=one,
                         alpha01=a, alpha12=a, B1=zero, B3=zero)
                choices.append(self.evaluate(problem, m, n=n, capacity=0))
            profiles.append(min(choices, key=lambda p: p.e))
        return profiles


def extract_mapping(variables: dict) -> dict:
    def val(name):
        x = float(variables[name].X)
        r = round(x)
        if not math.isfinite(x) or abs(x-r) > 1e-5:
            raise RuntimeError(f'Nonintegral Gurobi value {name}={x}')
        return r
    def axis(prefix):
        selected = [d for d in DIMS if val(prefix+'_'+d)]
        if len(selected) != 1: raise RuntimeError('Invalid one-hot walking axis')
        return selected[0]
    return dict(hatL_12={d: val('k_1_'+d) for d in DIMS},
                hatL_23={d: val('k_2_'+d) for d in DIMS},
                hatL_34={d: val('k_3_'+d) for d in DIMS},
                B1={d: val('B1_'+d) for d in DIMS}, B3={d: val('B3_'+d) for d in DIMS},
                alpha01=axis('a01'), alpha12=axis('a12'))


def set_start(variables, profile: Profile, problem: Problem):
    """Partial start only; Gurobi reconstructs auxiliary variables."""
    import gurobipy as gp
    m = profile.mapping
    for v in variables.values(): v.Start = gp.GRB.UNDEFINED
    L3 = m['hatL_34']; L2 = {d: m['hatL_23'][d]*L3[d] for d in DIMS}
    L1 = {d: m['hatL_12'][d]*L2[d] for d in DIMS}
    for d in DIMS:
        for p, L in ((1, L1), (2, L2), (3, L3)):
            variables[f'L_{p}_{d}'].Start = L[d]
        for p, h in ((0, {d: problem.cfg['L0'][d]//L1[d]}),
                     (1, m['hatL_12']), (2, m['hatL_23']), (3, m['hatL_34'])):
            variables[f'k_{p}_{d}'].Start = h[d]
        for b in ('B1', 'B3'): variables[f'{b}_{d}'].Start = m[b][d]
        variables['a01_'+d].Start = int(m['alpha01'] == d)
        variables['a12_'+d].Start = int(m['alpha12'] == d)


def make_variable_pe(model, problem: Problem, *, name='batch_n'):
    """Change only the PE resource equality; retain all original traffic rows."""
    import gurobipy as gp
    model.update()
    old = [c for c in model.getQConstrs() if c.QCName == 'pe_product_xyz']
    if len(old) != 1: raise RuntimeError('Unsupported upstream PE constraint interface')
    model.remove(old[0])
    n = model.addVar(vtype=gp.GRB.INTEGER, lb=1, ub=min(problem.N, problem.V), name=name)
    xy = model.getVarByName('product_k2_xy')
    kz = model.getVarByName('k_2_z')
    model.addQConstr(xy*kz == n, name='batch_variable_pe_product')
    model.update()
    return n


class GurobiOracle:
    """Build once; reuse the same GOMA model across n/c queries.

    Time-limited queries contribute real lower bounds but never certify plateaux.
    Persistent cache entries are tied to source hashes, cfg and solver parameters.
    """
    def __init__(self, problem: Problem, upstream: Upstream, *, params=None, cache_dir=None, log_dir=None):
        if upstream.full is None: raise ValueError('Optimizer source was not loaded')
        self.problem, self.upstream = problem, upstream
        self.params = dict(PARAMS); self.params.update(params or {})
        self.params.update(NonConvex=2, MIPGap=0.0, MIPGapAbs=0.0)
        self.model = upstream.full.build_model_full(problem.cfg, self.params)[0]
        self.nvar = make_variable_pe(self.model, problem)
        self.caprow = self.model.getConstrByName('capacity_level_1')
        if self.caprow is None: raise RuntimeError('Unsupported upstream capacity interface')
        self.scale = max(1.0, math.sqrt(problem.C))
        self.vars = {v.VarName: v for v in self.model.getVars()}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.log_dir = Path(log_dir) if log_dir else None
        for path in (self.cache_dir, self.log_dir):
            if path: path.mkdir(parents=True, exist_ok=True)
        import gurobipy as gp
        payload = [problem.cfg, upstream.hashes, self.params, gp.gurobi.version(), 'adapter-v1']
        self.fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.calls = 0; self.cache_hits = 0; self.history = []
        self.known = upstream.seeds(problem)

    def solve(self, n: int | None, capacity: int, seconds: float) -> OracleResult:
        import gurobipy as gp
        from gurobipy import GRB
        start = time.monotonic()
        p = self.problem
        if n is not None and n not in p.ns: raise ValueError('Illegal oracle PE query')
        capacity = integer(capacity, 'oracle capacity')
        if capacity > p.C: raise ValueError('Oracle capacity exceeds the physical SRAM')
        key = self.fingerprint + f'_{n}_{capacity}'
        path = self.cache_dir / (key+'.json') if self.cache_dir else None
        if path and path.exists():
            raw = json.loads(path.read_text())
            if raw['fingerprint'] != self.fingerprint: raise RuntimeError('Cache identity mismatch')
            data = raw['result']
            if data['optimal'] and data['profile']:
                prof = self.upstream.evaluate(p, data['profile']['mapping'], n=n, capacity=capacity)
                if not math.isclose(prof.e, data['profile']['e'], rel_tol=1e-12, abs_tol=1e-12):
                    raise RuntimeError('Cached mapping energy mismatch')
                data['profile'] = prof
                self.cache_hits += 1
                result = OracleResult(**data)
                self.known.append(prof)
                return result
        if seconds <= 0:
            return OracleResult(n, capacity, p.io_bound, None, False, 'BUDGET')
        m = self.model
        self.nvar.LB = 1 if n is None else n
        self.nvar.UB = min(p.N, p.V) if n is None else n
        self.caprow.RHS = capacity / self.scale
        candidates = [x for x in self.known if x.c <= capacity and (n is None or x.n == n)]
        if candidates:
            seed = min(candidates, key=lambda x: x.e)
            set_start(self.vars, seed, p)
            self.nvar.Start = seed.n
        else:
            for v in self.vars.values(): v.Start = GRB.UNDEFINED
        self.calls += 1
        if self.log_dir: m.Params.LogFile = str(self.log_dir / f'oracle_{self.calls:05d}_n{n}_c{capacity}.log')
        m.Params.TimeLimit = max(0.001, seconds-(time.monotonic()-start))
        m.optimize()
        status = str(m.Status)
        if m.Status in (GRB.INFEASIBLE, GRB.INF_OR_UNBD, GRB.UNBOUNDED, GRB.NUMERIC):
            # All-bypass supplies a constructive feasible witness for every query.
            raise RuntimeError(f'Unexpected oracle status {m.Status}; refusing an invalid certificate')
        bound = float(m.ObjBound)
        bound = max(p.io_bound, bound if math.isfinite(bound) else -math.inf)
        prof = None; obj = None; solver_gap = None
        if m.SolCount:
            prof = self.upstream.evaluate(p, extract_mapping(self.vars), n=n, capacity=capacity)
            obj = float(m.ObjVal); solver_gap = float(m.MIPGap)
            if not math.isclose(prof.e, obj, rel_tol=1e-7, abs_tol=1e-8):
                raise RuntimeError(f'GOMA objective/evaluator mismatch: solver={obj}, formula={prof.e}')
            if bound > prof.e + max(1e-8, abs(prof.e)*1e-7):
                raise RuntimeError('Oracle lower bound exceeds independently evaluated incumbent')
            bound = min(bound, prof.e)  # roundoff only, after the discrepancy check above
            self.known.append(prof)
        result = OracleResult(n, capacity, bound, prof, m.Status == GRB.OPTIMAL and prof is not None,
                              status, time.monotonic()-start, obj, float(m.ObjBound), solver_gap)
        self.history.append(asdict(result))
        if path:
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(dict(fingerprint=self.fingerprint, result=asdict(result)), indent=2))
            temp.replace(path)
        return result

    def close(self): self.model.dispose()
