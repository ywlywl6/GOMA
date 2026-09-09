"""Certified adaptive configuration generation, with optional joint-MIQCP probes.

Every capacity remains represented in the lower master, including unqueried
capacities. The upper master contains only actual, independently checked mappings.
"""
from __future__ import annotations
import math
from dataclasses import asdict
from .core import (Problem, Profile, Cell, Option, Budget, pareto, gap,
                   closed_gap, schedule)
from .master import solve_master


def solve_adaptive(problem: Problem, oracle, *, seeds: list[Profile], seconds=300.0,
                   oracle_seconds=30.0, master_seconds=10.0, backend='auto',
                   rel_gap=1e-6, abs_gap=1e-9, free_probe=True, seed_q1=True,
                   max_iterations=10000, joint_callback=None, joint_slots=8,
                   joint_every=12, on_progress=None) -> dict:
    if min(oracle_seconds, master_seconds) <= 0: raise ValueError('Positive query limits required')
    if rel_gap < 0 or abs_gap < 0: raise ValueError('Gap tolerances must be nonnegative')
    budget = Budget(seconds)
    profiles = pareto(list(seeds))
    serial = 0
    def make_cell(n, lo, hi, lower, closed=False):
        nonlocal serial
        serial += 1
        return Cell(f'cell_{serial}', n, lo, hi, lower, closed)
    cells = [make_cell(n, 0, problem.C, problem.io_bound) for n in problem.ns]
    lower = problem.universal_bound()
    upper, best = math.inf, None
    history, q_bounds, attempted_joint, calls, attempts = [], {}, set(), 0, {}
    status = 'TIME_LIMIT'

    def emit(kind, **kw):
        event = dict(event=kind, elapsed=budget.elapsed(), lower=lower,
                     upper=upper, relative_gap=gap(upper, lower), **kw)
        history.append(event)
        if on_progress: on_progress(event)

    def add_profiles(new):
        nonlocal profiles
        profiles = pareto(profiles + [p for p in new if p is not None])

    def absorb(cell: Cell, result):
        """Only an optimal solve may remove a proven constant-value plateau."""
        if result.profile is not None: add_profiles([result.profile])
        new_lb = max(cell.lb, result.lower)
        if not result.optimal or result.profile is None:
            cell.lb = new_lb
            return
        h = result.profile.c
        if result.profile.n != cell.n or h > cell.hi:
            raise RuntimeError('Oracle returned an incompatible witness')
        cells.remove(cell)
        if h > cell.lo: cells.append(make_cell(cell.n, cell.lo, h-1, new_lb))
        cells.append(make_cell(cell.n, max(cell.lo, h), cell.hi, new_lb, True))

    def accept(profs, counts):
        nonlocal upper, best
        candidate = schedule(problem, profs, counts)
        value = candidate['normalized_objective']
        if value < upper:
            upper, best = value, candidate

    if not problem.pairs():
        return dict(status='INFEASIBLE', schedule=None, lower_bound=None, upper_bound=None,
                    normalized_lower_bound=None, normalized_upper_bound=None,
                    relative_gap=None, history=[], runtime=budget.elapsed(), scope='all_periodic_partitions')
    # Establish an incumbent before spending time on any nonconvex model.
    um = solve_master(problem, [Option(p.id,p.n,p.c,p.e) for p in profiles],
                      seconds=min(master_seconds,budget.left()), backend=backend)
    if um.counts: accept(profiles, um.counts)
    emit('initial_master', variables=um.variables)

    if seed_q1 and not closed_gap(upper,lower,rel_gap,abs_gap) and problem.V % problem.N == 0 and budget.left() > 0:
        cell = next(c for c in cells if c.n == problem.N)
        r = oracle.solve(problem.N, problem.C, min(oracle_seconds,budget.left()))
        calls += 1; absorb(cell, r)
        if r.profile: accept([r.profile], {r.profile.id: 1})
        # q=1 shares its exact normalized objective with per-GEMM phi + P/N.
        q_bounds[1] = r.lower + problem.power/problem.N
        emit('q1_query', oracle_status=r.status)

    if free_probe and not closed_gap(upper,lower,rel_gap,abs_gap) and budget.left() > 0:
        r = oracle.solve(None, problem.C, min(oracle_seconds,budget.left()))
        calls += 1
        if r.profile: add_profiles([r.profile])
        for cell in cells: cell.lb = max(cell.lb, r.lower)
        lower = max(lower, min(problem.score(q,s,q*r.lower) for q,s in problem.pairs()))
        emit('free_pe_bound', oracle_status=r.status, per_gemm_lower=r.lower)

    for iteration in range(max_iterations):
        if closed_gap(upper,lower,rel_gap,abs_gap): status='GAP_CERTIFIED'; break
        if budget.left() <= 0: break
        um = solve_master(problem, [Option(p.id,p.n,p.c,p.e) for p in profiles],
                          seconds=min(master_seconds,budget.left()), backend=backend)
        if um.counts: accept(profiles,um.counts)
        if budget.left() <= 0: break
        lm = solve_master(problem, [Option(c.id,c.n,c.lo,c.lb) for c in cells],
                          seconds=min(master_seconds,budget.left()), backend=backend, q_bounds=q_bounds)
        if lm.status == 'INFEASIBLE':
            if best is not None: raise RuntimeError('Lower master excludes a known feasible mapping')
            lower=math.inf; status='INFEASIBLE'; break
        lower = max(lower,lm.lower)
        if math.isfinite(upper) and lower > upper + max(abs_gap, 1e-7*abs(upper)):
            raise RuntimeError('Global lower bound exceeds a feasible workload objective')
        lower = min(lower,upper) if math.isfinite(upper) else lower
        emit('master', iteration=iteration, variables=lm.variables, profiles=len(profiles),
             cells=len(cells), selected_q=lm.q, selected_min_pe=lm.s)
        if closed_gap(upper,lower,rel_gap,abs_gap): status='GAP_CERTIFIED'; break
        if not lm.counts:
            # A bounded master can time out without an incumbent; do not infer infeasibility.
            status='MASTER_LIMIT'; break

        # The hybrid spends bounded effort on a small, currently competitive q.
        # Its real bound strengthens the lower master; its mappings feed the upper one.
        use_joint = (joint_callback is not None and joint_every > 0 and iteration > 0
                     and iteration % joint_every == 0 and lm.q not in attempted_joint
                     and 1 < lm.q <= joint_slots and budget.left() > 0)
        if use_joint:
            attempted_joint.add(lm.q)
            jr = joint_callback(lm.q, min(oracle_seconds,budget.left()), best)
            if jr.get('lower') is not None:
                q_bounds[lm.q] = max(q_bounds.get(lm.q,0.0),jr['lower'])
            add_profiles(jr.get('profiles', []))
            if jr.get('counts'): accept(jr['profiles'],jr['counts'])
            emit('joint_probe', q=lm.q, joint_status=jr.get('status'))
            continue

        index = {c.id:c for c in cells}
        pending = [(index[k],z) for k,z in lm.counts.items() if not index[k].closed]
        if not pending:
            # The selected branch has only proven plateaux; the remaining gap is
            # numerical or a time-limited master bound, not a new unknown mapping.
            status='MASTER_OR_NUMERICAL_GAP'; break
        cell,z = max(pending,key=lambda t:(t[1]*(t[0].hi-t[0].lo+1), -t[0].n))
        key=(cell.n,cell.hi)
        attempts[key]=attempts.get(key,0)+1
        # Retry unresolved time-limited queries with increasing time, never treat
        # their incumbent as the true F(c,n).
        cap_seconds=min(oracle_seconds*2**min(attempts[key]-1,5),budget.left())
        if cap_seconds <= 0: break
        r=oracle.solve(cell.n,cell.hi,cap_seconds)
        calls += 1
        absorb(cell,r)
        emit('oracle', n=cell.n, capacity=cell.hi, oracle_status=r.status,
             proven_plateau=r.optimal, multiplicity=z)
    else:
        status='ITERATION_LIMIT'
    if closed_gap(upper,lower,rel_gap,abs_gap): status='GAP_CERTIFIED'
    scale=problem.dimensional_scale
    return dict(status=status, scope='all_periodic_partitions', schedule=best,
                normalized_lower_bound=lower, normalized_upper_bound=upper,
                lower_bound=lower*scale, upper_bound=upper*scale, relative_gap=gap(upper,lower),
                target_relative_gap=rel_gap, target_absolute_gap_normalized=abs_gap,
                certificate='solver-level floating-point bounds, not a rational proof',
                runtime=budget.elapsed(), oracle_queries=calls, profiles=len(profiles),
                cells=len(cells), closed_cells=sum(c.closed for c in cells),
                q_bounds=q_bounds, history=history,
                profile_catalogue=[asdict(p) for p in profiles],
                capacity_cells=[asdict(c) for c in cells])
