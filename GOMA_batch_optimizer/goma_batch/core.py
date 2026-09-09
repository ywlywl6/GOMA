"""Batch-only semantics. Energies are normalized dynamic energy (energy/MAC)."""
from __future__ import annotations
import hashlib
import json
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Any

DIMS = ('x', 'y', 'z')
ENERGY_KEYS = ('E_DDR_r', 'E_DDR_w', 'E_SRAM_r', 'E_SRAM_w',
               'E_RF_r', 'E_RF_w', 'E_MACC')


def integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be an integer')
    if not math.isfinite(value) or int(value) != value or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return int(value)


def divisors(n: int, cap: int | None = None) -> list[int]:
    cap = n if cap is None else min(cap, n)
    lo, hi = [], []
    for k in range(1, min(math.isqrt(n), cap) + 1):
        if n % k == 0:
            lo.append(k)
            if n // k != k and n // k <= cap:
                hi.append(n // k)
    return sorted(lo + hi)


@dataclass(frozen=True)
class Problem:
    cfg: dict[str, Any]
    B: int
    objective: str = 'edp'
    leakage: bool = False

    def __post_init__(self):
        c = dict(self.cfg)
        c['L0'] = {d: integer(c['L0'][d], 'L0.' + d, 1) for d in DIMS}
        for key in ('C1', 'C3', 'N_PE'):
            c[key] = integer(c[key], key, int(key == 'N_PE'))
        for key in ENERGY_KEYS + ('E_SRAM_leak', 'E_RF_leak'):
            v = float(c.get(key, 0.0)) if 'leak' in key else float(c[key])
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{key} must be finite and non-negative')
            c[key] = v
        object.__setattr__(self, 'cfg', c)
        object.__setattr__(self, 'B', integer(self.B, 'B', 1))
        if not isinstance(self.leakage, bool):
            raise ValueError('leakage must be a JSON boolean')
        if self.objective not in ('energy', 'edp'):
            raise ValueError('objective must be energy or edp')
        if max(c['C1'], c['C3'], c['N_PE'], *c['L0'].values()) > 2**53 - 1:
            raise ValueError('Integer solver data must fit exactly in float64')

    @property
    def V(self): return math.prod(self.cfg['L0'].values())
    @property
    def N(self): return self.cfg['N_PE']
    @property
    def C(self): return self.cfg['C1']
    @property
    def power(self):
        return (self.cfg['E_SRAM_leak'] + self.N * self.cfg['E_RF_leak']) if self.leakage else 0.0
    @property
    def io_bound(self):
        x, y, z = (self.cfg['L0'][d] for d in DIMS)
        return self.cfg['E_DDR_r'] * (1 / x + 1 / y) + self.cfg['E_DDR_w'] / z + self.cfg['E_MACC']
    @property
    def ns(self): return divisors(self.V, self.N)
    @property
    def qs(self): return [q for q in divisors(self.B, self.N) if q * self.V >= self.N]

    def pairs(self) -> list[tuple[int, int]]:
        # Safe necessary tests only. The MILP enforces exact PE packing.
        ns = self.ns
        out = []
        for q in self.qs:
            for s in ns:
                if q * s > self.N: break
                if self.N > s + (q - 1) * ns[-1]: continue
                if q == 1 and s != self.N: continue
                out.append((q, s))
        return out

    def score(self, q: int, s: int, sum_phi: float) -> float:
        energy = sum_phi / q + self.power / (q * s)
        return energy if self.objective == 'energy' else energy * self.N / (q * s)

    def universal_bound(self) -> float:
        vals = [self.score(q, s, q * self.io_bound) for q, s in self.pairs()]
        return min(vals, default=math.inf)

    @property
    def dimensional_scale(self):
        return self.B * self.V if self.objective == 'energy' else self.B**2 * self.V**2 / self.N


@dataclass
class Profile:
    n: int
    c: int
    e: float
    mapping: dict[str, Any]
    id: str = ''

    def __post_init__(self):
        self.n = integer(self.n, 'profile.n', 1)
        self.c = integer(self.c, 'profile.c')
        if not math.isfinite(self.e) or self.e < 0: raise ValueError('Invalid profile energy')
        if not self.id:
            raw = json.dumps([self.n, self.c, self.e, self.mapping], sort_keys=True).encode()
            self.id = hashlib.sha256(raw).hexdigest()[:20]


@dataclass
class Cell:
    id: str
    n: int
    lo: int
    hi: int
    lb: float
    closed: bool = False


@dataclass
class Option:
    id: str
    n: int
    c: int
    e: float


@dataclass
class OracleResult:
    n: int | None
    capacity: int
    lower: float
    profile: Profile | None
    optimal: bool
    status: str
    runtime: float = 0.0
    raw_objective: float | None = None
    raw_bound: float | None = None
    gap: float | None = None


@dataclass
class MasterResult:
    lower: float
    objective: float | None = None
    q: int | None = None
    s: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    status: str = 'UNKNOWN'
    runtime: float = 0.0
    variables: int = 0


def pareto(profiles: list[Profile]) -> list[Profile]:
    # Never dominate across distinct exact PE counts. No approximate pruning.
    best = {}
    out = []
    for p in sorted(profiles, key=lambda p: (p.n, p.c, p.e, p.id)):
        if p.e < best.get(p.n, math.inf):
            out.append(p)
            best[p.n] = p.e
    return out


def gap(upper: float, lower: float) -> float:
    if not math.isfinite(upper): return math.inf
    if upper == 0: return 0.0 if lower >= 0 else math.inf
    return max(0.0, upper - lower) / abs(upper)


def closed_gap(upper: float, lower: float, rtol: float, atol: float) -> bool:
    return math.isfinite(upper) and upper - lower <= max(atol, rtol * abs(upper))


def schedule(problem: Problem, profiles: list[Profile], counts: dict[str, int]) -> dict:
    index = {p.id: p for p in profiles}
    if any(k not in index or z < 0 or int(z) != z for k, z in counts.items()):
        raise ValueError('Invalid profile counts')
    entries = [(index[k], z) for k, z in counts.items() if z]
    q = sum(z for _, z in entries)
    if not q or problem.B % q or sum(p.n * z for p, z in entries) != problem.N:
        raise ValueError('Invalid batch/PE packing')
    used = sum(p.c * z for p, z in entries)
    if used > problem.C: raise ValueError('SRAM capacity exceeded')
    if any(problem.V % p.n for p, z in entries): raise ValueError('Illegal PE count')
    s = min(p.n for p, _ in entries)
    S = sum(p.e * z for p, z in entries)
    cycles = (problem.B // q) * (problem.V // s)
    dyn = problem.B * problem.V * S / q
    leak = problem.power * cycles
    groups = []
    extra = problem.C - used
    # Allocate all unused quota to one slot, without changing its mapping.
    for p, z in sorted(entries, key=lambda x: (x[0].n, x[0].c, x[0].id)):
        record = dict(profile_id=p.id, n=p.n, footprint=p.c, phi=p.e, mapping=p.mapping)
        if extra:
            groups.append(dict(record, multiplicity=1, C1_quota=p.c + extra))
            extra = 0
            z -= 1
        if z: groups.append(dict(record, multiplicity=z, C1_quota=p.c))
    return dict(q=q, rounds=problem.B // q, min_pe=s, total_cycles=cycles,
                gamma=problem.N / (q * s), dynamic_energy=dyn, leakage_energy=leak,
                total_energy=dyn + leak, edp=(dyn + leak) * cycles,
                normalized_objective=problem.score(q, s, S), used_SRAM=used,
                allocation_groups=groups)


class Budget:
    def __init__(self, seconds: float):
        if seconds <= 0: raise ValueError('Time budget must be positive')
        self.start = time.monotonic()
        self.seconds = seconds
    def left(self): return max(0.0, self.seconds - (time.monotonic() - self.start))
    def elapsed(self): return time.monotonic() - self.start
